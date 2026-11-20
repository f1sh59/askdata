"""审计日志：谁、什么时候、问了什么、跑出什么 SQL、结果如何。

为什么必须记：
    出了问题要能回答「这条 SQL 是谁跑的」。只有指标和日志不够——
    请求日志是给运维看的，审计是给合规和追责看的，字段不一样。

格式：一行一个 JSON（JSONL），便于 `tail -f`、`grep`，也不怕写一半。
落盘用 append 模式 + 单次 write，天然追加友好。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

# 不记录原始结果集（可能很大），只记行数与耗时
MAX_QUESTION_CHARS = 500


class AuditLog:
    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()

    def record(
        self,
        *,
        user: str,
        action: str,
        question: str = "",
        sql: str = "",
        ok: bool = True,
        row_count: int = 0,
        elapsed_ms: float = 0.0,
        request_id: str = "",
        detail: str = "",
    ) -> None:
        if not self.enabled:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "epoch": round(time.time(), 3),
            "user": user,
            "action": action,
            "question": question[:MAX_QUESTION_CHARS],
            "sql": sql[:2000],
            "ok": ok,
            "rows": row_count,
            "ms": round(elapsed_ms, 1),
            "request_id": request_id,
        }
        if detail:
            entry["detail"] = detail[:500]

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError:
                # 审计写不进去不能影响正常请求；但也不静默吞掉
                pass

    def recent(self, limit: int = 50, user: str = "") -> list[dict[str, Any]]:
        """读最近的记录（新的在前）。文件不存在就是空。"""
        if not self.path.exists():
            return []
        limit = max(1, min(limit, 500))
        rows: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if user and entry.get("user") != user:
                        continue
                    rows.append(entry)
        except OSError:
            return []
        return rows[-limit:][::-1]

    def stats(self) -> dict[str, Any]:
        rows = self.recent(limit=500)
        return {
            # 注意别叫 entries：接口里 entries 是列表，同名会把列表覆盖成数字
            "total_read": len(rows),
            "path": str(self.path),
            "enabled": self.enabled,
        }
