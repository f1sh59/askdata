from __future__ import annotations

import unittest

from askdata.retrieval import _ranks, rrf, tokenize
from askdata.schema import ColumnInfo, TableInfo


def make_table(name: str, comment: str, columns: list[tuple[str, str]], enums: dict | None = None):
    return TableInfo(
        name=name,
        comment=comment,
        columns=[ColumnInfo(c, t) for c, t in columns],
        enums=enums or {},
    )


class TokenizeTest(unittest.TestCase):
    def test_ascii_words_are_lowercased(self) -> None:
        self.assertIn("orders", tokenize("ORDERS.order_id"))

    def test_chinese_yields_unigrams_and_bigrams(self) -> None:
        tokens = tokenize("退款")
        self.assertIn("退", tokens)
        self.assertIn("款", tokens)
        self.assertIn("退款", tokens)


class RankTest(unittest.TestCase):
    def test_zero_scores_get_no_rank(self) -> None:
        """回归测试：零分文档不能参与排名。

        之前把零分也算进名次，它们会按原顺序拿到 1/(60+0)、1/(60+1)…
        等于给字母序靠前的表一个固定优势，实测会把 as_complaint 顶到 refunds 前面。
        """
        self.assertEqual(_ranks([0.0, 2.0, 0.0, 1.0]), [None, 0, None, 1])

    def test_rrf_ignores_unranked_documents(self) -> None:
        fused = rrf([[None, 0], [0, None]])
        self.assertAlmostEqual(fused[0], 1 / 61)
        self.assertAlmostEqual(fused[1], 1 / 61)

    def test_rrf_prefers_document_hit_by_both_rankers(self) -> None:
        # 两路都把文档 0 排第一；文档 1 只在一路里排第二
        fused = rrf([[0, 1], [0, 1]])
        self.assertGreater(fused[0], fused[1])


class SelectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.orders = make_table(
            "orders", "订单头，包含已取消/待支付/已退款订单",
            [("order_id", "INTEGER"), ("status", "VARCHAR")],
            {"status": ["已支付", "已发货", "已完成", "已取消", "已退款"]},
        )
        self.decoy = make_table(
            "as_return_apply", "退货申请单",
            [("apply_id", "INTEGER"), ("order_no", "VARCHAR"), ("status", "VARCHAR")],
            {"status": ["待处理", "处理中", "已完成"]},
        )
        self.finance = make_table(
            "fin_refund_audit", "退款审核记录",
            [("audit_id", "INTEGER"), ("refund_no", "VARCHAR"), ("status", "VARCHAR")],
            {"status": ["待处理", "处理中", "已关闭"]},
        )

    def test_relevant_table_ranks_first(self) -> None:
        from askdata.retrieval import TableIndex

        index = TableIndex([self.orders, self.decoy, self.finance])
        names = [name for name, _ in index.rank("2025 年有效订单的销售额是多少")]
        self.assertEqual(names[0], "orders")

    def test_select_keeps_schema_order(self) -> None:
        """select 返回的必须是输入顺序的子序列 —— 否则 prompt 顺序会随命中表变化。"""
        from askdata.retrieval import TableIndex

        tables = [self.decoy, self.finance, self.orders]   # 故意打乱输入顺序
        index = TableIndex(tables)
        picked = index.select(tables, "2025 年有效订单的销售额", top_k=2)
        names = [t.name for t in picked]

        self.assertIn("orders", names)
        self.assertEqual(names, [t.name for t in tables if t.name in names])


if __name__ == "__main__":
    unittest.main()
