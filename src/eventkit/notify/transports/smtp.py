"""SMTP via stdlib ``smtplib``, off the event loop.

The recommended real transport — see the package docstring for why SMTP over
ACS. ``smtplib`` is synchronous; :meth:`SmtpTransport.send` wraps the whole
connect/send/quit sequence in :func:`anyio.to_thread.run_sync` so an ``async
def`` caller (a webhook handler, typically) never blocks the event loop on a
relay that is slow or unreachable.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage
from typing import ClassVar

import anyio

from .. import Message

__all__ = ["SmtpTransport"]

logger = logging.getLogger("eventkit.notify.smtp")


class SmtpTransport:
    name: ClassVar[str] = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        timeout_s: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.timeout_s = timeout_s

    def _build_email(self, msg: Message) -> EmailMessage:
        email = EmailMessage()
        sender = msg.from_email or self.username or ""
        email["From"] = f"{msg.from_name} <{sender}>" if msg.from_name else sender
        email["To"] = ", ".join(msg.to)
        email["Subject"] = msg.subject
        if msg.reply_to:
            email["Reply-To"] = msg.reply_to
        email.set_content(msg.text or "")
        email.add_alternative(msg.html, subtype="html")
        return email

    def _send_sync(self, msg: Message) -> bool:
        import smtplib

        email = self._build_email(msg)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout_s) as client:
                if self.use_tls:
                    client.starttls()
                if self.username and self.password:
                    client.login(self.username, self.password)
                client.send_message(email)
            return True
        except (OSError, smtplib.SMTPException):
            logger.exception("notify.smtp.send outcome=failed host=%s to=%s", self.host, msg.to)
            return False

    async def send(self, msg: Message) -> bool:
        return await anyio.to_thread.run_sync(self._send_sync, msg)
