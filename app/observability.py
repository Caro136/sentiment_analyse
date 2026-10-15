"""可观测性：请求级 trace_id 与访问日志（设计规格 8.2「全链路可定位」）。

每个请求生成一个 `trace_id`，随响应头 `X-Trace-Id` 返回，并绑定到 loguru 的
上下文——同一次请求内所有模块的日志（含引擎层的重试、模型调用）都会自动带上它，
排障时按一个 id 就能把前后端日志串起来。

**用纯 ASGI 中间件而不是 `BaseHTTPMiddleware`**：后者会把响应包装成流式对象并
插入额外的任务与队列，对 SSE 这类永不结束的长连接是实打实的风险（T-18 已经因为
缓冲问题踩过一次）。纯 ASGI 中间件只做「记录 → 透传 → 补响应头」，不碰响应体。
"""

import time
import uuid
from contextvars import ContextVar

from loguru import logger

__all__ = [
    "TRACE_HEADER",
    "current_trace_id",
    "bind_trace_id",
    "bind_client_ip",
    "current_client_ip",
    "reset_client_ip",
    "client_ip_from_scope",
    "TraceIdMiddleware",
]

TRACE_HEADER = "X-Trace-Id"

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)

# 健康检查与静态文档被探针高频轮询，记访问日志只会淹没真正的请求
_QUIET_PATHS = ("/healthz", "/readyz", "/favicon.ico")


def current_trace_id() -> str:
    """取当前请求的 trace_id（不在请求上下文中时返回空串）。"""
    return _trace_id.get()


def bind_trace_id(trace_id: str):
    """设置当前上下文的 trace_id，返回可用于复原的 token。"""
    return _trace_id.set(trace_id)


def bind_client_ip(ip: str | None):
    """绑定当前请求的来源 IP，返回可用于复原的 token。"""
    return _client_ip.set(ip)


def current_client_ip() -> str | None:
    """当前请求的来源 IP。"""
    return _client_ip.get()


def reset_client_ip(token) -> None:
    """复原 IP 上下文（中间件收尾）。"""
    _client_ip.reset(token)


def client_ip_from_scope(scope) -> str | None:
    """从 ASGI scope 取客户端 IP。

    经 Nginx 反代时以 `X-Forwarded-For` 的首个地址为准——直接取 `scope["client"]`
    拿到的是反向代理的地址，审计与告警里就全是同一个 IP 了。
    单独成函数是因为登录日志（`app/routers/auth.py`）与审计（`app/services/audit`）
    都要用，两处各写一遍必然出现「日志是一个 IP、审计是另一个」。
    """
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in (scope.get("headers") or [])
    }
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    client = scope.get("client")
    return client[0] if client else None


class TraceIdMiddleware:
    """生成并透传 trace_id，同时记录一条访问日志。

    trace_id 优先取上游（Nginx / 前端）传来的值，便于跨服务串联；
    没有则新生成。格式用 16 位十六进制，短到能看清、长到不会碰撞。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(TRACE_HEADER.lower().encode("latin-1"), b"").decode("latin-1")
        trace_id = incoming or uuid.uuid4().hex[:16]

        token = bind_trace_id(trace_id)
        # 来源 IP 一并绑定：审计（audit_log.ip）、登录日志都要它，
        # 请求内取一次即可，不必各模块重复解析请求头
        ip_token = bind_client_ip(client_ip_from_scope(scope))
        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                # SSE 的长连接同样带上 trace_id，便于前端报错时回传
                headers_list = list(message.get("headers") or [])
                headers_list.append((TRACE_HEADER.lower().encode("latin-1"),
                                     trace_id.encode("latin-1")))
                message = {**message, "headers": headers_list}
            await send(message)

        path = scope.get("path", "")
        try:
            # 请求上下文用 loguru 的 contextualize 注入：日志格式里无需到处传参，
            # 且 asyncio 下按任务隔离，不会串到并发请求上
            with logger.contextualize(trace_id=trace_id):
                await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if path not in _QUIET_PATHS:
                logger.bind(trace_id=trace_id).info(
                    "{} {} → {} ({:.0f}ms)",
                    scope.get("method", "-"), path, status_holder["status"], elapsed_ms,
                )
            _trace_id.reset(token)
            reset_client_ip(ip_token)
