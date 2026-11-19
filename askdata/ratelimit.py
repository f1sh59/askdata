"""限流：按客户端 IP 的令牌桶。

为什么要有：
    /api/ask 会真的调一次大模型，一次几秒钟。没有限流的话，
    一个脚本循环打过来就能把配额烧光、把服务拖死。
    内网演示也要有基本防护——这是"能给别人用"和"只能自己玩"的区别。

策略：
    每个 IP 一个桶，容量 burst、每秒补充 rate 个令牌。
    超限直接 429 + Retry-After，不排队（排队会把内存吃掉）。

用令牌桶而不是固定窗口，是因为固定窗口在窗口边界会放过双倍流量。
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    __slots__ = ("capacity", "rate", "tokens", "updated")

    def __init__(self, capacity: float, rate: float) -> None:
        self.capacity = capacity
        self.rate = rate
        self.tokens = capacity
        self.updated = time.monotonic()

    def take(self, now: float, amount: float = 1.0) -> bool:
        elapsed = now - self.updated
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.updated = now
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def retry_after(self, amount: float = 1.0) -> float:
        """还要等多久才够一个令牌。"""
        missing = amount - self.tokens
        if missing <= 0:
            return 0.0
        return missing / self.rate if self.rate > 0 else 60.0


class RateLimiter:
    """按 key（通常是 IP）限流。线程安全。"""

    def __init__(
        self,
        *,
        rate: float = 20.0,
        burst: float = 60.0,
        enabled: bool = True,
        max_keys: int = 4096,
    ) -> None:
        self.rate = rate
        self.burst = burst
        self.enabled = enabled
        self.max_keys = max_keys
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """返回 (是否放行, 建议等待秒数)。"""
        if not self.enabled:
            return True, 0.0
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_keys:
                    # 简单防爆：桶太多就清掉一半最老的，避免内存无限增长
                    for old_key in list(self._buckets)[: self.max_keys // 2]:
                        del self._buckets[old_key]
                bucket = TokenBucket(self.burst, self.rate)
                self._buckets[key] = bucket
            if bucket.take(now):
                return True, 0.0
            return False, bucket.retry_after()

    @classmethod
    def from_env(cls) -> RateLimiter:
        import os

        enabled = (os.environ.get("ASKDATA_RATE_LIMIT", "1").strip().lower() not in {"0", "false", "off"})
        try:
            rate = float(os.environ.get("ASKDATA_RATE_PER_SEC", "20"))
        except ValueError:
            rate = 20.0
        try:
            burst = float(os.environ.get("ASKDATA_RATE_BURST", "60"))
        except ValueError:
            burst = 60.0
        return cls(rate=rate, burst=burst, enabled=enabled)
