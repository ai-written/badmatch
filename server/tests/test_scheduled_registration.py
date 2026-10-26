"""定时开放报名的时间校验（纯函数，不连库）。

规则只有一条：**启用时必须给未来时间；未启用一律立即开放（写 NULL）**。
创建与编辑两条路径共用 `_resolve_registration_open_at`，所以"编辑时把开关关掉 =
现在就能报名"这类语义只在一个地方定义，不会两边漂移。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.tournaments import _resolve_registration_open_at
from app.schemas.tournament import SITE_TZ, TournamentCreate


def _data(**over) -> TournamentCreate:
    base = dict(
        title="定时报名",
        start_date=datetime(2026, 11, 6, 19, 0),
        end_date=datetime(2026, 11, 6, 21, 0),
        max_participants=8,
    )
    base.update(over)
    return TournamentCreate(**base)


def test_disabled_always_means_open_now():
    """开关关着就写 NULL（= 立即开放），哪怕请求里带了个时间也不理。"""
    got = _resolve_registration_open_at(
        _data(enable_scheduled_registration=False, registration_open_at=datetime(2027, 1, 1, 10, 0))
    )
    assert got is None


def test_future_time_is_kept_as_is():
    future = datetime.now() + timedelta(hours=2)
    got = _resolve_registration_open_at(
        _data(enable_scheduled_registration=True, registration_open_at=future)
    )
    assert got == future


def test_past_time_is_rejected():
    """过去时间必须明确拒绝：否则库里会存一个"打开就立刻生效"的假定时。

    编辑页"开着表单跨过开放时刻再保存"时前端会先把开关关掉（按立即开放提交），
    所以这个 400 只该在直接调接口时出现。
    """
    with pytest.raises(HTTPException) as e:
        _resolve_registration_open_at(
            _data(enable_scheduled_registration=True,
                  registration_open_at=datetime.now() - timedelta(minutes=1))
        )
    assert e.value.status_code == 400
    assert "必须晚于当前时间" in str(e.value.detail)


def test_enabled_without_a_time_is_rejected():
    with pytest.raises(HTTPException) as e:
        _resolve_registration_open_at(_data(enable_scheduled_registration=True))
    assert e.value.status_code == 400
    assert "必须选择开放时间" in str(e.value.detail)


def test_aware_time_is_normalized_to_site_naive():
    """带时区的输入要换算成站点本地 naive 时间（DB 列是 naive TIMESTAMP）。"""
    aware = datetime.now(timezone.utc) + timedelta(hours=3)
    got = _resolve_registration_open_at(
        _data(enable_scheduled_registration=True, registration_open_at=aware)
    )
    assert got is not None
    assert got.tzinfo is None
    expect = aware.astimezone(SITE_TZ).replace(tzinfo=None)
    assert abs((got - expect).total_seconds()) < 1, (got, expect)
