# -*- coding: utf-8 -*-
"""Tests for extensions.py's check_production_safety() -- the startup-time
warning that fires when APP_ENV=production is combined with a missing
CLAMAV_HOST (which would silently fail-closed every document/photo upload,
per utils/helpers.py's _scan_for_malware()).

Deliberately calls the function directly with explicit parameters rather
than reloading extensions.py with different environment variables -- a
reload would re-run every other module-level side effect in that file
(CORS registration, rate limiter setup, Redis backend init) against the
Flask app instance the rest of this test session already shares, which is
both slow and risky. check_production_safety() was written to accept its
inputs as parameters specifically so it doesn't need that.

Run with:
    python -m pytest tests/test_production_safety_checks.py -v
"""
from unittest.mock import MagicMock

import pytest

from extensions import check_production_safety, check_secret_key_configured


def _fake_logger():
    logger = MagicMock()
    return logger


class TestSecretKeyBootCheck:
    """extensions.py used to fall back to a locally-generated, file-
    persisted secret key whenever SECRET_KEY was unset -- fine for local
    dev, but a container redeploy normally gets a fresh filesystem, so
    every deploy would silently rotate the key and invalidate every
    session/CSRF token in flight. Now refuses to start instead, same
    fail-secure posture as utils/razorpay_utils.py's checks."""

    def test_production_with_no_secret_key_refuses_to_boot(self):
        logger = _fake_logger()
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            check_secret_key_configured("production", "", logger=logger)
        logger.critical.assert_called_once()

    def test_production_with_whitespace_only_secret_key_refuses_to_boot(self):
        """A secret manager injection or template that resolved to just
        whitespace is functionally the same as unset -- must not slip
        through as a falsy-but-truthy string."""
        logger = _fake_logger()
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            check_secret_key_configured("production", "   ", logger=logger)

    def test_production_with_secret_key_set_boots_cleanly(self):
        logger = _fake_logger()
        check_secret_key_configured("production", "a-real-secret-key", logger=logger)
        logger.critical.assert_not_called()

    def test_development_with_no_secret_key_boots_cleanly(self):
        """Local dev without SECRET_KEY set is the normal, documented case
        -- extensions.py falls back to a persisted local file. Must not
        raise."""
        logger = _fake_logger()
        check_secret_key_configured("development", "", logger=logger)
        logger.critical.assert_not_called()

    def test_default_logger_is_extensions_app_log(self):
        """No logger passed -- must not raise a NameError/AttributeError
        finding the default logger, matching check_production_safety()'s
        own default-logger test."""
        check_secret_key_configured("development", "")


class TestClamavProductionCheck:
    def test_production_with_no_clamav_host_warns_critically(self):
        logger = _fake_logger()
        check_production_safety("production", None, malware_scan_enabled=True, logger=logger)
        logger.critical.assert_called_once()
        assert "CLAMAV_HOST" in logger.critical.call_args[0][0]

    def test_production_with_empty_string_clamav_host_warns(self):
        logger = _fake_logger()
        check_production_safety("production", "", malware_scan_enabled=True, logger=logger)
        logger.critical.assert_called_once()

    def test_production_with_clamav_host_configured_is_silent(self):
        logger = _fake_logger()
        check_production_safety("production", "clamav.internal", malware_scan_enabled=True, logger=logger)
        logger.critical.assert_not_called()

    def test_development_with_no_clamav_host_is_silent(self):
        """Local dev without a ClamAV instance is the normal, documented
        case (utils/helpers.py's _scan_for_malware() fails OPEN in dev) --
        must not warn."""
        logger = _fake_logger()
        check_production_safety("development", None, malware_scan_enabled=True, logger=logger)
        logger.critical.assert_not_called()

    def test_production_with_malware_scan_deliberately_disabled_is_silent(self):
        """MALWARE_SCAN_ENABLED=false is an intentional, documented choice
        (e.g. a memory-constrained deployment) -- not a misconfiguration."""
        logger = _fake_logger()
        check_production_safety("production", None, malware_scan_enabled=False, logger=logger)
        logger.critical.assert_not_called()

    def test_default_logger_is_extensions_app_log(self):
        """No logger passed -- must fall back to the real app_log rather
        than raising, so the real call site (extensions.py's module-level
        invocation) works with only two positional args."""
        # Should not raise even though we didn't pass a logger.
        check_production_safety("development", "clamav")


class TestAiAssistantProductionCheck:
    """utils/ai_assistant.py's ask_assistant() fails OPEN, not closed, when
    unconfigured -- it just returns a friendly fallback message forever,
    with nothing that would trip an error-rate alert. This check exists
    purely to surface that at boot instead of leaving it silently broken
    until an admin happens to try the chat themselves."""

    def test_production_with_no_backend_configured_warns_critically(self):
        logger = _fake_logger()
        check_production_safety("production", "clamav.internal", ai_assistant_configured=False, logger=logger)
        logger.critical.assert_called_once()
        assert "N8N_WEBHOOK_URL" in logger.critical.call_args[0][0]
        assert "GEMINI_API_KEY" in logger.critical.call_args[0][0]
        assert "ANTHROPIC_API_KEY" in logger.critical.call_args[0][0]

    def test_production_with_a_backend_configured_is_silent(self):
        logger = _fake_logger()
        check_production_safety("production", "clamav.internal", ai_assistant_configured=True, logger=logger)
        logger.critical.assert_not_called()

    def test_development_with_no_backend_configured_is_silent(self):
        """No AI backend configured is the normal, documented local-dev
        case -- must not warn."""
        logger = _fake_logger()
        check_production_safety("development", "clamav.internal", ai_assistant_configured=False, logger=logger)
        logger.critical.assert_not_called()

    def test_default_assumes_configured_so_existing_callers_stay_silent(self):
        """ai_assistant_configured defaults to True (fail-quiet on the
        default), matching malware_scan_enabled's default above -- so every
        existing call site/test that doesn't pass it explicitly keeps
        behaving exactly as before this check was added."""
        logger = _fake_logger()
        check_production_safety("production", "clamav.internal", logger=logger)
        logger.critical.assert_not_called()

    def test_both_clamav_and_ai_assistant_unconfigured_warns_twice(self):
        logger = _fake_logger()
        check_production_safety("production", None, malware_scan_enabled=True,
                                ai_assistant_configured=False, logger=logger)
        assert logger.critical.call_count == 2


class TestRedisProductionCheck:
    """Unlike the ClamAV/AI-assistant checks above (a feature silently
    doesn't work), a missing REDIS_HOST makes a security control silently
    weaker than it looks: utils/waf.py's auto-ban breach counter and
    Flask-Limiter both fall back to per-worker in-memory storage, so under
    multiple gunicorn workers the effective ban/rate threshold is the
    configured one times the worker count."""

    def test_production_with_no_redis_host_warns_critically(self):
        logger = _fake_logger()
        check_production_safety("production", "clamav.internal", redis_configured=False, logger=logger)
        logger.critical.assert_called_once()
        assert "REDIS_HOST" in logger.critical.call_args[0][0]

    def test_production_with_redis_configured_is_silent(self):
        logger = _fake_logger()
        check_production_safety("production", "clamav.internal", redis_configured=True, logger=logger)
        logger.critical.assert_not_called()

    def test_development_with_no_redis_host_is_silent(self):
        """No Redis in local dev/test is the normal, documented case --
        must not warn."""
        logger = _fake_logger()
        check_production_safety("development", "clamav.internal", redis_configured=False, logger=logger)
        logger.critical.assert_not_called()

    def test_default_assumes_configured_so_existing_callers_stay_silent(self):
        """redis_configured defaults to True, matching malware_scan_enabled/
        ai_assistant_configured's own defaults -- every existing call site/
        test that doesn't pass it explicitly keeps behaving exactly as
        before this check was added."""
        logger = _fake_logger()
        check_production_safety("production", "clamav.internal", logger=logger)
        logger.critical.assert_not_called()


class TestRazorpayProductionBootCheck:
    """utils/razorpay_utils.py's module-level fail-fast check used to only
    refuse to start when APP_ENV=production and a leftover TEST key
    (rzp_test_...) was configured -- it said nothing about the keys being
    MISSING entirely, in which case razorpay_configured() is simply False
    and every payment flow (create_id_or_demo()/verify_or_demo(), used by
    seats.py/billing.py/auto_debit.py/org.py/billing_dunning.py)
    transparently falls into its no-signature-check demo path in
    production. Now also refuses to start in that case.

    Reloads the module directly (safe here, unlike extensions.py -- this
    module has no Flask app registration/CORS/rate-limiter/Redis side
    effects, just the two module-level checks under test) rather than
    spawning a subprocess. Always reloads it back to the real test-suite
    environment afterward so later tests see the normal (unconfigured,
    APP_ENV=development) state again."""

    def _reload(self):
        import importlib
        import utils.razorpay_utils as razorpay_utils_module
        importlib.reload(razorpay_utils_module)
        return razorpay_utils_module

    def test_production_with_missing_keys_refuses_to_boot(self, monkeypatch):
        import pytest
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        try:
            with pytest.raises(RuntimeError, match="RAZORPAY_KEY_ID"):
                self._reload()
        finally:
            # Explicitly undo now (not at fixture teardown, which happens
            # after this function returns) so the restoring reload below
            # picks up the real test-suite defaults.
            monkeypatch.undo()
            self._reload()

    def test_production_with_only_key_id_set_still_refuses_to_boot(self, monkeypatch):
        """Both keys are required -- a half-configured pair (e.g. a secret-
        manager injection that silently dropped one of the two values)
        must fail exactly the same way as both being missing."""
        import pytest
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_partial")
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        try:
            with pytest.raises(RuntimeError, match="RAZORPAY_KEY_ID"):
                self._reload()
        finally:
            monkeypatch.undo()
            self._reload()

    def test_production_with_both_keys_configured_boots_cleanly(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_faketest")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
        try:
            module = self._reload()
            assert module.razorpay_configured() is True
        finally:
            monkeypatch.undo()
            self._reload()

    def test_development_with_missing_keys_boots_cleanly(self, monkeypatch):
        """The normal local-dev/test case (no real Razorpay keys at all) --
        must stay silent; this is what the whole demo-mode fallback exists
        for."""
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        try:
            module = self._reload()
            assert module.razorpay_configured() is False
        finally:
            monkeypatch.undo()
            self._reload()
