"""只读 SQL 执行沙箱：四道闸都收在这里。

1. **AST 白名单**（`guard.SqlGuard`）：只允许单条、根节点为查询、表为普通标识符的语句；
2. **强制 LIMIT**：没写 LIMIT 的查询补一个，防止一次拉全库；
3. **超时熔断**：执行放在主线程，另起一个定时器到点调 `connection.interrupt()`；
4. **行级权限**：把受限表改写成带谓词的子查询（由 `RowPolicy` 配置）。

闸门挡下的 SQL 不抛异常，而是返回带 error 的 QueryResult —— 这样它会被记成一次
`sql_error`，和"模型写错 SQL"走同一条路，不会让整轮评测崩掉。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .guard import RowPolicy, SqlGuard, SqlGuardError


@dataclass(slots=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    error: str | None = None
    elapsed_ms: float = 0.0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


class Sandbox:
    def __init__(
        self,
        db_path: Path,
        *,
        read_only: bool = True,
        max_rows: int = 1000,
        timeout_s: float = 10.0,
        policy: RowPolicy | None = None,
    ) -> None:
        self._con = duckdb.connect(str(db_path), read_only=read_only)
        self._max_rows = max_rows
        self._timeout_s = timeout_s
        self._guard = SqlGuard(max_rows=max_rows, policy=policy)

    def run(self, sql: str) -> QueryResult:
        started = time.perf_counter()

        try:
            safe_sql = self._guard.check(sql)
        except SqlGuardError as exc:
            return QueryResult(
                [], [], f"被安全闸拦截：{exc}", (time.perf_counter() - started) * 1000
            )

        timed_out = threading.Event()

        def _interrupt() -> None:
            timed_out.set()
            self._con.interrupt()

        timer = threading.Timer(self._timeout_s, _interrupt)
        timer.daemon = True
        timer.start()
        try:
            self._con.execute(safe_sql)
            columns = [d[0] for d in (self._con.description or [])]
            rows = self._con.fetchmany(self._max_rows + 1)
            truncated = len(rows) > self._max_rows
            return QueryResult(
                columns=columns,
                rows=rows[: self._max_rows],
                error=None,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                truncated=truncated,
            )
        except Exception as exc:  # noqa: BLE001 - 任何失败都要变成一条评测结果
            message = (
                f"查询超时（>{self._timeout_s:g}s），已中断"
                if timed_out.is_set()
                else f"{type(exc).__name__}: {exc}"
            )
            return QueryResult(
                columns=[], rows=[], error=message,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        finally:
            timer.cancel()

    def execute_trusted(self, sql: str, rows: list[tuple] | None = None) -> QueryResult:
        """给服务端受信代码用：跳过 AST 闸门（导入建表、批量插入）。

        只有服务内部调用，绝不接受来自模型或用户的原始 SQL。
        """
        started = time.perf_counter()
        try:
            if rows:
                self._con.executemany(sql, rows)
            else:
                self._con.execute(sql)
            columns = [d[0] for d in (self._con.description or [])]
            return QueryResult(columns, [], None, (time.perf_counter() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            return QueryResult(
                [], [], f"{type(exc).__name__}: {exc}", (time.perf_counter() - started) * 1000
            )

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
