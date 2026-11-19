"""压测：量出并发曲线和延迟分位，产出一份能放进文档的报告。

和 stress_test.py 的区别：
    stress_test 验的是"并发下结果会不会串、会不会死锁"（正确性）；
    这个脚本量的是"多少并发下开始变慢、P95 是多少"（性能）。

用法：
    # 先关掉限流，否则测到的是限流阈值不是性能
    ASKDATA_RATE_LIMIT=0 python -m askdata.server --port 8777
    python scripts/load_test.py --levels 1,4,8,16 --duration 5

输出：
    reports/perf_<时间戳>.json   原始数据
    reports/perf_<时间戳>.md     可读报告

说明：
    打的是 /api/check（SQL 安全闸），不调模型——它最能代表"服务端自身"的开销。
    问数的延迟主要花在模型上，压它测的是 API 供应商，不是这个项目。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"
SQL = "SELECT COUNT(*) FROM orders"


def one_request(base: str, timeout: float = 10.0) -> tuple[float, int]:
    """发一次 /api/check，返回（耗时毫秒, 状态码）。"""
    body = json.dumps({"sql": SQL}).encode()
    req = urllib.request.Request(
        base + "/api/check",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return (time.perf_counter() - started) * 1000, resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        return (time.perf_counter() - started) * 1000, exc.code
    except Exception:
        return (time.perf_counter() - started) * 1000, 0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((p / 100) * (len(ordered) - 1)))
    return ordered[index]


def run_level(base: str, concurrency: int, duration: float) -> dict:
    """固定并发、固定时长地打，统计吞吐和延迟分布。"""
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    stop_at = time.perf_counter() + duration

    def worker() -> None:
        while time.perf_counter() < stop_at:
            elapsed, status = one_request(base)
            latencies.append(elapsed)
            statuses[status] = statuses.get(status, 0) + 1

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(lambda _: worker(), range(concurrency)))
    wall = time.perf_counter() - started

    total = len(latencies)
    ok = statuses.get(200, 0)
    return {
        "concurrency": concurrency,
        "requests": total,
        "ok": ok,
        "errors": total - ok,
        "rps": round(total / wall, 1) if wall else 0.0,
        "p50": round(percentile(latencies, 50), 1),
        "p90": round(percentile(latencies, 90), 1),
        "p95": round(percentile(latencies, 95), 1),
        "p99": round(percentile(latencies, 99), 1),
        "max": round(max(latencies), 1) if latencies else 0.0,
        "statuses": {str(k): v for k, v in sorted(statuses.items())},
    }


def render_markdown(rows: list[dict], meta: dict) -> str:
    lines = [
        "# 压测报告",
        "",
        f"- 时间：{meta['started_at']}",
        f"- 目标：{meta['base']} `/api/check`（SQL 安全闸，不调模型）",
        f"- 每档时长：{meta['duration']}s · 客户端并发 × 服务端线程",
        f"- 限流：{meta['rate_limit']}",
        "",
        "| 并发 | 请求数 | 成功 | RPS | P50 | P90 | P95 | P99 | 最大 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['concurrency']} | {r['requests']} | {r['ok']} | {r['rps']} "
            f"| {r['p50']}ms | {r['p90']}ms | {r['p95']}ms | {r['p99']}ms | {r['max']}ms |"
        )

    lines += ["", "## 怎么看这张表", ""]
    if rows:
        single = rows[0]
        best = max(rows, key=lambda r: r["rps"])
        worst = rows[-1]
        scaling = best["rps"] / max(single["rps"], 0.1)
        p95_ratio = worst["p95"] / max(single["p95"], 0.1)

        lines.append(
            f"- 单并发 P50 = **{single['p50']}ms**：一次安全闸校验本身的开销"
            "（AST 解析 + 一次查询）。"
        )
        lines.append(f"- 吞吐最高 **{best['rps']} req/s**（并发 {best['concurrency']}）。")

        # 结论要由数据决定，不能预设。之前这里写死了一句"单连接导致吞吐上不去"，
        # 结果实测是近线性扩展，报告和自己打架。
        saturated = worst is not single and worst["rps"] < best["rps"] * 0.95
        if saturated:
            lines.append(
                f"- **拐点在并发 {best['concurrency']} 附近**：再往上加到 {worst['concurrency']}，"
                f"吞吐反而从 {best['rps']} 掉到 {worst['rps']} req/s，"
                f"P50 从 {best['p50']}ms 翻到 {worst['p50']}ms——"
                "这是饱和信号：请求开始排队，加线程只会更慢。"
            )
        elif scaling >= 4 and p95_ratio < 2:
            # 只有"吞吐涨得多 + 延迟没明显恶化"才叫没到拐点
            lines.append(
                f"- 并发提到 {worst['concurrency']} 时吞吐是单并发的 **{scaling:.1f} 倍**，"
                f"P95 只从 {single['p95']}ms 涨到 {worst['p95']}ms（{p95_ratio:.1f} 倍）——"
                "**没到拐点**：瓶颈还没出现，继续加压才有意义。"
            )
        elif p95_ratio >= 2:
            lines.append(
                f"- 并发到 {worst['concurrency']} 时 P95 涨到 **{worst['p95']}ms**"
                f"（单并发的 {p95_ratio:.1f} 倍），吞吐只到 {best['rps']} req/s——"
                "已经撞到天花板的迹象：请求在排队，而不是在并行。"
            )
        else:
            lines.append(
                f"- 并发 ×{worst['concurrency']} 下吞吐 {best['rps']} req/s、"
                f"P95 {worst['p95']}ms，增长放缓但延迟没恶化，属于温和的排队。"
            )

        lines.append("")
        lines.append(
            "> 数据只覆盖 `/api/check`。问数的延迟主要由模型决定（1~3 秒），"
            "压它不是测这个项目，是测上游供应商。"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8777")
    parser.add_argument("--levels", default="1,4,8,16", help="并发档位，逗号分隔")
    parser.add_argument("--duration", type=float, default=5.0, help="每档持续秒数")
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    print(f"目标 {args.base}  档位 {levels}  每档 {args.duration}s")

    rows: list[dict] = []
    for concurrency in levels:
        row = run_level(args.base, concurrency, args.duration)
        rows.append(row)
        print(
            f"  并发 {concurrency:>3}  请求 {row['requests']:>5}  RPS {row['rps']:>7}  "
            f"P50 {row['p50']:>6}ms  P95 {row['p95']:>7}ms  错误 {row['errors']}"
        )
        if 429 in [int(k) for k in row["statuses"]]:
            print("    ⚠ 出现 429：服务端开着限流，测到的是阈值不是性能（用 ASKDATA_RATE_LIMIT=0 重启）")

    started_at = datetime.now().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    json_path = REPORT_DIR / f"perf_{stamp}.json"
    md_path = REPORT_DIR / f"perf_{stamp}.md"

    meta = {
        "started_at": started_at,
        "base": args.base,
        "duration": args.duration,
        "rate_limit": "关闭（测的是性能上限）",
    }
    json_path.write_text(
        json.dumps({"meta": meta, "levels": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(rows, meta), encoding="utf-8")
    print(f"\n报告：{md_path}")
    print(f"原始：{json_path}")

    # 只在明显异常时返回非零：错误率超过 5%
    total = sum(r["requests"] for r in rows)
    errors = sum(r["errors"] for r in rows)
    return 1 if total and errors / total > 0.05 else 0


if __name__ == "__main__":
    raise SystemExit(main())
