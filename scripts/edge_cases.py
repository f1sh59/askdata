"""异常输入验收：拿坏数据去打接口，看它是有礼貌地拒绝，还是崩掉。

这些用例不在 acceptance.py 里，因为那个脚本验的是"正常功能对不对"；
这里验的是"不正常输入会不会把服务弄坏"。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8777"
RESULTS: list[tuple[bool, str, str]] = []


def case(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(("  [ ok ] " if ok else "  [FAIL] ") + name + (f"  — {detail}" if detail and not ok else ""))


def request(path: str, *, method: str = "GET", body: bytes | None = None, timeout: int = 60):
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def run() -> int:
    print("异常输入验收")

    # 1) 空问题
    status, body = request("/api/ask", method="POST", body=b'{"question":"   "}')
    case("空问题被拒绝", status == 400 and "不能为空" in body, f"{status} {body[:60]}")

    # 2) 非法 JSON
    status, body = request("/api/ask", method="POST", body=b"not json at all")
    case("非法 JSON 被拒绝", status == 400, f"{status} {body[:60]}")

    # 3) 超长问题（50 KB）
    huge = json.dumps({"question": "销售额" * 12000}).encode()
    status, body = request("/api/ask/stream", method="POST", body=huge, timeout=90)
    case("超长问题不崩（4xx 或 SSE 正常返回）", status in (200, 400, 413), f"{status}")

    # 4) 缺少参数
    status, body = request("/api/report")
    case("缺少 name 参数被拒绝", status == 400 and "name" in body, f"{status} {body[:60]}")

    # 5) 路径穿越：报告名
    status, body = request("/api/report?name=../../askdata/config.py")
    case("报告名路径穿越被挡住", status == 404 or "不存在" in body or "not found" in body, f"{status} {body[:60]}")

    # 6) 路径穿越：静态文件
    status, body = request("/../askdata/config.py")
    case("静态路径穿越被挡住", status == 404, f"{status}")

    # 7) 未知接口
    status, body = request("/api/nope")
    case("未知接口返回 404", status == 404, f"{status}")

    # 8) 不支持的方法
    status, _ = request("/api/overview", method="PUT", body=b"{}")
    case("PUT 不被支持", status in (501, 405), f"{status}")

    # 9) 危险 SQL 走 check 接口
    for sql in (
        "DROP TABLE orders",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "ATTACH DATABASE '/tmp/x.duckdb' AS x",
        "SELECT * FROM orders; DROP TABLE orders",
    ):
        payload = json.dumps({"sql": sql}).encode()
        status, body = request("/api/check", method="POST", body=payload)
        verdict = ""
        try:
            verdict = json.loads(body).get("verdict", "")
        except json.JSONDecodeError:
            pass
        case(f"危险 SQL 被拦：{sql[:28]}…", status == 200 and verdict == "blocked", f"{status} {verdict}")

    # 10) 导入垃圾数据
    for bad in ("", "   ", "\x00\x01\x02"):
        payload = json.dumps({"text": bad}).encode()
        status, body = request("/api/import/preview", method="POST", body=payload)
        case(f"导入垃圾数据被拒绝（{bad[:6]!r}）", status == 400, f"{status} {body[:60]}")

    # 11) 会话接口异常参数
    status, body = request("/api/session/clear", method="POST", body=b'{"session_id": 12345}')
    case("会话清空容忍非法类型", status == 200, f"{status} {body[:60]}")

    # 12) SSE 空问题应当返回 JSON 错误而不是挂起
    status, body = request("/api/ask/stream", method="POST", body=b'{"question":""}', timeout=30)
    case("SSE 空问题快速返回错误", status == 400, f"{status}")

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
