"""预选邀请邮件的正文组装（纯函数，不需要数据库、不真的发信）。"""
import pytest

import app.core.mailer as mailer


@pytest.fixture
def sent(monkeypatch):
    """把 send_email 换掉，抓下真正要发出去的 subject/html。"""
    box = {}

    def fake_send(to_email, subject, html):
        box.update(to=to_email, subject=subject, html=html)
        return True

    monkeypatch.setattr(mailer, "send_email", fake_send)
    return box


def test_invite_mail_includes_court(sent):
    """场地号和地点一样是到场必需信息，必须写进邮件。"""
    ok = mailer.send_tournament_invite(
        "a@x.com", "房主", "双十养生局", "2026-10-10 19:00", "奥体",
        "https://x/tournament/18", "1号场",
    )
    assert ok is True
    assert "地点：</strong>奥体" in sent["html"]
    assert "场地号：</strong>1号场" in sent["html"]


def test_invite_mail_omits_court_when_absent(sent):
    """没填场地号时不要出现「场地号：待定」这种空行。"""
    mailer.send_tournament_invite(
        "a@x.com", "房主", "局", "2026-10-10 19:00", "奥体",
        "https://x/tournament/18",
    )
    assert "场地号" not in sent["html"]
    assert "地点：</strong>奥体" in sent["html"]


def test_invite_mail_escapes_user_text(sent):
    """标题/地点/场地号都是用户自由文本，必须转义（邮件正文也是 HTML）。"""
    mailer.send_tournament_invite(
        "a@x.com", "房主", "<script>alert(1)</script>", "2026-10-10 19:00",
        "<img src=x onerror=alert(1)>", "https://x/tournament/18", '"><script>',
    )
    html = sent["html"]
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_invite_mail_subject_keeps_host_and_date(sent):
    """主题带上邀请人和日期（收件箱里一眼能看出是谁的哪场局）。"""
    mailer.send_tournament_invite(
        "a@x.com", "李祥", "双十养生局", "2026-10-10 19:00", "奥体",
        "https://x/tournament/18", "1号场",
    )
    assert sent["subject"] == "[李祥]邀请您加入2026-10-10羽毛球赛事"
