"""Webhook token verification, strength enforcement, and log hygiene."""

from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr

from eventkit.errors import ConfigError
from eventkit.webhook import (
    WEAK_TOKENS,
    WebhookTokens,
    assert_strong,
    deferred,
    fingerprint,
    generate_token,
    verify_signature,
    verify_token,
)

GOOD = "0123456789abcdef0123456789abcdef0123456789abcdef"


class TestAssertStrong:
    def test_accepts_a_generated_token(self):
        assert_strong(generate_token(), name="test token")

    @pytest.mark.parametrize("weak", sorted(WEAK_TOKENS))
    def test_rejects_every_known_placeholder(self, weak):
        with pytest.raises(ConfigError):
            assert_strong(weak, name="test token")

    def test_rejects_the_two_committed_live_defaults_by_name(self):
        """These are the actual committed defaults in the two repositories.

        ``ticketed/config.py:22`` and ``posted/config.py:23-24``. An adopter who
        forgets the app setting would otherwise deploy a token that is published
        in a public repository.
        """
        for token in ("secret_drupal_token", "secret_nametags_token"):
            with pytest.raises(ConfigError) as excinfo:
                assert_strong(token, name="DRUPAL_WEBHOOK_TOKEN")
            assert "rotate" in str(excinfo.value).lower()

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_rejects_absent(self, empty):
        with pytest.raises(ConfigError) as excinfo:
            assert_strong(empty, name="DRUPAL_WEBHOOK_TOKEN")
        assert "not set" in str(excinfo.value)

    def test_rejects_too_short(self):
        with pytest.raises(ConfigError) as excinfo:
            assert_strong("abcdefghijk123", name="tok")
        assert "minimum" in str(excinfo.value)

    def test_rejects_low_variety(self):
        with pytest.raises(ConfigError):
            assert_strong("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", name="tok")

    def test_accepts_secretstr(self):
        assert_strong(SecretStr(GOOD), name="tok")

    def test_error_message_never_contains_the_token(self):
        secret = "shortbutsecret"
        try:
            assert_strong(secret, name="tok", min_len=64)
        except ConfigError as exc:
            assert secret not in str(exc)


class TestVerifyToken:
    def test_matching_token(self):
        assert verify_token(GOOD, GOOD) is True

    def test_mismatched_token(self):
        assert verify_token("wrong", GOOD) is False

    @pytest.mark.parametrize("absent", [None, ""])
    def test_absent_token(self, absent):
        assert verify_token(absent, GOOD) is False

    def test_empty_expected_never_matches(self):
        # A misconfigured empty expected token must not accept everything.
        assert verify_token("", "") is False
        assert verify_token("anything", "") is False

    def test_accepts_secretstr_expected(self):
        assert verify_token(GOOD, SecretStr(GOOD)) is True


class TestFingerprint:
    def test_is_short_and_hex(self):
        fp = fingerprint(GOOD)
        assert len(fp) == 6
        assert all(c in "0123456789abcdef" for c in fp)

    def test_absent_is_labelled(self):
        assert fingerprint(None) == "absent"
        assert fingerprint("") == "absent"

    def test_does_not_reveal_the_token(self):
        assert GOOD[:6] not in fingerprint(GOOD)

    def test_is_stable(self):
        assert fingerprint(GOOD) == fingerprint(GOOD)

    def test_distinguishes_tokens(self):
        assert fingerprint(GOOD) != fingerprint(GOOD[:-1] + "0")


class TestWebhookTokensLogging:
    def test_allows_a_correct_token(self, caplog):
        tokens = WebhookTokens({"registration": GOOD})
        with caplog.at_level(logging.INFO, logger="eventkit.webhook"):
            assert tokens.check("registration", GOOD) is True
        assert "outcome=allow" in caplog.text

    def test_denies_a_wrong_token(self, caplog):
        tokens = WebhookTokens({"registration": GOOD})
        with caplog.at_level(logging.INFO, logger="eventkit.webhook"):
            assert tokens.check("registration", "nope") is False
        assert "outcome=deny" in caplog.text
        assert "reason=mismatch" in caplog.text

    def test_reports_an_absent_token_distinctly(self, caplog):
        tokens = WebhookTokens({"registration": GOOD})
        with caplog.at_level(logging.INFO, logger="eventkit.webhook"):
            tokens.check("registration", None)
        assert "reason=absent" in caplog.text

    def test_never_logs_either_token_value(self, caplog):
        """The specific regression: both values were logged at INFO on every call."""
        tokens = WebhookTokens({"registration": GOOD})
        presented = "some-other-token-value-that-is-long"
        with caplog.at_level(logging.INFO, logger="eventkit.webhook"):
            tokens.check("registration", presented)
        assert GOOD not in caplog.text
        assert presented not in caplog.text

    def test_unknown_handler_name_is_a_programming_error(self):
        tokens = WebhookTokens({"registration": GOOD})
        with pytest.raises(KeyError):
            tokens.check("nonexistent", GOOD)

    def test_assert_all_strong_checks_every_token(self):
        tokens = WebhookTokens({"registration": GOOD, "nametags": "secret_nametags_token"})
        with pytest.raises(ConfigError) as excinfo:
            tokens.assert_all_strong()
        assert "nametags" in str(excinfo.value)

    def test_per_app_tokens_are_independent(self):
        # A token leaked from one app must not write to another's database.
        other = generate_token()
        tokens = WebhookTokens({"registration": GOOD, "nametags": other})
        assert tokens.check("registration", GOOD) is True
        assert tokens.check("nametags", GOOD) is False


class TestVerifySignature:
    SECRET = GOOD

    def _sign(self, body: bytes, timestamp: str) -> str:
        import hashlib
        import hmac

        return hmac.new(
            self.SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()

    def test_valid_signature(self):
        body, ts = b'{"a":1}', "1000000000"
        ok, reason = verify_signature(
            body=body, signature=self._sign(body, ts), timestamp=ts,
            secret=self.SECRET, now=1000000000.0,
        )
        assert ok is True
        assert reason == "ok"

    def test_sha256_prefix_is_tolerated(self):
        body, ts = b"{}", "1000000000"
        ok, _ = verify_signature(
            body=body, signature="sha256=" + self._sign(body, ts), timestamp=ts,
            secret=self.SECRET, now=1000000000.0,
        )
        assert ok is True

    def test_tampered_body_fails(self):
        ts = "1000000000"
        ok, reason = verify_signature(
            body=b"tampered", signature=self._sign(b"original", ts), timestamp=ts,
            secret=self.SECRET, now=1000000000.0,
        )
        assert ok is False
        assert reason == "signature_mismatch"

    def test_stale_timestamp_fails_even_with_a_valid_signature(self):
        body, ts = b"{}", "1000000000"
        ok, reason = verify_signature(
            body=body, signature=self._sign(body, ts), timestamp=ts,
            secret=self.SECRET, now=1000000000.0 + 3600,
        )
        assert ok is False
        assert reason == "stale_timestamp"

    @pytest.mark.parametrize(
        ("signature", "timestamp", "expected_reason"),
        [
            (None, "1000000000", "no_signature"),
            ("abc", None, "no_timestamp"),
            ("abc", "not-a-number", "bad_timestamp"),
        ],
    )
    def test_missing_and_malformed_inputs(self, signature, timestamp, expected_reason):
        ok, reason = verify_signature(
            body=b"{}", signature=signature, timestamp=timestamp,
            secret=self.SECRET, now=1000000000.0,
        )
        assert ok is False
        assert reason == expected_reason


class TestDeferred:
    def test_sync_exception_is_swallowed_and_logged(self, caplog):
        @deferred
        def boom():
            raise RuntimeError("notification service down")

        with caplog.at_level(logging.ERROR, logger="eventkit.webhook"):
            assert boom() is None
        assert "boom" in caplog.text

    def test_sync_success_returns_the_value(self):
        @deferred
        def fine():
            return 42

        assert fine() == 42

    async def test_async_exception_is_swallowed(self, caplog):
        @deferred
        async def boom():
            raise RuntimeError("eventbrite timeout")

        with caplog.at_level(logging.ERROR, logger="eventkit.webhook"):
            assert await boom() is None
        assert "boom" in caplog.text

    async def test_async_success_returns_the_value(self):
        @deferred
        async def fine():
            return "ok"

        assert await fine() == "ok"

    def test_metadata_is_preserved(self):
        @deferred
        def named():
            """Docstring."""

        assert named.__name__ == "named"
        assert named.__doc__ == "Docstring."


class TestGenerateToken:
    def test_length_and_alphabet(self):
        token = generate_token(32)
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_is_not_weak(self):
        for _ in range(5):
            assert_strong(generate_token(), name="generated")

    def test_tokens_are_distinct(self):
        assert generate_token() != generate_token()
