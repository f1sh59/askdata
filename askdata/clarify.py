"""P5：该不该反问。

两类问题要一起测，只测一边会得到荒谬的结论：

* **该问的（vague）**：题目被挖掉了时间范围/指标/口径。系统应该追问，而不是猜一个算错。
* **不该问的（control）**：原有 50 道题信息完整。系统不应该反问，否则就是个没用的系统。

所以指标是一对：**追问率**与**误反问率**。只优化前者，系统就会变成"什么都问"。

判分方式说明：这里用**关键词覆盖**判断"有没有问对地方"，而不是让另一个 LLM 当裁判。
理由是可复现、零成本、可解释；代价是粗暴——它只能判断"问到了时间范围"，
判断不了"问得好不好"。这个取舍写在 README 里。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .schema import BUSINESS_RULES
from .strategies.llm import extract_sql

SYSTEM_PROMPT = "你是一名数据分析师助手。你只输出 JSON，不输出任何解释。"

USER_TEMPLATE = """\
你要把用户的问题翻译成 SQL。但在动手之前，先判断：**这个问题的信息够不够写出唯一确定的 SQL？**

## 数据库结构
{schema}

## 业务口径（已知）
{rules}

## 什么算"信息不够"
1. 没说时间范围（"最近""目前""现在"都不算明确的时间范围）。
2. 没说统计哪个指标（"卖得怎么样""数据怎么样"）。
3. 用了没定义的口径（"复购率""退款率""利润""活跃用户"，却没说是怎么算的）。
4. 没说按什么排序或拆维度（"Top 商品"没说是按销售额还是销量）。
5. 统计对象不明（"帮我看下数据"）。

## 什么算"信息够了"
问题里有明确的时间范围、明确的指标、口径在下面已经写明（比如"有效订单""销售额"），
那就直接写 SQL，不要反问。

## 输出格式（只输出这段 JSON，不要代码块）
{{"need_clarification": true 或 false,
  "question": "要反问用户的话（need_clarification 为 true 时必填）",
  "sql": "SQL 语句（need_clarification 为 false 时必填）"}}

用户问题：{question}"""

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S | re.I)
_BRACES = re.compile(r"\{.*\}", re.S)


@dataclass(slots=True)
class ClarifyDecision:
    need_clarification: bool
    question: str = ""
    sql: str = ""
    raw: str = ""
    parse_error: str | None = None


@dataclass(slots=True)
class Slot:
    name: str
    keywords: tuple[str, ...]


@dataclass(slots=True)
class VagueQuestion:
    id: str
    question: str
    why: str
    slots: list[Slot] = field(default_factory=list)
    source_id: str = ""


@dataclass(slots=True)
class VagueResult:
    item: VagueQuestion
    decision: ClarifyDecision
    covered: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.decision.need_clarification:
            return "guessed"
        if not self.item.slots:
            return "asked"
        if len(self.covered) == len(self.item.slots):
            return "good"
        return "partial" if self.covered else "irrelevant"

    @property
    def coverage(self) -> float:
        if not self.item.slots:
            return 1.0 if self.decision.need_clarification else 0.0
        return len(self.covered) / len(self.item.slots)


def load_vague(path: str | Path) -> list[VagueQuestion]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        VagueQuestion(
            id=item["id"],
            question=item["question"],
            why=item.get("why", ""),
            source_id=item.get("source_id", ""),
            slots=[Slot(s["name"], tuple(s.get("keywords", ()))) for s in item.get("slots", [])],
        )
        for item in raw["questions"]
    ]


def parse_decision(text: str) -> ClarifyDecision:
    """从模型回复里抠出 JSON。模型很爱加代码块和解释，所以先剥壳再找花括号。"""
    body = text.strip()
    fenced = _FENCE.search(body)
    if fenced:
        body = fenced.group(1)

    match = _BRACES.search(body)
    if not match:
        return ClarifyDecision(False, raw=text, parse_error="回复里没有 JSON")

    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return ClarifyDecision(False, raw=text, parse_error=f"JSON 解析失败：{exc}")

    need = bool(payload.get("need_clarification"))
    return ClarifyDecision(
        need_clarification=need,
        question=str(payload.get("question") or "").strip(),
        sql=str(payload.get("sql") or "").strip(),
        raw=text,
    )


class ClarifyStrategy:
    name = "llm-clarify"

    def __init__(
        self,
        llm,
        schema_provider: Callable[[str], str],
        model: str | None = None,
    ) -> None:
        self._llm = llm
        self._schema_provider = schema_provider
        self.model = getattr(llm, "model", model)

    @property
    def usage(self) -> dict:
        return self._llm.usage

    def decide(self, question_text: str) -> ClarifyDecision:
        prompt = USER_TEMPLATE.format(
            schema=self._schema_provider(question_text),
            rules=BUSINESS_RULES,
            question=question_text,
        )
        response = self._llm.complete(prompt)
        if response is None:
            return ClarifyDecision(False, parse_error="调用失败")
        return parse_decision(response.content)


def covered_slots(item: VagueQuestion, question_text: str) -> list[str]:
    haystack = question_text.lower()
    return [
        slot.name
        for slot in item.slots
        if any(keyword.lower() in haystack for keyword in slot.keywords)
    ]


def evaluate_vague(strategy: ClarifyStrategy, items: list[VagueQuestion]) -> list[VagueResult]:
    results = []
    for item in items:
        decision = strategy.decide(item.question)
        results.append(
            VagueResult(item=item, decision=decision, covered=covered_slots(item, decision.question))
        )
    return results


@dataclass(slots=True)
class ControlResult:
    asked: bool
    sql: str


def evaluate_control(strategy: ClarifyStrategy, questions) -> list[ControlResult]:
    """信息完整的题不该被反问。问了就是"过度澄清"，系统会变得没法用。"""
    results = []
    for question in questions:
        decision = strategy.decide(question.question)
        results.append(ControlResult(asked=decision.need_clarification, sql=decision.sql))
    return results


def _cell(text: str, limit: int = 60) -> str:
    flat = " ".join(str(text).split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat.replace("|", "\\|")


STATUS_LABELS = {
    "good": "问到点上",
    "partial": "问了一部分",
    "irrelevant": "问了但没问对",
    "guessed": "没问，直接猜",
    "asked": "已追问",
}


def render_markdown(
    results: list[VagueResult],
    controls: list[ControlResult],
    control_questions,
    strategy_name: str,
    model: str,
) -> str:
    total = len(results)
    asked = sum(1 for r in results if r.decision.need_clarification)
    good = sum(1 for r in results if r.status == "good")
    partial = sum(1 for r in results if r.status == "partial")
    irrelevant = sum(1 for r in results if r.status == "irrelevant")
    guessed = sum(1 for r in results if r.status == "guessed")
    avg_cov = sum(r.coverage for r in results) / total if total else 0.0

    over_asked = [q for q, c in zip(control_questions, controls, strict=True) if c.asked]
    over_rate = len(over_asked) / len(controls) if controls else 0.0

    lines: list[str] = []
    add = lines.append
    add("# askdata 澄清能力评测")
    add("")
    add(f"- **模型**：{model}")
    add(f"- **策略**：`{strategy_name}`")
    add(f"- **待澄清题**：{total} 题（信息被刻意挖掉）")
    add(f"- **对照题**：{len(controls)} 题（信息完整，不应该反问）")
    add("")
    add("## 1. 总览")
    add("")
    add("| 指标 | 值 | 说明 |")
    add("|------|----|------|")
    add(f"| 追问率 | {asked / (total or 1):.0%} | 该反问的题里，实际反问了的比例 |")
    add(f"| 问到点上 | {good / total:.0%} | 反问覆盖了全部缺失点 |")
    add(f"| 只问了一部分 | {partial / total:.0%} | 覆盖了部分缺失点 |")
    add(f"| 问了但没问对 | {irrelevant / total:.0%} | 反问了，但一个缺失点都没覆盖 |")
    add(f"| 猜测率 | {guessed / total:.0%} | 不追问，直接给 SQL |")
    add(f"| 平均槽位覆盖 | {avg_cov:.0%} | 缺失点的平均覆盖比例 |")
    add(f"| **误反问率** | **{over_rate:.0%}** | 信息完整的题被反问的比例（越低越好） |")
    add("")
    add("> 追问率和误反问率要一起看：只追求前者，系统会变成「什么都问」。")
    add("")
    add("## 2. 逐题结果")
    add("")
    add("| ID | 用户问题 | 缺什么 | 决策 | 覆盖 | 判定 |")
    add("|----|----------|--------|------|------|------|")
    for r in results:
        decision_text = r.decision.question if r.decision.need_clarification else "（直接出 SQL）"
        add(
            f"| {r.item.id} | {_cell(r.item.question, 24)} | {_cell(r.item.why, 24)} "
            f"| {_cell(decision_text, 40)} | {r.coverage:.0%} | {STATUS_LABELS.get(r.status, r.status)} |"
        )
    add("")
    add("## 3. 反问原文（便于人工复核）")
    add("")
    for r in results:
        if not r.decision.need_clarification:
            continue
        add(f"**{r.item.id}**：用户问「{r.item.question}」")
        add("")
        add(f"> {r.decision.question}")
        add("")
    add("## 4. 误反问的完整题")
    add("")
    if not over_asked:
        add("无。")
    else:
        for q in over_asked:
            add(f"- **{q.id}**：{q.question}")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------- P5.5 闭环

CLOSED_TEMPLATE = """\
你要把用户的问题翻译成一条可以在 DuckDB 上执行的 SQL。

## 数据库结构
{schema}

## 业务口径（必须遵守）
{rules}

## 对话
用户：{question}
助手：{clarification}
用户：{answer}

现在请根据补全后的信息输出 SQL。要求：
1. 只输出一条 SELECT 语句，放在 ```sql 代码块中，不要解释。
2. 使用 DuckDB 方言。
3. 结果列的顺序与用户明确要求的顺序一致。

用户问题：{question}"""


@dataclass(slots=True)
class ClosedLoopItem:
    id: str
    question: str
    answer: str
    gold_sql: str
    source_id: str = ""


def load_closed_loop(path: str | Path) -> list[ClosedLoopItem]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = [
        ClosedLoopItem(
            id=item["id"],
            question=item["question"],
            answer=item["answer"],
            gold_sql=item["gold_sql"],
            source_id=item.get("source_id", ""),
        )
        for item in raw["questions"]
    ]
    missing = [item.id for item in items if not item.answer or not item.gold_sql]
    if missing:
        raise ValueError(f"以下题目缺少 answer/gold_sql：{missing}")
    return items


class ClosedLoopStrategy:
    """两轮：先判断要不要问；问了就把「原问题 + 反问 + 用户补充」一起交给模型出 SQL。"""

    name = "llm-closed-loop"

    def __init__(self, llm, schema_provider: Callable[[str], str]) -> None:
        self._llm = llm
        self._schema = schema_provider
        self.model = getattr(llm, "model", None)

    @property
    def usage(self) -> dict:
        return self._llm.usage

    def answer(self, item: ClosedLoopItem) -> tuple[str | None, ClarifyDecision]:
        first_prompt = USER_TEMPLATE.format(
            schema=self._schema(item.question),
            rules=BUSINESS_RULES,
            question=item.question,
        )
        first = self._llm.complete(first_prompt)
        decision = parse_decision(first.content) if first else ClarifyDecision(False)

        if not decision.need_clarification:
            return (decision.sql or None), decision

        second = self._llm.complete(
            CLOSED_TEMPLATE.format(
                schema=self._schema(item.question),
                rules=BUSINESS_RULES,
                question=item.question,
                clarification=decision.question,
                answer=item.answer,
            )
        )
        if second is None:
            return None, decision
        # 模板要求"只输出 SQL 代码块"，但模型经常不听话、继续回 JSON。
        # 先试 JSON（里面有 sql 字段就取它），再退回抠代码块——
        # 否则 extract_sql 会把 {"sql": "SELECT ..."} 的尾巴一起切进 SQL 里。
        parsed = parse_decision(second.content)
        if parsed.sql:
            return parsed.sql, decision
        return extract_sql(second.content), decision


@dataclass(slots=True)
class ClosedResult:
    item: ClosedLoopItem
    asked: bool
    baseline_sql: str | None
    baseline_status: str
    closed_sql: str | None
    closed_status: str

    @property
    def fixed(self) -> bool:
        return self.baseline_status != "correct" and self.closed_status == "correct"


def run_closed_loop(sandbox, items, closed: ClosedLoopStrategy, baseline) -> list[ClosedResult]:
    """同一批题跑两条路：不问直接写 SQL（baseline） vs 先问一轮再写（closed）。"""
    from .models import Question
    from .scoring import NO_SQL, Outcome, score

    results: list[ClosedResult] = []
    for item in items:
        gold = sandbox.run(item.gold_sql)
        if not gold.ok:
            raise ValueError(f"{item.id}: gold SQL 执行失败 -> {gold.error}")

        question = Question(
            id=item.id, difficulty="vague", tags=(), question=item.question, gold_sql=item.gold_sql
        )
        baseline_sql = baseline.generate(question)
        baseline_outcome: Outcome = (
            score(gold, sandbox.run(baseline_sql))
            if baseline_sql
            else Outcome(NO_SQL, len(gold.rows), 0)
        )

        closed_sql, decision = closed.answer(item)
        closed_outcome: Outcome = (
            score(gold, sandbox.run(closed_sql))
            if closed_sql
            else Outcome(NO_SQL, len(gold.rows), 0)
        )

        results.append(
            ClosedResult(
                item=item,
                asked=decision.need_clarification,
                baseline_sql=baseline_sql,
                baseline_status=baseline_outcome.status,
                closed_sql=closed_sql,
                closed_status=closed_outcome.status,
            )
        )
    return results


def render_closed_markdown(results: list[ClosedResult], model: str) -> str:
    from .runner import status_label

    total = len(results)
    base_ok = sum(1 for r in results if r.baseline_status == "correct")
    closed_ok = sum(1 for r in results if r.closed_status == "correct")
    asked = sum(1 for r in results if r.asked)
    fixed = sum(1 for r in results if r.fixed)
    broken = sum(
        1 for r in results if r.baseline_status == "correct" and r.closed_status != "correct"
    )

    lines: list[str] = []
    add = lines.append
    add("# askdata 澄清闭环评测（P5.5）")
    add("")
    add(f"- **模型**：{model}")
    add(f"- **题目**：{total} 道含糊问题，每题配一条用户补充回答和对应 gold SQL")
    add("")
    add("## 1. 对比")
    add("")
    add("| 路线 | 正确 | EX | 说明 |")
    add("|------|------|----|------|")
    # total 为 0 时不能直接除——实验被中断时 results 可能是空的
    safe_total = total or 1
    add(
        f"| 不问，直接写 SQL | {base_ok}/{total} | {base_ok / safe_total:.0%} "
        "| 模型只能自己猜时间/指标/口径 |"
    )
    add(
        f"| 先反问一轮再写 SQL | {closed_ok}/{total} | {closed_ok / safe_total:.0%} "
        "| 用户补充信息后再出 SQL |"
    )
    add("")
    add(f"- 追问率：{asked / safe_total:.0%}")
    add(f"- 被澄清救回来：{fixed} 题")
    add(f"- 反而改坏：{broken} 题")
    add("")
    add("## 2. 逐题")
    add("")
    add("| ID | 用户问题 | 补充回答 | 直接答 | 澄清后 |")
    add("|----|----------|----------|--------|--------|")
    for r in results:
        add(
            f"| {r.item.id} | {_cell(r.item.question, 22)} | {_cell(r.item.answer, 30)} "
            f"| {status_label(r.baseline_status)} | {status_label(r.closed_status)} |"
        )
    add("")
    add("## 3. 澄清后仍然失败的题")
    add("")
    failed = [r for r in results if r.closed_status != "correct"]
    if not failed:
        add("无。")
    else:
        for r in failed:
            add(f"### {r.item.id} · {status_label(r.closed_status)}")
            add("")
            add(f"- 原问题：{r.item.question}")
            add(f"- 用户补充：{r.item.answer}")
            add("")
            add("```sql")
            add("-- gold")
            add(r.item.gold_sql)
            add("")
            add("-- generated")
            add(r.closed_sql or "（无）")
            add("```")
            add("")
    return "\n".join(lines)

