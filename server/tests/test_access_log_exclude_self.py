"""访问日志「排除自己」筛选（GET /api/auth/admin/access-logs?exclude_self=true）。

背景：管理面板每翻一页、每打开一次访问详情，都会往 access.log 里写一条自己的
记录；不排除自己的话，看第二页就已经全是自己刚才翻页留下的噪音，根本看不出
「别人在干什么」。因此这条筛选要钉死以下几点：

1. 默认（不传 exclude_self）行为不变，全量返回；
2. exclude_self=true 时只剔除**本人**的记录，别人的一条都不能少；
3. 本人判定以 user_id 为准（改名不影响），缺 user_id 的老记录才退回比用户名；
4. 匿名记录（user_id/username 都没有）绝不算本人 —— 否则管理员一开筛选，
   登录前的 401、预检请求这些「谁在敲门」的证据会一起消失（那正是最该看的部分）；
5. 与关键词/方法等既有筛选叠加时，分页的 total 必须是「过滤后的条数」。
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth as auth_mod
from app.core.database import get_db
from app.core.security import require_user

ADMIN_ID, ADMIN_NAME = 1, "boss"
OTHER_ID, OTHER_NAME = 2, "alice"
ADMIN = SimpleNamespace(id=ADMIN_ID, username=ADMIN_NAME, role="superadmin")
OTHER = SimpleNamespace(id=OTHER_ID, username=OTHER_NAME)


def _rec(*, uid=ADMIN_ID, uname=ADMIN_NAME, method="GET", path="/api/x",
         status=200, time="2026-10-27T10:00:00+08:00"):
    """一条访问日志记录；uid/uname 传 None 表示键存在但值为空。"""
    return {
        "time": time,
        "ip": "1.2.3.4",
        "user_id": uid,
        "username": uname,
        "method": method,
        "path": path,
        "query": "",
        "status": status,
        "duration_ms": 1.0,
        "user_agent": "pytest",
    }


@pytest.fixture
def log_file(tmp_path):
    """默认日志内容：本人 3 条（含 1 条只有用户名、没有 user_id 的老格式）、
    别人 2 条、匿名 1 条。"""
    legacy = {"username": ADMIN_NAME, "method": "GET", "path": "/api/legacy", "status": 200}
    lines = [
        _rec(method="GET", path="/api/self-1"),
        _rec(method="POST", path="/api/self-2"),
        legacy,
        _rec(uid=OTHER_ID, uname=OTHER_NAME, path="/api/other-1"),
        _rec(uid=OTHER_ID, uname=OTHER_NAME, method="DELETE", path="/api/other-2"),
        _rec(uid=None, uname=None, path="/api/anon", status=401),
    ]
    p = tmp_path / "access.log"
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    return p


@pytest.fixture
def client(log_file, monkeypatch):
    """只挂 auth 路由的最小应用：绕过鉴权/数据库，日志路径指向临时文件。"""
    monkeypatch.setattr(auth_mod, "get_settings", lambda: SimpleNamespace(AUDIT_LOG_PATH=str(log_file)))
    app = FastAPI()
    app.include_router(auth_mod.router)

    class _NoDb:
        """本测试的记录都带 username，不该触发「补齐老记录用户名」的查库。"""

        async def execute(self, *a, **kw):
            raise AssertionError("不应触发查库")

    async def _fake_db():
        yield _NoDb()

    app.dependency_overrides[require_user] = lambda: ADMIN
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


def _query(client, **params):
    res = client.get("/api/auth/admin/access-logs", params={"page_size": 100, **params})
    assert res.status_code == 200, res.text
    return res.json()


# --- 1. 判定函数本身 -----------------------------------------------------

def test_self_by_user_id():
    assert auth_mod._is_self_access(_rec(), ADMIN) is True
    assert auth_mod._is_self_access(_rec(uid=OTHER_ID, uname=OTHER_NAME), ADMIN) is False


def test_self_by_user_id_tolerates_string_ids():
    """中间件写的是 int，但容忍旧脚本/手工写入的字符串形式。"""
    assert auth_mod._is_self_access(_rec(uid=str(ADMIN_ID), uname=None), ADMIN) is True


def test_self_falls_back_to_username_only_without_user_id():
    # 缺键 / 为空 → 退回比名字
    assert auth_mod._is_self_access({"username": ADMIN_NAME}, ADMIN) is True
    assert auth_mod._is_self_access({"username": OTHER_NAME}, ADMIN) is False
    # 有 user_id 时不比名字：同名不能误伤，改过名的历史记录也不靠名字兜底
    assert auth_mod._is_self_access({"user_id": OTHER_ID, "username": ADMIN_NAME}, ADMIN) is False


def test_anonymous_record_is_never_self():
    """匿名请求谁都不是：管理员自己也会打出没带 token 的记录，但那不算「自己」。"""
    assert auth_mod._is_self_access({"user_id": None, "username": None}, ADMIN) is False
    assert auth_mod._is_self_access({}, ADMIN) is False


# --- 2. 接口行为 ---------------------------------------------------------

def test_default_keeps_everything(client):
    data = _query(client)
    assert data["total"] == 6
    assert len(data["items"]) == 6


def test_exclude_self_drops_only_own_records(client):
    data = _query(client, exclude_self="true")
    paths = [it["path"] for it in data["items"]]
    # 本人 3 条（含只带用户名的那条老记录）全部剔除，别人与匿名记录保留
    assert data["total"] == 3
    assert paths == ["/api/anon", "/api/other-2", "/api/other-1"]  # 最新在前
    assert "/api/self-1" not in paths and "/api/legacy" not in paths


def test_exclude_self_accepts_false(client):
    assert _query(client, exclude_self="false")["total"] == 6


def test_exclude_self_combines_with_method_and_keyword(client):
    data = _query(client, exclude_self="true", method="DELETE")
    assert data["total"] == 1
    assert data["items"][0]["path"] == "/api/other-2"

    # 自己的都被排除了，命中自己路径/用户名的关键词自然查不到
    assert _query(client, exclude_self="true", keyword="self")["total"] == 0
    assert _query(client, exclude_self="true", keyword="boss")["total"] == 0


def test_exclude_self_pagination_total_is_filtered_total(client):
    """total 必须是过滤后的条数，否则前端 van-list 会一直以为「还有更多」而空转。"""
    res = client.get("/api/auth/admin/access-logs",
                     params={"exclude_self": "true", "page": 1, "page_size": 2})
    body = res.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
