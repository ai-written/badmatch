"""报名海报二维码接口的接线测试。

不连数据库：认证依赖与 get_db 都被替换成假的，只验证路由、鉴权挂载、响应类型
与 404 语义这些「接线」层面的东西（URL 拼装规则本身见 test_poster.py）。
"""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.poster import poster_url, qr_png, router
from app.core.database import get_db
from app.core.security import require_user

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class _FakeUser:
    id = 1
    username = "tester"
    role = "user"
    invite_code = "ABC123"


class _FakeResult:
    """接口只用到 .first()（一次取 id + status 两列）。"""

    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._row)


def _client(row) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: _FakeUser()
    app.dependency_overrides[get_db] = lambda: _FakeSession(row)
    return TestClient(app)


ROW = SimpleNamespace(id=16, status="ongoing")


def test_poster_qr_returns_png():
    res = _client(ROW).get("/api/tournaments/16/poster-qr")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == PNG_MAGIC


def test_poster_qr_404_when_tournament_missing():
    res = _client(None).get("/api/tournaments/16/poster-qr")
    assert res.status_code == 404


def test_poster_qr_encodes_schedule_url_after_start():
    """按字节比对：接口出的图必须正好是「对阵表」那个地址编出来的码。

    没有解码器也能验证内容 —— 同样的入参自己编一张，逐字节相同即说明编码的 URL 一致。
    """
    res = _client(SimpleNamespace(id=16, status="ongoing")).get("/api/tournaments/16/poster-qr")
    assert res.content == qr_png(poster_url(16, "ABC123", status="ongoing"))


def test_poster_qr_encodes_signup_url_while_open():
    res = _client(SimpleNamespace(id=16, status="open")).get("/api/tournaments/16/poster-qr")
    assert res.content == qr_png(poster_url(16, "ABC123", status="open"))
