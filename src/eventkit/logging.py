"""Logging setup with secret redaction.

The reason this module exists rather than each app calling
``logging.basicConfig``: the live reconciler logs the webhook token at ``INFO``
on every call, and the live nametags app logs whole request header dicts. Both
were added for debugging and never removed. A filter that scrubs known secret
values makes the *next* such line harmless, which matters more than fixing the
two that exist — a future ``logger.info(settings)`` would otherwise dump every
credential the app holds.

Use it as::

    from eventkit.logging import configure_logging, register_secret

    configure_logging(level="INFO")
    register_secret(settings.drupal_webhook_token)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Iterable

from pydantic import SecretStr

__all__ = [
    "REDACTION",
    "RedactFilter",
    "configure_logging",
    "install_redacting_record_factory",
    "register_secret",
    "registered_secret_count",
    "reset_logging",
    "reset_secrets",
]

REDACTION = "[redacted]"

#: Minimum length for a value to be worth registering. Redacting a 3-character
#: string would corrupt unrelated log lines.
_MIN_SECRET_LENGTH = 8

_secrets: set[str] = set()

#: Patterns that look like credentials regardless of whether the value was
#: registered. Belt and braces for secrets that arrive from outside config —
#: an Authorization header echoed back in an httpx error, for instance.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), f"Bearer {REDACTION}"),
    (re.compile(r"(?i)\b(token|secret|password|api[_-]?key|apikey)"
                r"(['\"]?\s*[:=]\s*['\"]?)([^\s,'\"}\)]{8,})"), rf"\1\2{REDACTION}"),
    (re.compile(r"(?i)([?&](?:token|access_token|key|signature)=)[^&\s]{8,}"),
     rf"\1{REDACTION}"),
)


def register_secret(value: str | SecretStr | None) -> None:
    """Register a value to be scrubbed from all future log output.

    Safe to call repeatedly. Short and empty values are ignored so that a
    misconfigured setting cannot turn every log line into redaction markers.
    """
    if value is None:
        return
    raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    raw = raw.strip()
    if len(raw) < _MIN_SECRET_LENGTH:
        return
    _secrets.add(raw)


def registered_secret_count() -> int:
    """For a startup assertion that secrets were actually registered."""
    return len(_secrets)


def reset_secrets() -> None:
    """Clear the registry. For tests."""
    _secrets.clear()


class RedactFilter(logging.Filter):
    """Scrub registered secrets and credential-shaped substrings from records.

    Operates on the formatted message and on ``args``, because
    ``logger.info("token=%s", tok)`` keeps the secret in ``args`` until format
    time. Also scrubs ``exc_text`` so a traceback containing a settings repr is
    covered.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self.scrub(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        key: self._scrub_value(value) for key, value in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(self._scrub_value(a) for a in record.args)
            if getattr(record, "exc_text", None):
                record.exc_text = self.scrub(record.exc_text)
        except Exception:  # pragma: no cover - a logging filter must never raise
            return True
        return True

    def _scrub_value(self, value: Any) -> Any:
        if isinstance(value, SecretStr):
            return REDACTION
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, (dict, list, tuple)):
            # Header dicts are the specific thing that leaked. Stringify, scrub,
            # and hand back a string rather than trying to rebuild the structure.
            return self.scrub(repr(value))
        return value

    @staticmethod
    def scrub(text: str) -> str:
        if not text:
            return text
        result = text
        # Longest first, so a secret that contains another is fully removed.
        for secret in sorted(_secrets, key=len, reverse=True):
            if secret in result:
                result = result.replace(secret, REDACTION)
        for pattern, replacement in _PATTERNS:
            result = pattern.sub(replacement, result)
        return result


_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"

_original_factory: Callable[..., logging.LogRecord] | None = None


def install_redacting_record_factory() -> None:
    """Scrub secrets at :class:`logging.LogRecord` *creation*.

    This is the load-bearing half of the module, and it is a record factory
    rather than a filter for a specific reason: a filter attached to a handler
    only protects that handler. Azure App Service attaches its own handlers, and
    so does pytest's ``caplog``. A record whose ``msg``/``args`` still hold a
    token would reach those untouched, which would make the protection here
    theatre — the leak would simply move from our stream to the platform's.

    Logger-level filters are no better: they run only on the logger that creates
    the record, not on ancestors during propagation.

    The factory runs once per record, before any handler sees it, so every sink
    gets the scrubbed version. Idempotent.
    """
    global _original_factory
    if _original_factory is not None:
        return

    _original_factory = logging.getLogRecordFactory()
    base = _original_factory
    redactor = RedactFilter()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        redactor.filter(record)
        return record

    logging.setLogRecordFactory(factory)


def reset_logging() -> None:
    """Restore the original record factory and drop managed handlers. For tests."""
    global _original_factory
    if _original_factory is not None:
        logging.setLogRecordFactory(_original_factory)
        _original_factory = None
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_eventkit_managed", False)]:
        root.removeHandler(handler)
    root.filters = [f for f in root.filters if not isinstance(f, RedactFilter)]


def configure_logging(
    *,
    level: str | int | None = None,
    fmt: str = _DEFAULT_FORMAT,
    secrets: Iterable[str | SecretStr | None] = (),
    quiet_loggers: Iterable[str] = ("httpx", "httpcore", "azure"),
) -> None:
    """Install redaction and a stream handler.

    Idempotent: calling it twice does not double log lines, which matters because
    App Service restarts a container's ASGI app without a fresh process.

    Args:
        level: defaults to ``$LOG_LEVEL`` then ``INFO``.
        secrets: registered *before* redaction is installed, so there is no
            window in which a secret can be logged in the clear.
    """
    for secret in secrets:
        register_secret(secret)

    install_redacting_record_factory()

    resolved = level if level is not None else os.getenv("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    root.setLevel(resolved)

    existing = next(
        (h for h in root.handlers if getattr(h, "_eventkit_managed", False)), None
    )
    if existing is None:
        handler = logging.StreamHandler()
        handler._eventkit_managed = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter(fmt))
        # Belt and braces: the factory has already scrubbed the record, but a
        # handler filter costs nothing and covers a caller who bypassed
        # configure_logging and installed the filter directly.
        handler.addFilter(RedactFilter())
        root.addHandler(handler)
    else:
        existing.setFormatter(logging.Formatter(fmt))

    for name in quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)
