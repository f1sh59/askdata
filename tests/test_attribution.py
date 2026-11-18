from __future__ import annotations

import unittest

from askdata.service import _tables_of


class TablesOfTest(unittest.TestCase):
    def test_cte_name_is_not_a_table(self) -> None:
        sql = "WITH m AS (SELECT 1 FROM orders) SELECT * FROM m"
        self.assertEqual(_tables_of(sql), ["orders"])

    def test_join_tables_and_aliases(self) -> None:
        sql = "SELECT * FROM orders o JOIN users u ON u.user_id = o.user_id"
        self.assertEqual(_tables_of(sql), ["orders", "users"])

    def test_multiple_joins(self) -> None:
        sql = (
            "SELECT * FROM order_items oi "
            "JOIN products p ON p.product_id = oi.product_id "
            "JOIN orders o ON o.order_id = oi.order_id"
        )
        self.assertEqual(_tables_of(sql), ["order_items", "orders", "products"])

    def test_quoted_identifier(self) -> None:
        self.assertEqual(_tables_of('SELECT * FROM "imp_dataset"'), ["imp_dataset"])

    def test_garbage_does_not_raise(self) -> None:
        self.assertEqual(_tables_of("这不是 SQL"), [])


if __name__ == "__main__":
    unittest.main()
