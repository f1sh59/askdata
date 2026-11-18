"""重复运行与统计：把"一次跑分"变成"一个可以下结论的实验"。

单次跑分的问题在 P3 阶段就暴露了：同一个 prompt 两次跑出 83.3% 和 90.0%。
50 道题、±3 题的波动，意味着任何小于 6 个点的效应都测不出来。
所以任何"配置 A 比配置 B 好"的结论，都必须先回答"这个差异大于噪声吗"。

这里只做三件事：
1. 把同一配置跑 N 次，给出均值、标准差、极值；
2. 统计每道题在 N 次里的通过次数，找出"翻来覆去"的题；
3. 把结果落成 JSON，交给 `scripts/compare_runs.py` 做配对 bootstrap。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .runner import EvalResult, status_label


@dataclass(slots=True)
class Experiment:
    strategy_name: str
    dataset_version: str
    runs: list[EvalResult] = field(default_factory=list)

    @property
    def n_runs(self) -> int:
        return len(self.runs)

    @property
    def per_run_ex(self) -> list[float]:
        return [run.ex for run in self.runs]

    @property
    def mean_ex(self) -> float:
        return statistics.fmean(self.per_run_ex) if self.runs else 0.0

    @property
    def std_ex(self) -> float:
        """样本标准差；只有一次运行时返回 0。"""
        return statistics.stdev(self.per_run_ex) if len(self.runs) > 1 else 0.0

    @property
    def min_ex(self) -> float:
        return min(self.per_run_ex) if self.runs else 0.0

    @property
    def max_ex(self) -> float:
        return max(self.per_run_ex) if self.runs else 0.0

    def passes(self) -> dict[str, int]:
        """每道题在多少次运行里答对了。"""
        counts: dict[str, int] = {}
        for run in self.runs:
            for result in run.results:
                if result.outcome.status == "correct":
                    counts[result.question.id] = counts.get(result.question.id, 0) + 1
                else:
                    counts.setdefault(result.question.id, 0)
        return counts

    def stability_histogram(self) -> dict[int, list[str]]:
        buckets: dict[int, list[str]] = {}
        for qid, passed in sorted(self.passes().items(), key=lambda kv: (-kv[1], kv[0])):
            buckets.setdefault(passed, []).append(qid)
        return dict(sorted(buckets.items(), reverse=True))

    def unstable(self) -> list[str]:
        """0 < 通过次数 < N —— 这些题的分数在跑与跑之间是随机的。"""
        return [
            qid
            for qid, passed in self.passes().items()
            if 0 < passed < self.n_runs
        ]

    def stable_failures(self) -> list[str]:
        return [qid for qid, passed in self.passes().items() if passed == 0]

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "dataset_version": self.dataset_version,
            "n_runs": self.n_runs,
            "mean_ex": round(self.mean_ex, 4),
            "std_ex": round(self.std_ex, 4),
            "min_ex": round(self.min_ex, 4),
            "max_ex": round(self.max_ex, 4),
            "runs": [
                {
                    "ex": round(run.ex, 4),
                    "correct": run.correct,
                    "total": run.total,
                    "prompt_tokens": run.extra.get("prompt_tokens", 0),
                    "completion_tokens": run.extra.get("completion_tokens", 0),
                    "latency_ms": run.extra.get("latency_ms", 0),
                    "api_errors": run.extra.get("api_errors", 0),
                }
                for run in self.runs
            ],
            "passes": self.passes(),
        }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(experiment: Experiment) -> str:
    lines: list[str] = []
    add = lines.append

    add("# askdata 重复运行报告")
    add("")
    add(f"- **策略**：`{experiment.strategy_name}`")
    add(f"- **数据集**：{experiment.dataset_version}（{experiment.runs[0].total} 题）")
    add(f"- **运行次数**：{experiment.n_runs}（每次都是独立采样，未使用缓存）")
    add("")
    add("## 1. 总体")
    add("")
    add("| 指标 | 值 |")
    add("|------|----|")
    add(f"| 均值 EX | **{_pct(experiment.mean_ex)}** |")
    add(f"| 标准差 | {_pct(experiment.std_ex)} |")
    add(f"| 区间 | {_pct(experiment.min_ex)} ~ {_pct(experiment.max_ex)} |")
    if experiment.n_runs > 1:
        se = experiment.std_ex / (experiment.n_runs ** 0.5)
        add(f"| 标准误 | {_pct(se)} |")
    add("")

    add("## 2. 逐次运行")
    add("")
    add("| # | EX | 正确 | prompt tokens | 调用耗时 | 接口报错 |")
    add("|---|----|------|---------------|----------|----------|")
    for idx, run in enumerate(experiment.runs, start=1):
        add(
            f"| {idx} | {_pct(run.ex)} | {run.correct}/{run.total} "
            f"| {run.extra.get('prompt_tokens', 0):,} "
            f"| {run.extra.get('latency_ms', 0) / 1000:.1f}s "
            f"| {run.extra.get('api_errors', 0)} |"
        )
    add("")

    add("## 3. 题目稳定性")
    add("")
    add("> 每道题在 N 次运行里对了几次。「N/N」是稳的，「中间值」是分数波动的来源。")
    add("")
    add("| 通过次数 | 题数 | 题目 |")
    add("|----------|------|------|")
    histogram = experiment.stability_histogram()
    for passed in sorted(histogram, reverse=True):
        ids = histogram[passed]
        add(f"| {passed}/{experiment.n_runs} | {len(ids)} | {'、'.join(ids)} |")
    add("")

    unstable = experiment.unstable()
    failures = experiment.stable_failures()
    add("## 4. 结论")
    add("")
    if experiment.n_runs == 1:
        add("- 只跑了一次，无法估计波动；要用 `--repeat N` 才有统计意义。")
    else:
        add(
            f"- **稳定通过**：{len(experiment.stability_histogram().get(experiment.n_runs, []))} 题；"
            f"**稳定失败**：{len(failures)} 题；**不稳定**：{len(unstable)} 题。"
        )
        if unstable:
            add(f"- 不稳定题：{'、'.join(unstable)}")
            add(
                f"- 这 {len(unstable)} 道题贡献了 ±{_pct(len(unstable) / experiment.runs[0].total)} 的随机波动，"
                f"任何小于这个幅度的改进都不该被当真。"
            )
        add(
            f"- 每次运行的 EX 区间是 {_pct(experiment.min_ex)} ~ {_pct(experiment.max_ex)}；"
            "单次跑分只应被当作这个区间里的一个样本。"
        )
    add("")

    add("## 5. 稳定失败的题")
    add("")
    if not failures:
        add("无。")
    else:
        for qid in failures:
            question = next(
                r.question for r in experiment.runs[0].results if r.question.id == qid
            )
            statuses = [
                status_label(next(r for r in run.results if r.question.id == qid).outcome.status)
                for run in experiment.runs
            ]
            add(f"- **{qid}**（{question.difficulty}）：{question.question}")
            add(f"  - 各轮状态：{' / '.join(statuses)}")
    add("")
    return "\n".join(lines)
