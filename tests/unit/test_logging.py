"""Log redaction.

The point of this module is not to fix the two known leaky log lines — those are
deleted outright — but to make the *next* one harmless. A future
``logger.info(settings)`` must not be able to dump every credential the app holds.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr

from eventkit.logging import (
    REDACTION,
    RedactFilter,
    configure_logging,
    register_secret,
    registered_secret_count,
    reset_logging,
    reset_secrets,
)

TOKEN = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset both the secret registry and the global record factory.

    ``configure_logging`` installs a process-wide record factory, so without this
    a test that registers a secret would keep scrubbing other tests' log
    assertions.
    """
    reset_secrets()
    reset_logging()
    yield
    reset_secrets()
    reset_logging()


class TestRegisterSecret:
    def test_registers_a_long_value(self):
        register_secret(TOKEN)
        assert registered_secret_count() == 1

    @pytest.mark.parametrize("short", [None, "", "abc", "1234567"])
    def test_ignores_short_and_empty_values(self, short):
        register_secret(short)
        assert registered_secret_count() == 0

    def test_accepts_secretstr(self):
        register_secret(SecretStr(TOKEN))
        assert registered_secret_count() == 1

    def test_is_idempotent(self):
        register_secret(TOKEN)
        register_secret(TOKEN)
        assert registered_secret_count() == 1


class TestScrub:
    def test_removes_a_registered_secret(self):
        register_secret(TOKEN)
        assert TOKEN not in RedactFilter.scrub(f"token is {TOKEN} here")
        assert REDACTION in RedactFilter.scrub(f"token is {TOKEN} here")

    def test_leaves_unrelated_text_alone(self):
        register_secret(TOKEN)
        assert RedactFilter.scrub("nothing to see") == "nothing to see"

    def test_redacts_bearer_headers_without_registration(self):
        scrubbed = RedactFilter.scrub("Authorization: Bearer abcdef1234567890xyz")
        assert "abcdef1234567890xyz" not in scrubbed

    def test_redacts_key_value_shapes_without_registration(self):
        scrubbed = RedactFilter.scrub("api_key=supersecretvalue123")
        assert "supersecretvalue123" not in scrubbed

    def test_redacts_query_string_tokens(self):
        """The leaked speaker links are exactly this shape."""
        scrubbed = RedactFilter.scrub(
            "GET /form/speaker-bios?token=Ab3xY9zQ1mN7pL2kR8sT4vW6uJ0hG5dF"
        )
        assert "Ab3xY9zQ1mN7pL2kR8sT4vW6uJ0hG5dF" not in scrubbed

    def test_longest_secret_wins(self):
        register_secret(TOKEN)
        register_secret(TOKEN + "extra")
        assert TOKEN not in RedactFilter.scrub(f"value {TOKEN}extra")

    def test_empty_input(self):
        assert RedactFilter.scrub("") == ""


class TestFilterOnRecords:
    def _record(self, msg, args=()):
        return logging.LogRecord("t", logging.INFO, "f", 1, msg, args, None)

    def test_scrubs_the_message(self):
        register_secret(TOKEN)
        record = self._record(f"token={TOKEN}")
        RedactFilter().filter(record)
        assert TOKEN not in record.getMessage()

    def test_scrubs_lazy_args(self):
        """``logger.info("token=%s", tok)`` keeps the secret in args until format."""
        register_secret(TOKEN)
        record = self._record("token=%s", (TOKEN,))
        RedactFilter().filter(record)
        assert TOKEN not in record.getMessage()

    def test_scrubs_a_header_dict_argument(self):
        """The exact live regression: ``logger.info(f"...{dict(request.headers)}")``."""
        register_secret(TOKEN)
        record = self._record("headers=%s", ({"x-drupal-webhook-token": TOKEN},))
        RedactFilter().filter(record)
        assert TOKEN not in record.getMessage()

    def test_secretstr_argument_is_fully_redacted(self):
        record = self._record("settings=%s", (SecretStr("anything at all"),))
        RedactFilter().filter(record)
        assert REDACTION in record.getMessage()

    def test_scrubs_dict_style_args(self):
        """``logger.info("%(tok)s", {"tok": tok})`` — mapping-style lazy formatting.

        The dict must be wrapped in a 1-tuple, exactly as ``Logger._log`` passes it.
        Handing ``LogRecord`` a bare dict as ``args`` raises ``KeyError: 0`` inside
        the stdlib before the filter is ever reached, because ``LogRecord.__init__``
        probes ``args[0]`` to detect this very case. ``LogRecord.args`` then ends up
        as the unwrapped dict, which is what ``RedactFilter`` handles.
        """
        register_secret(TOKEN)
        record = self._record("%(tok)s", ({"tok": TOKEN},))
        assert record.args == {"tok": TOKEN}, "stdlib should unwrap the mapping"
        RedactFilter().filter(record)
        assert TOKEN not in record.getMessage()

    def test_filter_always_returns_true(self):
        # A logging filter that drops records would hide operational data.
        assert RedactFilter().filter(self._record("anything")) is True

    def test_filter_never_raises_on_odd_records(self):
        record = self._record(object())  # non-string msg
        assert RedactFilter().filter(record) is True


class TestConfigureLogging:
    def test_redacts_through_a_handler_it_does_not_own(self, caplog):
        """Redaction must reach sinks eventkit did not install.

        ``caplog`` stands in for the handlers Azure App Service attaches. If
        redaction were a filter on eventkit's own handler, this would pass the
        token straight through to the platform log — the leak would move rather
        than close.
        """
        configure_logging(level="INFO", secrets=[TOKEN])
        logger = logging.getLogger("eventkit.test.configure")
        with caplog.at_level(logging.INFO):
            logger.info("token=%s", TOKEN)
        assert TOKEN not in caplog.text
        assert REDACTION in caplog.text

    def test_redacts_an_f_string_message(self, caplog):
        # The live regression was an f-string, so the secret is already inside
        # `msg` by the time logging sees it.
        configure_logging(level="INFO", secrets=[TOKEN])
        logger = logging.getLogger("eventkit.test.fstring")
        with caplog.at_level(logging.INFO):
            logger.info(f"Received token: '{TOKEN}'")
        assert TOKEN not in caplog.text

    def test_redacts_a_whole_headers_dict(self, caplog):
        configure_logging(level="INFO", secrets=[TOKEN])
        logger = logging.getLogger("eventkit.test.headers")
        headers = {"host": "example.edu", "x-drupal-webhook-token": TOKEN}
        with caplog.at_level(logging.INFO):
            logger.info(f"Webhook request headers: {headers}")
        assert TOKEN not in caplog.text

    def test_is_idempotent(self):
        root = logging.getLogger()
        configure_logging(level="INFO")
        first = len([h for h in root.handlers if getattr(h, "_eventkit_managed", False)])
        configure_logging(level="INFO")
        second = len([h for h in root.handlers if getattr(h, "_eventkit_managed", False)])
        assert first == second == 1

    def test_record_factory_is_installed_once(self):
        configure_logging(level="INFO")
        first = logging.getLogRecordFactory()
        configure_logging(level="INFO")
        assert logging.getLogRecordFactory() is first

    def test_secrets_are_registered_before_redaction_is_installed(self):
        configure_logging(level="INFO", secrets=[TOKEN, SecretStr("another-long-secret")])
        assert registered_secret_count() == 2

    def test_reset_restores_the_original_factory(self):
        before = logging.getLogRecordFactory()
        configure_logging(level="INFO")
        assert logging.getLogRecordFactory() is not before
        reset_logging()
        assert logging.getLogRecordFactory() is before

    def test_unrelated_log_lines_are_untouched(self, caplog):
        configure_logging(level="INFO", secrets=[TOKEN])
        logger = logging.getLogger("eventkit.test.unrelated")
        with caplog.at_level(logging.INFO):
            logger.info("webhook.verify name=registration outcome=allow fp=3f9a21")
        assert "outcome=allow" in caplog.text
        assert "fp=3f9a21" in caplog.text
