"""问数主链路：用假模型把 ask() 的每条分支都走一遍。

为什么要桩而不是真调模型：
    真调用慢（每次 1~3 秒）、要花钱、结果还不确定——写进 CI 只会让人
    把测试注释掉。桩把"模型输出"变成可控输入，于是能精确构造：
    正常 SQL、反问、危险 SQL、会触发扇出的 SQL、模型抛错。

桩的契约很小：`chat(system, prompt, **kw) -> LLMResponse(content, prompt_tokens, completion_tokens)`。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from askdata.llm import LLMResponse
from askdata.service import Pipeline

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


class StubLLM:
    """按顺序吐预设回复；记录每次调用的参数。"""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        content = self.replies.pop(0) if self.replies else '{"need_clarification": false, "sql": "SELECT 1"}'
        return LLMResponse(content, prompt_tokens=100, completion_tokens=20)


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


class AskPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ask.duckdb"
        build_db(self.db)
        self.pipe = Pipeline(db_path=self.db, writable=True,
                                 sessions_path=Path(self._tmp.name) / "sessions.db")
        self.stub: StubLLM | None = None

    def tearDown(self) -> None:
        # 会话库是 SQLite，连接不关的话 Windows 上删不掉临时目录
        self.pipe.sessions.close()
        self.pipe.sandbox.close()
        self._tmp.cleanup()

    def use(self, *replies: str) -> StubLLM:
        stub = StubLLM(list(replies))
        self.stub = stub
        self.pipe._llm_client = lambda api_key=None: stub  # type: ignore[method-assign]
        return stub

    # ---- 正常路径 ----

    def test_success_path(self) -> None:
        self.use('{"need_clarification": false, "sql": "SELECT COUNT(*) AS cnt FROM orders"}')
        result = self.pipe.ask("一共有多少订单")

        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["rows"][0][0], 2)
        names = [s["name"] for s in result["stages"]]
        self.assertEqual(names, ["召回表", "模型生成", "安全闸", "执行", "结果校验"])
        self.assertEqual(result["issues"], [])

    def test_sql_gets_limit_appended(self) -> None:
        self.use('{"need_clarification": false, "sql": "SELECT order_id FROM orders"}')
        result = self.pipe.ask("列出订单")
        self.assertIn("LIMIT", result["rewritten_sql"].upper())

    # ---- 反问 ----

    def test_clarification_path(self) -> None:
        self.use('{"need_clarification": true, "question": "你说的是哪一年？"}')
        result = self.pipe.ask("销售额是多少")

        self.assertTrue(result["ok"])
        self.assertTrue(result["need_clarification"])
        self.assertEqual(result["clarification"], "你说的是哪一年？")
        self.assertIn("hints", result)
        self.assertIn("判定", [s["name"] for s in result["stages"]])

    # ---- 安全闸 ----

    def test_dangerous_sql_is_blocked(self) -> None:
        self.use('{"need_clarification": false, "sql": "DROP TABLE orders"}')
        result = self.pipe.ask("把订单表删了")

        self.assertFalse(result["ok"])
        self.assertIn("安全闸", [s["name"] for s in result["stages"]])
        guard_stage = next(s for s in result["stages"] if s["name"] == "安全闸")
        self.assertEqual(guard_stage["detail"], "拦截")  # 拦截 ≠ 执行失败，是两种不同的阶段结果
        self.assertTrue(result["error"])

    def test_read_file_function_is_blocked(self) -> None:
        self.use("{\"need_clarification\": false, \"sql\": \"SELECT * FROM read_csv('x.csv')\"}")
        result = self.pipe.ask("读一下文件")
        self.assertFalse(result["ok"])

    # ---- 结果校验 Agent ----

    def test_verification_flags_fanout(self) -> None:
        """金额被放大到远超全库总额时，校验环节要报警。"""
        self.use(
            '{"need_clarification": false, '
            '"sql": "SELECT SUM(pay_amount) * 1000 AS gmv FROM orders"}'
        )
        result = self.pipe.ask("2025 年销售额是多少")

        self.assertTrue(result["ok"])
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("magnitude_fanout", codes)
        stage = next(s for s in result["stages"] if s["name"] == "结果校验")
        self.assertIn("可疑", stage["detail"])

    def test_empty_result_is_flagged(self) -> None:
        self.use(
            '{"need_clarification": false, '
            '"sql": "SELECT COUNT(*) FROM orders WHERE order_id > 99999"}'
        )
        result = self.pipe.ask("2025 年有多少订单")
        # 计数题返回一行 0 不算空结果；这里验的是"结果为空"那条规则不误报
        self.assertTrue(result["ok"])

    # ---- 多轮 ----

    def test_followup_is_rewritten(self) -> None:
        self.use(
            "2024 年各渠道的销售额是多少？",
            '{"need_clarification": false, "sql": "SELECT COUNT(*) AS cnt FROM orders"}',
        )
        history = [{"question": "2025 年各渠道的销售额是多少？", "sql": "SELECT 1"}]
        result = self.pipe.ask("那 2024 年呢？", history=history)

        self.assertTrue(result["ok"])
        self.assertEqual(result["rewritten"], "2024 年各渠道的销售额是多少？")
        self.assertIn("改写追问", [s["name"] for s in result["stages"]])
        # 第二次 chat 用的 prompt 里应该带的是改写后的问题
        self.assertIn("2024 年各渠道的销售额", self.stub.calls[1]["prompt"])

    def test_standalone_question_skips_rewrite(self) -> None:
        self.use('{"need_clarification": false, "sql": "SELECT COUNT(*) AS cnt FROM orders"}')
        history = [{"question": "上一个问题", "sql": "SELECT 1"}]
        result = self.pipe.ask("2025 年有多少订单", history=history)
        self.assertNotIn("改写追问", [s["name"] for s in result["stages"]])

    # ---- 密钥与失败 ----

    def test_api_key_is_passed_through(self) -> None:
        captured: list[str | None] = []
        self.use('{"need_clarification": false, "sql": "SELECT COUNT(*) AS cnt FROM orders"}')
        stub = self.stub
        self.pipe._llm_client = lambda api_key=None: (captured.append(api_key), stub)[1]  # type: ignore[method-assign]

        self.pipe.ask("有多少订单", api_key="sk-abc")
        self.assertEqual(captured[0], "sk-abc")

    def test_missing_key_returns_friendly_error(self) -> None:
        def boom(api_key=None):
            raise RuntimeError("缺少 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入 key")

        self.pipe._llm_client = boom  # type: ignore[method-assign]
        result = self.pipe.ask("有多少订单")
        self.assertFalse(result["ok"])
        self.assertIn("DEEPSEEK_API_KEY", result["error"])

    def test_session_records_turn(self) -> None:
        self.use('{"need_clarification": false, "sql": "SELECT COUNT(*) AS cnt FROM orders"}')
        self.pipe.ask("有多少订单", session_id="s1")
        history = self.pipe.sessions.history("s1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["question"], "有多少订单")

    def test_failed_query_does_not_pollute_session(self) -> None:
        self.use('{"need_clarification": false, "sql": "DROP TABLE orders"}')
        self.pipe.ask("删表", session_id="s2")
        self.assertEqual(self.pipe.sessions.history("s2"), [])


if __name__ == "__main__":
    unittest.main()
