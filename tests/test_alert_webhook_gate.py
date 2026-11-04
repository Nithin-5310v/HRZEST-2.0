"""Webhook flood gate (utils/alerts.py): repeats of one event type inside the
window are counted, not posted, and the next posted alert says how many."""
import time
import types

import pytest

from utils import alerts


class _Posted(list):
    """Delivered payloads, plus the fake clock the gate reads."""
    clock = None


class _SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


@pytest.fixture
def posted(monkeypatch):
    out = _Posted()
    clock = {"t": 1000.0}
    monkeypatch.setattr(alerts, "_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setattr(alerts, "_PLATFORM", "slack")
    monkeypatch.delenv("SECURITY_ALERT_EMAIL", raising=False)
    monkeypatch.delenv("SECURITY_ALERT_WEBHOOK_WINDOW", raising=False)
    monkeypatch.setattr(alerts.threading, "Thread", _SyncThread)
    monkeypatch.setattr(alerts, "_webhook_last_sent", {})
    monkeypatch.setattr(alerts, "_webhook_suppressed", {})
    # A stand-in module, so only utils.alerts sees the fake clock.
    monkeypatch.setattr(alerts, "time", types.SimpleNamespace(time=lambda: clock["t"], strftime=time.strftime, gmtime=time.gmtime))
    monkeypatch.setattr(alerts, "_deliver", out.append)
    out.clock = clock
    return out


def _fields(payload):
    return payload["blocks"][2]["text"]["text"]


def test_a_burst_of_one_type_posts_once(posted):
    for i in range(40):
        alerts.send_security_alert("secops.honeypot_triggered", f"hit {i}", "CRITICAL", ip="198.51.100.7")
    assert len(posted) == 1
    assert "hit 0" in posted[0]["blocks"][1]["text"]["text"]


def test_the_next_alert_reports_what_was_folded(posted):
    for _ in range(5):
        alerts.send_security_alert("auth.lockout", "locked", ip="198.51.100.8")
    posted.clock["t"] += 61
    alerts.send_security_alert("auth.lockout", "locked again", ip="198.51.100.8")
    assert len(posted) == 2
    assert "suppressed" not in _fields(posted[0])
    assert "4 more auth.lockout alert(s)" in _fields(posted[1])
    posted.clock["t"] += 61
    alerts.send_security_alert("auth.lockout", "third")
    assert "suppressed" not in _fields(posted[2])       # the count was reported once, then reset


def test_types_are_gated_independently(posted):
    alerts.send_security_alert("auth.lockout", "a")
    alerts.send_security_alert("db.unavailable", "b", "CRITICAL")
    alerts.send_security_alert("auth.lockout", "c")
    assert [p["text"] for p in posted] == [
        ":rotating_light: *ERROR -- auth.lockout*",
        ":rotating_light: *CRITICAL -- db.unavailable*",
    ]


def test_window_zero_posts_every_alert(posted, monkeypatch):
    monkeypatch.setenv("SECURITY_ALERT_WEBHOOK_WINDOW", "0")
    for _ in range(3):
        alerts.send_security_alert("auth.lockout", "x")
    assert len(posted) == 3


def test_a_bad_window_value_falls_back_to_the_default(posted, monkeypatch):
    monkeypatch.setenv("SECURITY_ALERT_WEBHOOK_WINDOW", "soon")
    alerts.send_security_alert("auth.lockout", "x")
    alerts.send_security_alert("auth.lockout", "y")
    assert len(posted) == 1


def test_callers_cannot_forge_the_suppressed_note(posted):
    alerts.send_security_alert("auth.lockout", "x", suppressed="999 more alerts")
    assert "999" not in _fields(posted[0]) and "suppressed" not in _fields(posted[0])
