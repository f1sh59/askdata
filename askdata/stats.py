"""统计工具：给"A 比 B 好"这句话配一个置信区间。

评测的噪声来自两层：
1. **题目层**：50 道题只是一个样本，换一批题结果就变；
2. **采样层**：同一个 prompt 跑两次，模型给出的 SQL 也可能不同（P3 阶段实测到过 83.3% / 90.0%）。

`--repeat N` 只能估计第 2 层。第 1 层用配对 bootstrap 处理：
对题目做有放回重采样，算出「配对差值」的分布，看 0 在不在区间里。

配对是关键词 —— 两个配置跑的是同一批题，按题配对比按整体均值比较有效得多。
"""

from __future__ import annotations

import random
import statistics


def paired_bootstrap(
    a: list[float],
    b: list[float],
    n_boot: int = 10000,
    seed: int = 0,
) -> dict[str, float]:
    """对两个等长序列做配对 bootstrap，返回差值（a - b）的均值与 95% 区间。"""
    if len(a) != len(b):
        raise ValueError("配对 bootstrap 要求两组长度一致")
    diffs = [x - y for x, y in zip(a, b)]
    if not diffs:
        raise ValueError("空输入")

    rng = random.Random(seed)
    n = len(diffs)
    samples: list[float] = []
    for _ in range(n_boot):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        samples.append(total / n)
    samples.sort()

    return {
        "mean_diff": statistics.fmean(diffs),
        "ci_low": samples[int(0.025 * n_boot)],
        "ci_high": samples[min(int(0.975 * n_boot), n_boot - 1)],
        "p_le_zero": sum(1 for value in samples if value <= 0) / n_boot,
        "n": n,
    }


def summarize(samples: list[float]) -> dict[str, float]:
    """均值 / 标准差 / 标准误；样本少于 2 个时标准差记 0。"""
    mean = statistics.fmean(samples) if samples else 0.0
    std = statistics.stdev(samples) if len(samples) > 1 else 0.0
    return {
        "mean": mean,
        "std": std,
        "se": std / (len(samples) ** 0.5) if samples else 0.0,
        "min": min(samples) if samples else 0.0,
        "max": max(samples) if samples else 0.0,
    }
