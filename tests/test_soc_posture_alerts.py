"""SOC Posture: says whether security alerts actually reach anyone.

With no channel configured, utils/alerts.send_security_alert skips silently
(one debug line), so the console is the only place that can point it out.
"""
import pytest

from utils import alerts, soc_queries

HOOK = "https://discord.com/api/webhooks/123/very-secret-token"


def _check(monkeypatch, webhook="", email=None):
    monkeypatch.setattr(alerts, "_WEBHOOK_URL", webhook)
    if email is None:
        monkeypatch.delenv("SECURITY_ALERT_EMAIL", raising=False)
    else:
        monkeypatch.setenv("SECURITY_ALERT_EMAIL", email)
    return next(c for c in soc_queries.posture()["checks"] if c["id"] == "alerts")


def test_no_channel_is_a_warning(monkeypatch):
    c = _check(monkeypatch)
    assert c["status"] == "warn"
    assert "SECURITY_ALERT_WEBHOOK_URL" in c["fix"] and "SECURITY_ALERT_EMAIL" in c["fix"]


@pytest.mark.parametrize("webhook,email,expect", [
    (HOOK, None, "webhook"),
    ("", "soc@example.com, oncall@example.com", "email to 2 recipient(s)"),
    (HOOK, "soc@example.com", "webhook and email to 1 recipient(s)"),
])
def test_a_configured_channel_passes(monkeypatch, webhook, email, expect):
    c = _check(monkeypatch, webhook, email)
    assert c["status"] == "pass" and expect in c["detail"]


def test_a_webhook_without_http_scheme_warns(monkeypatch):
    # alerts._deliver refuses such a URL, so nothing would ever be posted.
    c = _check(monkeypatch, "discord.com/api/webhooks/1/x")
    assert c["status"] == "warn" and "http" in c["detail"]


def test_neither_the_url_nor_the_addresses_are_shown(monkeypatch):
    c = _check(monkeypatch, HOOK, "soc@example.com")
    text = " ".join(str(v) for v in c.values())
    assert "very-secret-token" not in text and "discord.com" not in text and "soc@example.com" not in text
