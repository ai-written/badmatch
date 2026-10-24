"""编辑报名中赛事的规则（纯函数，不连库）。

两条规则值得单独守住，因为它们都不是"顺手写的校验"，而是有具体后果的边界：

- **状态只限报名中**：已开赛的赛事赛程已按当时人数排好，改人数/名称不会让赛程跟着变，
  只会让人误以为改完就生效；已结束的赛事是完全只读。注意这里与删除**不同** ——
  超管能跨状态删除，但同样不能跨状态修改（真要改，删掉重建）。
- **最大人数不能小于当前已报名人数**：报名行是既成事实，改到人数之下会得到
  「9/8 人」这种自相矛盾的状态，而且被裁掉的人没有地方可去。
- **总场次必须与人数相容**（4M 能被人数整除，即每人场次相等）：保存时按人数把
  场次就近对齐，别把不整除的值写进库 —— 那种值开赛时只能临时换档，用户会看到
  「共 14 场」突然变成另一个数字。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.tournaments import _align_total_matches, _check_capacity, assert_can_edit
from app.engine.scheduler import compute_match_count
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


# --- 总场次与人数对齐（保存时就把不整除的值修掉）---

def test_total_matches_none_stays_auto():
    """没选总场次（“自动”）就保持自动，别凭空钉一个数。"""
    assert _align_total_matches(8, None) is None


def test_total_matches_kept_when_it_still_fits():
    """能整除就不动 —— 用户选的 20 场在 8 人下合法，凭什么改。"""
    assert _align_total_matches(8, 20) == 20


def test_total_matches_realigned_when_headcount_changed():
    """8 人 14 场改成 12 人：14 不可能均分，就近对齐到 15（不是缩到最小的 3）。"""
    assert _align_total_matches(12, 14) == 15
    assert _align_total_matches(12, 14) != compute_match_count(12)
    assert _align_total_matches(10, 14) == 15


def test_total_matches_alignment_always_yields_equal_play():
    """对齐后的值必须真的合法：4M 能被人数整除，且不超过 100 场上限。"""
    for n in range(4, 41):
        for m in (1, 7, 14, 20, 33, 100):
            got = _align_total_matches(n, m)
            assert (4 * got) % n == 0, (n, m, got)
            assert 1 <= got <= 100, (n, m, got)
