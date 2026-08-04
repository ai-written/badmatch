import logging
import smtplib
from html import escape
from email.header import Header
from email.mime.text import MIMEText

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, html: str) -> bool:
    settings = get_settings()
    if not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP 未配置，跳过邮件发送: %s", to_email)
        return False

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to_email
    try:
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception:
        logger.exception("邮件发送失败: %s", to_email)
        return False


def send_tournament_invite(
    to_email: str,
    tournament_title: str,
    start_date: str,
    location: str,
    url: str,
) -> bool:
    title_esc = escape(tournament_title)
    location_esc = escape(location or "待定")
    url_esc = escape(url)
    html = (
        f"<div style='font-family:sans-serif;line-height:1.6'>"
        f"<h2>您已被预选加入赛事</h2>"
        f"<p><strong>赛事名称：</strong>{title_esc}</p>"
        f"<p><strong>开始时间：</strong>{start_date}</p>"
        f"<p><strong>地点：</strong>{location_esc}</p>"
        f"<p>点击查看赛事详情：<a href='{url_esc}'>{url_esc}</a></p>"
        f"<p>如果不需要参加，可在赛事详情页取消报名。</p>"
        f"</div>"
    )
    return send_email(to_email, f"您已被预选加入赛事「{tournament_title}」", html)
