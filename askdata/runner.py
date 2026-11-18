"""评测执行器：加载数据集 → 逐题生成 SQL → 在沙箱里跑 → 打分 → 汇总。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from .db import QueryResult, Sandbox
from .models import Dataset, Question
from .scoring import (
    CORRECT,
    CORRECT_LENIENT,
    NO_SQL,
    STATUS_LABELS,
    Outcome,
    score,
)
from .strategies.base import Strategy


@dataclass(slots=True)
class QuestionResult:
    question: Question
    generated_sql: str | None
    outcome: Outcome
    gold: QueryResult
    pred: QueryResult | None
    first_attempt_sql: str | None = None
    first_attempt_outcome: Outcome | None = None
    attempt_count: int = 1


@dataclass(slots=True)
class EvalResult:
    strategy_name: str
    dataset_version: str
    started_at: str
    elapsed_s: float = 0.0
    results: list[QuestionResult] = field(default_factory=list)
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.results if r.outcome.status == CORRECT)

    @property
    def lenient_correct(self) -> int:
        return sum(1 for r in self.results if r.outcome.status in (CORRECT, CORRECT_LENIENT))

    @property
    def ex(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def lenient_ex(self) -> float:
        return self.lenient_correct / self.total if self.total else 0.0

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.outcome.status] = counts.get(r.outcome.status, 0) + 1
        return counts

    def by_difficulty(self) -> list[tuple[str, int, int, float]]:
        order = {"easy": 0, "medium": 1, "hard": 2}
        buckets: dict[str, list[QuestionResult]] = {}
        for r in self.results:
            buckets.setdefault(r.question.difficulty, []).append(r)
        rows = []
        for diff in sorted(buckets, key=lambda d: order.get(d, 99)):
            items = buckets[diff]
            ok = sum(1 for r in items if r.outcome.status == CORRECT)
            rows.append((diff, len(items), ok, ok / len(items)))
        return rows

    def by_cohort(self) -> list[tuple[str, int, int, float]]:
        """按题组统计 —— 扩题之后，只有同一个题组内部的分数才是可比的。"""
        buckets: dict[str, list[QuestionResult]] = {}
        for r in self.results:
            buckets.setdefault(r.question.cohort or "all", []).append(r)
        if len(buckets) <= 1:
            return []
        order = {"core30": 0, "ext20": 1}
        rows = []
        for cohort in sorted(buckets, key=lambda c: order.get(c, 99)):
            items = buckets[cohort]
            ok = sum(1 for r in items if r.outcome.status == CORRECT)
            rows.append((cohort, len(items), ok, ok / len(items)))
        return rows

    def by_tag(self) -> list[tuple[str, int, int, float]]:
        buckets: dict[str, list[QuestionResult]] = {}
        for r in self.results:
            for tag in r.question.tags:
                buckets.setdefault(tag, []).append(r)
        rows = [
            (tag, len(items), sum(1 for r in items if r.outcome.status == CORRECT),
             sum(1 for r in items if r.outcome.status == CORRECT) / len(items))
            for tag, items in buckets.items()
        ]
        rows.sort(key=lambda row: (row[3], -row[1]))
        return rows

    def sql_time_ms(self) -> float:
        preds = [r.pred.elapsed_ms for r in self.results if r.pred is not None and r.pred.ok]
        return sum(preds) / len(preds) if preds else 0.0

    def failures(self) -> list[QuestionResult]:
        return [r for r in self.results if r.outcome.status != CORRECT]

    # ---- P3：自纠相关指标 ----

    def _first_outcomes(self) -> list[Outcome]:
        return [r.first_attempt_outcome for r in self.results if r.first_attempt_outcome is not None]

    @property
    def first_attempt_ex(self) -> float:
        outcomes = self._first_outcomes()
        if not outcomes:
            return self.ex
        return sum(1 for o in outcomes if o.status == CORRECT) / len(outcomes)

    def fixed(self) -> list[QuestionResult]:
        """第一次错、最终对 —— 自纠救回来的题。"""
        return [
            r
            for r in self.results
            if r.first_attempt_outcome is not None
            and r.first_attempt_outcome.status != CORRECT
            and r.outcome.status == CORRECT
        ]

    def regressed(self) -> list[QuestionResult]:
        """第一次对、最终错 —— 越改越坏的题，必须报出来。"""
        return [
            r
            for r in self.results
            if r.first_attempt_outcome is not None
            and r.first_attempt_outcome.status == CORRECT
            and r.outcome.status != CORRECT
        ]

    def retried(self) -> list[QuestionResult]:
        return [r for r in self.results if r.attempt_count > 1]

    @property
    def avg_attempts(self) -> float:
        return sum(r.attempt_count for r in self.results) / self.total if self.total else 0.0


def validate_gold(sandbox: Sandbox, dataset: Dataset) -> list[str]:
    """评测集自检：gold SQL 必须能跑通、且不能返回空集。

    返回空集通常意味着数据造得不对或过滤条件写错了 —— 这种题会误导所有模型。
    """
    problems: list[str] = []
    for q in dataset.questions:
        result = sandbox.run(q.gold_sql)
        if not result.ok:
            problems.append(f"{q.id}: gold SQL 执行失败 -> {result.error}")
        elif not result.rows:
            problems.append(f"{q.id}: gold SQL 返回 0 行（{q.question}）")
    return problems


def run_eval(sandbox: Sandbox, dataset: Dataset, strategy: Strategy) -> EvalResult:
    started = datetime.now()
    t0 = time.perf_counter()

    result = EvalResult(
        strategy_name=strategy.name,
        dataset_version=dataset.version,
        started_at=started.strftime("%Y-%m-%d %H:%M:%S"),
    )

    for question in dataset.questions:
        gold = sandbox.run(question.gold_sql)
        if not gold.ok:
            raise ValueError(f"{question.id}: gold SQL 执行失败 -> {gold.error}")

        sql = strategy.generate(question)
        pred = sandbox.run(sql) if sql else None
        outcome: Outcome = score(gold, pred) if sql else Outcome(NO_SQL, len(gold.rows), 0)

        # 自纠类策略会留下每一轮的 SQL；这里额外给第一轮打分，
        # 否则报告里只看得到"最终分数"，看不到"是救回来的还是本来就对"
        attempts_map = getattr(strategy, "attempts", None)
        sequence = attempts_map.get(question.id, []) if isinstance(attempts_map, dict) else []
        first_sql: str | None = None
        first_outcome: Outcome | None = None
        if sequence:
            first_sql = sequence[0]
            first_outcome = score(gold, sandbox.run(first_sql))

        result.results.append(
            QuestionResult(
                question=question,
                generated_sql=sql,
                outcome=outcome,
                gold=gold,
                pred=pred,
                first_attempt_sql=first_sql,
                first_attempt_outcome=first_outcome,
                attempt_count=len(sequence) or 1,
            )
        )

    result.elapsed_s = time.perf_counter() - t0
    usage = getattr(strategy, "usage", None)
    if isinstance(usage, dict):
        result.extra = dict(usage)
    return result


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
