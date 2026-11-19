"""评测主循环：用假策略跑一遍 run_eval，验判分与汇总。

评测循环是"所有结论的来源"——它算错，后面所有报告和简历上的数字都是错的。
之前只有 scoring.py 的单点测试，没测过"跑完整批题"这条路径。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from askdata.db import Sandbox
from askdata.models import Dataset, Question
from askdata.runner import run_eval, validate_gold

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


def build_db(path: Path) -> None:
    import duckdb

    conn = duckdb.connect(str(path))
    for stmt in SCHEMA.read_text(encoding="utf-8").split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.execute(
        "INSERT INTO orders (order_id, user_id, channel_id, order_time, status, "
        "total_amount, discount_amount, pay_amount) VALUES "
        "(1, 1, 1, TIMESTAMP '2025-01-01 10:00:00', '已完成', 100, 0, 100),"
        "(2, 1, 1, TIMESTAMP '2025-02-01 10:00:00', '已完成', 200, 0, 200)"
    )
    conn.close()


class FakeStrategy:
    """按题号返回预设 SQL。"""

    name = "fake"

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping
        self.seen: list[str] = []

    def generate(self, question: Question) -> str | None:
        self.seen.append(question.id)
        return self.mapping.get(question.id)


def _dataset() -> Dataset:
    return Dataset(
        version="v-test",
        questions=[
            Question(id="q1", difficulty="easy", tags=(), question="多少订单", gold_sql="SELECT COUNT(*) FROM orders"),
            Question(id="q2", difficulty="easy", tags=(), question="多少用户", gold_sql="SELECT COUNT(*) FROM users"),
            Question(id="q3", difficulty="easy", tags=(), question="错误题", gold_sql="SELECT COUNT(*) FROM orders"),
        ],
    )


class RunEvalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "eval.duckdb"
        build_db(self.db)
        self.sandbox = Sandbox(self.db, read_only=True)

    def tearDown(self) -> None:
        self.sandbox.close()
        self._tmp.cleanup()

    def test_counts_correct_and_wrong(self) -> None:
        strategy = FakeStrategy(
            {
                "q1": "SELECT COUNT(*) FROM orders",  # 对
                "q2": "SELECT COUNT(*) FROM orders",  # gold 是 users（0 行），pred 是 2 → 结果错
                "q3": None,  # 拒答
            }
        )
        result = run_eval(self.sandbox, _dataset(), strategy)

        self.assertEqual(result.total, 3)
        self.assertEqual(result.correct, 1)
        self.assertLess(result.ex, 0.5)

    def test_strategy_is_called_for_every_question(self) -> None:
        strategy = FakeStrategy({"q1": "SELECT 1"})
        run_eval(self.sandbox, _dataset(), strategy)
        self.assertEqual(strategy.seen, ["q1", "q2", "q3"])

    def test_sql_error_is_recorded_not_raised(self) -> None:
        strategy = FakeStrategy({"q1": "SELECT * FROM no_such_table"})
        result = run_eval(self.sandbox, _dataset(), strategy)
        self.assertTrue(any(r.outcome.status == "sql_error" for r in result.results))

    def test_empty_result_is_recorded(self) -> None:
        strategy = FakeStrategy({"q1": "SELECT order_id FROM orders WHERE order_id > 9999"})
        result = run_eval(self.sandbox, _dataset(), strategy)
        statuses = {r.outcome.status for r in result.results}
        self.assertIn("empty_result", statuses)

    def test_result_metadata(self) -> None:
        result = run_eval(self.sandbox, _dataset(), FakeStrategy({"q1": "SELECT 1"}))
        self.assertEqual(result.strategy_name, "fake")
        self.assertEqual(result.dataset_version, "v-test")
        self.assertTrue(result.started_at)
        self.assertGreaterEqual(result.elapsed_s, 0.0)


class ValidateGoldTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "gold.duckdb"
        build_db(self.db)
        self.sandbox = Sandbox(self.db, read_only=True)

    def tearDown(self) -> None:
        self.sandbox.close()
        self._tmp.cleanup()

    def test_all_gold_sql_executable(self) -> None:
        problems = validate_gold(self.sandbox, _dataset())
        self.assertEqual(problems, [])

    def test_broken_gold_is_reported(self) -> None:
        broken = Dataset(
            version="v",
            questions=[
                Question(id="bad", difficulty="easy", tags=(), question="坏题", gold_sql="SELECT * FROM nope"),
            ],
        )
        problems = validate_gold(self.sandbox, broken)
        self.assertEqual(len(problems), 1)
        self.assertIn("bad", problems[0])


if __name__ == "__main__":
    unittest.main()
