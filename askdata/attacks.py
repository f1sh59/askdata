"""50 条注入用例：验证四道闸拦得住，也验证它没把正常 SQL 一起拦了。

用例分两类：
* `BLOCKED` —— 必须被 `SqlGuard` 拒绝（错误信息以「被安全闸拦截」开头）；
* `ALLOWED` —— 必须能正常执行并且有结果。

关键点：这些 SQL 都是在**只读连接**上跑的。如果闸门漏了，数据库自己也会报错，
但报的是 "Cannot execute statement of type ..."，和"被安全闸拦截"完全不同 ——
所以断言能区分"闸门拦住了"和"数据库恰好不让干"。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AttackCase:
    name: str
    sql: str
    category: str


BLOCKED: list[AttackCase] = [
    # —— DDL ——
    AttackCase("drop_table", "DROP TABLE orders", "ddl"),
    AttackCase("drop_lowercase", "drop table orders", "ddl"),
    AttackCase("drop_mixed_case", "DrOp TaBlE orders", "ddl"),
    AttackCase("create_table", "CREATE TABLE evil (a INTEGER)", "ddl"),
    AttackCase("create_view", "CREATE VIEW v AS SELECT 1", "ddl"),
    AttackCase("alter_table", "ALTER TABLE orders ADD COLUMN x INTEGER", "ddl"),
    AttackCase("truncate", "TRUNCATE TABLE orders", "ddl"),
    # —— DML ——
    AttackCase("insert", "INSERT INTO orders (order_id) VALUES (1)", "dml"),
    AttackCase("update", "UPDATE orders SET status = '已完成'", "dml"),
    AttackCase("delete", "DELETE FROM orders", "dml"),
    AttackCase("merge", "MERGE INTO orders USING channels ON TRUE WHEN MATCHED THEN DELETE", "dml"),
    # —— 元数据 / 配置 ——
    AttackCase("pragma_table_info", "PRAGMA table_info('orders')", "pragma"),
    AttackCase("pragma_database_list", "PRAGMA database_list", "pragma"),
    AttackCase("pragma_profiling", "PRAGMA enable_profiling", "pragma"),
    AttackCase("set_memory", "SET memory_limit = '1GB'", "config"),
    AttackCase("set_threads", "SET threads = 1", "config"),
    AttackCase("install_extension", "INSTALL httpfs", "extension"),
    AttackCase("load_extension", "LOAD httpfs", "extension"),
    # —— 文件与外联 ——
    AttackCase("attach_db", "ATTACH 'C:/secret.db' AS secret", "file"),
    AttackCase("detach_db", "DETACH secret", "file"),
    AttackCase("copy_to", "COPY (SELECT 1) TO 'C:/out.csv'", "file"),
    AttackCase("copy_from", "COPY orders FROM 'C:/in.csv'", "file"),
    AttackCase("read_csv", "SELECT * FROM read_csv('C:/secret.csv')", "file"),
    AttackCase("read_csv_auto", "SELECT * FROM read_csv_auto('C:/secret.csv')", "file"),
    AttackCase("read_parquet", "SELECT * FROM read_parquet('C:/secret.parquet')", "file"),
    AttackCase("read_json", "SELECT * FROM read_json('C:/secret.json')", "file"),
    AttackCase("glob_fs", "SELECT * FROM glob('C:/**')", "file"),
    AttackCase("sqlite_scan", "SELECT * FROM sqlite_scan('C:/x.db', 't')", "file"),
    AttackCase("read_csv_subquery", "SELECT * FROM (SELECT * FROM read_csv('a.csv')) t", "file"),
    # —— 多语句 ——
    AttackCase("stacked_drop", "SELECT 1; DROP TABLE orders", "stacked"),
    AttackCase("stacked_two_selects", "SELECT 1; SELECT 2", "stacked"),
    AttackCase("stacked_insert", "SELECT 1; INSERT INTO orders (order_id) VALUES (1)", "stacked"),
    AttackCase("stacked_attach", "SELECT 1; ATTACH 'x.db' AS x", "stacked"),
    AttackCase("cte_then_insert", "WITH x AS (SELECT 1 AS a) INSERT INTO orders SELECT * FROM x", "stacked"),
    # —— 事务 / 管理 ——
    AttackCase("begin", "BEGIN TRANSACTION", "admin"),
    AttackCase("commit", "COMMIT", "admin"),
    AttackCase("vacuum", "VACUUM", "admin"),
    AttackCase("checkpoint", "CHECKPOINT", "admin"),
    AttackCase("call_pragma", "CALL pragma_version()", "admin"),
    AttackCase("grant", "GRANT ALL ON orders TO evil", "admin"),
    AttackCase("describe", "DESCRIBE SELECT 1", "admin"),
    # —— 系统函数当表用 ——
    AttackCase("duckdb_settings", "SELECT * FROM duckdb_settings()", "function"),
    AttackCase("duckdb_extensions", "SELECT * FROM duckdb_extensions()", "function"),
    AttackCase("duckdb_databases", "SELECT * FROM duckdb_databases()", "function"),
    AttackCase("generate_series", "SELECT * FROM range(10)", "function"),
    AttackCase("query_table", "SELECT * FROM query_table('orders')", "function"),
    # —— 注释与空白绕过 ——
    AttackCase("empty", "   ", "trick"),
    AttackCase("comment_before_ddl", "/* hi */ DROP TABLE orders", "trick"),
    AttackCase("semicolon_only", ";;;", "trick"),
    AttackCase("newline_stacked", "SELECT 1\n;\nDROP TABLE orders", "trick"),
]


ALLOWED: list[AttackCase] = [
    AttackCase("count", "SELECT COUNT(*) FROM orders", "select"),
    AttackCase("where", "SELECT order_id FROM orders WHERE status = '已完成' ORDER BY order_id LIMIT 5", "select"),
    AttackCase("cte", "WITH x AS (SELECT 1 AS a) SELECT a FROM x", "select"),
    AttackCase("union", "SELECT 1 AS a UNION ALL SELECT 2", "select"),
    AttackCase("window", "SELECT order_id, ROW_NUMBER() OVER (ORDER BY order_id) AS rn FROM orders", "select"),
    AttackCase("join_group", """
        SELECT c.channel_name, COUNT(*) AS cnt
        FROM orders o JOIN channels c ON c.channel_id = o.channel_id
        GROUP BY c.channel_name ORDER BY cnt DESC
    """, "select"),
    AttackCase("timestamp_literal", """
        SELECT ROUND(SUM(pay_amount), 2) FROM orders
        WHERE status IN ('已支付', '已发货', '已完成')
          AND order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01'
    """, "select"),
    AttackCase("quoted_identifier", 'SELECT COUNT(*) FROM "orders"', "select"),
    AttackCase("trailing_semicolon", "SELECT 1;", "select"),
    AttackCase("lowercase_select", "select count(*) from orders", "select"),
]
