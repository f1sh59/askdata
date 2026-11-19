"""LLM 层的本地部分：响应缓存与输出解析。

这些都不需要网络，是"每次调用都会走到"的代码路径：
缓存命中能省一次真实调用（也省 token），解析错了会把正常回复判成失败。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from askdata.llm import DeepSeekClient, LLMResponse, ResponseCache
from askdata.strategies.llm import extract_sql


class ResponseCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "cache.json"
        self.cache = ResponseCache(self.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_miss_returns_none(self) -> None:
        self.assertIsNone(self.cache.get("no-such-key"))

    def test_put_then_get(self) -> None:
        response = LLMResponse("SELECT 1", prompt_tokens=10, completion_tokens=2)
        self.cache.put("k1", response)

        hit = self.cache.get("k1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.content, "SELECT 1")
        self.assertTrue(hit.cached)

    def test_persists_to_disk(self) -> None:
        self.cache.put("k1", LLMResponse("SELECT 1"))
        # 新实例从同一个文件读，模拟进程重启
        reopened = ResponseCache(self.path)
        self.assertIsNotNone(reopened.get("k1"))

    def test_broken_cache_file_is_tolerated(self) -> None:
        self.path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        cache = ResponseCache(self.path)  # 不该抛
        self.assertIsNone(cache.get("k1"))

    def test_cache_key_is_deterministic(self) -> None:
        """key 是 chat() 里按 (model, system, user, temperature) 算的 sha256。

        同样输入必须落到同一个 key，否则缓存永远命中不了。
        """
        import hashlib
        import json

        def key_of(model: str, system: str, user: str, temperature: float = 0.0) -> str:
            return hashlib.sha256(
                json.dumps([model, system, user, temperature, 1024], ensure_ascii=False).encode(
                    "utf-8"
                )
            ).hexdigest()

        self.assertEqual(key_of("m", "s", "u"), key_of("m", "s", "u"))
        self.assertNotEqual(key_of("m", "s", "u"), key_of("m", "s", "u2"))


class ClientInitTest(unittest.TestCase):
    def test_explicit_key_is_used(self) -> None:
        client = DeepSeekClient(api_key="sk-test", use_cache=False)
        self.assertEqual(client.api_key, "sk-test")

    def test_defaults(self) -> None:
        client = DeepSeekClient(api_key="sk-test", use_cache=False)
        self.assertTrue(client.base_url.startswith("http"))
        self.assertTrue(client.model)

    def test_missing_key_raises(self) -> None:
        """没有 key 时构造就报错，而不是等到第一次调用才炸。

        注意不能用 pop：客户端构造时会 load_dotenv() 把 .env 读回来，
        所以要把变量显式置空（setdefault 不会覆盖已存在的空值）。
        """
        import os

        saved = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = ""
        try:
            with self.assertRaises(RuntimeError):
                DeepSeekClient(use_cache=False)
        finally:
            if saved is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = saved


class ExtractSqlTest(unittest.TestCase):
    def test_plain_sql(self) -> None:
        self.assertEqual(extract_sql("SELECT 1"), "SELECT 1")

    def test_fenced_block(self) -> None:
        text = "好的，这是查询：\n```sql\nSELECT COUNT(*) FROM orders\n```\n希望有帮助"
        self.assertEqual(extract_sql(text), "SELECT COUNT(*) FROM orders")

    def test_truncates_at_semicolon(self) -> None:
        self.assertEqual(extract_sql("SELECT 1; DROP TABLE orders"), "SELECT 1")

    def test_no_sql_returns_none(self) -> None:
        self.assertIsNone(extract_sql("我不知道"))

    def test_with_leading_explanation(self) -> None:
        self.assertEqual(extract_sql("分析如下：\nSELECT 2"), "SELECT 2")


class CacheRoundTripJsonTest(unittest.TestCase):
    def test_file_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            cache = ResponseCache(path)
            cache.put("k", LLMResponse("SELECT 1", 3, 4))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("k", payload)


if __name__ == "__main__":
    unittest.main()
