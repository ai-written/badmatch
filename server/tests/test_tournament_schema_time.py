"""TournamentCreate 的时间字段：带时区偏移的输入必须换算成本地 naive，而不是 500。

真实问题（冒烟时踩到）：`POST /api/tournaments` 收到 `2026-10-20T19:00:00+08:00`
会直接 500 —— DB 列是 naive TIMESTAMP，asyncpg 报
`DataError: can't subtract offset-naive and offset-aware datetimes`。
前端发的本来就是 naive 本地时间，所以只有直接调接口的脚本/第三方会踩到。
"""
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.tournament import TournamentCreate


def _payload(**over):
    base = {
        "title": "测试赛事",
        "start_date": "2026-10-20T19:00:00",
        "end_date": "2026-10-20T22:00:00",
        "max_participants": 8,
    }
    base.update(over)
    return base


def test_naive_datetimes_unchanged():
    m = TournamentCreate(**_payload())
    assert m.start_date == datetime(2026, 10, 20, 19, 0)
    assert m.start_date.tzinfo is None
    assert m.end_date == datetime(2026, 10, 20, 22, 0)


def test_offset_datetimes_converted_to_site_naive():
    # UTC 11:00 == 北京 19:00（站点时区 Asia/Shanghai，无夏令时）
    m = TournamentCreate(**_payload(
        start_date="2026-10-20T11:00:00Z", end_date="2026-10-20T14:00:00Z",
    ))
    assert m.start_date == datetime(2026, 10, 20, 19, 0)
    assert m.start_date.tzinfo is None
    assert m.end_date == datetime(2026, 10, 20, 22, 0)


def test_explicit_offset_matches_site_time():
    m = TournamentCreate(**_payload(start_date="2026-10-20T19:00:00+08:00"))
    assert m.start_date == datetime(2026, 10, 20, 19, 0), "同一时刻不该被平移"
    assert m.start_date.tzinfo is None


def test_mixed_aware_and_naive_does_not_explode():
    """一个带偏移一个不带：以前比较会抛 TypeError（也被算成 500），现在都能比较。"""
    m = TournamentCreate(**_payload(start_date="2026-10-20T11:00:00Z"))
    assert m.start_date == datetime(2026, 10, 20, 19, 0)
    assert m.end_date == datetime(2026, 10, 20, 22, 0)


def test_registration_open_at_also_normalized():
    m = TournamentCreate(**_payload(
        enable_scheduled_registration=True,
        registration_open_at="2026-10-19T11:00:00Z",
    ))
    assert m.registration_open_at == datetime(2026, 10, 19, 19, 0)
    assert m.registration_open_at.tzinfo is None


def test_end_before_start_still_rejected():
    with pytest.raises(ValidationError):
        TournamentCreate(**_payload(end_date="2026-10-20T18:00:00"))
