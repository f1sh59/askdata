"""把评测结果渲染成 Markdown / JSON。

报告是这个项目给面试官看的第一样东西：先给数字，再给失败明细。
"""

from __future__ import annotations

from .runner import EvalResult, status_label


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _cell(text: str, limit: int = 120) -> str:
    flat = " ".join(str(text).split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat.replace("|", "\\|")


def render_markdown(result: EvalResult) -> str:
    lines: list[str] = []
    add = lines.append

    add("# askdata 评测报告")
    add("")
    add(f"- **策略**：`{result.strategy_name}`")
    add(f"- **数据集**：{result.dataset_version}（{result.total} 题）")
    add(f"- **运行时间**：{result.started_at}")
    add(f"- **总耗时**：{result.elapsed_s:.2f}s")
    add("")

    add("## 1. 总览")
    add("")
    add("| 指标 | 值 |")
    add("|------|----|")
    add(f"| Execution Accuracy（严格） | **{_pct(result.ex)}**（{result.correct}/{result.total}） |")
    add(f"| Execution Accuracy（忽略列序） | {_pct(result.lenient_ex)}（{result.lenient_correct}/{result.total}） |")
    add(f"| SQL 平均执行耗时 | {result.sql_time_ms():.1f} ms |")
    add("")

    if result.extra:
        add("### 模型调用")
        add("")
        add("| 项 | 值 |")
        add("|----|----|")
        labels = {
            "model": "模型",
            "calls": "实际调用次数",
            "cache_hits": "缓存命中",
            "prompt_tokens": "prompt tokens",
            "completion_tokens": "completion tokens",
            "parse_failures": "SQL 解析失败",
            "api_errors": "接口报错",
            "latency_ms": "累计调用耗时(ms)",
        }
        for key, value in result.extra.items():
            add(f"| {labels.get(key, key)} | {value} |")
        add("")

    if any(r.first_attempt_outcome is not None for r in result.results):
        fixed = result.fixed()
        regressed = result.regressed()
        add("### 执行反馈自纠（P3）")
        add("")
        add("| 指标 | 值 |")
        add("|------|----|")
        add(f"| 一次通过 EX | {_pct(result.first_attempt_ex)} |")
        add(f"| 自纠后 EX | **{_pct(result.ex)}** |")
        add(f"| 修复题数（先错后对） | {len(fixed)} |")
        add(f"| 改坏题数（先对后错） | {len(regressed)} |")
        add(f"| 触发重试的题数 | {len(result.retried())} |")
        add(f"| 平均尝试次数 | {result.avg_attempts:.2f} |")
        add("")
        if fixed:
            add(f"- 修复：{'、'.join(r.question.id for r in fixed)}")
        if regressed:
            add(f"- 改坏：{'、'.join(r.question.id for r in regressed)}")
        if fixed or regressed:
            add("")

    add("## 2. 按难度")
    add("")
    add("| 难度 | 题数 | 正确 | EX |")
    add("|------|------|------|----|")
    for diff, n, ok, rate in result.by_difficulty():
        add(f"| {diff} | {n} | {ok} | {_pct(rate)} |")
    add("")

    cohorts = result.by_cohort()
    if cohorts:
        add("## 3. 按题组（扩题后同组内才可比）")
        add("")
        add("| 题组 | 题数 | 正确 | EX |")
        add("|------|------|------|----|")
        for cohort, n, ok, rate in cohorts:
            add(f"| {cohort} | {n} | {ok} | {_pct(rate)} |")
        add("")

    add("## 4. 失败分布")
    add("")
    add("| 状态 | 题数 | 占比 |")
    add("|------|------|------|")
    for status, count in sorted(result.status_counts().items(), key=lambda kv: -kv[1]):
        add(f"| {status_label(status)} | {count} | {_pct(count / result.total)} |")
    add("")

    add("## 5. 按标签（最容易出错的能力点排在前面）")
    add("")
    add("| 标签 | 题数 | 正确 | EX |")
    add("|------|------|------|----|")
    for tag, n, ok, rate in result.by_tag():
        add(f"| {tag} | {n} | {ok} | {_pct(rate)} |")
    add("")

    add("## 6. 逐题结果")
    add("")
    add("| ID | 难度 | 状态 | 尝试 | gold 行数 | pred 行数 | 题目 |")
    add("|----|------|------|------|-----------|-----------|------|")
    for r in result.results:
        pred_rows = "-" if r.pred is None else str(r.outcome.pred_row_count)
        add(
            f"| {r.question.id} | {r.question.difficulty} | {status_label(r.outcome.status)} "
            f"| {r.attempt_count} | {r.outcome.gold_row_count} | {pred_rows} "
            f"| {_cell(r.question.question, 40)} |"
        )
    add("")

    failures = result.failures()
    if failures:
        add("## 7. 失败明细")
        add("")
        for r in failures:
            add(f"### {r.question.id} · {r.question.difficulty} · {status_label(r.outcome.status)}")
            add("")
            add(f"- 问题：{r.question.question}")
            if r.question.note:
                add(f"- 口径：{r.question.note}")
            if r.pred is not None and r.pred.error:
                add(f"- 报错：`{_cell(r.pred.error, 300)}`")
            add("")
            add("```sql")
            add("-- gold")
            add(r.question.gold_sql)
            if r.attempt_count > 1 and r.first_attempt_sql and r.first_attempt_sql != r.generated_sql:
                add("")
                add("-- 第 1 轮")
                add(r.first_attempt_sql)
            if r.generated_sql:
                add("")
                add("-- generated")
                add(r.generated_sql)
            add("```")
            add("")

    return "\n".join(lines) + "\n"


def to_dict(result: EvalResult) -> dict:
    return {
        "strategy": result.strategy_name,
        "dataset_version": result.dataset_version,
        "started_at": result.started_at,
        "elapsed_s": round(result.elapsed_s, 3),
        "total": result.total,
        "execution_accuracy": round(result.ex, 4),
        "execution_accuracy_lenient": round(result.lenient_ex, 4),
        "extra": result.extra,
        "by_difficulty": [
            {"difficulty": d, "count": n, "correct": ok, "ex": round(rate, 4)}
            for d, n, ok, rate in result.by_difficulty()
        ],
        "by_cohort": [
            {"cohort": c, "count": n, "correct": ok, "ex": round(rate, 4)}
            for c, n, ok, rate in result.by_cohort()
        ],
        "status_counts": result.status_counts(),
        "results": [
            {
                "id": r.question.id,
                "difficulty": r.question.difficulty,
                "cohort": r.question.cohort,
                "tags": list(r.question.tags),
                "question": r.question.question,
                "status": r.outcome.status,
                "gold_row_count": r.outcome.gold_row_count,
                "pred_row_count": r.outcome.pred_row_count,
                "attempt_count": r.attempt_count,
                "first_attempt_sql": r.first_attempt_sql,
                "first_attempt_status": (
                    r.first_attempt_outcome.status if r.first_attempt_outcome is not None else None
                ),
                "generated_sql": r.generated_sql,
                "error": r.pred.error if r.pred is not None else None,
            }
            for r in result.results
        ],
    }
