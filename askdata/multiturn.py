"""P9：多轮对话评测。

问的是一个很具体的问题：**接上上文之后，追问到底能答对多少？**

协议（每次评测跑三步）：

1. **建立上下文**：先用基础题正常问一次，把它的 SQL 作为历史；
2. **对照组（单轮）**：把追问当成全新问题直接问 —— 这是"没有多轮能力"时会发生的事；
3. **实验组（多轮）**：带上历史再问，服务端先把追问改写成独立问题。

对照组和实验组用同一份标答打分，差值就是多轮能力本身的价值。
改写和生成都是新采样，所以实验组重复 N 次取均值。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import QueryResult
from .scoring import STATUS_LABELS, Outcome, score


@dataclass(slots=True)
class PairResult:
    pair_id: str
    kind: str
    followup: str
    rewritten: str | None
    sql: str | None
    outcome: Outcome
    gold_rows: int
    pred_rows: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.pair_id,
            "kind": self.kind,
            "followup": self.followup,
            "rewritten": self.rewritten,
            "sql": self.sql,
            "status": self.outcome.status,
            "status_label": STATUS_LABELS.get(self.outcome.status, self.outcome.status),
            "gold_rows": self.gold_rows,
            "pred_rows": self.pred_rows,
        }


@dataclass(slots=True)
class MultiTurnReport:
    dataset_version: str
    started_at: str
    repeats: int
    control: list[PairResult] = field(default_factory=list)
    multiturn: list[list[PairResult]] = field(default_factory=list)
    elapsed_s: float = 0.0

    def _rates(self, results: list[PairResult]) -> dict[str, float]:
        total = len(results) or 1
        correct = sum(1 for r in results if r.outcome.is_correct)
        rewritten = sum(1 for r in results if r.rewritten)
        return {
            "ex": correct / total,
            "correct": correct,
            "total": len(results),
            "rewrite_rate": rewritten / total,
        }

    @property
    def control_rate(self) -> dict[str, float]:
        return self._rates(self.control)

    @property
    def multiturn_rates(self) -> list[dict[str, float]]:
        return [self._rates(run) for run in self.multiturn]

    @property
    def mean_ex(self) -> float:
        rates = self.multiturn_rates
        return sum(r["ex"] for r in rates) / len(rates) if rates else 0.0

    def by_kind(self) -> list[tuple[str, int, int, float]]:
        """每个变换类型的通过率（按平均值向上取整为整数票）。"""
        kinds: dict[str, list[float]] = {}
        if not self.multiturn:
            return []
        for idx, pair in enumerate(self.multiturn[0]):
            kinds.setdefault(pair.kind, [])
            hits = sum(1 for run in self.multiturn if run[idx].outcome.is_correct)
            kinds[pair.kind].append(hits / len(self.multiturn))
        out = []
        for kind, values in kinds.items():
            out.append((kind, len(values), sum(values), sum(values) / len(values)))
        return sorted(out, key=lambda x: x[3], reverse=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "started_at": self.started_at,
            "repeats": self.repeats,
            "elapsed_s": round(self.elapsed_s, 1),
            "control": self._rates(self.control),
            "multiturn": self.multiturn_rates,
            "mean_ex": self.mean_ex,
            "by_kind": [
                {"kind": k, "n": n, "correct": c, "rate": r} for k, n, c, r in self.by_kind()
            ],
            "pairs": [r.as_dict() for r in self.multiturn[-1]] if self.multiturn else [],
            "control_pairs": [r.as_dict() for r in self.control],
        }


def load_pairs(path: Path) -> tuple[str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("version", "mt1.0"), payload["pairs"]


def _pred_of(response: dict) -> QueryResult | None:
    if not response.get("ok") and response.get("error"):
        return QueryResult([], [], error=str(response["error"]))
    if response.get("sql") is None:
        return None
    return QueryResult(
        list(response.get("columns") or []),
        [tuple(row) for row in (response.get("rows") or [])],
        None if response.get("ok") else str(response.get("error") or "执行失败"),
    )


def run_multiturn(
    pipeline,
    pairs: list[dict],
    repeats: int = 1,
    version: str = "mt1.0",
    progress=None,
) -> MultiTurnReport:
    report = MultiTurnReport(
        dataset_version=version,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        repeats=repeats,
    )
    started = time.perf_counter()

    gold_cache: dict[str, QueryResult] = {}
    contexts: list[dict] = []

    # 第一步：建立上下文（每题只问一次，三次重复共用同一段历史）
    for pair in pairs:
        gold = gold_cache.get(pair["id"])
        if gold is None:
            gold = pipeline.sandbox.run(pair["gold_sql"])
            gold_cache[pair["id"]] = gold
        base = pipeline.ask(pair["base_question"])
        contexts.append({"pair": pair, "history": [{"question": pair["base_question"], "sql": base.get("sql") or ""}]})

    # 第二步：对照组——把追问当全新问题问一次
    for ctx in contexts:
        pair = ctx["pair"]
        response = pipeline.ask(pair["followup"])
        outcome = score(gold_cache[pair["id"]], _pred_of(response))
        report.control.append(
            PairResult(
                pair_id=pair["id"],
                kind=pair["kind"],
                followup=pair["followup"],
                rewritten=response.get("rewritten"),
                sql=response.get("sql"),
                outcome=outcome,
                gold_rows=len(gold_cache[pair["id"]].rows),
                pred_rows=response.get("row_count", 0),
            )
        )

    # 第三步：实验组——带历史，重复 N 次
    for run_idx in range(repeats):
        run: list[PairResult] = []
        for ctx in contexts:
            pair = ctx["pair"]
            response = pipeline.ask(pair["followup"], history=ctx["history"])
            outcome = score(gold_cache[pair["id"]], _pred_of(response))
            run.append(
                PairResult(
                    pair_id=pair["id"],
                    kind=pair["kind"],
                    followup=pair["followup"],
                    rewritten=response.get("rewritten"),
                    sql=response.get("sql"),
                    outcome=outcome,
                    gold_rows=len(gold_cache[pair["id"]].rows),
                    pred_rows=response.get("row_count", 0),
                )
            )
        report.multiturn.append(run)
        if progress:
            progress(run_idx + 1, repeats, report._rates(run)["ex"])

    report.elapsed_s = time.perf_counter() - started
    return report


def render_markdown(report: MultiTurnReport) -> str:
    control = report.control_rate
    lines = [
        "# 多轮对话评测",
        "",
        f"- 数据集：{report.dataset_version}",
        f"- 开始时间：{report.started_at}",
        f"- 追问条数：{control['total']}",
        f"- 重复次数：{report.repeats}",
        "",
        "## 核心对比",
        "",
        "| 设置 | 追问准确率 | 改写出问题 |",
        "|---|---|---|",
        f"| 单轮（不带历史） | **{control['ex'] * 100:.1f}%** ({control['correct']}/{control['total']}) | — |",
    ]
    for idx, rate in enumerate(report.multiturn_rates, 1):
        lines.append(
            f"| 多轮（带历史）第 {idx} 次 | **{rate['ex'] * 100:.1f}%** "
            f"({rate['correct']}/{rate['total']}) | {rate['rewrite_rate'] * 100:.0f}% |"
        )
    delta = (report.mean_ex - control["ex"]) * 100
    lines += [
        "",
        f"多轮相对单轮：**{delta:+.1f} 个百分点**",
        "",
        "## 按追问类型",
        "",
        "| 类型 | 条数 | 平均通过 |",
        "|---|---|---|",
    ]
    for kind, n, _, rate in report.by_kind():
        lines.append(f"| {kind} | {n} | {rate * 100:.0f}% |")
    lines.append("")
    return "\n".join(lines)
