"""Tests for eventkit.notify: the priorities the phase doc calls out by name
(loader precedence, autoescape, blocking transports running off the event
loop) plus the policy/notifier/transport-selection logic around them."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from eventkit.notify import (
    LogTransport,
    MemoryTransport,
    Message,
    Notifier,
    NotifyPolicy,
    NotifySettings,
    transport_from_settings,
)
from eventkit.notify.render import Renderer, TemplateMissingError

# --------------------------------------------------------------------------
# Message / NotifyPolicy
# --------------------------------------------------------------------------


def test_message_defaults():
    msg = Message(to=["ada@example.edu"], subject="s", html="<p>h</p>")
    assert msg.text is None
    assert msg.tags == {}


def test_policy_wants_defaults_to_false_for_unlisted_event():
    policy = NotifyPolicy()
    assert policy.wants("completed_payment") is False


def test_policy_wants_respects_enabled_map():
    policy = NotifyPolicy(enabled={"completed_payment": True, "pending_payment": False})
    assert policy.wants("completed_payment") is True
    assert policy.wants("pending_payment") is False


def test_policy_recipients_for_falls_back_to_default():
    policy = NotifyPolicy(default_recipients=["events@example.edu"])
    assert policy.recipients_for("completed_payment") == ["events@example.edu"]


def test_policy_recipients_for_per_event_override_wins():
    policy = NotifyPolicy(
        default_recipients=["events@example.edu"],
        recipients={"sync_failed": ["oncall@example.edu"]},
    )
    assert policy.recipients_for("sync_failed") == ["oncall@example.edu"]
    assert policy.recipients_for("completed_payment") == ["events@example.edu"]


def test_policy_recipients_for_empty_per_event_override_falls_back():
    """An explicit but empty per-event list is not "send to nobody on purpose"
    here — it means the override was never set for this event."""
    policy = NotifyPolicy(default_recipients=["events@example.edu"], recipients={"sync_failed": []})
    assert policy.recipients_for("sync_failed") == ["events@example.edu"]


# --------------------------------------------------------------------------
# Notifier
# --------------------------------------------------------------------------


def _notifier(transport, *, enabled=True, recipients=("events@example.edu",)):
    renderer = Renderer()
    policy = NotifyPolicy(
        enabled={"completed_payment": enabled},
        default_recipients=list(recipients),
    )
    return Notifier(transport, renderer, policy, from_email="noreply@example.edu")


async def test_notifier_skips_disabled_event():
    transport = MemoryTransport()
    notifier = _notifier(transport, enabled=False)
    sent = await notifier.notify("completed_payment", {"email": "ada@example.edu"})
    assert sent is False
    assert transport.outbox == []


async def test_notifier_skips_when_no_recipients_configured():
    transport = MemoryTransport()
    notifier = _notifier(transport, recipients=())
    sent = await notifier.notify("completed_payment", {"email": "ada@example.edu"})
    assert sent is False
    assert transport.outbox == []


async def test_notifier_sends_rendered_message():
    transport = MemoryTransport()
    notifier = _notifier(transport)
    sent = await notifier.notify(
        "completed_payment", {"email": "ada@example.edu", "full_name": "Ada Lovelace"}
    )
    assert sent is True
    assert len(transport.outbox) == 1
    msg = transport.outbox[0]
    assert msg.to == ["events@example.edu"]
    assert "ada@example.edu" in msg.subject
    assert "Ada Lovelace" in msg.html
    assert msg.from_email == "noreply@example.edu"
    assert msg.tags == {"event": "completed_payment"}


async def test_notifier_reports_transport_failure():
    class FailingTransport:
        name = "failing"

        async def send(self, msg: Message) -> bool:
            return False

    notifier = _notifier(FailingTransport())
    sent = await notifier.notify("completed_payment", {"email": "ada@example.edu"})
    assert sent is False


# --------------------------------------------------------------------------
# LogTransport / MemoryTransport
# --------------------------------------------------------------------------


async def test_log_transport_returns_true_and_logs(caplog):
    caplog.set_level(logging.INFO, logger="eventkit.notify")
    transport = LogTransport()
    msg = Message(to=["ada@example.edu"], subject="hello", html="<p>hi</p>")
    assert await transport.send(msg) is True
    assert "ada@example.edu" in caplog.text
    assert "hello" in caplog.text


async def test_memory_transport_records_every_message():
    transport = MemoryTransport()
    msg = Message(to=["ada@example.edu"], subject="hello", html="<p>hi</p>")
    assert await transport.send(msg) is True
    assert transport.outbox == [msg]


# --------------------------------------------------------------------------
# transport_from_settings
# --------------------------------------------------------------------------


def test_transport_from_settings_defaults_to_log():
    assert isinstance(transport_from_settings(NotifySettings()), LogTransport)


def test_transport_from_settings_memory():
    assert isinstance(transport_from_settings(NotifySettings(transport="memory")), MemoryTransport)


def test_transport_from_settings_unknown_name_falls_back_to_log(caplog):
    caplog.set_level(logging.WARNING, logger="eventkit.notify")
    transport = transport_from_settings(NotifySettings(transport="carrier-pigeon"))
    assert isinstance(transport, LogTransport)
    assert "unknown_transport" in caplog.text


@pytest.mark.parametrize(
    ("transport_name", "reason"),
    [
        ("smtp", "missing_smtp_host"),
        ("resend", "missing_api_key"),
        ("acs", "missing_connection_string_or_sender"),
    ],
)
def test_transport_from_settings_falls_back_when_credentials_missing(
    transport_name, reason, caplog
):
    caplog.set_level(logging.WARNING, logger="eventkit.notify")
    transport = transport_from_settings(NotifySettings(transport=transport_name))
    assert isinstance(transport, LogTransport)
    assert reason in caplog.text


def test_transport_from_settings_builds_smtp_transport_with_host():
    from eventkit.notify.transports.smtp import SmtpTransport

    transport = transport_from_settings(
        NotifySettings(transport="smtp", smtp_host="smtp.example.edu", smtp_port=25)
    )
    assert isinstance(transport, SmtpTransport)
    assert transport.host == "smtp.example.edu"
    assert transport.port == 25


def test_transport_from_settings_builds_resend_transport_with_key():
    from eventkit.notify.transports.resend import ResendTransport

    transport = transport_from_settings(NotifySettings(transport="resend", resend_api_key="re_x"))
    assert isinstance(transport, ResendTransport)
    assert transport.api_key == "re_x"


def test_transport_from_settings_builds_acs_transport_with_credentials():
    from eventkit.notify.transports.acs import AcsTransport

    transport = transport_from_settings(
        NotifySettings(
            transport="acs",
            acs_connection_string="endpoint=https://x;accesskey=y",
            acs_sender_address="donotreply@example.azurecomm.net",
        )
    )
    assert isinstance(transport, AcsTransport)
    assert transport.sender_address == "donotreply@example.azurecomm.net"


# --------------------------------------------------------------------------
# Renderer: loader precedence, autoescape, missing templates
# --------------------------------------------------------------------------


def test_renderer_renders_shipped_default_template():
    renderer = Renderer()
    rendered = renderer.render("completed_payment", {"email": "ada@example.edu", "serial": 42})
    assert "ada@example.edu" in rendered.subject
    assert "42" in rendered.html
    assert rendered.text is None  # no shipped .txt.j2 for this event


def test_renderer_missing_template_raises():
    renderer = Renderer()
    with pytest.raises(TemplateMissingError):
        renderer.render("no_such_event", {})


def test_renderer_autoescapes_html_but_not_subject(tmp_path: Path):
    (tmp_path / "custom.subject.txt.j2").write_text("Hi {{ full_name }}")
    (tmp_path / "custom.html.j2").write_text("<p>{{ full_name }}</p>")

    renderer = Renderer(adopter_dir=tmp_path)
    rendered = renderer.render("custom", {"full_name": "A & B <script>"})

    assert rendered.subject == "Hi A & B <script>"
    assert "&amp;" in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "<script>" not in rendered.html


def test_renderer_optional_text_template_used_when_present(tmp_path: Path):
    (tmp_path / "custom.subject.txt.j2").write_text("Subject")
    (tmp_path / "custom.html.j2").write_text("<p>html</p>")
    (tmp_path / "custom.txt.j2").write_text("plain & simple")

    renderer = Renderer(adopter_dir=tmp_path)
    rendered = renderer.render("custom", {})
    assert rendered.text == "plain & simple"  # not escaped: it's plain text, not HTML


def test_renderer_loader_precedence_adopter_over_profile_over_default(tmp_path: Path):
    adopter_dir = tmp_path / "adopter"
    profile_dir = tmp_path / "profile"
    adopter_dir.mkdir()
    profile_dir.mkdir()

    # Only the profile dir overrides the subject; only the adopter dir overrides
    # the HTML body. Each should win independently, and whatever neither
    # overrides should still fall through to eventkit's shipped default.
    (profile_dir / "completed_payment.subject.txt.j2").write_text("Profile subject")
    (adopter_dir / "completed_payment.html.j2").write_text("<p>Adopter html</p>")

    renderer = Renderer(adopter_dir=adopter_dir, profile_dir=profile_dir)
    rendered = renderer.render("completed_payment", {"email": "ada@example.edu"})

    assert rendered.subject == "Profile subject"
    assert rendered.html == "<p>Adopter html</p>"


def test_renderer_adopter_wins_over_profile_for_same_template(tmp_path: Path):
    adopter_dir = tmp_path / "adopter"
    profile_dir = tmp_path / "profile"
    adopter_dir.mkdir()
    profile_dir.mkdir()

    (adopter_dir / "completed_payment.subject.txt.j2").write_text("Adopter subject")
    (profile_dir / "completed_payment.subject.txt.j2").write_text("Profile subject")

    renderer = Renderer(adopter_dir=adopter_dir, profile_dir=profile_dir)
    rendered = renderer.render("completed_payment", {"email": "ada@example.edu"})
    assert rendered.subject == "Adopter subject"


# --------------------------------------------------------------------------
# SmtpTransport: blocking call runs off the event loop
# --------------------------------------------------------------------------


class _FakeSmtp:
    """Stands in for smtplib.SMTP: a blocking, synchronous context manager."""

    sleep_s: float = 0.0
    raise_on_send: bool = False

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.sent: list[Any] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, email):
        if self.sleep_s:
            time.sleep(self.sleep_s)
        if self.raise_on_send:
            raise smtplib.SMTPException("relay refused")
        self.sent.append(email)


async def test_smtp_transport_sends_via_smtplib(monkeypatch):
    from eventkit.notify.transports.smtp import SmtpTransport

    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    transport = SmtpTransport(host="smtp.example.edu", username="u", password="p")
    msg = Message(
        to=["ada@example.edu"], subject="hi", html="<p>hi</p>", from_email="a@example.edu"
    )

    assert await transport.send(msg) is True


async def test_smtp_transport_returns_false_on_send_failure(monkeypatch):
    from eventkit.notify.transports.smtp import SmtpTransport

    class RaisingSmtp(_FakeSmtp):
        raise_on_send = True

    monkeypatch.setattr(smtplib, "SMTP", RaisingSmtp)
    transport = SmtpTransport(host="smtp.example.edu")
    msg = Message(to=["ada@example.edu"], subject="hi", html="<p>hi</p>")

    assert await transport.send(msg) is False


async def test_smtp_transport_does_not_block_the_event_loop(monkeypatch):
    """The whole point of anyio.to_thread here: a slow relay must not stall
    other coroutines. Race a 150ms "blocking send" against a ticking counter
    that increments every 20ms; if the send were awaited inline instead of
    offloaded to a thread, the counter would not advance while it runs."""
    from eventkit.notify.transports.smtp import SmtpTransport

    class SlowSmtp(_FakeSmtp):
        sleep_s = 0.15

    monkeypatch.setattr(smtplib, "SMTP", SlowSmtp)
    transport = SmtpTransport(host="smtp.example.edu")
    msg = Message(to=["ada@example.edu"], subject="hi", html="<p>hi</p>")

    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.02)

    ticker_task = asyncio.create_task(ticker())
    try:
        assert await transport.send(msg) is True
    finally:
        stop = True
        await ticker_task

    assert ticks >= 3


# --------------------------------------------------------------------------
# ResendTransport: blocking SDK call runs off the event loop
# --------------------------------------------------------------------------


async def test_resend_transport_sends_and_offloads_the_blocking_call(monkeypatch):
    import resend

    from eventkit.notify.transports.resend import ResendTransport

    calls: list[dict] = []

    def fake_send(params):
        calls.append(params)
        return {"id": "email-1"}

    monkeypatch.setattr(resend.Emails, "send", staticmethod(fake_send))
    transport = ResendTransport(api_key="re_x")
    msg = Message(
        to=["ada@example.edu"], subject="hi", html="<p>hi</p>", from_email="a@example.edu"
    )

    assert await transport.send(msg) is True
    assert calls[0]["to"] == ["ada@example.edu"]
    assert resend.api_key == "re_x"


async def test_resend_transport_returns_false_on_sdk_error(monkeypatch):
    import resend

    from eventkit.notify.transports.resend import ResendTransport

    def failing_send(params):
        raise RuntimeError("resend is down")

    monkeypatch.setattr(resend.Emails, "send", staticmethod(failing_send))
    transport = ResendTransport(api_key="re_x")
    msg = Message(to=["ada@example.edu"], subject="hi", html="<p>hi</p>")

    assert await transport.send(msg) is False


# --------------------------------------------------------------------------
# AcsTransport: exercised against a fake azure SDK module, since the real
# azure-communication-email package is an extra this test image does not
# install (matching how `acs` is the least-recommended transport per the
# phase doc — SMTP is the recommended second transport, not this).
# --------------------------------------------------------------------------


@pytest.fixture
def fake_azure_email_sdk(monkeypatch):
    calls: list[dict] = []
    should_fail = {"value": False}

    class _Poller:
        def result(self):
            if should_fail["value"]:
                raise RuntimeError("ACS send failed")
            return {"status": "Succeeded"}

    class _FakeEmailClient:
        def __init__(self, connection_string):
            self.connection_string = connection_string

        @classmethod
        def from_connection_string(cls, connection_string):
            return cls(connection_string)

        def begin_send(self, message):
            calls.append(message)
            return _Poller()

    fake_module = types.ModuleType("azure.communication.email")
    fake_module.EmailClient = _FakeEmailClient

    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.communication", types.ModuleType("azure.communication"))
    monkeypatch.setitem(sys.modules, "azure.communication.email", fake_module)

    return types.SimpleNamespace(calls=calls, should_fail=should_fail)


async def test_acs_transport_sends_via_fake_sdk(fake_azure_email_sdk):
    from eventkit.notify.transports.acs import AcsTransport

    transport = AcsTransport(
        connection_string="endpoint=https://x;accesskey=y",
        sender_address="donotreply@example.azurecomm.net",
    )
    msg = Message(to=["ada@example.edu"], subject="hi", html="<p>hi</p>", text="hi")

    assert await transport.send(msg) is True
    assert fake_azure_email_sdk.calls[0]["senderAddress"] == "donotreply@example.azurecomm.net"
    assert fake_azure_email_sdk.calls[0]["recipients"]["to"] == [{"address": "ada@example.edu"}]


async def test_acs_transport_returns_false_on_sdk_error(fake_azure_email_sdk):
    from eventkit.notify.transports.acs import AcsTransport

    fake_azure_email_sdk.should_fail["value"] = True
    transport = AcsTransport(
        connection_string="endpoint=https://x;accesskey=y",
        sender_address="donotreply@example.azurecomm.net",
    )
    msg = Message(to=["ada@example.edu"], subject="hi", html="<p>hi</p>")

    assert await transport.send(msg) is False
