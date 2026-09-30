"""报名海报二维码接口的接线测试。

不连数据库：认证依赖与 get_db 都被替换成假的，只验证路由、鉴权挂载、
响应类型与 404 语义这些"接线"层面的东西（纯函数本身见 test_poster.py 前半部分）。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.poster import router
from app.core.database import get_db
from app.core.security import require_user

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class _FakeUser:
    id = 1
    username = "tester"
    role = "user"
    invite_code = "ABC123"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, tournament_exists: bool):
        self._exists = tournament_exists

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(1 if self._exists else None)


def _client(tournament_exists: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: _FakeUser()
    app.dependency_overrides[get_db] = lambda: _FakeSession(tournament_exists)
    return TestClient(app)


def test_poster_qr_returns_png():
    res = _client().get("/api/tournaments/16/poster-qr")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == PNG_MAGIC


def test_poster_qr_404_when_tournament_missing():
    res = _client(tournament_exists=False).get("/api/tournaments/16/poster-qr")
    assert res.status_code == 404
