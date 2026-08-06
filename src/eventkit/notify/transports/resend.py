"""Resend, off the event loop.

``ticketed/backend/notifications.py:132`` calls the blocking ``resend.Emails.send``
directly from an ``async def`` function — every notification sent through it
stalls the event loop for the HTTP round trip. Fixed here the same way as
:mod:`eventkit.notify.transports.smtp`: the blocking call runs in
:func:`anyio.to_thread.run_sync`.
"""

from __future__ import annotations

import logging
from typing import ClassVar

import anyio

from .. import Message

__all__ = ["ResendTransport"]

logger = logging.getLogger("eventkit.notify.resend")


class ResendTransport:
    name: ClassVar[str] = "resend"

    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    def _send_sync(self, msg: Message) -> bool:
        import resend

        resend.api_key = self.api_key
        sender = msg.from_email or ""
        params: dict[str, object] = {
            "from": f"{msg.from_name} <{sender}>" if msg.from_name else sender,
            "to": msg.to,
            "subject": msg.subject,
            "html": msg.html,
        }
        if msg.text:
            params["text"] = msg.text
        if msg.reply_to:
            params["reply_to"] = msg.reply_to
        try:
            resend.Emails.send(params)
            return True
        except Exception:  # noqa: BLE001 - a third-party SDK's exception surface
            logger.exception("notify.resend.send outcome=failed to=%s", msg.to)
            return False

    async def send(self, msg: Message) -> bool:
        return await anyio.to_thread.run_sync(self._send_sync, msg)
