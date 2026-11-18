"""测流式输出：记录每个事件的到达时间，算出首字节延迟（TTFB）。

判据是**收到 done 帧**，而不是"收到 result 就收工"。
这一点很关键：曾经客户端和服务端都以为流该结束了，但服务端没关连接、
也没发结束帧，浏览器就一直挂在"处理中"——而这个探针因为提前 break，把 bug 掩盖了。

用法：
    python scripts/stream_probe.py "2025 年各渠道的销售额分别是多少？"
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8777"
DONE_TIMEOUT = 60.0


def run(question: str, session_id: str | None = None) -> int:
    body = json.dumps({"question": question, "session_id": session_id}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{BASE}/api/ask/stream",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    started = time.perf_counter()
    first_byte = None
    events: list[tuple[float, str, str]] = []
    saw_done = False

    with urllib.request.urlopen(req, timeout=DONE_TIMEOUT) as resp:
        event = ""
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n")
            if first_byte is None:
                first_byte = time.perf_counter() - started
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                events.append((time.perf_counter() - started, event, line[6:]))
            if event == "done":
                saw_done = True
                break

    total = time.perf_counter() - started
    print(f"问题：{question}")
    print(f"首字节(TTFB)：{first_byte * 1000:.0f} ms" if first_byte else "首字节：无")
    print(f"总耗时：{total * 1000:.0f} ms")
    print(f"收到结束帧(done)：{'是' if saw_done else '否 ← 客户端会一直卡住，这是 bug'}\n")

    for at, name, data in events:
        payload = json.loads(data)
        if name == "stage":
            print(f"  [{at * 1000:6.0f} ms] stage  {payload['name']:<6} {payload['detail']}")
        elif name == "result":
            print(
                f"  [{at * 1000:6.0f} ms] result ok={payload.get('ok')} 行数={payload.get('row_count')}"
            )
        elif name == "error":
            print(f"  [{at * 1000:6.0f} ms] error  {payload.get('error')}")

    print(f"\n首帧到结果之间，用户已经在看进度：约 {(total - (first_byte or total)) * 1000:.0f} ms")
    return 0 if saw_done else 1


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1] if len(sys.argv) > 1 else "2025 年各渠道的销售额分别是多少？"))
