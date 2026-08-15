"""Azure Communication Services Email, off the event loop.

The third transport, behind everything SMTP already covers for most adopters
— see the package docstring for why SMTP, not this, is recommended. Needs a
provisioned Communication Service, a verified sender domain, and the
``azure-communication-email`` extra (``eventkit-core[acs]``).

``EmailClient.begin_send`` starts a long-running operation and
``poller.result()`` blocks until it finishes; both run inside
:func:`anyio.to_thread.run_sync`, same as every other real transport here.
"""

from __future__ import annotations

import logging
from typing import ClassVar

import anyio

from .. import Message

__all__ = ["AcsTransport"]

logger = logging.getLogger("eventkit.notify.acs")


class AcsTransport:
    name: ClassVar[str] = "acs"

    def __init__(self, *, connection_string: str, sender_address: str) -> None:
        self.connection_string = connection_string
        self.sender_address = sender_address

    def _send_sync(self, msg: Message) -> bool:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(self.connection_string)
        message = {
            "senderAddress": self.sender_address,
            "recipients": {"to": [{"address": address} for address in msg.to]},
            "content": {
                "subject": msg.subject,
                "html": msg.html,
                "plainText": msg.text or "",
            },
        }
        if msg.reply_to:
            message["replyTo"] = [{"address": msg.reply_to}]
        try:
            poller = client.begin_send(message)
            poller.result()
            return True
        except Exception:  # noqa: BLE001 - a third-party SDK's exception surface
            logger.exception("notify.acs.send outcome=failed to=%s", msg.to)
            return False

    async def send(self, msg: Message) -> bool:
        return await anyio.to_thread.run_sync(self._send_sync, msg)
