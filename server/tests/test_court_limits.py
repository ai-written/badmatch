"""「每片场地至少 5 人」这条规则的单元测试。

为什么值得单独测：这条规则同时被**创建/编辑赛事**和**开赛**两处用来挡人，
口径一旦分叉，就会出现「界面让建、开赛被拒」或者反过来「建得出来但没人执裁」
（9 个人开 2 片场地 = 8 人上场、只剩 1 个场下，两场里必有一场没人吹）。
"""
import pytest
from fastapi import HTTPException

from app.api.tournaments import MAX_COURTS, _check_courts
from app.engine.scheduler import PLAYERS_PER_COURT, courts_allowed


def _courts(count):
    """造 count 个场地对象（只关心数量）。"""
    return [object() for _ in range(count)]


def test_every_court_needs_five_players():
    assert PLAYERS_PER_COURT == 5

    assert courts_allowed(10, 2) and courts_allowed(12, 2)
    assert not courts_allowed(9, 2), "9 人 2 片场地：场下只剩 1 人，两场里必有一场没人执裁"
    assert courts_allowed(15, 3)
    assert not courts_allowed(14, 3)


def test_single_court_has_no_minimum_beyond_four():
    """一片场地不设这条：4 人打满全场、没有裁判是既有的现实用法。"""
    assert courts_allowed(4, 1)
    assert courts_allowed(4, 0)
    assert courts_allowed(4, None)


def test_check_courts_allows_the_normal_cases():
    _check_courts([], 4)              # 没填场地号
    _check_courts(_courts(1), 4)      # 4 人一片场地
    _check_courts(_courts(1), 14)
    _check_courts(_courts(2), 10)     # 2 片场地刚好 10 人
    _check_courts(_courts(2), 12)
    _check_courts(_courts(3), 15)


def test_check_courts_rejects_too_few_players():
    with pytest.raises(HTTPException) as exc:
        _check_courts(_courts(2), 9)
    assert exc.value.status_code == 400
    assert "10 人" in exc.value.detail, "报错要说清到底需要多少人"

    with pytest.raises(HTTPException):
        _check_courts(_courts(3), 14)


def test_check_courts_rejects_too_many_courts():
    with pytest.raises(HTTPException) as exc:
        _check_courts(_courts(MAX_COURTS + 1), 100)
    assert exc.value.status_code == 400
    assert str(MAX_COURTS) in exc.value.detail
