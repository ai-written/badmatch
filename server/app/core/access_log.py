"""
HTTP 访问日志中间件（A 层审计）。

每个 HTTP 请求记录一行 JSON 到日志文件：
  time / ip / user_id / username / method / path / query / status / duration_ms / user_agent

- 用户身份：从 Authorization Bearer token 解码（不查库，纯 CPU 解码）
- 过滤：/api/health、/static/*、/ws/*（健康检查/静态资源/WebSocket 握手不记录）
- 落盘：RotatingFileHandler 按大小轮转，JSON Lines 格式
"""
import json
import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

from app.core.config import get_settings
from app.core.security import decode_access_token

# 独立 logger，避免污染业务日志
access_logger = logging.getLogger("app.access")
_access_logger_configured = False


def _configure_logger() -> None:
    global _access_logger_configured
    if _access_logger_configured:
        return
    settings = get_settings()
    path = settings.AUDIT_LOG_PATH
    if path:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            handler = RotatingFileHandler(
                path,
                maxBytes=settings.AUDIT_LOG_MAX_MB * 1024 * 1024,
                backupCount=settings.AUDIT_LOG_BACKUPS,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            access_logger.addHandler(handler)
            access_logger.setLevel(logging.INFO)
            access_logger.propagate = False  # 不重复输出到根 logger
            _access_logger_configured = True
        except OSError:
            # 日志目录不可写时降级为 stderr，保证服务可用
            access_logger.setLevel(logging.INFO)
            access_logger.propagate = True
            _access_logger_configured = True


def _headers(scope: dict) -> dict:
    """headers 转小写键 dict（bytes -> str），一次解析供多处复用。"""
    return {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in scope.get("headers", [])
    }


def _client_ip(scope: dict, headers: dict | None = None) -> str:
    headers = headers if headers is not None else _headers(scope)
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else ""


def _identity(scope: dict, headers: dict | None = None) -> tuple[str | None, str | None]:
    """从 Authorization header 解码用户身份（不查库）。"""
    headers = headers if headers is not None else _headers(scope)
    auth = headers.get("authorization", "")
    if auth.startswith("Bearer "):
        payload = decode_access_token(auth[7:].strip())
        if payload:
            return payload.get("sub"), payload.get("username")
    return None, None


class AccessLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # 过滤健康检查 / 静态资源 / WebSocket
        if path.startswith("/api/health") or path.startswith("/static") or path.startswith("/ws"):
            await self.app(scope, receive, send)
            return

        _configure_logger()
        start = time.perf_counter()
        status_holder = {"status": 500}
        error = None

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # 记录后继续抛给上层（500 处理）
            error = exc
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            headers = _headers(scope)
            user_id, username = _identity(scope, headers)
            query = scope.get("query_string", b"").decode("latin-1")
            record = {
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "ip": _client_ip(scope, headers),
                "user_id": int(user_id) if isinstance(user_id, str) and user_id.isdigit() else None,
                "username": username,
                "method": scope.get("method", ""),
                "path": path,
                "query": query,
                "status": status_holder["status"],
                "duration_ms": duration_ms,
                "user_agent": headers.get("user-agent", ""),
            }
            access_logger.info(json.dumps(record, ensure_ascii=False))
