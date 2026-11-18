"""服务端会话：把多轮对话的历史从浏览器挪到服务端。

之前在浏览器的 localStorage 里，带来三个问题：
1. 换个设备、开个隐身窗口，上下文就没了；
2. 每次提问都要把整段历史当请求体发上来，越长越浪费；
3. 客户端可以随意改写历史——这对"可审计"来说是个隐患。

现在改成：客户端只带一个 session_id，历史由服务端维护。

设计取舍：
* **内存态**，不落盘。会话是「当前这次对话」的临时状态，没有持久化价值；
  服务重启丢失是可接受的，而且避免了磁盘上的隐私残留。
* **有上限**：单会话最多保留 max_turns 轮，全局最多 max_sessions 个会话，
  超时（默认 6 小时）自动过期——不然一个长期运行的服务会被慢慢吃满内存。
* **可手动清除**：`clear()` 对应界面上那个「清空对话」按钮。
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class Turn:
    question: str
    sql: str
    rewritten: str | None = None
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"question": self.question, "sql": self.sql, "rewritten": self.rewritten}


class SessionStore:
    """按 session_id 保存最近若干轮问答。线程安全。"""

    def __init__(
        self,
        max_turns: int = 6,
        max_sessions: int = 200,
        ttl_seconds: int = 6 * 3600,
    ) -> None:
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._turns: dict[str, list[Turn]] = {}
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---------------- 内部 ----------------

    def _expired(self, sid: str, now: float) -> bool:
        last = self._touched.get(sid, 0)
        return now - last > self.ttl_seconds

    def _evict(self, now: float) -> None:
        for sid in [s for s in self._touched if self._expired(s, now)]:
            self._turns.pop(sid, None)
            self._touched.pop(sid, None)
        # 会话数超限时，丢掉最久没动过的
        if len(self._touched) > self.max_sessions:
            oldest = sorted(self._touched.items(), key=lambda kv: kv[1])[
                : len(self._touched) - self.max_sessions
            ]
            for sid, _ in oldest:
                self._turns.pop(sid, None)
                self._touched.pop(sid, None)

    # ---------------- 对外 ----------------

    @staticmethod
    def new_id() -> str:
        return secrets.token_urlsafe(9)

    def history(self, session_id: str | None) -> list[dict]:
        """取出会话历史（给模型看的形态）。不存在就返回空列表。"""
        if not session_id:
            return []
        now = time.time()
        with self._lock:
            if self._expired(session_id, now):
                self._turns.pop(session_id, None)
                self._touched.pop(session_id, None)
                return []
            return [t.as_dict() for t in self._turns.get(session_id, [])]

    def append(self, session_id: str | None, question: str, sql: str, rewritten: str | None = None) -> None:
        if not session_id:
            return
        now = time.time()
        with self._lock:
            self._evict(now)
            turns = self._turns.setdefault(session_id, [])
            turns.append(Turn(question=question, sql=sql, rewritten=rewritten))
            if len(turns) > self.max_turns:
                del turns[: len(turns) - self.max_turns]
            self._touched[session_id] = now

    def clear(self, session_id: str | None) -> bool:
        """清空某个会话。返回是否真的清掉了东西（方便接口回报）。"""
        if not session_id:
            return False
        with self._lock:
            existed = session_id in self._turns
            self._turns.pop(session_id, None)
            self._touched.pop(session_id, None)
            return existed

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            self._evict(now)
            return {
                "sessions": len(self._turns),
                "turns": sum(len(v) for v in self._turns.values()),
                "max_turns_per_session": self.max_turns,
                "ttl_seconds": self.ttl_seconds,
            }
