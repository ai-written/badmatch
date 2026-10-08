"""自动结束赛事必须留下 `tournament_end` 审计，且并发收尾要正确（回归测试）。

真实问题一：比赛正常打完（最后一场被裁判结束）时，`_maybe_finish_tournament` 会静默把
赛事置为 finished —— 这是**最常见的结束路径**，却从来不写审计。于是管理面板按
「结束赛事」筛选时一条都查不到，看起来就像日志丢了。

真实问题二（并发）：两场「最后比赛」几乎同时结束时，双方都可能在对方提交前数到
「还剩 1 场」而各自返回，赛事永远停在 ongoing（既没有 tournament_end 也没有广播）。
修法是「先提交本场结果、释放 match 行锁 → 锁住 tournaments 行 → 才数剩余场次」。

这里用假 db 钉死调用顺序，并**校验语句本身**（锁 / 计数条件）——只断言"调用了 execute"
是不够的：漏掉 FOR UPDATE、或者把计数条件写反，功能测试照样全绿。
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.api import matches as matches_mod
from app.models.tournament import Tournament, TournamentStatus


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    """按调用顺序返回「赛事（已加锁）」和「未完成场次计数」，并把动作记进 events。

    结果用尽后再被调用会抛 IndexError —— 即"这一步不该被调用"是真的会被抓到的守卫。
    """

    def __init__(self, tournament, remaining, events=None):
        self._results = [_Result(tournament), _Result(remaining)]
        self._labels = ["lock-tournament", "count-unfinished"]
        self.events = events if events is not None else []

    async def execute(self, stmt):
        label = self._labels.pop(0)
        if label == "lock-tournament":
            assert stmt._for_update_arg is not None, "取赛事行必须带 FOR UPDATE"
        else:
            assert "matches.status !=" in str(stmt), f"只该数未完成的比赛，实际：{stmt}"
        self.events.append(label)
        return self._results.pop(0)

    async def flush(self):
        self.events.append("flush")

    async def commit(self):
        self.events.append("commit")


def _tournament(status=TournamentStatus.ONGOING):
    now = datetime(2026, 10, 8, 19, 0, 0)
    # status 显式给值：ORM 的 default 只在 INSERT 时生效，不赋值时这里是 None
    return Tournament(
        creator_id=1, title="测试赛事", start_date=now, end_date=now + timedelta(hours=3),
        max_participants=8, status=status,
    )


def _patch(monkeypatch, events):
    recorded = []
    broadcasts = []

    async def fake_audit(**kwargs):
        recorded.append(kwargs)
        events.append("audit")

    async def fake_broadcast(tournament_id, message):
        broadcasts.append((tournament_id, message))
        events.append("broadcast")

    monkeypatch.setattr(matches_mod, "audit", fake_audit)
    monkeypatch.setattr(matches_mod.manager, "broadcast", fake_broadcast)
    return recorded, broadcasts


def test_auto_finish_writes_tournament_end_audit(monkeypatch):
    t = _tournament()
    events = []
    db = _FakeDB(tournament=t, remaining=0, events=events)
    recorded, broadcasts = _patch(monkeypatch, events)

    asyncio.run(matches_mod._maybe_finish_tournament(9, db, user=None, request=None))

    assert t.status == TournamentStatus.FINISHED
    assert len(recorded) == 1, "自动结束必须写一条审计"
    assert recorded[0]["action"] == "tournament_end"
    assert recorded[0]["target_id"] == 9
    assert recorded[0]["detail"] == {"auto": True, "reason": "all_matches_finished"}
    assert broadcasts == [(9, {"type": "tournament_finished"})]
    # 顺序：提交（释放 match 行锁）→ 锁赛事行 → 数剩余场次 → 置位+提交 → 广播 → 审计。
    # - 必须先提交再锁赛事行：否则与 withdraw（先锁 tournaments 再删 matches）反向加锁死锁
    # - 必须锁之后再计数：否则两场"最后比赛"并发时双方都数到"还剩 1 场"而漏收尾
    # - 审计在提交和广播之后：提交前写会留假日志；广播前写会被请求取消连带跳过
    assert events == [
        "commit", "lock-tournament", "count-unfinished",
        "flush", "commit", "broadcast", "audit",
    ], events


def test_still_has_unfinished_matches_is_not_touched(monkeypatch):
    """还有没打完的比赛：不改状态、不写审计、不广播（但事务边界已被本函数提交）。"""
    t = _tournament()
    events = []
    db = _FakeDB(tournament=t, remaining=3, events=events)
    recorded, broadcasts = _patch(monkeypatch, events)

    asyncio.run(matches_mod._maybe_finish_tournament(9, db, user=None, request=None))

    assert t.status == TournamentStatus.ONGOING
    assert recorded == []
    assert broadcasts == []
    assert events == ["commit", "lock-tournament", "count-unfinished"], events


def test_already_finished_tournament_is_not_counted(monkeypatch):
    """赛事已被别人收尾（拿到锁才发现）：直接返回，不再数场次、不再写审计。"""
    t = _tournament(status=TournamentStatus.FINISHED)
    events = []
    db = _FakeDB(tournament=t, remaining=0, events=events)
    recorded, broadcasts = _patch(monkeypatch, events)

    asyncio.run(matches_mod._maybe_finish_tournament(9, db, user=None, request=None))

    assert recorded == []
    assert broadcasts == []
    # 只到"锁赛事行"为止：计数这一步根本不该发生
    assert events == ["commit", "lock-tournament"], events


def test_missing_tournament_is_a_noop(monkeypatch):
    events = []
    db = _FakeDB(tournament=None, remaining=0, events=events)
    recorded, broadcasts = _patch(monkeypatch, events)

    asyncio.run(matches_mod._maybe_finish_tournament(9, db, user=None, request=None))

    assert recorded == []
    assert broadcasts == []
    assert events == ["commit", "lock-tournament"], events


def test_audit_carries_ip_and_user_agent(monkeypatch):
    """触发者与来源要落到审计里（自动结束的 detail.auto 负责标明是系统收尾）。"""
    t = _tournament()
    events = []
    db = _FakeDB(tournament=t, remaining=0, events=events)
    recorded, _ = _patch(monkeypatch, events)
    request = SimpleNamespace(
        headers={"user-agent": "UA-test"},
        client=SimpleNamespace(host="10.0.0.9"),
    )

    asyncio.run(matches_mod._maybe_finish_tournament(9, db, user=None, request=request))

    assert recorded[0]["ip"] == "10.0.0.9"
    assert recorded[0]["user_agent"] == "UA-test"
