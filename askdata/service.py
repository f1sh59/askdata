"""给前端用的服务层：把「一次问数」拆成可展示的阶段。

前端要展示的不是一个结果，而是**过程**：召回了哪几张表、模型有没有反问、
SQL 有没有过安全闸、执行花了多久。所以这里的返回值刻意做得很啰嗦。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, schema
from .attacks import ALLOWED, BLOCKED
from .clarify import CLOSED_TEMPLATE, USER_TEMPLATE, parse_decision
from .db import Sandbox
from .guard import RowPolicy, SqlGuard, SqlGuardError
from .llm import DeepSeekClient
from .models import load_dataset
from .retrieval import TableIndex
from .schema import BUSINESS_RULES
from .session import SessionStore
from .strategies.llm import SYSTEM_PROMPT, extract_sql


@dataclass(slots=True)
class Stage:
    name: str
    ms: float
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ms": round(self.ms, 1), "detail": self.detail}


class Pipeline:
    """全局只建一次：schema 内省 + 召回索引 + 数据库连接。"""

    def __init__(self, db_path: Path = config.DB_PATH, writable: bool = False) -> None:
        self._lock = threading.Lock()
        self.writable = writable
        self.sandbox = Sandbox(db_path, timeout_s=8.0, read_only=not writable)
        self.sessions = SessionStore()
        self.tables = schema.introspect(self.sandbox)
        self.index = TableIndex(self.tables)
        self._llm: DeepSeekClient | None = None
        self._full_schema = schema.render(self.tables)

    def reload_schema(self) -> None:
        """导入新表之后重建召回索引 —— 新表要能立刻被问到。"""
        with self._lock:
            self.tables = schema.introspect(self.sandbox)
            self.index = TableIndex(self.tables)
            self._full_schema = schema.render(self.tables)

    # ---------------- 基础信息 ----------------

    def overview(self) -> dict:
        dataset = load_dataset(config.DEFAULT_QUESTIONS)
        return {
            "tables": len(self.tables),
            "business_tables": [t.name for t in self.tables if t.name in _BUSINESS_TABLES],
            "schema_chars": len(self._full_schema),
            "columns": sum(len(t.columns) for t in self.tables),
            "questions": len(dataset.questions),
            "dataset_version": dataset.version,
            "attacks": {"blocked": len(BLOCKED), "allowed": len(ALLOWED)},
            "model": self._client_model(),
        }

    def _client_model(self) -> str:
        try:
            return DeepSeekClient(use_cache=False).model
        except RuntimeError:
            return "（未配置 API Key）"

    def _llm_client(self, api_key: str | None = None) -> DeepSeekClient:
        """取模型客户端。

        密钥可以按请求传进来（界面里填的那种），也可以走环境变量。
        按密钥缓存，避免每个请求都新建连接池；但绝不把密钥写进任何文件。
        """
        if api_key:
            cache = getattr(self, "_llm_by_key", None)
            if cache is None:
                cache = {}
                self._llm_by_key = cache
            client = cache.get(api_key)
            if client is None:
                client = DeepSeekClient(api_key=api_key)
                cache[api_key] = client
            return client
        if self._llm is None:
            self._llm = DeepSeekClient()
        return self._llm

    def tables_payload(self) -> list[dict]:
        out = []
        for t in self.tables:
            sample_rows: list[list] = []
            sample_columns: list[str] = []
            total = 0
            try:
                with self._lock:
                    counted = self.sandbox.run(f'SELECT COUNT(*) FROM "{t.name}"')
                    sampled = self.sandbox.run(f'SELECT * FROM "{t.name}" LIMIT 3')
                if counted.ok and counted.rows:
                    total = int(counted.rows[0][0])
                if sampled.ok:
                    sample_columns = sampled.columns
                    sample_rows = [[_cell(v) for v in row] for row in sampled.rows]
            except Exception:  # noqa: BLE001 - 字典页拿不到样例也不该整页崩
                pass
            out.append(
                {
                    "name": t.name,
                    "comment": t.comment,
                    "business": t.name in _BUSINESS_TABLES,
                    "row_count": total,
                    "columns": [
                        {"name": c.name, "type": c.data_type, "note": c.note} for c in t.columns
                    ],
                    "enums": t.enums,
                    "sample_columns": sample_columns,
                    "samples": sample_rows,
                }
            )
        return out

    # ---------------- 召回 ----------------

    def retrieve(self, question: str, top_k: int) -> tuple[list[dict], str]:
        ranked = self.index.rank(question)[:top_k]
        max_score = ranked[0][1] if ranked else 0.0
        payload = [
            {"name": name, "score": round(score, 4), "rel": round(score / max_score, 3) if max_score else 0.0}
            for name, score in ranked
        ]
        chosen = self.index.select(self.tables, question, top_k)
        return payload, schema.render(chosen)

    # ---------------- 数据导入 ----------------

    def import_preview(self, text: str, options: dict | None = None) -> dict:
        from . import imports

        try:
            result = imports.preview(text, options)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": str(exc)}
        result["suggested_name"] = imports.sanitize_table_name("dataset")
        return result

    def import_commit(
        self,
        text: str,
        name: str,
        columns: list[dict],
        options: dict | None = None,
    ) -> dict:
        from . import imports

        if not self.writable:
            return {"ok": False, "error": "服务以只读模式启动，无法导入（用 --writable 重启）"}

        try:
            table = imports.sanitize_table_name(name)
            ddl = imports.build_ddl(table, columns)
            header_names, rows = imports.build_rows(text, options or {}, columns)
        except (ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc)}

        with self._lock:
            created = self.sandbox.execute_trusted(ddl)
            if not created.ok:
                return {"ok": False, "error": f"建表失败：{created.error}"}
            placeholders = ", ".join("?" for _ in header_names)
            inserted = self.sandbox.execute_trusted(
                f'INSERT INTO "{table}" VALUES ({placeholders})', rows
            )
            if not inserted.ok:
                self.sandbox.execute_trusted(f'DROP TABLE "{table}"')
                return {"ok": False, "error": f"写入失败：{inserted.error}"}

        self.reload_schema()
        return {"ok": True, "table": table, "rows": len(rows), "columns": header_names}

    def drop_import(self, table: str) -> dict:
        """删掉一张导入的表。只允许 imp_ 前缀——不允许碰业务表。"""
        name = (table or "").strip()
        if not name.startswith("imp_"):
            return {"ok": False, "error": "只能删除 imp_ 开头的导入表"}
        if not self.writable:
            return {"ok": False, "error": "服务以只读模式启动，无法删除"}
        if not any(t.name == name for t in self.tables):
            return {"ok": False, "error": f"表不存在：{name}"}

        with self._lock:
            unsafe = any(ch in name for ch in '"\'`;')
            if unsafe:
                return {"ok": False, "error": "表名包含非法字符"}
            result = self.sandbox.execute_trusted(f'DROP TABLE IF EXISTS "{name}"')
        if not result.ok:
            return {"ok": False, "error": f"删除失败：{result.error}"}

        self.reload_schema()
        return {"ok": True, "table": name}

    # ---------------- 数据范围提示 ----------------
    def data_hints(self) -> dict:
        """从库里实时读「可以填什么」。

        每次请求都现查，不做缓存 —— 数据一变，提示就跟着变。
        这是给澄清环节用的：用户不知道能填什么，不如把库里的真实取值列给他。
        """

        def rows(sql: str) -> list:
            with self._lock:
                result = self.sandbox.run(sql)
            return result.rows if result.ok else []

        def flat(sql: str) -> list[str]:
            return [str(r[0]) for r in rows(sql) if r[0] is not None]

        span = rows("SELECT MIN(order_time), MAX(order_time) FROM orders")
        min_t = str(span[0][0])[:10] if span and span[0][0] else None
        max_t = str(span[0][1])[:10] if span and span[0][1] else None

        years = [int(r[0]) for r in rows(
            "SELECT DISTINCT EXTRACT(year FROM order_time)::INT FROM orders ORDER BY 1"
        ) if r[0] is not None]
        months = flat(
            "SELECT DISTINCT strftime(order_time, '%Y-%m') FROM orders ORDER BY 1"
        )

        return {
            "time": {
                "min": min_t,
                "max": max_t,
                "years": years,
                "months": months,
                "month_count": len(months),
            },
            "dimensions": {
                "渠道": flat("SELECT channel_name FROM channels ORDER BY channel_id"),
                "渠道类型": flat("SELECT DISTINCT channel_type FROM channels ORDER BY 1"),
                "商品品类": flat("SELECT DISTINCT category FROM products ORDER BY 1"),
                "二级品类": flat("SELECT DISTINCT sub_category FROM products ORDER BY 1"),
                "会员等级": flat(
                    "SELECT DISTINCT member_level FROM users WHERE member_level IS NOT NULL ORDER BY 1"
                ),
                "省份": flat(
                    "SELECT DISTINCT province FROM users WHERE province IS NOT NULL ORDER BY 1"
                ),
                # 城市是比省份更细的粒度，库里确实有值，用户按城市问也必须能用
                "城市": flat(
                    "SELECT DISTINCT city FROM users WHERE city IS NOT NULL ORDER BY 1"
                ),
                "支付方式": flat("SELECT DISTINCT pay_method FROM payments ORDER BY 1"),
                "支付状态": flat("SELECT DISTINCT pay_status FROM payments ORDER BY 1"),
                "订单状态": flat("SELECT DISTINCT status FROM orders ORDER BY 1"),
                "退款原因": flat("SELECT DISTINCT reason FROM refunds ORDER BY 1"),
                "退款状态": flat("SELECT DISTINCT status FROM refunds ORDER BY 1"),
            },
            "metrics": ["销售额", "有效订单数", "客单价", "退款金额", "退款率", "复购率", "毛利率", "销量件数"],
            "rules": _business_rule_lines(),
        }

    # ---------------- 问数 ----------------

    def _policy_default(self) -> bool:
        """环境变量 ASKDATA_ROW_POLICY 打开时，所有查询都套行级权限。"""
        if not hasattr(self, "_policy_flag"):
            from .auth import row_policy_enabled

            self._policy_flag = row_policy_enabled()
        return self._policy_flag

    def _total_revenue(self) -> float | None:
        """全库有效订单的实付总额，给结果校验做量级参考。

        只查一次、缓存住：这是固定的口径基准，数据不变就不用重算。
        """
        if getattr(self, "_revenue_cache", None) is not None:
            return self._revenue_cache
        with self._lock:
            result = self.sandbox.run(
                "SELECT ROUND(SUM(pay_amount), 2) FROM orders "
                "WHERE status IN ('已支付', '已发货', '已完成')"
            )
        if not result.ok or not result.rows:
            return None
        value = result.rows[0][0]
        self._revenue_cache = float(value) if value is not None else None
        return self._revenue_cache

    def ask(
        self,
        question: str,
        top_k: int = 8,
        clarification: str | None = None,
        answer: str | None = None,
        history: list[dict] | None = None,
        session_id: str | None = None,
        on_stage: Callable[[dict], None] | None = None,
        api_key: str | None = None,
    ) -> dict:
        stages: list[Stage] = []
        started = time.perf_counter()

        def emit(stage: Stage) -> None:
            """记一个阶段；有流式回调就顺手推给前端，让进度实时可见。"""
            stages.append(stage)
            if on_stage is not None:
                try:
                    on_stage(stage.as_dict())
                except Exception:  # noqa: BLE001 - 推送失败不该影响问数本身
                    pass

        # 会话历史以服务端为准：客户端只要带一个 session_id 就够了。
        # 客户端仍可以传 history（兼容/replay 场景），但服务端有记录时优先用服务端的——
        # 否则客户端可以伪造上文，绕过"可审计"。
        server_history = self.sessions.history(session_id)
        if server_history:
            history = server_history

        # 多轮：先把追问改写成独立问题，再走完全相同的单轮管线
        original_question = question
        standalone = None
        if history:
            from .dialog import (
                REWRITE_SYSTEM,
                REWRITE_TEMPLATE,
                clean_rewrite,
                looks_like_followup,
                render_history,
            )

            if looks_like_followup(question):
                t0 = time.perf_counter()
                try:
                    client = self._llm_client(api_key)
                    response = client.chat(
                        REWRITE_SYSTEM,
                        REWRITE_TEMPLATE.format(
                            history=render_history(history), question=question
                        ),
                        temperature=0.0,
                        max_tokens=160,
                    )
                    standalone = clean_rewrite(response.content)
                except RuntimeError:
                    standalone = None
                emit(
                    Stage(
                        "改写追问",
                        (time.perf_counter() - t0) * 1000,
                        standalone or "改写失败，按原句处理",
                    )
                )
                if standalone and standalone != question:
                    question = standalone

        t0 = time.perf_counter()
        tables_used, schema_text = self.retrieve(question, top_k)
        emit(Stage("召回表", (time.perf_counter() - t0) * 1000, f"{len(tables_used)} 张"))

        follow_up = bool(clarification and answer)
        if follow_up:
            prompt = CLOSED_TEMPLATE.format(
                schema=schema_text,
                rules=BUSINESS_RULES,
                question=question,
                clarification=clarification,
                answer=answer,
            )
        else:
            prompt = USER_TEMPLATE.format(schema=schema_text, rules=BUSINESS_RULES, question=question)

        t0 = time.perf_counter()
        try:
            client = self._llm_client(api_key)
            response = client.chat(SYSTEM_PROMPT, prompt, temperature=0.0, max_tokens=1024)
        except RuntimeError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "stages": [s.as_dict() for s in stages],
                "tables": tables_used,
                "original_question": original_question,
                "rewritten": standalone,
            }
        emit(
            Stage("模型生成", (time.perf_counter() - t0) * 1000,
                  f"{response.prompt_tokens}+{response.completion_tokens} tokens")
        )

        decision = parse_decision(response.content)
        if not follow_up and decision.need_clarification:
            emit(Stage("判定", 0.0, "信息不足，先反问"))
            return {
                "ok": True,
                "need_clarification": True,
                "clarification": decision.question,
                "hints": self.data_hints(),
                "stages": [s.as_dict() for s in stages],
                "tables": tables_used,
                "usage": {"prompt": response.prompt_tokens, "completion": response.completion_tokens},
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
            }

        sql = decision.sql if decision.need_clarification is False and decision.sql else None
        if follow_up:
            sql = extract_sql(response.content)
        if not sql:
            return {
                "ok": False,
                "error": "模型没有产出 SQL",
                "raw": response.content[:400],
                "stages": [s.as_dict() for s in stages],
                "tables": tables_used,
                "original_question": original_question,
                "rewritten": standalone,
            }

        t0 = time.perf_counter()
        # 行级权限是构造时传给 SqlGuard 的，不是 check() 的参数
        force_policy = self._policy_default()
        row_policy = (
            RowPolicy({"orders": "channel_id IN (1, 2)", "users": "province IS NOT NULL"})
            if force_policy
            else None
        )
        guard = SqlGuard(max_rows=1000, policy=row_policy)
        try:
            rewritten = guard.check(sql)
            blocked, reason = False, ""
        except SqlGuardError as exc:
            rewritten, blocked, reason = sql, True, str(exc)
        emit(
            Stage("安全闸", (time.perf_counter() - t0) * 1000, "拦截" if blocked else "通过")
        )

        if blocked:
            return {
                "ok": False,
                "error": f"被安全闸拦截：{reason}",
                "sql": sql,
                "stages": [s.as_dict() for s in stages],
                "tables": tables_used,
                "original_question": original_question,
                "rewritten": standalone,
            }

        t0 = time.perf_counter()
        with self._lock:
            result = self.sandbox.run(rewritten)
        emit(
            Stage("执行", (time.perf_counter() - t0) * 1000,
                  "失败" if not result.ok else f"{len(result.rows)} 行")
        )

        # 校验 Agent：跑成功了不代表算对了，把可疑的数字标出来
        t0 = time.perf_counter()
        issues: list[dict] = []
        if result.ok:
            from .verify import check_result

            rows_for_check = [[_cell(v) for v in row] for row in result.rows[:200]]
            found = check_result(
                original_question,
                result.columns,
                rows_for_check,
                total_revenue=self._total_revenue(),
            )
            issues = [i.as_dict() for i in found]
            emit(
                Stage(
                    "结果校验",
                    (time.perf_counter() - t0) * 1000,
                    "通过" if not issues else f"{len(issues)} 处可疑",
                )
            )

        # 只有真正算出了东西才记进会话：反问、拦截、执行失败都不该污染上下文
        if result.ok:
            self.sessions.append(session_id, original_question, sql, standalone)

        return {
            "ok": result.ok,
            "need_clarification": False,
            "session_id": session_id,
            "session_turns": len(self.sessions.history(session_id)),
            "sql": sql,
            "rewritten_sql": rewritten,
            "columns": result.columns,
            "rows": [[_cell(v) for v in row] for row in result.rows[:200]],
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "error": result.error,
            "issues": issues,
            "stages": [s.as_dict() for s in stages],
            "tables": tables_used,
            "original_question": original_question,
            "rewritten": standalone,
            "usage": {"prompt": response.prompt_tokens, "completion": response.completion_tokens},
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    # ---------------- 安全闸 ----------------

    def check_sql(self, sql: str, execute: bool = True, policy: bool = False) -> dict:
        t0 = time.perf_counter()
        row_policy = (
            RowPolicy({"orders": "channel_id IN (1, 2)", "users": "province IS NOT NULL"})
            if policy
            else None
        )
        guard = SqlGuard(max_rows=1000, policy=row_policy)
        try:
            rewritten = guard.check(sql)
            verdict = "allowed"
            reason = ""
        except SqlGuardError as exc:
            rewritten = ""
            verdict = "blocked"
            reason = str(exc)
        guard_ms = round((time.perf_counter() - t0) * 1000, 2)

        payload = {
            "verdict": verdict,
            "reason": reason,
            "rewritten": rewritten,
            "guard_ms": guard_ms,
            "policy": bool(policy),
        }
        if verdict == "allowed" and execute:
            with self._lock:
                result = self.sandbox.run(rewritten)
            payload["executed"] = {
                "ok": result.ok,
                "columns": result.columns,
                "rows": [[_cell(v) for v in row] for row in result.rows[:100]],
                "row_count": len(result.rows),
                "error": result.error,
                "ms": round(result.elapsed_ms, 1),
            }
        return payload

    def attack_cases(self) -> list[dict]:
        return [
            {"name": c.name, "sql": c.sql, "category": c.category, "expect": "blocked"} for c in BLOCKED
        ] + [
            {"name": c.name, "sql": c.sql, "category": c.category, "expect": "allowed"} for c in ALLOWED
        ]

    # ---------------- 报告 ----------------

    def reports(self) -> list[dict]:
        out = []
        for path in sorted(config.REPORT_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            kind = path.stem.split("_")[0]
            out.append(
                {
                    "name": path.stem,
                    "kind": kind,
                    "mtime": path.stat().st_mtime,
                    "dataset_version": data.get("dataset_version"),
                    "ex": data.get("execution_accuracy") or data.get("mean_ex"),
                    "strategy": data.get("strategy"),
                    "n_runs": data.get("n_runs") or data.get("repeats"),
                    "baseline_ex": data.get("baseline_ex"),
                    "closed_ex": data.get("closed_ex"),
                    # 多轮报告才有 control；clarify 报告里的 control 是列表，要判类型
                    "control_ex": (
                        data["control"].get("ex")
                        if isinstance(data.get("control"), dict)
                        else None
                    ),
                }
            )
        return out

    def report(self, name: str) -> dict:
        path = config.REPORT_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(name)
        return json.loads(path.read_text(encoding="utf-8"))

    def questions(self) -> list[dict]:
        dataset = load_dataset(config.DEFAULT_QUESTIONS)
        return [
            {
                "id": q.id,
                "difficulty": q.difficulty,
                "cohort": q.cohort,
                "tags": list(q.tags),
                "question": q.question,
                "note": q.note,
                # 前端做失败归因要用：判断"选错表"还是"口径算错"
                "gold_sql": q.gold_sql,
                "gold_tables": _tables_of(q.gold_sql),
            }
            for q in dataset.questions
        ]


_BUSINESS_TABLES = {"channels", "users", "products", "orders", "order_items", "payments", "refunds"}


def _business_rule_lines() -> list[str]:
    """把语义层的编号规则拆成一条条，供澄清卡片展示。

    规则只有一份（`schema.BUSINESS_RULES`），这里只是解析，避免两处各写一遍、慢慢走偏。
    """
    import re

    rules: list[str] = []
    for raw in BUSINESS_RULES.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^\d+\.\s*(.+)$", line)
        if match:
            rules.append(match.group(1))
        elif rules:
            rules[-1] += line  # 后半行接上一条，别断句
    return rules


def _tables_of(sql: str) -> list[str]:
    """从 SQL 里抠出真正用到的表名（排除 CTE 名字）。

    归因要用它判断"选错表"还是"口径算错"，所以必须准确：
    `WITH m AS (...) SELECT * FROM m` 里的 m 是 CTE，不是表。
    """
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, dialect="duckdb")
        cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        return sorted(
            {t.name for t in tree.find_all(exp.Table) if t.name and t.name not in cte_names}
        )
    except Exception:  # noqa: BLE001 - 解析失败就退化成正则
        import re

        names = set()
        for match in re.finditer(r"\b(?:FROM|JOIN)\s+[\"']?([A-Za-z_][\w]*)", sql or "", re.IGNORECASE):
            name = match.group(1)
            if name.lower() not in {"select", "where", "group", "order", "as"}:
                names.add(name)
        return sorted(names)


def _cell(value: object) -> object:
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
