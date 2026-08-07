"""
API 响应 ETag 中间件。

为 GET 接口的 2xx 响应计算弱 ETag（W/"..."，适配 nginx gzip 后表示），并在浏览器携带 If-None-Match 且内容未变时
返回 304（不传 body）。配合 `Cache-Control: private, no-cache`，重复访问时
只传输几百字节的协商头，显著降低低带宽场景下的 API 流量。

- 只处理 GET（HEAD 无 body，跳过）
- 跳过 /static、/ws、/api/health
- 非 2xx 或空 body 不处理
- 纯 ASGI 实现，不依赖 Starlette/FastAPI 类型，与 AccessLogMiddleware 一致
"""
import hashlib

_SKIP_PREFIXES = ("/static", "/ws", "/api/health")
_CACHE_CONTROL = "private, no-cache"


class ETagMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if method != "GET" or path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        state = {"status": 500, "headers": None}
        body = bytearray()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["headers"] = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        # 先缓冲完整响应（本项目 GET 均为小体积 JSON，无流式接口）
        await self.app(scope, receive, send_wrapper)

        headers = state["headers"]
        if headers is None or not (200 <= state["status"] < 300) or not body:
            if headers is not None:
                await send({
                    "type": "http.response.start",
                    "status": state["status"],
                    "headers": headers,
                })
                await send({"type": "http.response.body", "body": bytes(body), "more_body": False})
            return

        etag = 'W/"' + hashlib.sha1(bytes(body)).hexdigest() + '"'
        req_headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        if_none_match = req_headers.get("if-none-match")
        matched = False
        if if_none_match:
            matched = etag in [tag.strip() for tag in if_none_match.split(",")]

        if matched:
            await send({
                "type": "http.response.start",
                "status": 304,
                "headers": [
                    (b"etag", etag.encode("latin-1")),
                    (b"cache-control", _CACHE_CONTROL.encode("latin-1")),
                ],
            })
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        final_headers = [
            (k, v) for k, v in headers
            if k.lower() not in (b"etag", b"cache-control")
        ]
        final_headers.append((b"etag", etag.encode("latin-1")))
        final_headers.append((b"cache-control", _CACHE_CONTROL.encode("latin-1")))
        await send({
            "type": "http.response.start",
            "status": state["status"],
            "headers": final_headers,
        })
        await send({"type": "http.response.body", "body": bytes(body), "more_body": False})
