import logging
import smtplib
import ssl
from html import escape
from email.header import Header
from email.mime.text import MIMEText

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _mask_email(addr: str | None) -> str:
    """日志里不写完整邮箱：收件人地址属个人信息。"""
    if not addr or "@" not in addr:
        return "<invalid>"
    name, _, domain = addr.partition("@")
    return f"{name[:1]}***@{domain}"


def _ssl_context() -> ssl.SSLContext:
    """默认的 smtplib 上下文**不校验证书**（verify_mode=CERT_NONE）。

    这点对本项目很关键：邮件里带的是可改密码的凭证，如果「服务→SMTP」之间
    能被中间人插入，攻击者可直接读到重置链接并接管账号。
    （Python 的 SMTP_SSL/starttls 在未显式传 context 时用的是
    ssl._create_stdlib_context()，实测 verify_mode=0、check_hostname=False。）
    """
    return ssl.create_default_context()


def send_email(to_email: str, subject: str, html: str) -> bool:
    settings = get_settings()
    if not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP 未配置，跳过邮件发送: %s", _mask_email(to_email))
        return False

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to_email
    try:
        context = _ssl_context()
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=15, context=context
            ) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.starttls(context=context)
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception:
        logger.exception("邮件发送失败: %s", _mask_email(to_email))
        return False


def send_password_reset(to_email: str, username: str, url: str, minutes: int) -> bool:
    """密码重置邮件。

    链接里的 token 放在 URL 的 `#` 之后（fragment）：fragment 不会随请求发给
    服务器，因此不会进入 nginx/应用访问日志，也不会进浏览器的 Referer，
    比放在查询串里安全。
    """
    html = (
        f"<div style='font-family:sans-serif;line-height:1.6'>"
        f"<h2>重置密码</h2>"
        f"<p>你好 {escape(username)}，我们收到了你的密码重置请求。</p>"
        f"<p>点击下面的链接设置新密码（{minutes} 分钟内有效，且只能使用一次）：</p>"
        f"<p><a href='{escape(url)}'>{escape(url)}</a></p>"
        f"<p>如果不是你本人的操作，请忽略本邮件，你的密码不会被修改。</p>"
        f"</div>"
    )
    return send_email(to_email, "重置密码 - 爱玩羽社", html)


def send_tournament_invite(
    to_email: str,
    host_username: str,
    tournament_title: str,
    start_date: str,
    location: str,
    url: str,
) -> bool:
    date_only = start_date[:10]
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
    subject = f"[{host_username}]邀请您加入{date_only}羽毛球赛事"
    return send_email(to_email, subject, html)
