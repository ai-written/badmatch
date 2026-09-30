"""报名海报二维码的纯函数单测（不依赖数据库/HTTP）。"""
from app.api.poster import poster_path, poster_url, qr_png
from app.models.tournament import TournamentStatus

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_poster_url_carries_invite_and_marker():
    url = poster_url(16, "ABC123", frontend_url="https://example.com/")
    # 末尾斜杠要归一化掉，否则会出现 "//tournament"
    assert url == "https://example.com/tournament/16?from=poster&invite=ABC123"


def test_poster_url_without_invite():
    assert poster_url(7, None, frontend_url="https://example.com") == (
        "https://example.com/tournament/7?from=poster"
    )


def test_poster_path_by_status():
    """报名中进报名页；已开赛/已结束直接进对阵表（海报上写的是「扫码看赛程」）。"""
    assert poster_path(16, "open") == "/tournament/16"
    assert poster_path(16, "ongoing") == "/tournament/16/schedule"
    assert poster_path(16, "finished") == "/tournament/16/schedule"
    # 不带状态时按报名页处理（兼容旧调用）
    assert poster_path(16, None) == "/tournament/16"
    # 接口传进来的是枚举（TournamentStatus 继承自 str），比较必须成立
    assert poster_path(16, TournamentStatus.OPEN) == "/tournament/16"
    assert poster_path(16, TournamentStatus.ONGOING) == "/tournament/16/schedule"


def test_poster_url_after_start_points_to_schedule():
    url = poster_url(16, "ABC123", frontend_url="https://example.com", status=TournamentStatus.ONGOING)
    assert url == "https://example.com/tournament/16/schedule?from=poster&invite=ABC123"


def test_qr_png_is_square_and_big_enough():
    """海报里二维码会被缩放绘制，出图太小放大后会糊；这里守住尺寸下限。"""
    data = qr_png(poster_url(16, "ABC123", frontend_url="https://aiwanyushe.example.cn"))

    assert data[:8] == PNG_MAGIC
    # PNG 的 IHDR 紧跟 8 字节签名 + 4 字节长度 + 4 字节类型，宽高各占 4 字节
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    assert width == height
    assert width >= 300


def test_qr_png_handles_long_url():
    """fit=True 会按内容自动升版本：长 URL 也必须能出图，而不是抛异常。"""
    long_url = poster_url(99999, "Z" * 32, frontend_url="https://aiwanyushe.hsianglee.cn")
    assert len(long_url) > 80
    assert qr_png(long_url)[:8] == PNG_MAGIC
