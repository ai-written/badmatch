"""core/etag 中间件单元测试（纯 ASGI，不依赖 FastAPI）。"""
import asyncio

import pytest

from app.core.etag import ETagMiddleware


def _json_app(body: bytes, status: int = 200):
    async def app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return app


def _run(middleware, path, method="GET", request_headers=None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (request_headers or {}).items()
        ],
        "client": ("127.0.0.1", 12345),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    async def main():
        await middleware(scope, receive, send)

    asyncio.run(main())
    return messages


def _headers_of(messages):
    start = next(m for m in messages if m["type"] == "http.response.start")
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]}


def test_get_adds_etag_and_cache_control():
    body = b'{"a": 1}'
    mw = ETagMiddleware(_json_app(body))
    messages = _run(mw, "/api/tournaments")
    headers = _headers_of(messages)

    assert messages[0]["status"] == 200
    assert headers["etag"].startswith('W/"')
    assert headers["cache-control"] == "private, no-cache"
    assert messages[-1]["body"] == body


def test_if_none_match_returns_304():
    body = b'{"a": 1}'
    mw = ETagMiddleware(_json_app(body))
    first = _run(mw, "/api/tournaments")
    etag = _headers_of(first)["etag"]

    second = _run(mw, "/api/tournaments", request_headers={"If-None-Match": etag})
    assert second[0]["status"] == 304
    assert second[-1]["body"] == b""
    assert "content-length" not in _headers_of(second)


def test_mismatched_etag_returns_full_body():
    body = b'{"a": 1}'
    mw = ETagMiddleware(_json_app(body))
    messages = _run(mw, "/api/tournaments", request_headers={"If-None-Match": '"deadbeef"'})
    assert messages[0]["status"] == 200
    assert messages[-1]["body"] == body


def test_non_get_passthrough():
    body = b"{}"
    mw = ETagMiddleware(_json_app(body))
    messages = _run(mw, "/api/tournaments", method="POST")
    assert messages[0]["status"] == 200
    assert "etag" not in _headers_of(messages)


def test_static_passthrough():
    body = b"\xff\xd8\xff"
    mw = ETagMiddleware(_json_app(body))
    messages = _run(mw, "/static/uploads/x.jpg")
    assert "etag" not in _headers_of(messages)


def test_error_response_passthrough():
    body = b'{"detail": "not found"}'
    mw = ETagMiddleware(_json_app(body, status=404))
    messages = _run(mw, "/api/nope")
    assert messages[0]["status"] == 404
    assert "etag" not in _headers_of(messages)
