"""截图：用 CDP 精确控制视口尺寸。

为什么不用 `msedge --screenshot --window-size=390,844`：
    Windows 上 headless 窗口有最小宽度（约 496px），传 390 会被夹到 496，
    截出来的图只有 390 宽——等于把右侧裁掉了。看起来像"布局溢出"，
    其实是截图工具的问题，白白调半天 CSS。

CDP 的 Emulation.setDeviceMetricsOverride 直接设定 CSS 视口，
不受窗口最小值限制，所以能真正测到 390 / 375 / 320 这些手机宽度。

用法：
    python scripts/screenshot.py --width 390 --height 844 --out shots/mobile.png
    python scripts/screenshot.py --width 390 --height 844 --url http://127.0.0.1:8777/#settings
    python scripts/screenshot.py --width 1440 --height 900 --out shots/desktop.png --full-page
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for path in EDGE_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit("找不到 Edge，请用 --browser 指定")


class CDP:
    """极简 CDP 客户端：只要导航 + 截图 + 设置视口。"""

    def __init__(self, port: int) -> None:
        import websocket  # 延迟导入，没装时才提示

        self._ws = websocket.create_connection(
            self._target_url(port), timeout=30, max_size=64 * 1024 * 1024
        )
        self._id = 0

    def _target_url(self, port: int) -> str:
        for _ in range(40):
            try:
                raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2).read()
                targets = json.loads(raw)
                for target in targets:
                    if target["type"] == "page":
                        return target["webSocketDebuggerUrl"]
            except Exception:
                time.sleep(0.25)
        raise SystemExit("连不上浏览器调试端口")

    def send(self, method: str, **params) -> dict:
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") == self._id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    def close(self) -> None:
        self._ws.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8777/")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--out", default="shot.png")
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--mobile", action="store_true", default=True)
    parser.add_argument("--wait", type=float, default=2.5, help="页面稳定等待秒数")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--browser", default="")
    args = parser.parse_args()

    browser = args.browser or find_browser()
    profile = tempfile.mkdtemp(prefix="cdp-profile-")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={args.port}",
            # Chrome 111+ 默认拒绝没带 Origin 的 WebSocket，必须放开
            "--remote-allow-origins=*",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        cdp = CDP(args.port)
        cdp.send("Page.enable")
        # 关键一步：直接设定 CSS 视口，绕开窗口最小宽度
        cdp.send(
            "Emulation.setDeviceMetricsOverride",
            width=args.width,
            height=args.height,
            deviceScaleFactor=1,
            mobile=args.width < 700,
        )
        cdp.send("Page.navigate", url=args.url)
        time.sleep(args.wait)

        params: dict = {"format": "png"}
        if args.full_page:
            params["captureBeyondViewport"] = True
        shot = cdp.send("Page.captureScreenshot", **params)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(shot["data"]))
        print(f"已保存 {out}（视口 {args.width}x{args.height}）")
        cdp.close()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
