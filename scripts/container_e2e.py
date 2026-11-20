"""容器端到端验证的 HTTP 部分（不管 Docker）。

为什么拆开：
    这个沙箱里 Python 启动的任何子进程都拿不到 Docker 的命名管道权限，
    `docker info` 直接 permission denied。所以 Docker 动作交给外层的
    container_e2e.ps1，这里只做 HTTP 断言。

两个阶段：
    --phase before   身份、建用户、鉴权、真实问数、审计、会话
    --phase after    重启之后再验一遍：用户还在、会话还在、审计还在
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / ".tmp" / "container-token.txt"
SNAPSHOT = ROOT / ".tmp" / "container-session.json"
BASE = "http://127.0.0.1:8778"


def call(method: str, path: str, payload: dict | None = None, token: str = "", timeout: int = 180):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def env_key() -> str:
    env = ROOT / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


class Checks:
    def __init__(self) -> None:
        self.results: list[bool] = []

    def __call__(self, label: str, cond: bool, extra: str = "") -> None:
        self.results.append(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}  {extra}")

    def summary(self) -> int:
        passed = sum(self.results)
        print(f"\n共 {len(self.results)} 项：通过 {passed}，失败 {len(self.results) - passed}")
        return 0 if passed == len(self.results) else 1


def phase_before(check: Checks) -> int:
    status, me = call("GET", "/api/auth/me")
    check("身份接口免鉴权", status == 200 and me.get("mode") == "open", f"mode={me.get('mode')}")

    status, created = call("POST", "/api/users", {"name": "容器测试", "role": "admin"})
    name = created.get("user", {}).get("name", "")
    check("建用户（中文名）", status == 200 and name == "容器测试", name)
    token = created.get("token", "")
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")

    status, users = call("GET", "/api/users", token=token)
    check("带 token 列用户", status == 200 and any(u["name"] == "容器测试" for u in users.get("users", [])),
          f"{len(users.get('users', []))} 个")

    status, _ = call("GET", "/api/users")
    check("无 token 被拒", status == 401, f"HTTP {status}")

    key = env_key()
    if not key:
        print("  SKIP  没有 .env 里的 key")
    else:
        status, answer = call(
            "POST", "/api/ask",
            {"question": "2025 年各渠道的销售额分别是多少？", "session_id": "container-e2e"},
            token=token,
        )
        check("真实问数", status == 200 and bool(answer.get("ok")), (answer.get("error") or "")[:60])
        sql = answer.get("rewritten_sql") or answer.get("sql") or ""
        check("生成了 SQL", bool(sql), sql[:70])
        check("有结果行", int(answer.get("row_count") or 0) > 0, f"rows={answer.get('row_count')}")

        status, audit = call("GET", "/api/audit?limit=10", token=token)
        asked = [e for e in audit.get("entries", []) if e.get("action") == "ask"]
        check("审计记录了提问", bool(asked), f"{len(audit.get('entries', []))} 条")
        if asked:
            check("审计里用户名正确", asked[0].get("user") == "容器测试", asked[0].get("user", ""))
            check("审计里记了 SQL", "select" in (asked[0].get("sql") or "").lower())

    status, stats = call("GET", "/api/session/stats", token=token)
    snapshot = {
        "sessions": int(stats.get("sessions") or 0),
        "turns": int(stats.get("turns") or 0),
        "persistent": stats.get("persistent"),
    }
    SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    check("会话已建立", snapshot["sessions"] >= 1, str(snapshot))
    check("会话是持久化的", snapshot["persistent"] is True, str(snapshot.get("persistent")))
    return check.summary()


def phase_after(check: Checks) -> int:
    token = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
    before = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}

    status, me = call("GET", "/api/auth/me", token=token)
    name = (me.get("user") or {}).get("name", "")
    check("重启后用户仍有效", status == 200 and name == "容器测试", f"{status} {name}")

    status, stats = call("GET", "/api/session/stats", token=token)
    after = {
        "sessions": int(stats.get("sessions") or 0),
        "turns": int(stats.get("turns") or 0),
    }
    check(
        "重启后会话还在",
        after["sessions"] == before.get("sessions") and after["turns"] == before.get("turns"),
        f"before={before} after={after}",
    )

    status, audit = call("GET", "/api/audit?limit=5", token=token)
    check("重启后审计还在", len(audit.get("entries", [])) >= 1, f"{len(audit.get('entries', []))} 条")

    status, _ = call("GET", "/api/users")
    check("重启后仍然拒绝匿名", status == 401, f"HTTP {status}")
    return check.summary()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["before", "after"], default="before")
    args = parser.parse_args()

    check = Checks()
    return phase_before(check) if args.phase == "before" else phase_after(check)


if __name__ == "__main__":
    raise SystemExit(main())
