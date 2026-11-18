from __future__ import annotations

import unittest

from askdata.verify import Issue, check_result, parse_verify_reply

TOTAL = 23_668_264.6


class EmptyResultTest(unittest.TestCase):
    def test_empty_is_flagged(self) -> None:
        issues = check_result("2025 年每个月的销售额", ["ym", "amt"], [], total_revenue=TOTAL)
        codes = [i.code for i in issues]
        self.assertIn("empty_result", codes)

    def test_empty_aggregate_is_warning(self) -> None:
        issues = check_result("2025 年的销售额是多少", ["gmv"], [], total_revenue=TOTAL)
        self.assertEqual(issues[0].level, "warn")


class MagnitudeTest(unittest.TestCase):
    def test_fanout_is_caught(self) -> None:
        # 典型的 join 扇出：金额被放大了 5 倍
        issues = check_result(
            "2025 年的销售额是多少", ["gmv"], [[TOTAL * 5]], total_revenue=TOTAL
        )
        self.assertIn("magnitude_fanout", [i.code for i in issues])

    def test_normal_value_passes(self) -> None:
        issues = check_result(
            "2025 年的销售额是多少", ["gmv"], [[TOTAL]], total_revenue=TOTAL
        )
        self.assertEqual(issues, [])

    def test_no_reference_means_no_magnitude_check(self) -> None:
        issues = check_result("2025 年的销售额是多少", ["gmv"], [[TOTAL * 5]])
        self.assertNotIn("magnitude_fanout", [i.code for i in issues])


class NegativeAndRateTest(unittest.TestCase):
    def test_negative_count(self) -> None:
        issues = check_result(
            "各渠道的订单数", ["channel_name", "order_cnt"], [["天猫", -5]], total_revenue=TOTAL
        )
        self.assertIn("negative_value", [i.code for i in issues])

    def test_rate_over_100(self) -> None:
        issues = check_result(
            "各渠道退款率", ["channel_name", "refund_rate"], [["天猫", 132.5]], total_revenue=TOTAL
        )
        self.assertIn("rate_over_100", [i.code for i in issues])

    def test_rate_under_100_passes(self) -> None:
        issues = check_result(
            "各渠道退款率", ["channel_name", "refund_rate"], [["天猫", 8.2]], total_revenue=TOTAL
        )
        self.assertEqual(issues, [])

    def test_fraction_rate_passes(self) -> None:
        # 0.02 这种小数形式的比率不该被拦
        issues = check_result("退款率", ["refund_rate"], [[0.02]], total_revenue=TOTAL)
        self.assertEqual(issues, [])


class ExtraColumnTest(unittest.TestCase):
    def test_more_data_columns_than_asked(self) -> None:
        issues = check_result(
            "各品类的销售额和毛利率",
            ["category", "amt", "gp", "extra"],
            [["家电", 1.0, 2.0, 3.0]],
            total_revenue=TOTAL,
        )
        self.assertIn("extra_columns", [i.code for i in issues])


class ParseReplyTest(unittest.TestCase):
    def test_ok_reply_returns_none(self) -> None:
        self.assertIsNone(parse_verify_reply('{"ok": true, "reason": "无明显问题"}'))

    def test_bad_reply_returns_issue(self) -> None:
        issue = parse_verify_reply('```json\n{"ok": false, "reason": "金额被放大了"}\n```')
        self.assertIsInstance(issue, Issue)
        self.assertIn("金额被放大", issue.message)

    def test_garbage_reply_is_ignored(self) -> None:
        self.assertIsNone(parse_verify_reply("我不确定"))


if __name__ == "__main__":
    unittest.main()
