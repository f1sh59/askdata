"""零依赖的 HTTP 服务：给前端提供 API，同时托管静态页面。

    python -m askdata.server --port 8777

为什么不用 FastAPI/Flask：这个环境的 pip 装不了包（临时目录被拦），
而 http.server 是标准库，够用且没有副作用。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config
from .auth import authorized, configured_api_key
from .observability import METRICS, log_enabled, log_event, new_request_id
from .ratelimit import RateLimiter
from .service import Pipeline

WEB_DIR = config.ROOT / "web"

_PIPELINE: Pipeline | None = None
_PIPELINE_LOCK = threading.Lock()
_WRITABLE = True
LIMITER = RateLimiter.from_env()


def pipeline() -> Pipeline:
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is None:
            _PIPELINE = Pipeline(writable=_WRITABLE)
        return _PIPELINE


def _code_for(status: int) -> str:
    """HTTP 状态码 → 机器可读的错误码。"""
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        413: "payload_too_large",
        429: "rate_limited",
        500: "internal_error",
    }.get(status, "error")


def _readiness() -> dict:
    """就绪探针：不只回 200，还要确认数据库真的能查。

    K8s 用 /health 判断要不要重启容器，用 /ready 判断要不要往里导流量。
    后者如果只看进程活着，会出现"容器在、但查询全挂"还继续接流量的情况。
    """
    checks: dict[str, object] = {}
    ready = True
    try:
        pipe = pipeline()
        result = pipe.sandbox.run("SELECT 1 AS ok")
        checks["database"] = "ok" if result.ok else f"error: {result.error}"
        ready = ready and result.ok
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        ready = False
    checks["tables"] = len(getattr(pipeline(), "tables", []) or [])
    checks["model"] = pipeline()._client_model()
    return {"ok": ready, "status": "ready" if ready else "not_ready", "checks": checks}


class Handler(BaseHTTPRequestHandler):
    request_id: str = "-"
    server_version = "askdata/1.0"

    # 在途请求计数：优雅停机时靠它判断"还有没有活没干完"
    _inflight = 0
    _inflight_lock = threading.Lock()

    @classmethod
    def wait_inflight(cls, timeout: float = 8.0) -> int:
        """等在途请求归零，返回等到了几个。超时就返回剩余数量。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with cls._inflight_lock:
                if cls._inflight == 0:
                    return 0
            time.sleep(0.05)
        with cls._inflight_lock:
            return cls._inflight

    # ---------------- 基础 ----------------

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[web] {fmt % args}\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self._last_status = status
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Request-Id", self.request_id)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400, code: str | None = None) -> None:
        """统一的错误响应：ok=false + error 文案 + 机器可读的 code。

        前端只看 error 文案，接入方按 code 分支，不用去匹配中文字符串。
        """
        self._json({"ok": False, "error": message, "code": code or _code_for(status)}, status)

    # ---------------- 流式问数（SSE） ----------------

    def _json_result(self, result: dict) -> None:
        """业务层返回 ok=false 时，补上机器可读的 code 并用 400。

        之前导入失败只回 {"ok": false, "error": ...}，和 _error() 出去的
        响应结构不一致——接入方按 code 分支就会漏掉这种错误。
        """
        if result.get("ok") is False:
            return self._json({**result, "code": result.get("code") or "bad_request"}, 400)
        return self._json(result)

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
                    "code": "bad_request",
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
            self.wfile.write(f"event: {event}\ndata: {body}\n\n".encode())
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
        except Exception as exc:
            with contextlib.suppress(Exception):
                frame("error", {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            # 收尾帧 + 关闭连接：客户端读到 done 或 EOF 才会停止等待
            with contextlib.suppress(Exception):
                frame("done", {"ok": True})
            self.close_connection = True

    # ---------------- 中间件：请求 ID / 限流 / 指标 ----------------

    def _client_ip(self) -> str:
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return forwarded or self.client_address[0]

    def _dispatch(self, method: str, route) -> None:
        """所有请求的统一入口：分配 ID、限流、计时、记指标、写日志。

        横切逻辑集中在这一处——加新接口时不会漏掉限流或埋点。
        """
        self.request_id = new_request_id()
        self._last_status = 200
        started = time.perf_counter()
        with Handler._inflight_lock:
            Handler._inflight += 1
        path = urlparse(self.path).path
        route_label = path if path.startswith("/api/") else "_static"

        # 探针和指标采集不限流：它们是高频且可信的
        if path not in ("/health", "/ready", "/metrics"):
            allowed, retry_after = LIMITER.allow(self._client_ip())
            if not allowed:
                METRICS.inc("rate_limited")
                self._json(
                    {
                        "ok": False,
                        "error": "请求太频繁，稍后再试",
                        "code": "rate_limited",
                    },
                    429,
                )
                self._record(method, route_label, 429, started, retry_after=retry_after)
                return

        try:
            route()
        except BrokenPipeError:
            self._last_status = 499  # 客户端主动断开
        except Exception:
            log_event(
                "error",
                "unhandled_exception",
                request_id=self.request_id,
                path=path,
                exc=repr(sys.exc_info()[1]),
            )
            self._json({"ok": False, "error": "服务内部错误", "code": "internal_error"}, 500)
        finally:
            with Handler._inflight_lock:
                Handler._inflight -= 1
            self._record(method, route_label, self._last_status, started)

    def _record(
        self, method: str, route: str, status: int, started: float, retry_after: float = 0.0
    ) -> None:
        elapsed = time.perf_counter() - started
        METRICS.observe_request(method, route, status, elapsed)
        if log_enabled():
            log_event(
                "info",
                "http_request",
                request_id=self.request_id,
                method=method,
                route=route,
                status=status,
                ms=round(elapsed * 1000, 1),
                retry_after=round(retry_after, 2) or None,
            )

    def do_GET(self) -> None:
        self._dispatch("GET", self._route_get)

    def do_POST(self) -> None:
        self._dispatch("POST", self._route_post)

    # ---------------- 路由 ----------------

    def _route_get(self) -> None:
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
            if path == "/health":
                return self._json({"ok": True, "status": "alive"})
            if path == "/ready":
                return self._json(_readiness())
            if path == "/metrics":
                body = METRICS.render().encode("utf-8")
                return self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")
            if path.startswith("/api/"):
                return self._error("未知接口", 404)
            return self._static(path)
        except FileNotFoundError:
            self._error("找不到该报告", 404)
        except Exception as exc:
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _route_post(self) -> None:
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
                            "code": "bad_request",
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
                # 解析失败是"请求内容有问题"，不是成功响应；统一走 _json_result
                return self._json_result(pipeline().import_preview(text, payload.get("options") or {}))
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
                return self._json_result(
                    pipeline().import_commit(text, name, columns, payload.get("options") or {})
                )
            return self._error("未知接口", 404)
        except Exception as exc:
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
    # 优雅停机：收到 SIGTERM/SIGINT 后先停止接收新连接，再等在途请求做完。
    # 不这么做的话，发版时正在跑的那次问数会被直接掐断——用户看到没头没尾的错误，
    # 日志里也查不到。
    stopping = threading.Event()

    def _graceful(signum: int, _frame) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print(f"[web] 收到信号 {signum}，开始优雅停机 ...", flush=True)
        # shutdown() 必须在别的线程里调，否则会和服务循环互相等待
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, _graceful)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        drained = Handler.wait_inflight(timeout=8.0)
        print(f"[web] 已停止，在途请求 {drained} 个已完成", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
