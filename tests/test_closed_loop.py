"""澄清闭环：问了之后，答案能不能真的收敛到正确 SQL。

这是 P5 那组数字的来源（收敛率 0% → 87%）。核心是两种策略的对比：
- baseline：不问，直接写 SQL（含糊题必然写错）
- closed：先反问，把「原问题 + 反问 + 用户补充」一起给模型

假模型可以精确构造"第一轮反问、第二轮写对"这种序列，把对比逻辑跑透。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from askdata.clarify import (
    ClosedLoopItem,
    ClosedLoopStrategy,
    load_closed_loop,
    render_closed_markdown,
    run_closed_loop,
)
from askdata.db import Sandbox
from askdata.llm import LLMResponse

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
        "(1, 1, 1, TIMESTAMP '2025-01-01 10:00:00', '已完成', 100, 0, 100)"
    )
    conn.close()


class ScriptedModel:
    """按顺序吐预设回复，记录调用次数。"""

    def __init__(self, replies: list[str | None]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.usage: dict = {}

    def complete(self, prompt: str) -> LLMResponse | None:
        self.calls += 1
        if not self.replies:
            return None
        reply = self.replies.pop(0)
        return None if reply is None else LLMResponse(reply)


class BaselineStrategy:
    """对照组：不问，直接给（错的）SQL。"""

    name = "baseline"

    def generate(self, question):
        return "SELECT COUNT(*) FROM orders WHERE order_id > 999"


class ClosedLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "closed.duckdb"
        build_db(self.db)
        self.sandbox = Sandbox(self.db, read_only=True)
        self.item = ClosedLoopItem(
            id="c1",
            question="销售额是多少",
            answer="2025 年的",
            gold_sql="SELECT COUNT(*) FROM orders",
        )

    def tearDown(self) -> None:
        self.sandbox.close()
        self._tmp.cleanup()

    def _strategy(self, replies: list[str | None]) -> ClosedLoopStrategy:
        return ClosedLoopStrategy(ScriptedModel(replies), schema_provider=lambda _q: "schema")

    def test_closed_loop_fixes_baseline_failure(self) -> None:
        """baseline 写错、closed 先问再写对 —— 这正是「收敛」的含义。"""
        closed = self._strategy(
            [
                '{"need_clarification": true, "question": "哪一年？"}',
                "```sql\nSELECT COUNT(*) FROM orders\n```",
            ]
        )
        results = run_closed_loop(self.sandbox, [self.item], closed, BaselineStrategy())

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].asked)
        self.assertEqual(results[0].baseline_status, "value_mismatch")  # 状态码叫 value_mismatch
        self.assertEqual(results[0].closed_status, "correct")
        self.assertTrue(results[0].fixed)

    def test_no_clarification_skips_second_turn(self) -> None:
        """模型觉得信息够了，就只用一次调用。"""
        model = ScriptedModel(
            ['{"need_clarification": false, "sql": "SELECT COUNT(*) FROM orders"}']
        )
        closed = ClosedLoopStrategy(model, schema_provider=lambda _q: "schema")
        results = run_closed_loop(self.sandbox, [self.item], closed, BaselineStrategy())

        self.assertEqual(model.calls, 1)
        self.assertFalse(results[0].asked)
        self.assertEqual(results[0].closed_status, "correct")

    def test_second_turn_failure_falls_back_to_no_sql(self) -> None:
        closed = self._strategy(
            ['{"need_clarification": true, "question": "哪一年？"}', None]
        )
        results = run_closed_loop(self.sandbox, [self.item], closed, BaselineStrategy())
        self.assertTrue(results[0].asked)
        self.assertIsNone(results[0].closed_sql)

    def test_second_turn_json_reply_is_tolerated(self) -> None:
        """模板要求只输出 SQL，但模型常继续回 JSON。要能兜住。"""
        closed = self._strategy(
            [
                '{"need_clarification": true, "question": "哪一年？"}',
                '{"need_clarification": false, "sql": "SELECT COUNT(*) FROM orders"}',
            ]
        )
        results = run_closed_loop(self.sandbox, [self.item], closed, BaselineStrategy())
        self.assertEqual(results[0].closed_status, "correct")

    def test_broken_gold_sql_raises(self) -> None:
        bad = ClosedLoopItem(id="bad", question="q", answer="a", gold_sql="SELECT * FROM nope")
        closed = self._strategy(['{"need_clarification": false, "sql": "SELECT 1"}'])
        with self.assertRaises(ValueError):
            run_closed_loop(self.sandbox, [bad], closed, BaselineStrategy())

    def test_render_markdown(self) -> None:
        closed = self._strategy(
            [
                '{"need_clarification": true, "question": "哪一年？"}',
                "```sql\nSELECT COUNT(*) FROM orders\n```",
            ]
        )
        results = run_closed_loop(self.sandbox, [self.item], closed, BaselineStrategy())
        text = render_closed_markdown(results, model="fake-model")

        self.assertIn("fake-model", text)
        self.assertIn("100%", text)  # 收敛率
        self.assertIn("|", text)

    def test_render_markdown_empty(self) -> None:
        self.assertIsInstance(render_closed_markdown([], model="m"), str)


class LoadClosedLoopTest(unittest.TestCase):
    def test_loads_valid_fixture(self) -> None:
        payload = {
            "version": "v",
            "questions": [
                {
                    "id": "c1",
                    "question": "销售额是多少",
                    "answer": "2025 年的",
                    "gold_sql": "SELECT 1",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "closed.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            items = load_closed_loop(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].answer, "2025 年的")

    def test_missing_answer_is_rejected(self) -> None:
        """缺 answer 的题不能悄悄混进实验，否则"收敛率"会被注水。"""
        payload = {
            "version": "v",
            "questions": [{"id": "c1", "question": "q", "answer": "", "gold_sql": "SELECT 1"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "closed.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_closed_loop(path)


if __name__ == "__main__":
    unittest.main()
