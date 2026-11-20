"""服务端会话：把多轮对话的历史从浏览器挪到服务端。

之前在浏览器的 localStorage 里，带来三个问题：
1. 换个设备、开个隐身窗口，上下文就没了；
2. 每次提问都要把整段历史当请求体发上来，越长越浪费；
3. 客户端可以随意改写历史——这对"可审计"来说是个隐患。

现在改成：客户端只带一个 session_id，历史由服务端维护。

持久化：
    默认落盘到 `data/sessions.db`（SQLite，标准库自带）。重启服务后，
    没超时的会话还在——刷新页面、重启进程都不再丢上下文。
    传 `path=None` 则退回纯内存（测试和临时演示用）。

设计取舍：
* **写穿（write-through）**：内存是缓存，落盘是事实来源。单个服务进程内
  历史很短、写入很少，不值得做写回或批量。
* **有上限**：单会话最多 max_turns 轮，全局最多 max_sessions 个会话，
  超时（默认 6 小时）自动过期——不然长期运行会把磁盘慢慢吃满。
* **可手动清除**：`clear()` 对应界面上那个「清空对话」按钮，同时删库里的行。
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    session_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    question   TEXT NOT NULL,
    sql        TEXT NOT NULL,
    rewritten  TEXT,
    at         REAL NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_turns_at ON turns(at);
"""


@dataclass(slots=True)
class Turn:
    question: str
    sql: str
    rewritten: str | None = None
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"question": self.question, "sql": self.sql, "rewritten": self.rewritten}


class SessionStore:
    """按 session_id 保存最近若干轮问答。线程安全，可持久化。"""

    def __init__(
        self,
        max_turns: int = 6,
        max_sessions: int = 200,
        ttl_seconds: int = 6 * 3600,
        path: Path | None = None,
    ) -> None:
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self.path = path
        self._turns: dict[str, list[Turn]] = {}
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False：HTTP handler 是多个线程
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.executescript(SCHEMA)
            self._db.commit()
            self._restore()

    # ---------------- 持久化 ----------------

    def _restore(self) -> None:
        """启动时把没过期的会话读回内存。"""
        if self._db is None:
            return
        cutoff = time.time() - self.ttl_seconds
        try:
            rows = self._db.execute(
                "SELECT session_id, question, sql, rewritten, at FROM turns "
                "WHERE at >= ? ORDER BY session_id, seq",
                (cutoff,),
            ).fetchall()
        except sqlite3.DatabaseError:
            return
        for sid, question, sql, rewritten, at in rows:
            self._turns.setdefault(sid, []).append(
                Turn(question=question, sql=sql, rewritten=rewritten, at=at)
            )
            self._touched[sid] = max(self._touched.get(sid, 0.0), at)
        # 只保留最近 max_turns 轮，和内存态行为一致
        for sid, turns in self._turns.items():
            if len(turns) > self.max_turns:
                self._turns[sid] = turns[-self.max_turns :]

    def _persist_append(self, session_id: str, turn: Turn) -> None:
        if self._db is None:
            return
        with self._lock:
            try:
                seq = self._db.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM turns WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                self._db.execute(
                    "INSERT INTO turns (session_id, seq, question, sql, rewritten, at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, seq, turn.question, turn.sql, turn.rewritten, turn.at),
                )
                # 只留最近 max_turns 轮，避免单会话无限长
                self._db.execute(
                    "DELETE FROM turns WHERE session_id = ? AND seq <= ?",
                    (session_id, seq - self.max_turns),
                )
                self._db.commit()
            except sqlite3.DatabaseError:
                self._db.rollback()

    def _persist_clear(self, session_id: str) -> None:
        if self._db is None:
            return
        with self._lock:
            try:
                self._db.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
                self._db.commit()
            except sqlite3.DatabaseError:
                self._db.rollback()

    def _persist_evict(self, sids: list[str]) -> None:
        if self._db is None or not sids:
            return
        with self._lock:
            try:
                self._db.executemany(
                    "DELETE FROM turns WHERE session_id = ?", [(s,) for s in sids]
                )
                self._db.commit()
            except sqlite3.DatabaseError:
                self._db.rollback()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # ---------------- 内部 ----------------

    def _expired(self, sid: str, now: float) -> bool:
        last = self._touched.get(sid, 0)
        return now - last > self.ttl_seconds

    def _evict(self, now: float) -> None:
        gone: list[str] = []
        for sid in [s for s in self._touched if self._expired(s, now)]:
            self._turns.pop(sid, None)
            self._touched.pop(sid, None)
            gone.append(sid)
        # 会话数超限时，丢掉最久没动过的
        if len(self._touched) > self.max_sessions:
            oldest = sorted(self._touched.items(), key=lambda kv: kv[1])[
                : len(self._touched) - self.max_sessions
            ]
            for sid, _ in oldest:
                self._turns.pop(sid, None)
                self._touched.pop(sid, None)
                gone.append(sid)
        if gone:
            self._persist_evict(gone)

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
            return [t.as_dict() for t in self._turns.get(session_id, [])]

    def append(
        self, session_id: str | None, question: str, sql: str, rewritten: str | None = None
    ) -> None:
        if not session_id:
            return
        now = time.time()
        with self._lock:
            self._evict(now)
            turn = Turn(question=question, sql=sql, rewritten=rewritten)
            turns = self._turns.setdefault(session_id, [])
            turns.append(turn)
            if len(turns) > self.max_turns:
                del turns[: len(turns) - self.max_turns]
            self._touched[session_id] = now
        self._persist_append(session_id, turn)

    def clear(self, session_id: str | None) -> bool:
        """清空某个会话。返回是否真的清掉了东西（方便接口回报）。"""
        if not session_id:
            return False
        with self._lock:
            existed = session_id in self._turns
            self._turns.pop(session_id, None)
            self._touched.pop(session_id, None)
        self._persist_clear(session_id)
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
                "persistent": self._db is not None,
                "path": str(self.path) if self.path else "",
            }
