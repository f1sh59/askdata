from __future__ import annotations

import contextlib
import os
import unittest
from pathlib import Path

import duckdb
import sqlglot

from askdata import attacks
from askdata.config import DATA_DIR
from askdata.db import Sandbox
from askdata.guard import RowPolicy, SqlGuard, SqlGuardError

# 不用系统临时目录：这个环境的 AppData\Temp 被沙箱限制，清理时会报拒绝访问
TEST_DB = DATA_DIR / f"_timeout_test_{os.getpid()}.duckdb"


class GuardBlockTest(unittest.TestCase):
    def test_all_injection_cases_are_rejected(self) -> None:
        guard = SqlGuard()
        for case in attacks.BLOCKED:
            with self.subTest(case=case.name, category=case.category):
                with self.assertRaises(SqlGuardError):
                    guard.check(case.sql)


class GuardAllowTest(unittest.TestCase):
    def test_legitimate_queries_still_pass(self) -> None:
        guard = SqlGuard()
        for case in attacks.ALLOWED:
            with self.subTest(case=case.name):
                rewritten = guard.check(case.sql)
                # 改写结果本身必须还能被解析，否则就是造出了非法 SQL
                self.assertIsNotNone(sqlglot.parse_one(rewritten, dialect="duckdb"))


class LimitTest(unittest.TestCase):
    def test_missing_limit_is_added(self) -> None:
        sql = SqlGuard(max_rows=100).check("SELECT * FROM orders")
        self.assertIn("LIMIT 100", sql.upper())

    def test_existing_limit_is_preserved(self) -> None:
        sql = SqlGuard(max_rows=1000).check("SELECT * FROM orders LIMIT 5")
        self.assertIn("LIMIT 5", sql)
        self.assertNotIn("LIMIT 1000", sql)


class PolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = SqlGuard(policy=RowPolicy({"orders": "channel_id IN (1, 2)"}))

    def test_restricted_table_is_rewritten(self) -> None:
        sql = self.guard.check("SELECT COUNT(*) FROM orders")
        self.assertIn('FROM (SELECT * FROM "orders" WHERE channel_id IN (1, 2))', sql)

    def test_alias_is_preserved(self) -> None:
        sql = self.guard.check(
            "SELECT o.order_id FROM orders o JOIN channels c ON c.channel_id = o.channel_id"
        )
        self.assertIn('AS o', sql)
        self.assertIn("o.channel_id", sql)

    def test_unrestricted_table_is_untouched(self) -> None:
        sql = self.guard.check("SELECT * FROM channels")
        self.assertNotIn("WHERE", sql)


class TimeoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = TEST_DB
        con = duckdb.connect(str(cls.db_path))
        con.execute("CREATE TABLE t AS SELECT range AS i FROM range(2000)")
        con.close()

    @classmethod
    def tearDownClass(cls) -> None:
        for suffix in ("", ".wal"):
            candidate = Path(str(cls.db_path) + suffix)
            with contextlib.suppress(OSError):
                candidate.unlink(missing_ok=True)

    def test_heavy_query_is_interrupted(self) -> None:
        sandbox = Sandbox(self.db_path, timeout_s=0.5)
        try:
            result = sandbox.run("SELECT SUM(a.i * b.i) FROM t a, t b, t c")
            self.assertFalse(result.ok)
            self.assertIn("超时", result.error or "")
            # 中断之后连接还得能用，否则一轮评测会被一条超时查询带崩
            self.assertTrue(sandbox.run("SELECT 1").ok)
        finally:
            sandbox.close()

    def test_fast_query_is_not_interrupted(self) -> None:
        sandbox = Sandbox(self.db_path, timeout_s=5)
        try:
            result = sandbox.run("SELECT COUNT(*) FROM t")
            self.assertTrue(result.ok)
            self.assertEqual(result.rows[0][0], 2000)
        finally:
            sandbox.close()


if __name__ == "__main__":
    unittest.main()
