"""比较两份 `--repeat` 报告，判断差异是否大于噪声。

用法：
    python scripts/compare_runs.py reports/repeat_llm_xxx.json reports/repeat_llm-linked_yyy.json

两个配置必须跑的是同一批题（按题 id 配对）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from askdata.stats import paired_bootstrap


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pass_rates(payload: dict) -> dict[str, float]:
    n = payload["n_runs"]
    return {qid: passed / n for qid, passed in payload["passes"].items()}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1

    a, b = load(argv[0]), load(argv[1])
    rates_a, rates_b = pass_rates(a), pass_rates(b)

    shared = sorted(set(rates_a) & set(rates_b))
    if not shared:
        print("两份报告没有共同的题目 id，无法配对")
        return 1
    if len(shared) != len(rates_a):
        print(f"[warn] 只比较了 {len(shared)} 道共同题目")

    xs = [rates_a[qid] for qid in shared]
    ys = [rates_b[qid] for qid in shared]
    result = paired_bootstrap(xs, ys)

    print(f"A = {a['strategy']}  (mean EX {a['mean_ex'] * 100:.1f}%, n={a['n_runs']})")
    print(f"B = {b['strategy']}  (mean EX {b['mean_ex'] * 100:.1f}%, n={b['n_runs']})")
    print(f"配对题目数: {len(shared)}")
    print()
    print(f"均值差 (A - B)      : {result['mean_diff'] * 100:+.1f} pt")
    print(f"95% CI              : [{result['ci_low'] * 100:+.1f}, {result['ci_high'] * 100:+.1f}] pt")
    print(f"P(差值 <= 0)        : {result['p_le_zero']:.3f}")
    print()
    if result["ci_low"] > 0:
        print("结论：区间不含 0，A 显著更好。")
    elif result["ci_high"] < 0:
        print("结论：区间不含 0，B 显著更好。")
    else:
        print("结论：区间跨过 0，差异不显著 —— 不能宣称 A 比 B 好。")

    flips = [qid for qid in shared if rates_a[qid] != rates_b[qid]]
    print(f"\n两配置结果不同的题: {len(flips)} 道")
    for qid in flips:
        print(f"  {qid}: A={rates_a[qid]:.2f}  B={rates_b[qid]:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
