"""场地名校验：空白场地名必须被挡住。

真实问题（线上被问过）：某赛事详情页「场地号」只显示了一个名字（21号），
但赛程卡片上「这场在哪片场地」是**隔一场才显示一个 21号**，其余场次空白。

成因就是"有场地行、但没有名字"：赛程按 2 片场地排（每轮 2 场），其中一片场地的
名字是空的 → 解析出来是空字符串 → 前端 `v-if="m.court_label"` 静默不渲染。
也就是说这条数据缺陷在界面上表现成"看起来像 bug 的空白"。

所以两处一起收：
1. 这里——创建/编辑赛事时就不接受空白场地名（本文件测的）；
2. ScheduleView——单场地赛事干脆不显示这条信息；多场地里个别场次解析不到名字时
   显示「场地待定」，不留空白。
"""
import pytest
from pydantic import ValidationError

from app.schemas.tournament import CourtCreate, TournamentCreate


def test_blank_court_name_rejected():
    with pytest.raises(ValidationError) as e:
        CourtCreate(name="")
    assert "场地名不能为空" in str(e.value)


def test_whitespace_court_name_rejected():
    # 纯空格比空字符串更隐蔽：界面上看着"填了"，解析出来仍是空
    with pytest.raises(ValidationError) as e:
        CourtCreate(name="   ")
    assert "场地名不能为空" in str(e.value)


def test_court_name_is_stripped():
    assert CourtCreate(name="  21号场地  ").name == "21号场地"


def test_normal_court_name_ok():
    assert CourtCreate(name="A2").name == "A2"
