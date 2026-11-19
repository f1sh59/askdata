"""限流：令牌桶的边界行为。

限流写错的两种典型后果：
  1. 太松 → 等于没有，挡不住脚本；
  2. 太紧 → 正常用户被误伤。
所以这里既测"该挡的挡住"，也测"该放行的放行"和"回血"。
"""

from __future__ import annotations

import time
import unittest

from askdata.ratelimit import RateLimiter, TokenBucket


class TokenBucketTest(unittest.TestCase):
    def test_burst_then_empty(self) -> None:
        bucket = TokenBucket(capacity=3, rate=1)
        now = time.monotonic()
        self.assertTrue(bucket.take(now))
        self.assertTrue(bucket.take(now))
        self.assertTrue(bucket.take(now))
        self.assertFalse(bucket.take(now))

    def test_refills_over_time(self) -> None:
        bucket = TokenBucket(capacity=2, rate=10)  # 每秒补 10 个
        now = time.monotonic()
        bucket.take(now)
        bucket.take(now)
        self.assertFalse(bucket.take(now))
        # 过了 0.25 秒应该补回 ~2.5 个（上限 2）
        self.assertTrue(bucket.take(now + 0.25))

    def test_never_exceeds_capacity(self) -> None:
        bucket = TokenBucket(capacity=2, rate=100)
        now = time.monotonic()
        bucket.take(now)
        bucket.take(now)
        self.assertTrue(bucket.take(now + 10))  # 补满但不超过 2
        self.assertTrue(bucket.take(now + 10))
        self.assertFalse(bucket.take(now + 10))

    def test_retry_after_is_positive_when_empty(self) -> None:
        bucket = TokenBucket(capacity=1, rate=2)
        now = time.monotonic()
        bucket.take(now)
        self.assertGreater(bucket.retry_after(), 0)


class RateLimiterTest(unittest.TestCase):
    def test_allows_burst_then_blocks(self) -> None:
        limiter = RateLimiter(rate=1, burst=3)
        allowed = [limiter.allow("1.2.3.4")[0] for _ in range(5)]
        self.assertEqual(allowed, [True, True, True, False, False])

    def test_keys_are_isolated(self) -> None:
        limiter = RateLimiter(rate=1, burst=1)
        self.assertTrue(limiter.allow("a")[0])
        self.assertFalse(limiter.allow("a")[0])
        # 另一个 IP 有自己的桶
        self.assertTrue(limiter.allow("b")[0])

    def test_disabled_always_allows(self) -> None:
        limiter = RateLimiter(rate=1, burst=1, enabled=False)
        self.assertTrue(all(limiter.allow("x")[0] for _ in range(50)))

    def test_retry_after_reported(self) -> None:
        limiter = RateLimiter(rate=1, burst=1)
        limiter.allow("k")
        ok, retry = limiter.allow("k")
        self.assertFalse(ok)
        self.assertGreater(retry, 0)

    def test_key_table_is_bounded(self) -> None:
        """桶数量有上限，否则被扫端口能把内存打满。"""
        limiter = RateLimiter(rate=100, burst=100, max_keys=10)
        for i in range(200):
            limiter.allow(f"ip-{i}")
        self.assertLessEqual(len(limiter._buckets), 10)

    def test_from_env_defaults(self) -> None:
        limiter = RateLimiter.from_env()
        self.assertTrue(limiter.enabled)
        self.assertGreater(limiter.rate, 0)


if __name__ == "__main__":
    unittest.main()
