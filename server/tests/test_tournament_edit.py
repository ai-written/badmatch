"""编辑报名中赛事的规则（纯函数，不连库）。

两条规则值得单独守住，因为它们都不是"顺手写的校验"，而是有具体后果的边界：

- **状态只限报名中**：已开赛的赛事赛程已按当时人数排好，改人数/名称不会让赛程跟着变，
  只会让人误以为改完就生效；已结束的赛事是完全只读。注意这里与删除**不同** ——
  超管能跨状态删除，但同样不能跨状态修改（真要改，删掉重建）。
- **最大人数不能小于当前已报名人数**：报名行是既成事实，改到人数之下会得到
  「9/8 人」这种自相矛盾的状态，而且被裁掉的人没有地方可去。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.tournaments import _check_capacity, assert_can_edit
from app.models.tournament import TournamentStatus


def _user(uid: int, role: str) -> SimpleNamespace:
    return SimpleNamespace(id=uid, role=role)


def _t(status: TournamentStatus, creator_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(status=status, creator_id=creator_id)


def _status_of(exc_info) -> int:
    return exc_info.value.status_code


# --- 权限与状态 ---

def test_creator_can_edit_open():
    assert_can_edit(_t(TournamentStatus.OPEN), _user(1, "user"))


@pytest.mark.parametrize("status", [TournamentStatus.ONGOING, TournamentStatus.FINISHED])
def test_creator_cannot_edit_after_start(status):
    with pytest.raises(HTTPException) as e:
        assert_can_edit(_t(status), _user(1, "user"))
    assert _status_of(e) == 400


def test_admin_can_edit_others_open_tournament():
    assert_can_edit(_t(TournamentStatus.OPEN, creator_id=9), _user(2, "admin"))


@pytest.mark.parametrize("status", [TournamentStatus.ONGOING, TournamentStatus.FINISHED])
def test_superadmin_is_also_limited_to_open(status):
    """与删除刻意不同：超管能跨状态删，但不能跨状态改（赛程不会跟着变）。"""
    with pytest.raises(HTTPException) as e:
        assert_can_edit(_t(status, creator_id=9), _user(2, "superadmin"))
    assert _status_of(e) == 400


def test_stranger_cannot_edit():
    with pytest.raises(HTTPException) as e:
        assert_can_edit(_t(TournamentStatus.OPEN, creator_id=9), _user(2, "user"))
    assert _status_of(e) == 403


# --- 最大人数下限 ---

def test_capacity_floor_is_four():
    with pytest.raises(HTTPException) as e:
        _check_capacity(3, 0)
    assert _status_of(e) == 400


def test_capacity_may_equal_registered_count():
    """正好等于已报名人数是允许的：人满了就停止报名，这正是下调上限的常见用途。"""
    _check_capacity(8, 8)


def test_capacity_below_registered_count_is_rejected():
    with pytest.raises(HTTPException) as e:
        _check_capacity(5, 8)
    assert _status_of(e) == 400
    # 报错里必须带上当前人数，否则用户不知道该填多少
    assert "8" in str(e.value.detail)


def test_capacity_raise_is_fine_when_nobody_registered():
    _check_capacity(16, 0)


def test_capacity_checks_registered_before_anything_else():
    """下限 4 仍然优先：既低于 4 又低于已报名人数时报「至少 4 人」这条更好懂。"""
    with pytest.raises(HTTPException) as e:
        _check_capacity(3, 8)
    assert "4" in str(e.value.detail)
