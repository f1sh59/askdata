"""SQL 四道闸里的三道：AST 白名单、强制 LIMIT、行级权限。

这里的原则是**白名单，不是黑名单**：只接受"单条、根节点是查询、表只能是普通标识符"的语句，
其余一律拒绝。黑名单（比如"禁止 DROP"）永远堵不完，`PRAGMA`、`ATTACH`、`COPY`、
`read_csv` 这些能力随便漏一个就能读到进程能读的任何文件。

超时熔断在 `db.Sandbox` 里（需要连接对象，跟 AST 无关）。
"""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# sqlglot 对 LOAD / CALL 这类语法会打一条"降级成 Command"的 warning。
# 降级对我们没有影响 —— Command 节点本来就在禁止列表里 —— 但日志太吵。
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# 根节点只允许这两种：SELECT / UNION。WITH ... SELECT 的根节点仍然是 Select。
ALLOWED_ROOTS = {"Select", "Union"}

# 出现在树里任何位置都拒绝的节点类型。
# 用类型名而不是类对象，是为了不被 sqlglot 版本差异咬到。
FORBIDDEN_NODES = {
    "Insert", "Update", "Delete", "Merge",
    "Create", "Drop", "Alter", "AlterTable", "TruncateTable",
    "Pragma", "Attach", "Detach", "Copy", "Export", "Import",
    "Install", "Load", "Set", "Use", "Call", "Command",
    "Transaction", "Commit", "Rollback", "Begin",
    "Grant", "Revoke", "Analyze", "Vacuum", "Checkpoint", "Describe",
    "Comment", "Refresh", "Cache", "Uncache", "Kill",
}

MAX_STATEMENTS = 1


class SqlGuardError(Exception):
    """SQL 被闸门拦下。消息里会写清楚是哪一道闸。"""


class RowPolicy:
    """行级权限：按表注入 WHERE 谓词，模拟"每个租户只能看自己的数据"。

    真实系统里这些谓词来自权限服务；这里用固定映射演示，接口是 `predicates` 这个字典。
    """

    def __init__(self, predicates: dict[str, str] | None = None) -> None:
        self.predicates = dict(predicates or {})

    def predicate_for(self, table: str) -> str | None:
        return self.predicates.get(table)


class SqlGuard:
    def __init__(self, max_rows: int = 1000, policy: RowPolicy | None = None) -> None:
        self.max_rows = max_rows
        self.policy = policy or RowPolicy()

    def check(self, sql: str) -> str:
        """校验并改写 SQL；不合格抛 SqlGuardError。返回可执行的单条语句。"""
        raw = sql.strip().rstrip(";").strip()
        if not raw:
            raise SqlGuardError("空 SQL")

        try:
            parsed = [node for node in sqlglot.parse(raw, dialect="duckdb") if node is not None]
        except ParseError as exc:
            raise SqlGuardError(f"无法解析：{exc}") from exc

        if len(parsed) != MAX_STATEMENTS:
            raise SqlGuardError(f"只允许单条语句，实际 {len(parsed)} 条")

        root = parsed[0]

        if type(root).__name__ not in ALLOWED_ROOTS:
            raise SqlGuardError(f"只允许 SELECT 查询，实际是 {type(root).__name__}")

        for node in root.walk():
            name = type(node).__name__
            if name in FORBIDDEN_NODES:
                raise SqlGuardError(f"语句中包含被禁止的操作：{name}")

        for table in root.find_all(exp.Table):
            # 普通表名的 this 是 Identifier；read_csv / range / duckdb_settings 之类是函数节点
            if type(table.this).__name__ != "Identifier":
                raise SqlGuardError(f"不允许表函数：{table.name or type(table.this).__name__}")

        root = self._apply_policy(root)
        root = self._enforce_limit(root)
        return root.sql(dialect="duckdb")

    def _apply_policy(self, root: exp.Expression) -> exp.Expression:
        """把受限表替换成带谓词的子查询，别名保持不变，尽量不影响列引用。"""
        if not self.policy.predicates:
            return root

        for table in list(root.find_all(exp.Table)):
            name = table.name.lower()
            predicate = self.policy.predicate_for(name)
            if predicate is None:
                continue

            alias = table.alias_or_name
            inner = sqlglot.parse_one(
                f'SELECT * FROM "{name}" WHERE {predicate}', dialect="duckdb"
            )
            replacement = exp.Subquery(
                this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias))
            )
            table.replace(replacement)

        return root

    def _enforce_limit(self, root: exp.Expression) -> exp.Expression:
        """没有 LIMIT 的查询补一个，防止一条 SELECT 把整个库拉出来。"""
        existing = root.args.get("limit")
        if existing is not None:
            return root
        return root.limit(self.max_rows)
