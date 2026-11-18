"""把运行中的服务接口快照抓下来，给前端渲染验收（frontend_audit.js）用。

CI 里没有浏览器，所以前端验录取三条路：
1. 起一个真实服务（和线上同一份代码）；
2. 用本脚本把它返回的真实 JSON 抓成本地文件；
3. node 脚本在 DOM 模拟里跑真实数据的渲染函数。

这样即使没有 Chromium，也能验出"接口正常但页面崩了"这类问题——
之前的 highlightSQL 丢失、选择器撞车、按钮点不动，全是这一类。
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TARGETS = ["hints", "questions", "reports", "tables", "attacks", "overview"]
REPORT_KINDS = ["eval", "repeat", "multiturn", "clarify", "closed"]


def fetch(base: str, path: str, timeout: int = 120):
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8777"
    out = Path("tmp_acceptance")
    out.mkdir(parents=True, exist_ok=True)

    for key in TARGETS:
        data = fetch(base, f"/api/{key}")
        (out / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"  {key:<12} ok")

    reports = json.loads((out / "reports.json").read_text(encoding="utf-8"))
    for kind in REPORT_KINDS:
        item = next((r for r in reports if r.get("kind") == kind), None)
        if not item:
            print(f"  report_{kind:<5} 跳过（没有这类报告）")
            continue
        name = urllib.parse.quote(item["name"])
        data = fetch(base, f"/api/report?name={name}")
        (out / f"report_{kind}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  report_{kind:<5} ok")

    print(f"快照已写入 {out}/（{len(list(out.glob('*.json')))} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
