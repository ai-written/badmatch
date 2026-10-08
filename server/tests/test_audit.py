"""core/audit 的写入路径单元测试（纯逻辑，不依赖数据库）。

背景（真实事故）：生产库里 audit_logs 的 id 序列被消耗了 756 次，但只有 543 行 ——
约 27% 的审计写入没有落库，其中一段时间内连续 121 次全部失败；同时有 5 次
返回 200 的认领裁判请求完全没有日志。根因线索丢失，因为失败只打到了容器 stdout，
容器一重建就没了（生产 compose 已挂 ./logs，但 logger 的输出不在里面）。

因此这里钉死几条不允许再退化的性质：
1. 超长 ip/user_agent 必须裁剪而不是让整条 INSERT 失败（PostgreSQL 不截断，直接报错）
2. 写入失败要重试一次，最终仍失败要落盘留痕
3. 关掉开关时不该碰数据库
4. 裁剪用的常量必须与模型列宽一致（改了列宽就必须同步改常量）
"""
import asyncio
import json
from types import SimpleNamespace

# 必须先导入全部模型模块再构造 AuditLog：只导入 audit 时，SQLAlchemy 配置 mapper
# 会因为找不到 Tournament/Round 这些关系目标而抛 InvalidRequestError
# （main.py 里也是同样的一批导入）。
from app.models import audit as _audit_models  # noqa: F401
from app.models import password_reset, round, tournament, user  # noqa: F401

from app.core import audit as audit_mod
from app.core.audit import IP_LIMIT, UA_LIMIT, _fit


def _settings(**over):
    base = {"AUDIT_DB_ENABLED": True, "AUDIT_HIGH_FREQ_ENABLED": False, "AUDIT_LOG_PATH": "logs/access.log"}
    base.update(over)
    return SimpleNamespace(**base)


class _FakeSession:
    """只记录 add() 进来的对象，commit() 直接成功。"""

    def __init__(self, sink):
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.sink.append(obj)

    async def commit(self):
        pass


class _BoomSession:
    async def __aenter__(self):
        raise RuntimeError("boom")

    async def __aexit__(self, *exc):
        return False


def test_fit_keeps_short_and_none():
    assert _fit(None, UA_LIMIT) == (None, False)
    assert _fit("short", UA_LIMIT) == ("short", False)
    assert _fit("x" * UA_LIMIT, UA_LIMIT) == ("x" * UA_LIMIT, False)


def test_fit_truncates_to_limit():
    value, cut = _fit("x" * (UA_LIMIT + 100), UA_LIMIT)
    assert cut is True
    assert len(value) == UA_LIMIT


def test_limits_match_model_columns():
    """裁剪常量与列宽必须一致：不一致时要么又整条丢失，要么白白截断。"""
    from app.models.audit import AuditLog

    assert AuditLog.__table__.c.user_agent.type.length == UA_LIMIT
    assert AuditLog.__table__.c.ip.type.length == IP_LIMIT


def test_long_fields_are_truncated_not_dropped(monkeypatch):
    sink = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: _FakeSession(sink))

    asyncio.run(audit_mod.audit(
        user=None, action="login_failed", ip="1" * 60, user_agent="x" * 400,
    ))

    assert len(sink) == 1, "超长字段不该让整条记录消失"
    assert len(sink[0].user_agent) == UA_LIMIT
    assert len(sink[0].ip) == IP_LIMIT


def test_write_failure_is_retried_then_logged(monkeypatch):
    attempts = []

    def factory():
        attempts.append(1)
        return _BoomSession()

    failures = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", factory)
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)

    # 失败不该把异常抛给业务
    asyncio.run(audit_mod.audit(user=None, action="login_failed"))

    assert len(attempts) == 2, "失败后应重试一次"
    assert len(failures) == 1, "最终失败必须落盘留痕"
    assert "boom" in failures[0]["error"]
    assert failures[0]["action"] == "login_failed"


class _BrokenUser:
    """用户对象已失效（会话关闭/回滚）时，取属性会抛错。"""

    @property
    def id(self):
        raise RuntimeError("detached instance")

    @property
    def username(self):
        raise RuntimeError("detached instance")


def test_broken_user_object_does_not_break_business(monkeypatch):
    """取值本身抛错也不能逸出 audit()：否则业务请求会被打成 500。"""
    sink = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: _FakeSession(sink))

    asyncio.run(audit_mod.audit(user=_BrokenUser(), action="update_profile"))

    assert len(sink) == 1, "仍然要写一条（按匿名）"
    assert sink[0].user_id is None
    assert sink[0].username is None


def test_broken_user_object_in_failure_path_does_not_raise(monkeypatch):
    attempts = []
    failures = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory",
                        lambda: (attempts.append(1), _BoomSession())[1])
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)

    asyncio.run(audit_mod.audit(user=_BrokenUser(), action="update_profile"))

    assert len(attempts) == 2
    assert len(failures) == 1
    assert failures[0]["user_id"] is None


def test_db_disabled_skips_without_touching_db(monkeypatch):
    called = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings(AUDIT_DB_ENABLED=False))
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: called.append(1))

    asyncio.run(audit_mod.audit(user=None, action="login_failed"))

    assert called == []


def test_high_freq_disabled_skips_without_touching_db(monkeypatch):
    """记分/投票默认不记录（AUDIT_HIGH_FREQ_ENABLED=false）—— 这是设计，不是丢日志。"""
    called = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: called.append(1))

    asyncio.run(audit_mod.audit(user=None, action="match_score_update", high_freq=True))

    assert called == []


class _FlakySession:
    """第一次失败、第二次成功：验证「重试能把瞬时故障救回来」这个前提。"""

    def __init__(self, sink):
        self.sink = sink
        self.calls = 0

    async def __aenter__(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.sink.append(obj)

    async def commit(self):
        pass


class _CancelSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        pass

    async def commit(self):
        raise asyncio.CancelledError()


def test_transient_failure_is_recovered_by_retry(monkeypatch):
    sink = []
    failures = []
    # 同一个会话实例驱动两次尝试：第一次 __aenter__ 失败，第二次成功
    session = _FlakySession(sink)
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: session)
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)

    asyncio.run(audit_mod.audit(user=None, action="logout"))

    assert len(sink) == 1, "重试成功应留下记录"
    assert failures == [], "救回来了就不该写失败日志"


def test_slow_failure_is_not_retried(monkeypatch):
    """慢失败（连接池排队/锁等待）不再重试：重试只会把故障放大一倍。"""
    attempts = []
    failures = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory",
                        lambda: (attempts.append(1), _BoomSession())[1])
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)
    # 阈值压成负数 ≡ 这次失败已经耗掉很久
    monkeypatch.setattr(audit_mod, "RETRY_MAX_ELAPSED_SECONDS", -1.0)

    asyncio.run(audit_mod.audit(user=None, action="logout"))

    assert len(attempts) == 1
    assert len(failures) == 1


def test_cancellation_is_recorded_and_reraised(monkeypatch):
    """客户端中断不该被吞掉，但也必须留痕（业务可能已提交）。"""
    failures = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: _CancelSession())
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)

    async def run():
        await audit_mod.audit(user=None, action="logout")

    try:
        asyncio.run(run())
        raised = False
    except asyncio.CancelledError:
        raised = True

    assert raised, "取消必须继续向上传播"
    assert len(failures) == 1
    assert "cancelled" in failures[0]["error"].lower()


class _CommitThenCancelSession:
    """提交成功、取消落在关闭会话期间：审计其实已落库。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        raise asyncio.CancelledError()

    def add(self, obj):
        pass

    async def commit(self):
        pass


def test_cancellation_after_commit_is_not_a_false_alarm(monkeypatch):
    failures = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: _CommitThenCancelSession())
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)

    async def run():
        await audit_mod.audit(user=None, action="logout")

    try:
        asyncio.run(run())
        raised = False
    except asyncio.CancelledError:
        raised = True

    assert raised
    assert failures == [], "已经写成功的不该再记一条「丢失」"


def test_cancellation_during_retry_wait_is_recorded(monkeypatch):
    """取消正好落在重试等待里，也不能出现「无任何痕迹」的丢失。"""
    failures = []
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: _BoomSession())
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)

    async def cancel_sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(audit_mod.asyncio, "sleep", cancel_sleep)

    async def run():
        await audit_mod.audit(user=None, action="logout")

    try:
        asyncio.run(run())
        raised = False
    except asyncio.CancelledError:
        raised = True

    assert raised
    assert len(failures) == 1
    assert "retry wait" in failures[0]["error"]


def test_failure_record_carries_user_agent(monkeypatch):
    """失败记录里要能直接看出 UA —— 否则「是不是超长请求头」这个假设永远验证不了。"""
    failures = []
    long_ua = "UA-1" * 100
    monkeypatch.setattr(audit_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_mod, "async_session_factory", lambda: _BoomSession())
    monkeypatch.setattr(audit_mod, "_append_failure_log", failures.append)

    asyncio.run(audit_mod.audit(user=None, action="login_failed", user_agent=long_ua))

    assert failures[0]["user_agent"] == long_ua[:UA_LIMIT]


def test_failure_log_is_written_to_disk(monkeypatch, tmp_path):
    settings = _settings(AUDIT_LOG_PATH=str(tmp_path / "access.log"))
    monkeypatch.setattr(audit_mod, "get_settings", lambda: settings)

    audit_mod._append_failure_log({"action": "logout", "error": "boom"})

    path = tmp_path / "audit-failures.log"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8").strip())["action"] == "logout"


def test_failure_log_rotates_when_too_large(monkeypatch, tmp_path):
    """系统性故障下它会一直写，必须有上限（转成 .1）。"""
    settings = _settings(AUDIT_LOG_PATH=str(tmp_path / "access.log"))
    monkeypatch.setattr(audit_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(audit_mod, "MAX_FAILURE_LOG_BYTES", 10)

    path = tmp_path / "audit-failures.log"
    path.write_text("x" * 50, encoding="utf-8")

    audit_mod._append_failure_log({"action": "logout", "error": "boom"})

    assert (tmp_path / "audit-failures.log.1").exists()
    assert path.read_text(encoding="utf-8").strip().startswith("{")
