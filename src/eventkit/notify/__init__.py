"""Templated email, with a transport that can never block a deploy.

Replaces ``ticketed/backend/notifications.py``: one ``if/elif`` chain of
f-string HTML per event type, hardwired to Resend, with the sender name
``"Drupal Reconciler"`` baked into every message regardless of which event
uses it. Two problems that chain caused, both fixed here:

* :func:`send_reconciliation_alert` is ``async def`` but calls blocking
  ``resend.Emails.send`` directly (``notifications.py:132``), stalling the
  event loop for the duration of the HTTP call on every webhook that fires a
  notification. Every real transport in this module wraps its blocking SDK
  call in :func:`anyio.to_thread.run_sync`.
* A missing or wrong ``RESEND_API_KEY`` degraded to "log the email body at
  WARNING and return ``False``" — silent in production, easy to miss. Here the
  degrading-to-log behaviour is the explicit default (:class:`LogTransport`),
  not an accidental fallback discovered only when a real transport's
  credentials turn out to be missing. :func:`transport_from_settings` still
  falls back to :class:`LogTransport` (with a ``WARNING``) when the *named*
  transport's credentials are absent, so a misconfigured ``NOTIFY_TRANSPORT``
  degrades a deploy rather than blocking it — but that fallback is now a
  documented, tested behaviour rather than the only path that ever ran.

SMTP, not ACS, is the recommended real transport: every university already
runs a relay, whereas Azure Communication Services needs a provisioned
Communication Service and DNS access the adopter may not have. Resend and ACS
both ship behind their own extras (``eventkit-core[resend]`` /
``eventkit-core[acs]``); neither package is a base dependency.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EventKitError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .render import Renderer

logger = logging.getLogger("eventkit.notify")

__all__ = [
    "AcsTransport",
    "LogTransport",
    "Message",
    "MemoryTransport",
    "NotifyError",
    "NotifyPolicy",
    "NotifySettings",
    "Notifier",
    "ResendTransport",
    "SmtpTransport",
    "Transport",
    "transport_from_settings",
]


class NotifyError(EventKitError):
    """A notification could not be built or sent in a way callers must see.

    Transport failures are deliberately *not* raised this way — see
    :class:`Transport`'s docstring — so this is reserved for programmer error:
    an unknown transport name reaching :func:`transport_from_settings` with no
    sensible fallback, for instance.
    """


class Message(BaseModel):
    """One rendered email, transport-agnostic."""

    model_config = ConfigDict(extra="forbid")

    to: list[str]
    subject: str
    html: str
    text: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class Transport(Protocol):
    """Sends a :class:`Message`. Never raises — a send failure is a logged
    ``False``, not an exception, so one bad SMTP relay cannot turn an
    otherwise-successful webhook handler into a 500. Every shipped
    implementation follows this contract; a transport that needs to
    distinguish failure reasons should log them itself.
    """

    name: ClassVar[str]

    async def send(self, msg: Message) -> bool: ...


class NotifySettings(BaseModel):
    """Credentials and transport choice, kept separate from :class:`Message`
    content. Deliberately not the same object as
    :class:`eventkit.eventprofile.models.NotifyConfig`: the profile is
    per-event content policy checked into an app's config repo, while this is
    per-deployment secrets an app builds from its own environment variables.
    Passing a profile's ``NotifyConfig`` fields straight into this model would
    make an SMTP password something a YAML file could carry.
    """

    model_config = ConfigDict(extra="forbid")

    transport: str = "log"
    from_email: str | None = None
    from_name: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    resend_api_key: str | None = None

    acs_connection_string: str | None = None
    acs_sender_address: str | None = None


def transport_from_settings(settings: NotifySettings) -> Transport:
    """Build the :class:`Transport` named by ``settings.transport``.

    Never raises. An unknown name, or a named transport whose required
    credentials are absent, falls back to :class:`LogTransport` with a
    ``WARNING`` — a missing credential must degrade a deploy, not block one.
    """
    name = settings.transport

    if name == "log":
        return LogTransport()
    if name == "memory":
        return MemoryTransport()

    if name == "smtp":
        if not settings.smtp_host:
            logger.warning(
                "notify.transport_from_settings outcome=fallback requested=smtp "
                "reason=missing_smtp_host"
            )
            return LogTransport()
        from .transports.smtp import SmtpTransport

        return SmtpTransport(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
        )

    if name == "resend":
        if not settings.resend_api_key:
            logger.warning(
                "notify.transport_from_settings outcome=fallback requested=resend "
                "reason=missing_api_key"
            )
            return LogTransport()
        from .transports.resend import ResendTransport

        return ResendTransport(api_key=settings.resend_api_key)

    if name == "acs":
        if not (settings.acs_connection_string and settings.acs_sender_address):
            logger.warning(
                "notify.transport_from_settings outcome=fallback requested=acs "
                "reason=missing_connection_string_or_sender"
            )
            return LogTransport()
        from .transports.acs import AcsTransport

        return AcsTransport(
            connection_string=settings.acs_connection_string,
            sender_address=settings.acs_sender_address,
        )

    logger.warning(
        "notify.transport_from_settings outcome=fallback requested=%s reason=unknown_transport",
        name,
    )
    return LogTransport()


class LogTransport:
    """The default. Logs the rendered message at INFO and returns ``True``.

    Zero dependencies beyond the standard library, so a fresh checkout with no
    credentials configured anywhere still "sends" every notification — visibly,
    in the logs — rather than raising or silently dropping it.
    """

    name: ClassVar[str] = "log"

    async def send(self, msg: Message) -> bool:
        logger.info(
            "notify.send transport=log to=%s subject=%s tags=%s",
            msg.to,
            msg.subject,
            dict(msg.tags),
        )
        return True


class MemoryTransport:
    """For tests. Every sent :class:`Message` is appended to :attr:`outbox`."""

    name: ClassVar[str] = "memory"

    def __init__(self) -> None:
        self.outbox: list[Message] = []

    async def send(self, msg: Message) -> bool:
        self.outbox.append(msg)
        return True


class NotifyPolicy(BaseModel):
    """Which events send, and to whom. Empty ``enabled`` sends nothing, matching
    the deny-by-default posture :class:`eventkit.auth.AllowList` already
    established: an app that never configured notifications should not
    discover it has been emailing someone the whole time.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: dict[str, bool] = Field(default_factory=dict)
    recipients: dict[str, list[str]] = Field(default_factory=dict)
    default_recipients: list[str] = Field(default_factory=list)

    def wants(self, event: str) -> bool:
        return self.enabled.get(event, False)

    def recipients_for(self, event: str) -> list[str]:
        """The per-event override list if set and non-empty, else the default list."""
        return self.recipients.get(event) or list(self.default_recipients)


class Notifier:
    """Ties a :class:`Transport`, a :class:`~eventkit.notify.render.Renderer`
    and a :class:`NotifyPolicy` together into the one call sites use.
    """

    def __init__(
        self,
        transport: Transport,
        renderer: Renderer,
        policy: NotifyPolicy,
        *,
        from_email: str | None = None,
        from_name: str | None = None,
    ) -> None:
        self.transport = transport
        self.renderer = renderer
        self.policy = policy
        self.from_email = from_email
        self.from_name = from_name

    async def notify(self, event: str, ctx: Mapping[str, Any]) -> bool:
        """Render and send ``event``, or skip it. Returns whether a send was
        attempted *and* the transport reported success — ``False`` covers
        "policy says skip this event", "no recipient is configured for it",
        and "the transport itself failed" alike, since none of the three
        should raise out of a webhook handler.
        """
        if not self.policy.wants(event):
            logger.debug("notify.notify outcome=skip event=%s reason=policy_disabled", event)
            return False

        recipients = self.policy.recipients_for(event)
        if not recipients:
            logger.debug("notify.notify outcome=skip event=%s reason=no_recipients", event)
            return False

        rendered = self.renderer.render(event, ctx)
        msg = Message(
            to=recipients,
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
            from_email=self.from_email,
            from_name=self.from_name,
            tags={"event": event},
        )
        sent = await self.transport.send(msg)
        logger.info(
            "notify.notify outcome=%s event=%s transport=%s to=%s",
            "sent" if sent else "failed",
            event,
            self.transport.name,
            recipients,
        )
        return sent
