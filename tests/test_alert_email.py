"""Security alerts also go out by email (SECURITY_ALERT_EMAIL), through the
app's normal SMTP config + email queue, throttled and HTML-escaped."""
import pytest

from utils import alerts


class _SyncThread:
    """threading.Thread stand-in that runs the target immediately."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


@pytest.fixture
def mail(monkeypatch):
    """Capture emails handed to the queue; SMTP 'configured'; threads synchronous."""
    sent = []
    monkeypatch.setattr(alerts, "_WEBHOOK_URL", "")
    monkeypatch.setattr(alerts.threading, "Thread", _SyncThread)
    monkeypatch.setattr(alerts, "_email_last_sent", {})
    monkeypatch.setenv("SECURITY_ALERT_EMAIL", "soc@example.com, oncall@example.com")
    import utils.email_utils as eu
    monkeypatch.setattr(eu, "get_email_config", lambda: {"host": "smtp.example.com", "user": "u", "password": "p",
                                                        "port": 587, "from_name": "HRzest", "from_email": "u@example.com"})
    monkeypatch.setattr(eu, "send_email_async", lambda to, subj, body, cfg, **k: sent.append((to, subj, body)))
    return sent


def test_no_channel_configured_is_a_silent_noop(monkeypatch):
    monkeypatch.setattr(alerts, "_WEBHOOK_URL", "")
    monkeypatch.delenv("SECURITY_ALERT_EMAIL", raising=False)
    alerts.send_security_alert("auth.lockout", "locked", ip="1.2.3.4")  # must not raise


def test_email_goes_to_every_recipient(mail):
    alerts.send_security_alert("auth.lockout", "Account locked", identifier="alice", ip="1.2.3.4")
    assert [m[0] for m in mail] == ["soc@example.com", "oncall@example.com"]
    to, subject, body = mail[0]
    assert subject == "[HRzest security ERROR] auth.lockout"
    assert "Account locked" in body and "alice" in body and "1.2.3.4" in body


def test_attacker_controlled_text_is_html_escaped(mail):
    alerts.send_security_alert("auth.lockout", "<script>alert(1)</script>", identifier='"><img src=x onerror=1>')
    body = mail[0][2]
    assert "<script>" not in body and "<img" not in body
    assert "&lt;script&gt;" in body


def test_secret_looking_values_are_redacted_in_email_too(mail):
    alerts.send_security_alert("x.y", "login with password=hunter2 failed")
    assert "hunter2" not in mail[0][2]


def test_throttled_per_event_type(mail):
    alerts.send_security_alert("auth.lockout", "first")
    alerts.send_security_alert("auth.lockout", "second")     # same type: suppressed
    alerts.send_security_alert("malware.detected", "other")  # different type: sent
    assert len(mail) == 4  # 2 recipients x (auth.lockout once + malware.detected once)
    assert {m[1] for m in mail} == {"[HRzest security ERROR] auth.lockout", "[HRzest security ERROR] malware.detected"}


def test_no_smtp_configured_skips_without_raising(mail, monkeypatch):
    import utils.email_utils as eu
    monkeypatch.setattr(eu, "get_email_config", lambda: {"host": "", "user": ""})
    alerts.send_security_alert("auth.lockout", "locked")
    assert mail == []


def test_email_failure_never_propagates(mail, monkeypatch):
    import utils.email_utils as eu

    def boom(*a, **k):
        raise RuntimeError("queue down")
    monkeypatch.setattr(eu, "send_email_async", boom)
    alerts.send_security_alert("auth.lockout", "locked")  # must not raise


def test_recipient_parsing_ignores_junk(monkeypatch):
    monkeypatch.setenv("SECURITY_ALERT_EMAIL", " a@x.com ,, not-an-address, b@y.org ")
    assert alerts._alert_recipients() == ["a@x.com", "b@y.org"]
