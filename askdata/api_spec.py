"""接口清单：**唯一的事实来源**。

路由、OpenAPI 文档、接口测试都从这张表读，避免"代码改了文档没改"。
之前是 if 链 + 散落的中文注释，加了接口就得同时改三处，必然漂移。

写法刻意朴素：一个 dataclass 列表 + 一个 build_openapi()。
不引 pydantic，不引 fastapi——**零依赖是设计约束**，
OpenAPI 3.1 的结构用 dict 手拼就够，而且生成出来的文件可以被
任何标准工具（Swagger UI、Postman、代码生成器）消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------- 数据结构

_SCHEMA_COUNTER = {"n": 0}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


@dataclass(frozen=True)
class Endpoint:
    """一个 HTTP 接口。"""

    method: str
    path: str
    summary: str
    tag: str
    # 请求体 schema 名（None 表示无请求体）
    request: str | None = None
    # 成功响应 schema 名
    response: str = "GenericOk"
    # 非 200 的成功码（比如 SSE 流）
    status: int = 200
    auth: bool = False
    notes: str = ""


# ---------------------------------------------------------------- 接口清单

ENDPOINTS: tuple[Endpoint, ...] = (
    # ---- 元信息 ----
    Endpoint("GET", "/health", "存活探针", "运维", response="HealthResponse"),
    Endpoint("GET", "/ready", "就绪探针（含数据库连通性）", "运维", response="ReadyResponse"),
    Endpoint("GET", "/metrics", "Prometheus 指标", "运维", response="MetricsText"),
    Endpoint("GET", "/api/openapi.json", "本文件的机器可读版本", "运维", response="OpenApiDocument"),
    # ---- 问数 ----
    Endpoint(
        "POST",
        "/api/ask",
        "提问（一次性返回）",
        "问数",
        request="AskRequest",
        response="AskResponse",
        auth=True,
        notes="会调用一次大模型；缺 API Key 时返回 400 且 need_api_key=true。",
    ),
    Endpoint(
        "POST",
        "/api/ask/stream",
        "提问（SSE 流式，逐阶段推送）",
        "问数",
        request="AskRequest",
        response="AskResponse",
        status=200,
        auth=True,
        notes="返回 text/event-stream：open → stage* → result → done。",
    ),
    # ---- 数据 ----
    Endpoint("GET", "/api/overview", "数据集概览", "数据", response="OverviewResponse"),
    Endpoint("GET", "/api/tables", "表清单（含样例行）", "数据", response="TableList"),
    Endpoint("GET", "/api/hints", "数据范围提示（维度/指标/时间）", "数据", response="HintsResponse"),
    # ---- 安全 ----
    Endpoint(
        "POST",
        "/api/check",
        "SQL 安全闸校验（可执行）",
        "安全",
        request="CheckRequest",
        response="CheckResponse",
        notes="四道闸：AST 白名单、强制 LIMIT、超时熔断、行级权限。",
    ),
    Endpoint("GET", "/api/attacks", "注入用例集", "安全", response="AttackList"),
    # ---- 评测 ----
    Endpoint("GET", "/api/questions", "评测题集", "评测", response="QuestionList"),
    Endpoint("GET", "/api/reports", "报告列表", "评测", response="ReportList"),
    Endpoint("GET", "/api/report?name={name}", "单份报告详情", "评测", response="ReportDetail"),
    # ---- 导入 ----
    Endpoint(
        "POST",
        "/api/import/preview",
        "解析待导入数据（只预览，不落库）",
        "导入",
        request="ImportPreviewRequest",
        response="ImportPreviewResponse",
    ),
    Endpoint(
        "POST",
        "/api/import/commit",
        "建表并写入",
        "导入",
        request="ImportCommitRequest",
        response="ImportCommitResponse",
    ),
    Endpoint("POST", "/api/import/drop", "删除导入的表", "导入", request="DropRequest", response="DropResponse"),
    # ---- 会话 ----
    Endpoint("POST", "/api/session/clear", "清空某个会话的上下文", "会话", request="SessionRequest", response="SessionClearResponse"),
    Endpoint("GET", "/api/session/stats", "会话存储统计", "会话", response="SessionStatsResponse"),
)


# ---------------------------------------------------------------- schema

SCHEMAS: dict[str, dict[str, Any]] = {
    "GenericOk": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
    "Error": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "const": False},
            "error": {"type": "string", "description": "给人看的错误说明"},
            "code": {
                "type": "string",
                "description": "机器可读的错误码",
                "enum": [
                    "bad_request",
                    "unauthorized",
                    "forbidden",
                    "not_found",
                    "rate_limited",
                    "internal_error",
                ],
            },
        },
        "required": ["ok", "error"],
    },
    "AskRequest": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1, "description": "中文自然语言问题"},
            "top_k": {"type": "integer", "minimum": 3, "maximum": 30, "default": 8},
            "session_id": {"type": "string", "description": "会话 ID，带上则启用多轮上下文"},
            "history": {
                "type": "array",
                "description": "兼容字段：服务端有会话记录时以服务端为准",
                "items": {"$ref": "#/components/schemas/Turn"},
            },
            "clarification": {"type": "string", "description": "用户对反问的回答"},
            "answer": {"type": "string", "description": "用户对反问的回答（别名）"},
        },
        "required": ["question"],
    },
    "Turn": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "sql": {"type": "string"},
            "rewritten": {"type": ["string", "null"]},
        },
    },
    "Stage": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "ms": {"type": "number"},
            "detail": {"type": "string"},
        },
    },
    "Issue": {
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["warn", "info"]},
            "code": {"type": "string"},
            "message": {"type": "string"},
        },
    },
    "AskResponse": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "need_clarification": {"type": "boolean"},
            "session_id": {"type": ["string", "null"]},
            "session_turns": {"type": "integer"},
            "sql": {"type": "string"},
            "rewritten_sql": {"type": "string", "description": "过完安全闸、真正执行的 SQL"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array"}},
            "row_count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "issues": {"type": "array", "items": _ref("Issue")},
            "stages": {"type": "array", "items": _ref("Stage")},
            "tables": {"type": "array", "items": {"type": "string"}},
            "original_question": {"type": "string"},
            "rewritten": {"type": ["string", "null"], "description": "追问被改写成独立问题后的文本"},
            "usage": {
                "type": "object",
                "properties": {"prompt": {"type": "integer"}, "completion": {"type": "integer"}},
            },
            "total_ms": {"type": "number"},
            "error": {"type": ["string", "null"]},
        },
        "required": ["ok"],
    },
    "CheckRequest": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "minLength": 1},
            "policy": {"type": "boolean", "default": False, "description": "是否套行级权限"},
        },
        "required": ["sql"],
    },
    "CheckResponse": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["allowed", "blocked"]},
            "reason": {"type": "string"},
            "rewritten": {"type": "string"},
            "guard_ms": {"type": "number"},
            "policy": {"type": "boolean"},
            "executed": {"type": "object", "description": "执行结果（columns/rows/error）"},
        },
        "required": ["verdict"],
    },
    "OverviewResponse": {
        "type": "object",
        "properties": {
            "tables": {"type": "integer"},
            "business_tables": {"type": "array", "items": {"type": "string"}},
            "schema_chars": {"type": "integer"},
            "columns": {"type": "integer"},
            "questions": {"type": "integer"},
            "dataset_version": {"type": "string"},
            "attacks": {"type": "object"},
            "model": {"type": "string"},
        },
    },
    "TableList": {"type": "array", "items": {"type": "object"}},
    "QuestionList": {"type": "array", "items": {"type": "object"}},
    "AttackList": {"type": "array", "items": {"type": "object"}},
    "ReportList": {"type": "array", "items": {"type": "object"}},
    "ReportDetail": {"type": "object"},
    "HintsResponse": {
        "type": "object",
        "properties": {
            "time": {"type": "object"},
            "dimensions": {"type": "object"},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "rules": {"type": "array", "items": {"type": "string"}},
        },
    },
    "ImportPreviewRequest": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "maxLength": 4_000_000},
            "options": {"type": "object"},
        },
        "required": ["text"],
    },
    "ImportPreviewResponse": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "columns": {"type": "array", "items": {"type": "object"}},
            "stats": {"type": "object"},
            "suggested_name": {"type": "string"},
            "error": {"type": "string"},
        },
    },
    "ImportCommitRequest": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "name": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "object"}},
            "options": {"type": "object"},
        },
        "required": ["text", "columns"],
    },
    "ImportCommitResponse": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "table": {"type": "string"},
            "rows": {"type": "integer"},
            "error": {"type": "string"},
        },
    },
    "DropRequest": {
        "type": "object",
        "properties": {"table": {"type": "string"}},
        "required": ["table"],
    },
    "DropResponse": {"type": "object", "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"}}},
    "SessionRequest": {"type": "object", "properties": {"session_id": {"type": "string"}}},
    "SessionClearResponse": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "cleared": {"type": "boolean"},
            "stats": {"type": "object"},
        },
    },
    "SessionStatsResponse": {
        "type": "object",
        "properties": {
            "sessions": {"type": "integer"},
            "turns": {"type": "integer"},
            "max_turns_per_session": {"type": "integer"},
            "ttl_seconds": {"type": "integer"},
        },
    },
    "HealthResponse": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "status": {"type": "string"}},
    },
    "ReadyResponse": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "checks": {"type": "object"},
        },
    },
    "MetricsText": {"type": "string", "description": "Prometheus 文本格式"},
    "OpenApiDocument": {"type": "object", "description": "OpenAPI 3.1 文档"},
}


def build_openapi(*, server_url: str = "/") -> dict[str, Any]:
    """生成 OpenAPI 3.1 文档。"""
    paths: dict[str, dict[str, Any]] = {}

    for ep in ENDPOINTS:
        # 查询串在文档里要拆成 parameters
        raw_path = ep.path
        query_part = ""
        if "?" in raw_path:
            raw_path, query_part = raw_path.split("?", 1)

        operation: dict[str, Any] = {
            "summary": ep.summary,
            "tags": [ep.tag],
            "responses": {
                str(ep.status): {
                    "description": "成功",
                    "content": _content_for(ep.response),
                },
                "400": {"description": "请求有问题", "content": _content_for("Error")},
                "401": {"description": "缺少或错误的访问令牌"},
                "429": {"description": "触发限流"},
            },
        }
        if ep.notes:
            operation["description"] = ep.notes
        if ep.request:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": _ref(ep.request)}},
            }
        if ep.auth:
            operation["security"] = [{"bearerAuth": []}]
        if query_part:
            name = query_part.split("=", 1)[0]
            operation["parameters"] = [
                {"name": name, "in": "query", "required": True, "schema": {"type": "string"}}
            ]

        paths.setdefault(raw_path, {})[ep.method.lower()] = operation

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "askdata API",
            "version": "1.0.0",
            "description": (
                "中文问数 Agent 的 HTTP 接口。所有 /api/* 在设置 ASKDATA_TOKEN 后需要 "
                "Authorization: Bearer <token>；/health、/ready、/metrics 不限流。"
            ),
            "license": {"name": "MIT"},
        },
        "servers": [{"url": server_url, "description": "本地服务"}],
        "tags": [
            {"name": "问数", "description": "自然语言 → SQL → 结果"},
            {"name": "数据", "description": "数据集概览与字典"},
            {"name": "安全", "description": "SQL 安全闸"},
            {"name": "评测", "description": "评测题与报告"},
            {"name": "导入", "description": "CSV/JSON 接入"},
            {"name": "会话", "description": "多轮上下文"},
            {"name": "运维", "description": "探针、指标、文档"},
        ],
        "paths": paths,
        "components": {
            "schemas": SCHEMAS,
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"},
                "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            },
        },
    }


def _content_for(schema_name: str) -> dict[str, Any]:
    if schema_name == "MetricsText":
        return {"text/plain": {"schema": _ref(schema_name)}}
    return {"application/json": {"schema": _ref(schema_name)}}
