from __future__ import annotations

import unittest

from askdata.dialog import clean_rewrite, looks_like_followup, render_history


class FollowupTest(unittest.TestCase):
    def test_obvious_followups(self) -> None:
        for text in ["那 2024 年呢？", "只看天猫", "换成销售额呢", "再按品类拆一下", "还有其他吗"]:
            self.assertTrue(looks_like_followup(text), text)

    def test_standalone_questions(self) -> None:
        for text in [
            "2025 年各渠道的销售额分别是多少？",
            "平台有多少注册用户",
            "退款率是多少",
        ]:
            self.assertFalse(looks_like_followup(text), text)


class CleanRewriteTest(unittest.TestCase):
    def test_strips_fence(self) -> None:
        self.assertEqual(clean_rewrite("```\n2025 年天猫销售额\n```"), "2025 年天猫销售额")

    def test_strips_prefix_and_quotes(self) -> None:
        self.assertEqual(clean_rewrite("改写后的问题：2025 年天猫销售额"), "2025 年天猫销售额")
        self.assertEqual(clean_rewrite('"2025 年天猫销售额"'), "2025 年天猫销售额")

    def test_sql_output_is_rejected(self) -> None:
        # 模型偶尔会把"改写"写成 SQL，这种必须判失败并回退到原句
        self.assertIsNone(clean_rewrite("SELECT SUM(x) FROM orders"))
        self.assertIsNone(clean_rewrite("  with t as (select 1) select * from t"))

    def test_empty_is_none(self) -> None:
        self.assertIsNone(clean_rewrite(""))


class HistoryTest(unittest.TestCase):
    def test_only_questions_are_rendered(self) -> None:
        text = render_history(
            [{"question": "2025 年各渠道销售额", "sql": "SELECT c.channel_name, SUM(pay_amount)"}]
        )
        self.assertIn("2025 年各渠道销售额", text)
        # SQL 不能出现在历史里，否则模型会被带进 SQL 模式
        self.assertNotIn("SELECT", text)

    def test_empty_history(self) -> None:
        self.assertEqual(render_history([]), "（无）")


if __name__ == "__main__":
    unittest.main()
