"""Отправка писем через SMTP (код входа). Почта это ПДн: провайдер SMTP должен быть российским."""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger("mailer")


def available() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_user and s.smtp_password)


def _send(to: str, subject: str, text: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.mail_from or s.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    ctx = ssl.create_default_context()
    if s.smtp_port == 465:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=20) as c:
            c.login(s.smtp_user, s.smtp_password)
            c.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as c:
            c.starttls(context=ctx)
            c.login(s.smtp_user, s.smtp_password)
            c.send_message(msg)


async def send(to: str, subject: str, text: str) -> None:
    await asyncio.to_thread(_send, to, subject, text)
