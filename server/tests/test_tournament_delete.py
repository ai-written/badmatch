"""删除赛事的权限判定（纯函数，不连库）。

规则：创建者/管理员/超管都能删，但默认只限「报名中」；超级管理员可跨状态删除
（已开赛/已结束的赛事会连轮次、比赛、战绩一起级联删除，所以只有超管能碰）。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.tournaments import assert_can_delete
from app.models.tournament import TournamentStatus


def _user(uid: int, role: str) -> SimpleNamespace:
    return SimpleNamespace(id=uid, role=role)


def _t(status: TournamentStatus, creator_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(status=status, creator_id=creator_id)


def _status_of(exc_info) -> int:
    return exc_info.value.status_code


def test_creator_can_delete_open():
    assert_can_delete(_t(TournamentStatus.OPEN), _user(1, "user"))


def test_creator_cannot_delete_ongoing():
    with pytest.raises(HTTPException) as e:
        assert_can_delete(_t(TournamentStatus.ONGOING), _user(1, "user"))
    assert _status_of(e) == 400


def test_admin_can_delete_others_open_tournament():
    assert_can_delete(_t(TournamentStatus.OPEN, creator_id=9), _user(2, "admin"))


def test_admin_cannot_delete_finished():
    with pytest.raises(HTTPException) as e:
        assert_can_delete(_t(TournamentStatus.FINISHED, creator_id=9), _user(2, "admin"))
    assert _status_of(e) == 400


def test_superadmin_can_delete_any_status():
    """超管是唯一能跨状态删的角色 —— 建错的局、脏数据总得有人收尾。"""
    for status in (TournamentStatus.OPEN, TournamentStatus.ONGOING, TournamentStatus.FINISHED):
        assert_can_delete(_t(status, creator_id=9), _user(2, "superadmin"))


def test_stranger_cannot_delete():
    with pytest.raises(HTTPException) as e:
        assert_can_delete(_t(TournamentStatus.OPEN, creator_id=9), _user(2, "user"))
    assert _status_of(e) == 403
