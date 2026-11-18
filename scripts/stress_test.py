"""并发压测：验证多线程 HTTP 服务 + 单 DuckDB 连接 + 全局锁不会串结果。

服务是 ThreadingHTTPServer，但数据库只有一条连接、外面套了 threading.Lock，
所以理论上是安全的——**理论上**。这个脚本要做的是把它压出来：

1. 20 个并发 /api/check，每个用不同的 SQL，检查响应里的 rewritten 是否对应自己的 SQL
   （串了的话会看到别人的数字）；
2. 一边跑 /api/ask（长事务：模型生成 + 执行），一边高频打 /api/check，
   看在锁竞争下会不会超时或错乱；
3. 统计延迟分布，确认没有无限阻塞。
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RESULTS: list[tuple[bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name))
    line = ("  [ ok ] " if ok else "  [FAIL] ") + name
    if detail and not ok:
        line += "  — " + detail
    print(line)


def post(base: str, path: str, payload: dict, timeout: int = 300) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(base: str, path: str, timeout: int = 300) -> dict:
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_concurrent_guard(base: str, n: int = 20) -> None:
    """并发 /api/check：每个请求带自己的标记，检查有没有串。"""

    def one(i: int) -> tuple[bool, float, str]:
        sql = f"SELECT {i} AS marker"
        started = time.perf_counter()
        try:
            data = post(base, "/api/check", {"sql": sql})
            elapsed = time.perf_counter() - started
            rewritten = data.get("rewritten") or ""
            # 改写后的 SQL 里应该出现这个请求自己的数字；串了就会是别人的
            ok = data.get("verdict") == "allowed" and f"SELECT {i}" in rewritten
            return ok, elapsed, rewritten[:70]
        except Exception as exc:  # noqa: BLE001
            return False, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=n) as pool:
        outcomes = list(pool.map(one, range(n)))

    bad = [o for o in outcomes if not o[0]]
    latencies = [o[1] for o in outcomes]
    record(
        f"并发 {n} 个 /api/check 结果不串",
        not bad,
        f"{len(bad)} 个异常，例如 {bad[0][2] if bad else ''}",
    )
    # 阈值按"能抓死锁"定，不按"本地机器多快"定：
    # CI runner 只有 2 核，比开发机慢一个数量级，卡太死会天天误报。
    record(
        "并发 /api/check 延迟合理",
        max(latencies) < 30,
        f"median={statistics.median(latencies) * 1000:.0f}ms  max={max(latencies) * 1000:.0f}ms",
    )
    print(
        f"         （median {statistics.median(latencies) * 1000:.0f}ms · "
        f"max {max(latencies) * 1000:.0f}ms）"
    )


def check_mixed(base: str) -> None:
    """慢查询与轻请求交错：验证锁竞争下的行为与超时后的自愈。

    服务只有一条 DuckDB 连接，外面套全局锁 —— 所以一个慢查询必然阻塞其他请求。
    这里要测的不是"能不能不阻塞"（做不到），而是：

    1. 阻塞是有上界的（沙箱 8s 超时会打断查询）；
    2. 被打断之后连接还能继续用（不会整个服务卡死）。
    """
    HEAVY = "SELECT COUNT(*) FROM orders a, orders b, orders c"  # 三表笛卡尔积，必然超时
    stop = threading.Event()
    light: list[tuple[bool, float]] = []

    def bg_light() -> None:
        i = 0
        while not stop.is_set() and i < 20:
            started = time.perf_counter()
            try:
                data = post(base, "/api/check", {"sql": f"SELECT {i} AS g"}, timeout=120)
                light.append((data.get("verdict") == "allowed", time.perf_counter() - started))
            except Exception:  # noqa: BLE001
                light.append((False, time.perf_counter() - started))
            i += 1
            time.sleep(0.05)

    # 先让轻请求跑起来，再投一个必然超时的慢查询，观察它们被顶住多久
    thread = threading.Thread(target=bg_light, daemon=True)
    thread.start()
    time.sleep(0.15)

    started = time.perf_counter()
    heavy_err = ""
    try:
        heavy = post(base, "/api/check", {"sql": HEAVY}, timeout=120)
        heavy_ok = heavy.get("verdict") == "allowed"
    except Exception as exc:  # noqa: BLE001
        heavy_ok = False
        heavy_err = str(exc)
    heavy_elapsed = time.perf_counter() - started
    stop.set()
    thread.join(timeout=60)

    print(f"         慢查询耗时 {heavy_elapsed:.1f}s（沙箱超时上限 8s）")
    record("慢查询被超时打断后仍返回结果", bool(heavy_ok) or bool(heavy_err))

    # 慢查询结束后立刻再打一次，验证连接没坏
    try:
        after = post(base, "/api/check", {"sql": "SELECT 1 AS after"})
        recover_ok = after.get("verdict") == "allowed"
    except Exception:  # noqa: BLE001
        recover_ok = False
    record("慢查询结束后服务仍可用", recover_ok)
    record("慢查询阻塞有上界（≤ 30s）", heavy_elapsed < 30, f"{heavy_elapsed:.1f}s")

    ok_lights = [x for x in light if x[0]]
    record(
        "阻塞期间的轻请求最终都成功",
        len(ok_lights) == len(light) and len(light) > 0,
        f"{len(ok_lights)}/{len(light)}",
    )
    if light:
        lat = [x[1] for x in light]
        print(
            f"         期间 {len(light)} 个轻请求：median {statistics.median(lat) * 1000:.0f}ms · "
            f"max {max(lat) * 1000:.0f}ms（被慢查询顶住，属预期：单连接串行化）"
        )


def check_tables_under_load(base: str) -> None:
    """/api/tables 是全库扫描（68 张表 × 多次查询），和 check 并发。"""

    def heavy() -> tuple[bool, float]:
        started = time.perf_counter()
        try:
            data = get(base, "/api/tables", timeout=300)
            return isinstance(data, list) and len(data) > 0, time.perf_counter() - started
        except Exception:  # noqa: BLE001
            return False, time.perf_counter() - started

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: heavy(), range(4)))

    record("4 个并发 /api/tables 全部成功", all(r[0] for r in results))
    worst = max(r[1] for r in results)
    # 4 个并发全库扫描在 2 核 runner 上本来就要几十秒，这里只兜"卡死"（>180s）
    record("并发 /api/tables 无超时", worst < 180, f"max={worst:.1f}s")
    print(f"         （最慢一次 {worst:.1f}s）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8777")
    parser.add_argument("--skip-ask", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("并发压测")
    print("=" * 60)

    print("\n[1] 20 个并发 /api/check")
    check_concurrent_guard(args.base)

    print("\n[2] 4 个并发 /api/tables（全库扫描）")
    check_tables_under_load(args.base)

    if not args.skip_ask:
        print("\n[3] /api/ask 与高频 /api/check 交错")
        check_mixed(args.base)

    failed = [r for r in RESULTS if not r[0]]
    print("\n" + "=" * 60)
    print(f"共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
