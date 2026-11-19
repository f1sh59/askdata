"""命令行入口。

    python -m askdata.cli validate                          # 评测集自检
    python -m askdata.cli eval --strategy template          # 规则 baseline
    python -m askdata.cli eval --strategy llm               # P1：全量 schema + LLM
    python -m askdata.cli eval --strategy llm-selfcorrect   # P3：执行反馈自纠
    python -m askdata.cli schema --out reports/schema.txt   # 打印给模型看的语义层
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import attacks, config, schema
from .clarify import (
    ClarifyStrategy,
    ClosedLoopStrategy,
    evaluate_control,
    evaluate_vague,
    load_closed_loop,
    load_vague,
    render_closed_markdown,
    run_closed_loop,
)
from .clarify import (
    render_markdown as render_clarify,
)
from .db import Sandbox
from .experiment import Experiment
from .experiment import render_markdown as render_experiment
from .models import load_dataset
from .report import render_markdown, to_dict
from .retrieval import TableIndex
from .runner import run_eval, validate_gold
from .strategies.llm import ExampleBank, LLMStrategy
from .strategies.selfcorrect import SelfCorrectingStrategy
from .strategies.template import TemplateStrategy

STRATEGIES = ("template", "llm", "llm-linked", "llm-selfcorrect", "llm-linked-selfcorrect")


def _load(args: argparse.Namespace):
    dataset = load_dataset(args.questions)
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        dataset.questions = [q for q in dataset.questions if q.id in wanted]
        if not dataset.questions:
            raise SystemExit(f"没有匹配的题目 id: {sorted(wanted)}")
    if args.limit:
        dataset.questions = dataset.questions[: args.limit]
    return dataset


def _build_strategy(args: argparse.Namespace, sandbox: Sandbox):
    if args.strategy == "template":
        return TemplateStrategy()

    tables = schema.introspect(sandbox)

    if "linked" in args.strategy:
        index = TableIndex(tables)

        def provider(question):
            return schema.render(index.select(tables, question.question, args.top_k))
    else:
        full_schema = schema.render(tables)

        def provider(question):
            return full_schema

    bank = None
    fewshot = getattr(args, "fewshot", 0) or 0
    if fewshot > 0:
        dataset = load_dataset(args.questions)
        bank_ids = getattr(args, "bank_ids", "") or ""
        if bank_ids:
            # 严格留出：示例池只允许用这里列出的题，避免和评测题同源
            allowed = {x.strip() for x in bank_ids.split(",") if x.strip()}
            pool = [q for q in dataset.questions if q.id in allowed]
        else:
            pool = dataset.questions
        bank = ExampleBank(pool)

    base = LLMStrategy(
        provider,
        model=args.model or None,
        use_cache=not args.no_cache,
        example_bank=bank,
        fewshot=fewshot,
    )
    suffix = "-fewshot" if fewshot > 0 else ""
    if "selfcorrect" not in args.strategy:
        base.name = ("llm-linked" if "linked" in args.strategy else "llm") + suffix
        return base

    strategy = SelfCorrectingStrategy(
        base,
        sandbox,
        max_attempts=args.max_rounds,
        retry_on_empty=not args.no_empty_retry,
    )
    strategy.name = ("llm-linked-selfcorrect" if "linked" in args.strategy else "llm-selfcorrect") + suffix
    return strategy


def cmd_validate(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.questions)
    with Sandbox(args.db) as sandbox:
        problems = validate_gold(sandbox, dataset)
    print(f"dataset={dataset.version} questions={len(dataset.questions)}")
    if problems:
        for problem in problems:
            print(f"  [FAIL] {problem}")
        return 1
    print("all gold SQL executed and returned at least one row")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    with Sandbox(args.db) as sandbox:
        text = schema.describe(sandbox)
    if args.out:
        path = config.ROOT / args.out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"written {path} ({len(text)} chars)")
    else:
        print(text)
    return 0


def cmd_attack(args: argparse.Namespace) -> int:
    """跑注入用例：既验证闸门拦得住，也验证它没把正常 SQL 一起拦掉。"""
    blocked_ok = blocked_fail = 0
    allowed_ok = allowed_fail = 0

    with Sandbox(args.db) as sandbox:
        for case in attacks.BLOCKED:
            result = sandbox.run(case.sql)
            if result.error and result.error.startswith("被安全闸拦截"):
                blocked_ok += 1
            else:
                blocked_fail += 1
                print(f"  [漏了] {case.name:<24} {result.error or '居然执行成功'}")

        for case in attacks.ALLOWED:
            result = sandbox.run(case.sql)
            if result.ok and result.rows:
                allowed_ok += 1
            else:
                allowed_fail += 1
                print(f"  [误伤] {case.name:<24} {result.error or '返回空结果'}")

    print(
        f"blocked {blocked_ok}/{len(attacks.BLOCKED)} "
        f"allowed {allowed_ok}/{len(attacks.ALLOWED)}"
    )
    return 0 if blocked_fail == 0 and allowed_fail == 0 else 1


def cmd_clarify(args: argparse.Namespace) -> int:
    """P5：该问就问，不该问别问。两边一起测。"""
    vague_questions = load_vague(args.vague)
    dataset = load_dataset(args.questions)
    controls = dataset.questions[: args.control] if args.control else dataset.questions

    with Sandbox(args.db) as sandbox:
        tables = schema.introspect(sandbox)
        index = TableIndex(tables)

        def provider(text: str) -> str:
            return schema.render(index.select(tables, text, args.top_k))

        llm = LLMStrategy(
            lambda question: "",  # 真正的 prompt 由 ClarifyStrategy 自己拼
            model=args.model or None,
            use_cache=not args.no_cache,
        )
        strategy = ClarifyStrategy(llm, provider)

        vague_results = evaluate_vague(strategy, vague_questions)
        control_results = evaluate_control(strategy, controls)

    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = config.REPORT_DIR / f"clarify_{stamp}"
    md_path = base.with_suffix(".md")
    md_path.write_text(
        render_clarify(vague_results, control_results, controls, strategy.name, llm.model),
        encoding="utf-8",
    )
    base.with_suffix(".json").write_text(
        json.dumps(
            {
                "model": llm.model,
                "vague": [
                    {
                        "id": r.item.id,
                        "question": r.item.question,
                        "status": r.status,
                        "coverage": round(r.coverage, 3),
                        "asked": r.decision.need_clarification,
                        "clarification": r.decision.question,
                    }
                    for r in vague_results
                ],
                "control": [
                    {"id": q.id, "asked": c.asked} for q, c in zip(controls, control_results, strict=True)
                ],
                "usage": llm.usage,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    total = len(vague_results)
    asked = sum(1 for r in vague_results if r.decision.need_clarification)
    good = sum(1 for r in vague_results if r.status == "good")
    over = sum(1 for c in control_results if c.asked)
    print(
        f"vague={total} 追问率={asked / total:.0%} 问到点上={good / total:.0%} "
        f"control={len(control_results)} 误反问率={over / len(control_results):.0%}"
    )
    print(f"report={md_path}")
    return 0


def cmd_closed(args: argparse.Namespace) -> int:
    """P5.5：问了之后，能不能收敛到正确 SQL。"""
    items = load_closed_loop(args.vague)

    with Sandbox(args.db) as sandbox:
        tables = schema.introspect(sandbox)
        index = TableIndex(tables)

        def provider(text: str) -> str:
            return schema.render(index.select(tables, text, args.top_k))

        # 同一个实例同时给 baseline 和闭环用，避免两个缓存互相覆盖
        llm = LLMStrategy(
            lambda question: provider(question.question),
            model=args.model or None,
            use_cache=not args.no_cache,
        )
        closed = ClosedLoopStrategy(llm, provider)
        results = run_closed_loop(sandbox, items, closed, llm)

    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = config.REPORT_DIR / f"closed_{stamp}"
    md_path = base.with_suffix(".md")
    md_path.write_text(render_closed_markdown(results, llm.model or ""), encoding="utf-8")
    base.with_suffix(".json").write_text(
        json.dumps(
            {
                "model": llm.model,
                "baseline_ex": sum(1 for r in results if r.baseline_status == "correct") / len(results),
                "closed_ex": sum(1 for r in results if r.closed_status == "correct") / len(results),
                "usage": llm.usage,
                "items": [
                    {
                        "id": r.item.id,
                        "asked": r.asked,
                        "baseline_status": r.baseline_status,
                        "closed_status": r.closed_status,
                    }
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    total = len(results)
    base_ok = sum(1 for r in results if r.baseline_status == "correct")
    closed_ok = sum(1 for r in results if r.closed_status == "correct")
    print(
        f"baseline（不问直接答）={base_ok}/{total} ({base_ok / total:.0%})  "
        f"closed-loop（问一轮再答）={closed_ok}/{total} ({closed_ok / total:.0%})  "
        f"救回={sum(1 for r in results if r.fixed)}"
    )
    print(f"report={md_path}")
    return 0


def cmd_multiturn(args: argparse.Namespace) -> int:
    from .multiturn import load_pairs, run_multiturn
    from .multiturn import render_markdown as render_mt
    from .service import Pipeline

    version, pairs = load_pairs(args.pairs)
    if args.limit:
        pairs = pairs[: args.limit]

    pipeline = Pipeline()
    try:
        report = run_multiturn(
            pipeline,
            pairs,
            repeats=args.repeat,
            version=version,
            progress=lambda i, n, ex: print(f"  run {i}/{n}: 追问 EX={ex * 100:.1f}%", flush=True),
        )
    finally:
        pipeline.sandbox.close()

    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = config.REPORT_DIR / f"multiturn_{stamp}"
    base.with_suffix(".md").write_text(render_mt(report), encoding="utf-8")
    base.with_suffix(".json").write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    control = report.control_rate
    print(
        f"pairs={control['total']} repeats={report.repeats} "
        f"单轮={control['ex'] * 100:.1f}% 多轮={report.mean_ex * 100:.1f}% "
        f"改写产出率={report.multiturn_rates[0]['rewrite_rate'] * 100:.0f}%"
    )
    for kind, n, _, rate in report.by_kind():
        print(f"  {kind:<12} {rate * 100:>5.1f}%  ({n} 条)")
    print(f"report={base.with_suffix('.md')}")
    print(f"json={base.with_suffix('.json')}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    dataset = _load(args)

    if args.repeat <= 1:
        with Sandbox(args.db) as sandbox:
            strategy = _build_strategy(args, sandbox)
            result = run_eval(sandbox, dataset, strategy)

        config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = config.REPORT_DIR / f"eval_{result.strategy_name}_{stamp}"
        md_path = base.with_suffix(".md")
        json_path = base.with_suffix(".json")
        md_path.write_text(render_markdown(result), encoding="utf-8")
        json_path.write_text(
            json.dumps(to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(
            f"strategy={result.strategy_name} questions={result.total} "
            f"EX={result.ex * 100:.1f}% ({result.correct}/{result.total}) "
            f"lenient={result.lenient_ex * 100:.1f}%"
        )
        for diff, n, ok, rate in result.by_difficulty():
            print(f"  {diff:<7} {ok:>2}/{n:<2} {rate * 100:>5.1f}%")
        if result.extra:
            print("  " + " ".join(f"{key}={value}" for key, value in result.extra.items()))
        print(f"report={md_path}")
        print(f"json={json_path}")
        return 0

    # 重复运行：每次都要新的采样，所以强制关缓存
    args.no_cache = True
    experiment = Experiment(strategy_name=args.strategy, dataset_version=dataset.version)
    with Sandbox(args.db) as sandbox:
        for idx in range(args.repeat):
            strategy = _build_strategy(args, sandbox)
            run = run_eval(sandbox, dataset, strategy)
            experiment.strategy_name = strategy.name
            experiment.runs.append(run)
            print(
                f"  run {idx + 1}/{args.repeat}: EX={run.ex * 100:.1f}% "
                f"({run.correct}/{run.total}) tokens={run.extra.get('prompt_tokens', 0):,}"
            )

    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = config.REPORT_DIR / f"repeat_{experiment.strategy_name}_{stamp}"
    md_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")
    md_path.write_text(render_experiment(experiment), encoding="utf-8")
    json_path.write_text(
        json.dumps(experiment.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"strategy={experiment.strategy_name} runs={experiment.n_runs} "
        f"mean={experiment.mean_ex * 100:.1f}% std={experiment.std_ex * 100:.1f}% "
        f"range={experiment.min_ex * 100:.1f}%~{experiment.max_ex * 100:.1f}%"
    )
    print(f"unstable={len(experiment.unstable())} stable_fail={len(experiment.stable_failures())}")
    print(f"report={md_path}")
    print(f"json={json_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="askdata", description="askdata Text2SQL 评测工具")
    parser.add_argument("--db", default=str(config.DB_PATH), help="DuckDB 数据库路径")
    parser.add_argument("--questions", default=str(config.DEFAULT_QUESTIONS), help="评测集 JSON 路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="校验 gold SQL 是否可执行且非空")
    p_validate.set_defaults(func=cmd_validate)

    p_schema = sub.add_parser("schema", help="输出给模型看的 schema 语义层")
    p_schema.add_argument("--out", default="", help="写入文件（相对项目根目录）")
    p_schema.set_defaults(func=cmd_schema)

    p_attack = sub.add_parser("attack", help="跑 SQL 注入用例，验证四道闸")
    p_attack.set_defaults(func=cmd_attack)

    p_clarify = sub.add_parser("clarify", help="P5：该反问时有没有反问")
    p_clarify.add_argument("--vague", default=str(config.EVAL_DIR / "vague_v1.json"),
                           help="待澄清题集")
    p_clarify.add_argument("--control", type=int, default=50,
                           help="用前 N 道完整题做对照（0 表示全部）")
    p_clarify.add_argument("--top-k", type=int, default=15, help="召回表数量")
    p_clarify.add_argument("--model", default="", help="覆盖 LLM 模型名")
    p_clarify.add_argument("--no-cache", action="store_true", help="跳过响应缓存")
    p_clarify.set_defaults(func=cmd_clarify)

    p_closed = sub.add_parser("closed", help="P5.5：澄清之后能不能收敛到正确 SQL")
    p_closed.add_argument("--vague", default=str(config.EVAL_DIR / "vague_v1.json"),
                          help="含 answer / gold_sql 的题集")
    p_closed.add_argument("--top-k", type=int, default=15, help="召回表数量")
    p_closed.add_argument("--model", default="", help="覆盖 LLM 模型名")
    p_closed.add_argument("--no-cache", action="store_true", help="跳过响应缓存")
    p_closed.set_defaults(func=cmd_closed)

    p_eval = sub.add_parser("eval", help="跑评测并生成报告")
    p_eval.add_argument("--strategy", default="template", choices=STRATEGIES)
    p_eval.add_argument("--ids", default="", help="只跑指定题目，逗号分隔，如 e01,m03")
    p_eval.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    p_eval.add_argument("--model", default="", help="覆盖 LLM 模型名")
    p_eval.add_argument("--no-cache", action="store_true", help="跳过 LLM 响应缓存")
    p_eval.add_argument("--max-rounds", type=int, default=3,
                        help="自纠策略的总尝试次数（含首次），默认 3")
    p_eval.add_argument("--no-empty-retry", action="store_true",
                        help="自纠时只在执行报错才重试，结果为空不重试")
    p_eval.add_argument("--top-k", type=int, default=6,
                        help="llm-linked 策略交给模型的表数量，默认 6")
    p_eval.add_argument("--repeat", type=int, default=1,
                        help="同一配置重复运行 N 次（自动关闭缓存），用于估计波动")
    p_eval.add_argument("--fewshot", type=int, default=0,
                        help="动态 few-shot 的示例数量（0 = 关闭）。示例从题库里按 BM25 检索，"
                             "评测时 leave-one-out")
    p_eval.add_argument("--bank-ids", default="",
                        help="限定示例池只能来自这些题号（逗号分隔），用于严格留出实验")
    p_eval.set_defaults(func=cmd_eval)

    p_mt = sub.add_parser("multiturn", help="P9：多轮对话评测（追问能不能答对）")
    p_mt.add_argument("--pairs", type=Path, default=Path("eval/multiturn_v1.json"),
                      help="多轮评测集路径")
    p_mt.add_argument("--repeat", type=int, default=1, help="实验组重复次数")
    p_mt.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    p_mt.set_defaults(func=cmd_multiturn)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        # 题库、报告这类文件不存在时，给人话而不是一串 Traceback
        print(f"[ERROR] 找不到文件：{exc.filename}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"[ERROR] JSON 格式不对：{exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        # 典型场景：没配 DEEPSEEK_API_KEY
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
