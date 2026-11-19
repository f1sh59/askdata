"""端到端验收脚本：把服务当成黑盒，逐项检查功能是否真的可用。

用法（服务需要先起来）：

    python -m askdata.server --port 8777 &
    python scripts/acceptance.py --base http://127.0.0.1:8777

设计原则：
* 只通过 HTTP 调用，不 import 项目代码——这样验的是"用户能用到的东西"；
* 每项检查独立捕异常，一项失败不影响其余项；
* 写操作（导入）放在最后，并且自己清理。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


RESULTS: list[Check] = []


def record(name: str, ok: bool, detail: str = "", skip: bool = False) -> None:
    RESULTS.append(Check(name, SKIP if skip else (PASS if ok else FAIL), detail))
    mark = "[SKIP]" if skip else ("[ ok ]" if ok else "[FAIL]")
    print(f"{mark} {name}" + (f"  — {detail}" if detail and not ok else ""))


def get(base: str, path: str, timeout: int = 60):
    req = urllib.request.Request(base + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        ctype = resp.headers.get("Content-Type", "")
        return resp.status, raw, ctype


def post(base: str, path: str, payload: dict, timeout: int = 300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def expect_error(fn, label: str) -> None:
    """期望请求被拒绝（4xx/5xx）。"""
    try:
        fn()
        record(label, False, "本应失败但成功了")
    except urllib.error.HTTPError as exc:
        record(label, 400 <= exc.code < 600, f"HTTP {exc.code}")
    except Exception as exc:
        record(label, True, type(exc).__name__)


# ---------------------------------------------------------------- 静态资源


def check_static(base: str) -> None:
    for path, needle in [("/", "page-ask"), ("/styles.css", "--accent"), ("/app.js", "PAGES")]:
        try:
            status, body, _ = get(base, path)
            record(
                f"静态资源 {path}",
                status == 200 and needle in body,
                f"status={status} 缺少 {needle}",
            )
        except Exception as exc:
            record(f"静态资源 {path}", False, f"{type(exc).__name__}: {exc}")

    expect_error(lambda: get(base, "/no-such-file.js"), "不存在的静态文件返回错误")


# ---------------------------------------------------------------- 只读接口


def check_overview(base: str) -> None:
    try:
        _, raw, _ = get(base, "/api/overview")
        data = json.loads(raw)
        need = {"tables", "questions", "model"}
        record("/api/overview 字段完整", need <= set(data), f"缺 {need - set(data)}")
        record("/api/overview 表数合理", 60 <= data["tables"] <= 200, f"tables={data['tables']}")
    except Exception as exc:
        record("/api/overview", False, f"{type(exc).__name__}: {exc}")


def check_tables(base: str) -> None:
    try:
        _, raw, _ = get(base, "/api/tables", timeout=120)
        tables = json.loads(raw)
        record("/api/tables 返回列表", isinstance(tables, list) and len(tables) > 0)
        first = tables[0]
        record(
            "/api/tables 字段完整",
            {"name", "columns", "row_count"} <= set(first),
            str(set(first)),
        )
        with_samples = sum(1 for t in tables if t.get("samples"))
        record("/api/tables 有样例数据", with_samples > len(tables) * 0.8, f"{with_samples}/{len(tables)}")
    except Exception as exc:
        record("/api/tables", False, f"{type(exc).__name__}: {exc}")


def check_questions(base: str) -> None:
    try:
        _, raw, _ = get(base, "/api/questions")
        qs = json.loads(raw)
        record("/api/questions 50 题", len(qs) == 50, f"实际 {len(qs)}")
        record(
            "题目含 gold_sql / gold_tables",
            all("gold_sql" in q and "gold_tables" in q for q in qs),
        )
        empty = [q["id"] for q in qs if not q["gold_tables"]]
        record("每题都能解析出表名", not empty, f"空表名: {empty}")
    except Exception as exc:
        record("/api/questions", False, f"{type(exc).__name__}: {exc}")


def check_hints(base: str) -> None:
    try:
        _, raw, _ = get(base, "/api/hints", timeout=120)
        h = json.loads(raw)
        record("/api/hints 有时间范围", bool(h.get("time", {}).get("min")))
        record("/api/hints 有年份列表", len(h.get("time", {}).get("years", [])) >= 2)
        record("/api/hints 有维度值", bool(h.get("dimensions", {}).get("渠道")))
        record("/api/hints 口径 10 条", len(h.get("rules", [])) >= 10, f"{len(h.get('rules', []))} 条")
        record(
            "/api/hints 含毛利率口径",
            any("毛利率" in r for r in h.get("rules", [])),
        )
    except Exception as exc:
        record("/api/hints", False, f"{type(exc).__name__}: {exc}")


def check_attacks(base: str) -> None:
    try:
        _, raw, _ = get(base, "/api/attacks")
        cases = json.loads(raw)
        blocked = sum(1 for c in cases if c.get("expect") == "blocked")
        allowed = sum(1 for c in cases if c.get("expect") == "allowed")
        record("/api/attacks 60 条", len(cases) == 60, f"实际 {len(cases)}")
        record("/api/attacks 50 拦 10 放", (blocked, allowed) == (50, 10), f"{blocked}/{allowed}")
    except Exception as exc:
        record("/api/attacks", False, f"{type(exc).__name__}: {exc}")


def check_reports(base: str) -> None:
    try:
        _, raw, _ = get(base, "/api/reports")
        reports = json.loads(raw)
        record("/api/reports 有报告", len(reports) > 10, f"{len(reports)} 份")
        kinds = {r["kind"] for r in reports}
        record(
            "报告类型齐全",
            {"eval", "repeat", "clarify", "closed", "multiturn"} <= kinds,
            f"缺 {({'eval','repeat','clarify','closed','multiturn'} - kinds)}",
        )

        # 每种类型都真的能读出来
        broken = []
        for kind in ["eval", "repeat", "multiturn"]:
            item = next((r for r in reports if r["kind"] == kind), None)
            if not item:
                broken.append(f"{kind}: 无样本")
                continue
            try:
                _, body, _ = get(base, f"/api/report?name={item['name']}", timeout=60)
                json.loads(body)
            except Exception as exc:
                broken.append(f"{kind}: {type(exc).__name__}")
        record("/api/report 各类型可读", not broken, "; ".join(broken))

        expect_error(lambda: get(base, "/api/report?name=nope_xxx"), "不存在的报告返回错误")
    except Exception as exc:
        record("/api/reports", False, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 安全闸


def check_guard(base: str) -> None:
    cases = [
        ("SELECT COUNT(*) FROM orders", "allowed"),
        ("DROP TABLE orders", "blocked"),
        ("SELECT * FROM read_csv('C:/secret.csv')", "blocked"),
        ("SELECT 1; DELETE FROM orders", "blocked"),
        ("ATTACH 'C:/x.db' AS x", "blocked"),
        ("COPY (SELECT 1) TO 'C:/out.csv'", "blocked"),
    ]
    try:
        for sql, expect in cases:
            _, data = post(base, "/api/check", {"sql": sql})
            ok = data.get("verdict") == expect
            record(f"安全闸 · {sql[:34]}", ok, f"期望 {expect}，实际 {data.get('verdict')}")

        _, data = post(base, "/api/check", {"sql": "SELECT COUNT(*) FROM orders"})
        record("安全闸 · 自动补 LIMIT", "LIMIT" in (data.get("rewritten") or ""), data.get("rewritten", ""))

        _, data = post(base, "/api/check", {"sql": "SELECT COUNT(*) FROM orders", "policy": True})
        record("安全闸 · 行级权限改写", "channel_id IN" in (data.get("rewritten") or ""), data.get("rewritten", ""))

        expect_error(lambda: post(base, "/api/check", {"sql": ""}), "空 SQL 被拒绝")
    except Exception as exc:
        record("安全闸", False, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 问数


def check_ask(base: str) -> dict:
    result = {}
    try:
        _, data = post(base, "/api/ask", {"question": "2025 年各渠道的销售额分别是多少？"}, timeout=300)
        result["base"] = data
        record("/api/ask 正常问答", data.get("ok") and data.get("row_count", 0) > 0, str(data.get("error")))
        record(
            "/api/ask 返回阶段耗时",
            len(data.get("stages") or []) >= 4,
            f"{len(data.get('stages') or [])} 个阶段",
        )
        record("/api/ask 返回 SQL", bool(data.get("sql")))
        record("/api/ask 带 tokens", bool((data.get("usage") or {}).get("prompt")))
    except Exception as exc:
        record("/api/ask 正常问答", False, f"{type(exc).__name__}: {exc}")
    return result


def check_ask_followup(base: str, base_sql: str) -> None:
    try:
        _, data = post(
            base,
            "/api/ask",
            {
                "question": "那只看天猫呢？",
                "history": [{"question": "2025 年各渠道的销售额分别是多少？", "sql": base_sql}],
            },
            timeout=300,
        )
        record("/api/ask 多轮改写有产出", bool(data.get("rewritten")), str(data.get("rewritten")))
        record(
            "/api/ask 多轮改写不是 SQL",
            not (data.get("rewritten") or "").strip().lower().startswith(("select", "with")),
            str(data.get("rewritten")),
        )
        record("/api/ask 多轮执行成功", data.get("ok") and data.get("row_count", 0) > 0, str(data.get("error")))
    except Exception as exc:
        record("/api/ask 多轮", False, f"{type(exc).__name__}: {exc}")

    expect_error(lambda: post(base, "/api/ask", {"question": "  "}), "空白问题被拒绝")


# ---------------------------------------------------------------- 导入


def check_session(base: str) -> None:
    try:
        # stats 是 GET 接口
        _, raw_stats, _ = get(base, "/api/session/stats")
        stats = json.loads(raw_stats)
        record(
            "会话 · 统计接口可用",
            "sessions" in stats and "max_turns_per_session" in stats,
            str(stats)[:80],
        )

        sid = "acc-test-session"
        _, out = post(base, "/api/session/clear", {"session_id": sid})
        record("会话 · 清空接口可用", out.get("ok") is True, str(out)[:80])
        record("会话 · 清空不存在的会话不报错", out.get("cleared") is False)

        _, empty = post(base, "/api/session/clear", {"session_id": ""})
        record("会话 · 空 session_id 被容忍", empty.get("ok") is True)
    except Exception as exc:
        record("会话", False, f"{type(exc).__name__}: {exc}")


def check_observability(base: str) -> None:
    """探针、指标、请求 ID、错误码——这些是"能被运维接住"的前提。"""
    try:
        status, raw, _ = get(base, "/health")
        body_text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        record("探针 · /health 存活", status == 200 and "alive" in body_text, f"{status}")

        status, raw, _ = get(base, "/ready")
        ready = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        record(
            "探针 · /ready 检查数据库",
            status == 200 and ready.get("checks", {}).get("database") == "ok",
            str(ready.get("checks", {}))[:80],
        )

        status, raw, _headers = get(base, "/metrics")
        text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        record(
            "指标 · Prometheus 文本格式",
            status == 200 and "askdata_up 1" in text and "askdata_http_requests_total" in text,
            f"{status} {len(text)}B",
        )

        # get() 的第三个返回值是 Content-Type，要拿响应头得单独发一次请求
        req = urllib.request.Request(base + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            rid = resp.headers.get("X-Request-Id")
        record("中间件 · 每个响应带 X-Request-Id", bool(rid) and len(rid) == 12, str(rid))

        try:
            status, raw, _ = get(base, "/api/does-not-exist")
            payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 404 会抛，需要从异常里取响应体
            status = exc.code
            payload = json.loads(exc.read().decode("utf-8"))
        record(
            "错误码 · 未知接口返回 code",
            status == 404 and payload.get("code") == "not_found",
            f"{status} {payload.get('code')}",
        )

        # OpenAPI 文档：能被标准工具消费
        status, raw, _ = get(base, "/api/openapi.json")
        spec = json.loads(raw)
        record(
            "文档 · OpenAPI 可读且含路径",
            status == 200
            and str(spec.get("openapi", "")).startswith("3.")
            and len(spec.get("paths", {})) > 5,
            f"paths={len(spec.get('paths', {}))}",
        )

        status, raw, _ = get(base, "/docs")
        html = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        record("文档 · /docs 页面可打开", status == 200 and "askdata API" in html, f"{status}")

        try:
            status, raw = post(base, "/api/ask", {"question": "  "})
            payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read().decode("utf-8"))
        record(
            "错误码 · 参数错误返回 code",
            status == 400 and payload.get("code") == "bad_request",
            f"{status} {payload.get('code')}",
        )
    except Exception as exc:
        record("可观测性", False, f"{type(exc).__name__}: {exc}")


def check_import(base: str) -> None:
    csv = "城市,年份,销售额,日期\n杭州,2025,\"1,234.50\",2025/01/05\n上海,2025,￥2000,2025-01-06\n杭州,2025,\"1,234.50\",2025/01/05\n"
    try:
        _, preview = post(base, "/api/import/preview", {"text": csv})
        record("导入 · 预览成功", preview.get("ok"), str(preview.get("error")))
        cols = {c["name"]: c["type"] for c in preview.get("columns", [])}
        record("导入 · 类型推断", cols.get("销售额") == "DOUBLE" and cols.get("日期") == "DATE", str(cols))
        record("导入 · 去重", preview.get("stats", {}).get("duplicates_removed") == 1, str(preview.get("stats")))

        columns = [{"name": c["name"], "type": c["type"], "include": True} for c in preview["columns"]]
        _, commit = post(base, "/api/import/commit", {"text": csv, "name": "acceptance", "columns": columns})
        record("导入 · 建表成功", commit.get("ok"), str(commit.get("error")))
        table = commit.get("table")
        record("导入 · 表名带 imp_ 前缀", str(table).startswith("imp_"), str(table))

        if commit.get("ok"):
            _, tables_raw, _ = get(base, "/api/tables", timeout=120)
            names = {t["name"] for t in json.loads(tables_raw)}
            record("导入 · 新表出现在字典里", table in names, str(table))

            _, check = post(
                base, "/api/check", {"sql": f'SELECT city, sales FROM "{table}" LIMIT 5'}
            )
            record("导入 · 新表可查询", check.get("verdict") == "allowed", str(check.get("reason")))

            # 清理：验收不该在库里留下垃圾
            _, dropped = post(base, "/api/import/drop", {"table": table})
            record("导入 · 可删除", dropped.get("ok"), str(dropped.get("error")))
            _, tables_raw2, _ = get(base, "/api/tables", timeout=120)
            still = table in {t["name"] for t in json.loads(tables_raw2)}
            record("导入 · 删除后表已消失", not still, table)

            _, refused = post(base, "/api/import/drop", {"table": "orders"})
            record("导入 · 拒绝删除业务表", refused.get("ok") is False, str(refused.get("error")))
    except Exception as exc:
        record("导入", False, f"{type(exc).__name__}: {exc}")

    try:
        _, bad = post(base, "/api/import/preview", {"text": ""})
        record("导入 · 空内容被拒绝", bad.get("ok") is False)
    except Exception:
        record("导入 · 空内容被拒绝", True)


# ---------------------------------------------------------------- 主流程


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8777")
    parser.add_argument("--skip-llm", action="store_true", help="跳过要花钱的问数检查")
    args = parser.parse_args()

    print("=" * 62)
    print("askdata 验收")
    print("=" * 62)

    check_static(args.base)
    check_overview(args.base)
    check_tables(args.base)
    check_questions(args.base)
    check_hints(args.base)
    check_attacks(args.base)
    check_reports(args.base)
    check_guard(args.base)
    check_session(args.base)
    check_observability(args.base)
    check_import(args.base)

    if args.skip_llm:
        record("/api/ask 系列", True, "跳过（--skip-llm）", skip=True)
    else:
        ask = check_ask(args.base)
        sql = (ask.get("base") or {}).get("sql") or ""
        check_ask_followup(args.base, sql)

    failed = [c for c in RESULTS if c.status == FAIL]
    skipped = [c for c in RESULTS if c.status == SKIP]
    print("=" * 62)
    print(f"共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed) - len(skipped)}，"
          f"失败 {len(failed)}，跳过 {len(skipped)}")
    if failed:
        print("\n失败项：")
        for c in failed:
            print(f"  - {c.name}  {c.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
