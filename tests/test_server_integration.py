"""集成测试：真的起一个 HTTP 服务，打真实接口。

单元测试能证明函数对，证明不了"服务装起来能用"：
路由是否挂上、响应头对不对、错误码是否一致——这些只有真起服务才看得见。

做法：
    1. 用 schema.sql 在临时目录造一个空库（不跑 build_db，省 20 秒）；
    2. 把 server 模块的 pipeline 单例替换成指向这个库的实例；
    3. 起在随机端口上，用 urllib 打接口；
    4. 关掉限流，避免测试互相 429。
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import askdata.server as server_mod
from askdata.service import Pipeline

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


def _build_empty_db(path: Path) -> None:
    import duckdb

    conn = duckdb.connect(str(path))
    for stmt in SCHEMA.read_text(encoding="utf-8").split(";"):
        if stmt.strip():
            conn.execute(stmt)
    # 给 orders 塞几行，保证 /api/check 的 SELECT 有东西可查
    conn.execute(
        "INSERT INTO orders (order_id, user_id, channel_id, order_time, status, "
        "total_amount, discount_amount, pay_amount) VALUES "
        "(1, 1, 1, TIMESTAMP '2025-01-01 10:00:00', '已完成', 100, 0, 100)"
    )
    conn.close()


class ServerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "test.duckdb"
        _build_empty_db(cls.db_path)

        cls.pipeline = Pipeline(db_path=cls.db_path, writable=True)
        cls._original_pipeline = server_mod.pipeline
        server_mod.pipeline = lambda: cls.pipeline  # 替换单例

        cls._original_limit = server_mod.LIMITER.enabled
        server_mod.LIMITER.enabled = False  # 测试里不限流

        Handler = server_mod.Handler

        class QuietHandler(Handler):
            def log_message(self, fmt: str, *args) -> None:  # 静音访问日志
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        server_mod.pipeline = cls._original_pipeline
        server_mod.LIMITER.enabled = cls._original_limit
        cls._tmp.cleanup()

    # ---- 工具 ----

    def _get(self, path: str, *, headers: dict | None = None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers=headers or {}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, {"raw": body}

    # ---- 探针 ----

    def test_health(self) -> None:
        status, body, _ = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "alive")

    def test_ready_reports_database(self) -> None:
        status, body, _ = self._get("/ready")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["checks"]["database"], "ok")
        self.assertGreater(payload["checks"]["tables"], 0)

    def test_metrics_is_prometheus_text(self) -> None:
        self._get("/api/overview")  # 制造一次请求，确保有数据
        status, body, headers = self._get("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get("Content-Type", ""))
        self.assertIn("askdata_up 1", body)
        self.assertIn("askdata_http_requests_total", body)

    # ---- 中间件 ----

    def test_every_response_has_request_id(self) -> None:
        _, _, headers = self._get("/health")
        rid = headers.get("X-Request-Id")
        self.assertIsNotNone(rid)
        self.assertEqual(len(rid), 12)

    def test_unknown_api_returns_code(self) -> None:
        status, body, _ = self._get("/api/does-not-exist")
        self.assertEqual(status, 404)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "not_found")

    def test_bad_request_carries_code(self) -> None:
        status, payload = self._post("/api/ask", {"question": "   "})
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "bad_request")

    # ---- 业务接口（不调模型）----

    def test_overview(self) -> None:
        status, body, _ = self._get("/api/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertGreater(payload["tables"], 0)
        self.assertIn("model", payload)

    def test_check_allows_select(self) -> None:
        status, payload = self._post("/api/check", {"sql": "SELECT COUNT(*) FROM orders"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["verdict"], "allowed")
        self.assertIn("LIMIT", payload["rewritten"].upper())

    def test_check_blocks_drop(self) -> None:
        status, payload = self._post("/api/check", {"sql": "DROP TABLE orders"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["verdict"], "blocked")

    def test_static_index_served(self) -> None:
        status, body, headers = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("askdata", body)

    def test_path_traversal_blocked(self) -> None:
        status, _, _ = self._get("/../askdata/config.py")
        self.assertEqual(status, 404)

    # ---- 其余不依赖模型的接口 ----

    def test_tables_payload(self) -> None:
        status, body, _ = self._get("/api/tables")
        self.assertEqual(status, 200)
        tables = json.loads(body)
        self.assertIsInstance(tables, list)
        self.assertGreater(len(tables), 0)

    def test_questions_and_attacks(self) -> None:
        for path in ("/api/questions", "/api/attacks"):
            status, body, _ = self._get(path)
            self.assertEqual(status, 200, path)
            self.assertIsInstance(json.loads(body), list)

    def test_hints(self) -> None:
        status, body, _ = self._get("/api/hints")
        self.assertEqual(status, 200)
        hints = json.loads(body)
        self.assertIn("dimensions", hints)
        self.assertIn("metrics", hints)

    def test_reports_listing(self) -> None:
        status, body, _ = self._get("/api/reports")
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body), list)

    def test_report_missing_name(self) -> None:
        status, body, _ = self._get("/api/report")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "bad_request")

    def test_session_clear(self) -> None:
        status, payload = self._post("/api/session/clear", {"session_id": "nope"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["cleared"])

    def test_import_roundtrip(self) -> None:
        """预览 → 导入 → 删除，走完整条数据接入链路。"""
        csv_text = "地区,金额\n杭州,100\n上海,200\n"
        status, preview = self._post("/api/import/preview", {"text": csv_text})
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview["ok"])
        columns = preview["columns"]

        status, commit = self._post(
            "/api/import/commit",
            {"text": csv_text, "name": "it_test", "columns": columns},
        )
        self.assertEqual(status, 200, commit)
        self.assertTrue(commit["ok"])
        self.assertIn("imp_it_test", commit.get("table", ""))  # sanitize 会补 imp_ 前缀

        # 新表要立刻出现在数据字典里
        _, body, _ = self._get("/api/tables")
        names = {t.get("name") for t in json.loads(body)}
        self.assertIn("imp_it_test", names)

        status, dropped = self._post("/api/import/drop", {"table": "imp_it_test"})
        self.assertEqual(status, 200, dropped)
        self.assertTrue(dropped["ok"])

    def test_import_rejects_garbage(self) -> None:
        status, payload = self._post("/api/import/preview", {"text": "only_header\n"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "bad_request")

    def test_check_with_row_policy(self) -> None:
        status, payload = self._post(
            "/api/check", {"sql": "SELECT COUNT(*) FROM orders", "policy": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["verdict"], "allowed")
        self.assertIn("channel_id", payload["rewritten"])

    # ---- 缺 Key 的友好错误 / 静态兜底 ----

    def test_ask_without_key_asks_for_one(self) -> None:
        """没有可用 Key 时，要回一个能指导用户下一步的错误，而不是 500。"""
        original = server_mod.configured_api_key
        server_mod.configured_api_key = lambda: None
        try:
            status, payload = self._post("/api/ask", {"question": "2025 年销售额是多少"})
            self.assertEqual(status, 400)
            self.assertTrue(payload.get("need_api_key"))
            self.assertEqual(payload["code"], "bad_request")
        finally:
            server_mod.configured_api_key = original

    def test_stream_without_key_returns_json_error(self) -> None:
        original = server_mod.configured_api_key
        server_mod.configured_api_key = lambda: None
        try:
            status, payload = self._post(
                "/api/ask/stream", {"question": "2025 年销售额是多少"}
            )
            self.assertEqual(status, 400)
            self.assertTrue(payload.get("need_api_key"))
        finally:
            server_mod.configured_api_key = original

    def test_missing_static_file(self) -> None:
        status, _, _ = self._get("/no-such-file.js")
        self.assertEqual(status, 404)

    def test_drop_unknown_table(self) -> None:
        status, payload = self._post("/api/import/drop", {"table": "imp_not_exists"})
        self.assertEqual(status, 200)
        self.assertFalse(payload.get("ok"))

    def test_report_detail(self) -> None:
        _, body, _ = self._get("/api/reports")
        reports = json.loads(body)
        if not reports:
            self.skipTest("仓库里没有报告")
        status, detail, _ = self._get(f"/api/report?name={reports[0]['name']}")
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(detail), dict)

    def test_check_reports_execution_error(self) -> None:
        """不存在的表能通过 AST 闸门（语法合法），错误在执行阶段暴露。"""
        status, payload = self._post("/api/check", {"sql": "SELECT * FROM no_such_table"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["verdict"], "allowed")
        # 执行结果嵌在 executed 里：AST 合规 ≠ 能跑通
        self.assertFalse(payload["executed"]["ok"])
        self.assertTrue(payload["executed"]["error"])


if __name__ == "__main__":
    unittest.main()
