"""创建赛事时的默认名称：`10月16日养生局`。

两段都测，且都不连库：
1. `default_title_for`（纯函数）—— 名字怎么拼、撞名怎么加序号；
2. `default_title`（接口）—— 查询到底把哪些行算作"已占用"。**这段以前是空的**，
   而"别的日期/别的年份不抢序号"恰恰是查询负责的：只按名字前缀查的话，
   「10月16日养生局」里没有年份，去年那场同名赛事会让今年的默认名从（2）起步。

老格式「十月第N次养生局」按月计数，同一天排两场看不出是哪天、跨月还在继续涨；
改成直接带日期，日期跟着创建页选中的那天走（默认本周五）。
"""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.tournaments import default_title, default_title_for


# --------------------------------------------------------------- 纯函数

def test_title_uses_month_and_day_without_leading_zero():
    assert default_title_for(date(2026, 10, 16)) == "10月16日养生局"
    assert default_title_for(date(2026, 1, 5)) == "1月5日养生局"
    assert default_title_for(date(2026, 12, 31)) == "12月31日养生局"


def test_weekday_is_not_in_the_title():
    # 2026-10-16 周五、2026-10-17 周六：只按日期编号，不带星期几
    assert default_title_for(date(2026, 10, 17)) == "10月17日养生局"


def test_second_tournament_same_day_gets_suffix():
    assert default_title_for(date(2026, 10, 16), ["10月16日养生局"]) == "10月16日养生局（2）"
    assert (
        default_title_for(date(2026, 10, 16), ["10月16日养生局", "10月16日养生局（2）"])
        == "10月16日养生局（3）"
    )


def test_suffix_skips_used_numbers():
    # 删掉过（2）时不要生成重复名
    existing = ["10月16日养生局", "10月16日养生局（3）"]
    assert default_title_for(date(2026, 10, 16), existing) == "10月16日养生局（2）"


def test_renamed_tournament_with_same_prefix_does_not_force_a_suffix():
    # 「10月16日养生局加练」跟默认名并不相同，不必加序号——只有**完全同名**才加
    assert default_title_for(date(2026, 10, 16), ["10月16日养生局加练"]) == "10月16日养生局"


def test_other_dates_do_not_consume_the_number():
    existing = ["10月15日养生局", "10月16日养生局", "10月16日养生局（2）"]
    assert default_title_for(date(2026, 10, 17), existing) == "10月17日养生局"


# --------------------------------------------------------------- 接口查询

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """记下 execute 收到的语句并按给定行集返回；不连库、不执行 SQL。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _FakeResult(self.rows)


def _call(date_str: str, rows=()):
    db = _FakeSession(rows)
    title = asyncio.run(default_title(date=date_str, db=db, user=SimpleNamespace(id=1)))
    assert len(db.stmts) == 1
    return title, db.stmts[0]


def _compiled(stmt):
    return stmt.compile(dialect=postgresql.dialect())


def test_endpoint_returns_base_name_when_day_is_free():
    title, _ = _call("2026-10-16")
    assert title == {"title": "10月16日养生局"}


def test_endpoint_suffixes_when_the_same_day_is_taken():
    title, _ = _call("2026-10-16", ["10月16日养生局"])
    assert title == {"title": "10月16日养生局（2）"}


def test_endpoint_query_is_scoped_to_that_day_not_just_the_name():
    """只按名字前缀查会跨年串味：去年 10/16 那场会让今年的默认名从（2）起步。"""
    _, stmt = _call("2026-10-16", ["10月16日养生局"])
    compiled = _compiled(stmt)
    sql = str(compiled)

    assert "tournaments.title LIKE" in sql
    assert "date(tournaments.start_date)" in sql, "必须按开始日期限定同一天"
    params = list(compiled.params.values())
    assert "10月16日养生局%" in params
    assert date(2026, 10, 16) in params, "比较的日期要是请求里那一天"
    # 去年的同名赛事（2025-10-16）不会被这条语句捞出来
    assert date(2025, 10, 16) not in params


def test_endpoint_without_date_uses_today():
    # 不传 date：走 date.today()，只能断言"今天几号就用几月几号"
    today = date.today()
    db = _FakeSession()
    result = asyncio.run(default_title(date=None, db=db, user=SimpleNamespace(id=1)))
    assert result == {"title": f"{today.month}月{today.day}日养生局"}
    assert today in list(_compiled(db.stmts[0]).params.values())


def test_endpoint_rejects_malformed_date():
    with pytest.raises(HTTPException) as e:
        asyncio.run(default_title(date="2026-13-99", db=_FakeSession(), user=SimpleNamespace(id=1)))
    assert e.value.status_code == 400
