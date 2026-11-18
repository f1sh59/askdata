"""零依赖的 HTTP 服务：给前端提供 API，同时托管静态页面。

    python -m askdata.server --port 8777

为什么不用 FastAPI/Flask：这个环境的 pip 装不了包（临时目录被拦），
而 http.server 是标准库，够用且没有副作用。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .auth import authorized, configured_api_key
from .service import Pipeline

WEB_DIR = config.ROOT / "web"

_PIPELINE: Pipeline | None = None
_PIPELINE_LOCK = threading.Lock()
_WRITABLE = True


def pipeline() -> Pipeline:
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is None:
            _PIPELINE = Pipeline(writable=_WRITABLE)
        return _PIPELINE


class Handler(BaseHTTPRequestHandler):
    server_version = "askdata/0.1"

    # ---------------- 基础 ----------------

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[web] {fmt % args}\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    # ---------------- 流式问数（SSE） ----------------

    def _request_api_key(self) -> str | None:
        """界面里填的模型密钥随请求头过来，不落盘、不打日志。"""
        return (self.headers.get("X-API-Key") or "").strip() or None

    def _stream_ask(self, payload: dict) -> None:
        """把每个阶段实时推给前端，最后再推完整结果。

        为什么用 SSE 而不是 WebSocket：只需要服务端单向推送，
        不必维护双向连接、心跳、重连，还能直接复用现有的 HTTP 处理流程。
        """
        question = str(payload.get("question") or "").strip()
        if not question:
            return self._error("问题不能为空")
        session_id = str(payload.get("session_id") or "") or None
        if not (self._request_api_key() or configured_api_key()):
            return self._json(
                {
                    "ok": False,
                    "error": "还没有配置模型 API Key：点右上角「API」填入，或在 .env 里设置 DEEPSEEK_API_KEY",
                    "need_api_key": True,
                },
                400,
            )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        # 不用 keep-alive：SSE 没有 Content-Length，客户端不知道流什么时候结束。
        # 显式 close + 最后一帧 done，客户端才能拿到 EOF。
        self.send_header("Connection", "close")
        # 反向代理（nginx）默认会缓冲，导致"流式"变成"一次性"，显式关掉
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def frame(event: str, data: object) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str)
            self.wfile.write(f"event: {event}\ndata: {body}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            frame("open", {"ok": True, "session_id": session_id})
            result = pipeline().ask(
                question,
                top_k=int(payload.get("top_k") or 8),
                clarification=payload.get("clarification"),
                answer=payload.get("answer"),
                session_id=session_id,
                on_stage=lambda stage: frame("stage", stage),
                api_key=self._request_api_key(),
            )
            frame("result", result)
        except BrokenPipeError:
            return  # 客户端断开，安静收场
        except Exception as exc:  # noqa: BLE001
            try:
                frame("error", {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            except Exception:  # noqa: BLE001
                pass
        finally:
            # 收尾帧 + 关闭连接：客户端读到 done 或 EOF 才会停止等待
            try:
                frame("done", {"ok": True})
            except Exception:  # noqa: BLE001
                pass
            self.close_connection = True

    # ---------------- 路由 ----------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            # 鉴权必须放在所有 /api/ 分支之前，否则前面几个接口会绕过它
            if path.startswith("/api/") and not authorized(self.headers.get("Authorization")):
                return self._json(
                    {"ok": False, "error": "未授权：需要 Bearer token", "auth_required": True}, 401
                )
            if path == "/api/overview":
                return self._json(pipeline().overview())
            if path == "/api/tables":
                return self._json(pipeline().tables_payload())
            if path == "/api/questions":
                return self._json(pipeline().questions())
            if path == "/api/attacks":
                return self._json(pipeline().attack_cases())
            if path == "/api/hints":
                return self._json(pipeline().data_hints())
            if path == "/api/reports":
                return self._json(pipeline().reports())
            if path == "/api/session/stats":
                return self._json(pipeline().sessions.stats())
            if path == "/api/report":
                name = (query.get("name") or [""])[0]
                if not name:
                    return self._error("缺少 name 参数")
                return self._json(pipeline().report(name))
            if path.startswith("/api/"):
                return self._error("未知接口", 404)
            return self._static(path)
        except FileNotFoundError:
            self._error("找不到该报告", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._error("请求体不是合法 JSON")

        try:
            if parsed.path.startswith("/api/") and not authorized(self.headers.get("Authorization")):
                return self._json(
                    {"ok": False, "error": "未授权：需要 Bearer token", "auth_required": True}, 401
                )
            if parsed.path == "/api/ask/stream":
                return self._stream_ask(payload)
            if parsed.path == "/api/ask":
                question = str(payload.get("question") or "").strip()
                if not question:
                    return self._error("问题不能为空")
                if not (self._request_api_key() or configured_api_key()):
                    return self._json(
                        {
                            "ok": False,
                            "error": "还没有配置模型 API Key：点右上角「API」填入，或在 .env 里设置 DEEPSEEK_API_KEY",
                            "need_api_key": True,
                        },
                        400,
                    )
                history = payload.get("history") or []
                if not isinstance(history, list):
                    history = []
                # 只保留最近 3 轮，避免 prompt 无限膨胀
                history = [h for h in history if isinstance(h, dict)][-3:]
                return self._json(
                    pipeline().ask(
                        question,
                        top_k=int(payload.get("top_k") or 8),
                        clarification=payload.get("clarification"),
                        answer=payload.get("answer"),
                        history=history or None,
                        session_id=str(payload.get("session_id") or "") or None,
                        api_key=self._request_api_key(),
                    )
                )
            if parsed.path == "/api/check":
                sql = str(payload.get("sql") or "").strip()
                if not sql:
                    return self._error("SQL 不能为空")
                return self._json(
                    pipeline().check_sql(sql, policy=bool(payload.get("policy")))
                )
            if parsed.path == "/api/import/preview":
                text = str(payload.get("text") or "")
                if not text.strip():
                    return self._error("内容不能为空")
                if len(text) > 4_000_000:
                    return self._error("文本太大，请控制在 4MB 以内")
                result = pipeline().import_preview(text, payload.get("options") or {})
                # 解析失败是"请求内容有问题"，不是成功响应；统一用 400，
                # 免得前端和服务端对同一类错误有两种理解。
                return self._json(result, 200 if result.get("ok") is not False else 400)
            if parsed.path == "/api/session/clear":
                sid = str(payload.get("session_id") or "").strip()
                cleared = pipeline().sessions.clear(sid or None)
                return self._json(
                    {"ok": True, "cleared": cleared, "stats": pipeline().sessions.stats()}
                )

            if parsed.path == "/api/import/drop":
                return self._json(pipeline().drop_import(str(payload.get("table") or "")))
            if parsed.path == "/api/import/commit":
                text = str(payload.get("text") or "")
                name = str(payload.get("name") or "dataset")
                columns = payload.get("columns") or []
                if not text.strip():
                    return self._error("内容不能为空")
                if not columns:
                    return self._error("缺少列定义")
                result = pipeline().import_commit(
                    text, name, columns, payload.get("options") or {}
                )
                return self._json(result, 200 if result.get("ok") is not False else 400)
            return self._error("未知接口", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

    # ---------------- 静态文件 ----------------

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        target = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._error("not found", 404)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)


def main(argv: list[str] | None = None) -> int:
    global _WRITABLE

    parser = argparse.ArgumentParser(prog="askdata.server")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="以只读模式启动：关闭导入功能，但可以和 CLI 同时使用数据库",
    )
    args = parser.parse_args(argv)
    _WRITABLE = not args.read_only

    print("[web] 正在加载 schema 与召回索引 ...", flush=True)
    pipeline()

    class Server(ThreadingHTTPServer):
        # socketserver 默认 listen backlog 只有 5。
        # 20 个并发请求就能把它打爆：Windows 上多半表现为偶发变慢，
        # Linux 上是直接 RST（客户端看到 Connection reset）——CI 就是这么挂的。
        request_queue_size = 128
        daemon_threads = True

    server = Server((args.host, args.port), Handler)
    mode = "可写（支持导入）" if _WRITABLE else "只读（导入已关闭）"
    print(f"[web] askdata 已启动: http://{args.host}:{args.port}  模式：{mode}", flush=True)
    if _WRITABLE:
        print("[web] 注意：可写模式会独占数据库文件，此时 CLI 无法同时打开（用 --read-only 可共存）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
