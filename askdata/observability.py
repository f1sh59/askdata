"""可观测性：请求 ID、结构化日志、Prometheus 指标。

为什么要自己写而不是引第三方：
    **零依赖是设计约束**。这个项目刻意只依赖 duckdb 和 sqlglot，
    部署时不用拉一堆包。指标和日志的格式都是稳定标准，
    自己写 200 行比引一个框架更可控。

三个东西：
    1. request id——每个请求一个短 ID，贯穿日志和响应头，出事能按线索查；
    2. JSON 日志——一行一条，字段固定，方便 grep 和后续接采集；
    3. 指标——计数器 + 耗时直方图，输出 Prometheus 文本格式。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from collections import defaultdict

# 常用状态的耗时桶（秒）。只为看清"快 / 慢 / 超时"三档，不追求毫秒级精度。
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class Metrics:
    """极简指标收集器。线程安全，跑在进程内存里，重启即清零。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._latency_buckets: dict[tuple[str, str], list[int]] = defaultdict(
            lambda: [0] * len(_LATENCY_BUCKETS)
        )
        self._latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self._counters: dict[str, float] = defaultdict(float)

    # ---- 采集 ----

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self._requests[(method, route, str(status))] += 1
            key = (method, route)
            for i, bound in enumerate(_LATENCY_BUCKETS):
                if seconds <= bound:
                    self._latency_buckets[key][i] += 1
            self._latency_sum[key] += seconds
            self._latency_count[key] += 1

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    # ---- 输出（Prometheus 文本格式）----

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []

            lines.append("# HELP askdata_up 服务是否存活")
            lines.append("# TYPE askdata_up gauge")
            lines.append("askdata_up 1")

            lines.append("# HELP askdata_uptime_seconds 进程运行时长")
            lines.append("# TYPE askdata_uptime_seconds gauge")
            lines.append(f"askdata_uptime_seconds {time.time() - self.started_at:.3f}")

            lines.append("# HELP askdata_http_requests_total 按方法/路由/状态码统计的请求数")
            lines.append("# TYPE askdata_http_requests_total counter")
            for (method, route, status), count in sorted(self._requests.items()):
                lines.append(
                    f'askdata_http_requests_total{{method="{method}",route="{route}",'
                    f'status="{status}"}} {count}'
                )

            lines.append("# HELP askdata_http_request_duration_seconds 请求耗时分布")
            lines.append("# TYPE askdata_http_request_duration_seconds histogram")
            for (method, route), buckets in sorted(self._latency_buckets.items()):
                cumulative = 0
                for i, bound in enumerate(_LATENCY_BUCKETS):
                    cumulative = buckets[i] if i == 0 else cumulative + buckets[i]
                    lines.append(
                        f'askdata_http_request_duration_seconds_bucket{{method="{method}",'
                        f'route="{route}",le="{bound}"}} {cumulative}'
                    )
                total = cumulative
                lines.append(
                    f'askdata_http_request_duration_seconds_bucket{{method="{method}",'
                    f'route="{route}",le="+Inf"}} {total}'
                )
                lines.append(
                    f'askdata_http_request_duration_seconds_sum{{method="{method}",'
                    f'route="{route}"}} {self._latency_sum[(method, route)]:.6f}'
                )
                lines.append(
                    f'askdata_http_request_duration_seconds_count{{method="{method}",'
                    f'route="{route}"}} {self._latency_count[(method, route)]}'
                )

            if self._counters:
                lines.append("# HELP askdata_events_total 业务事件计数")
                lines.append("# TYPE askdata_events_total counter")
                for name, value in sorted(self._counters.items()):
                    lines.append(f'askdata_events_total{{event="{name}"}} {value}')

            return "\n".join(lines) + "\n"


METRICS = Metrics()


def log_event(
    level: str,
    event: str,
    *,
    request_id: str | None = None,
    **fields: object,
) -> None:
    """写一条 JSON 日志。

    字段固定为 ts / level / event，其余按需附加。用 JSON 是因为
    后续接采集（Loki、ELK）不用写正则解析，grep 也好用。
    """
    record = {"ts": round(time.time(), 3), "level": level, "event": event}
    if request_id:
        record["request_id"] = request_id
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    sys.stderr.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def log_enabled() -> bool:
    """结构化日志可以用 ASKDATA_LOG=json 强制打开，默认在 TTY 下也可开。"""
    return os.environ.get("ASKDATA_LOG", "").lower() in {"json", "1", "true"}
