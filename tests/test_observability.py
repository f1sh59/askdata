"""可观测性：指标输出格式 + 结构化日志。

这两块一旦写错，监控上看不出来（格式不对采集器直接不认），
所以用测试把输出格式钉死。
"""

from __future__ import annotations

import json
import re
import unittest

from askdata.observability import Metrics, log_event, new_request_id


class RequestIdTest(unittest.TestCase):
    def test_ids_are_short_hex(self) -> None:
        rid = new_request_id()
        self.assertEqual(len(rid), 12)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{12}", rid))

    def test_ids_are_unique(self) -> None:
        ids = {new_request_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)


class MetricsRenderTest(unittest.TestCase):
    def test_up_and_uptime_present(self) -> None:
        text = Metrics().render()
        self.assertIn("askdata_up 1", text)
        self.assertRegex(text, r"askdata_uptime_seconds \d+\.\d+")

    def test_counter_and_histogram(self) -> None:
        m = Metrics()
        m.observe_request("GET", "/api/overview", 200, 0.012)
        m.observe_request("GET", "/api/overview", 500, 0.9)
        text = m.render()

        self.assertIn(
            'askdata_http_requests_total{method="GET",route="/api/overview",status="200"} 1',
            text,
        )
        self.assertIn(
            'askdata_http_requests_total{method="GET",route="/api/overview",status="500"} 1',
            text,
        )
        # 耗时是累计桶：0.9s 只落在 >=1.0 之前的最后几个桶里
        self.assertIn('le="0.01"', text)
        self.assertIn('le="+Inf"', text)
        self.assertIn('askdata_http_request_duration_seconds_count', text)

    def test_histogram_is_cumulative(self) -> None:
        m = Metrics()
        m.observe_request("GET", "/x", 200, 0.003)  # 落在第一个桶
        text = m.render()
        # 第一个桶和 +Inf 桶都应该是 1
        self.assertRegex(text, r'le="0\.005"\} 1')
        self.assertRegex(text, r'le="\+Inf"\} 1')

    def test_custom_counters(self) -> None:
        m = Metrics()
        m.inc("rate_limited", 3)
        self.assertIn('askdata_events_total{event="rate_limited"} 3', m.render())

    def test_empty_metrics_are_valid(self) -> None:
        """没有任何请求时也要输出合法文本，否则采集器会报错。"""
        text = Metrics().render()
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("None", text)


class LogEventTest(unittest.TestCase):
    def test_writes_single_json_line(self) -> None:
        import io
        import sys

        buffer = io.StringIO()
        original = sys.stderr
        sys.stderr = buffer
        try:
            log_event("info", "http_request", request_id="abc123", status=200, ms=12.5)
        finally:
            sys.stderr = original

        line = buffer.getvalue().strip()
        payload = json.loads(line)
        self.assertEqual(payload["level"], "info")
        self.assertEqual(payload["event"], "http_request")
        self.assertEqual(payload["request_id"], "abc123")
        self.assertEqual(payload["status"], 200)
        self.assertIn("ts", payload)


if __name__ == "__main__":
    unittest.main()
