"""极简 DeepSeek 客户端（只用标准库）。

不引 openai SDK 的原因很实际：这个环境 pip 装不了包，而一次 chat 调用
本来也只需要 urllib + json 二十行代码。

设计上只做三件事：读 .env、发请求（带重试）、按 prompt 哈希缓存。
缓存很重要 —— 调 prompt 的时候没人想每次重跑都把 30 道题的钱再花一遍。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config


def load_dotenv(path: Path = config.ROOT / ".env") -> None:
    """把 .env 里的键值读进 os.environ（已存在的环境变量优先）。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(slots=True)
class LLMResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False


class ResponseCache:
    def __init__(self, path: Path = config.CACHE_DIR / "llm_responses.json") -> None:
        self._path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def get(self, key: str) -> LLMResponse | None:
        item = self._data.get(key)
        if item is None:
            return None
        return LLMResponse(item["content"], item.get("prompt_tokens", 0),
                           item.get("completion_tokens", 0), cached=True)

    def put(self, key: str, response: LLMResponse) -> None:
        self._data[key] = {
            "content": response.content,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")


class DeepSeekClient:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 120,
        max_retries: int = 3,
        use_cache: bool = True,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache = ResponseCache() if use_cache else None
        if not self.api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入 key")

    def chat(self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 1024) -> LLMResponse:
        key = hashlib.sha256(
            json.dumps([self.model, system, user, temperature, max_tokens], ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                usage = data.get("usage", {})
                result = LLMResponse(
                    content=data["choices"][0]["message"]["content"] or "",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
                if self.cache is not None:
                    self.cache.put(key, result)
                return result
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
                # 4xx（除限流）重试没有意义
                if 400 <= exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 8))

        raise RuntimeError(f"DeepSeek 调用失败（{self.max_retries} 次重试后）：{last_error}")
