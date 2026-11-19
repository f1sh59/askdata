"""chat() 的请求、重试与解析——用假 urlopen，不碰网络。

这段代码平时跑不到（要真调用），但它承担"和上游打交道"的全部逻辑：
重试策略、4xx 不重试、响应解析、缓存写入。坏了就是线上全挂，
所以必须能在离线环境里测。
"""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from askdata.llm import DeepSeekClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class ChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        cache_path = Path(self._tmp.name) / "cache.json"
        self.client = DeepSeekClient(api_key="sk-test", max_retries=2)
        # 把缓存指到临时目录，避免污染仓库里的 .cache
        from askdata.llm import ResponseCache

        self.client.cache = ResponseCache(cache_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_successful_call(self) -> None:
        payload = {
            "choices": [{"message": {"content": "SELECT 1"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            response = self.client.chat("system", "user")

        self.assertEqual(response.content, "SELECT 1")
        self.assertEqual(response.prompt_tokens, 12)
        self.assertEqual(response.completion_tokens, 3)

    def test_4xx_is_not_retried(self) -> None:
        """401 之类的错误重试没有意义，不该白白等几秒。"""
        error = urllib.error.HTTPError(url="x", code=401, msg="unauthorized", hdrs=None, fp=None)
        error.read = lambda: b'{"error": "bad key"}'

        with patch("urllib.request.urlopen", side_effect=error) as mocked:
            with self.assertRaises(RuntimeError):
                self.client.chat("system", "user")
        self.assertEqual(mocked.call_count, 1)

    def test_5xx_is_retried(self) -> None:
        error = urllib.error.HTTPError(url="x", code=500, msg="boom", hdrs=None, fp=None)
        error.read = lambda: b"server error"

        with patch("urllib.request.urlopen", side_effect=error) as mocked, patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                self.client.chat("system", "user")
        self.assertEqual(mocked.call_count, 2)  # max_retries=2

    def test_cache_short_circuits_second_call(self) -> None:
        payload = {"choices": [{"message": {"content": "SELECT 1"}}], "usage": {}}
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)) as mocked:
            first = self.client.chat("system", "user")
            second = self.client.chat("system", "user")

        self.assertEqual(mocked.call_count, 1)  # 第二次走缓存
        self.assertEqual(second.content, first.content)
        self.assertTrue(second.cached)

    def test_broken_json_is_retried_then_fails(self) -> None:
        class BrokenResponse(FakeResponse):
            def __init__(self) -> None:
                self._body = b"{ not json"

        with patch("urllib.request.urlopen", return_value=BrokenResponse()), patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                self.client.chat("system", "user")


if __name__ == "__main__":
    unittest.main()
