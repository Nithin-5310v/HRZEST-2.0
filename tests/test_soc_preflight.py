"""scripts/soc_preflight.py: the SOC console's go-live check.

Why: going live needs about a dozen settings right at once (who may sign in,
which networks, email, alerts). A wrong one shows up only
when someone is locked out of /socperson. The check says which, with the fix,
before the release is approved -- using the app's own rules, never printing a
secret or an address.
"""
import pytest

from scripts import soc_preflight as pf

READY = {
    "APP_ENV": "production",
    "SOC_LOGIN_EMAILS": "lead@example.com, ana@example.com:analyst",
    "SOC_ALLOWED_CIDRS": "203.0.113.0/24",
    "ALLOWED_ORIGINS": "https://hrzest.example,https://www.hrzest.example",
    "SMTP_HOST": "smtp.example.com", "SMTP_USER": "mailer", "SMTP_PASS": "mail-secret-123",
    "APP_URL": "https://hrzest.example",
    "SECURITY_ALERT_WEBHOOK_URL": "https://hooks.example.com/services/T000/B000/hook-secret-456",
    "TRUSTED_PROXY_HOPS": "1",
}
ALL_KEYS = list(READY) + ["SOC_PASSKEY", "SOC_DASHBOARD_URL", "SOC_ADMIN_TOKEN", "SECURITY_ALERT_EMAIL",
                          "SOC_TEST_CIDRS", "SOC_TEST_ACCOUNTS", "SOC_TEST_COMPANIES", "SOC_DIGEST"]


@pytest.fixture
def env(monkeypatch):
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in READY.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def statuses():
    return {title: status for status, title, _, _ in pf.checks()}


def test_a_ready_server_passes(env, capsys):
    s = statuses()
    assert pf.FAIL not in s.values() and pf.WARN not in s.values(), s
    assert pf.main(["--env-file", "/nonexistent"]) == 0
    assert "Result: ready to go live." in capsys.readouterr().out


@pytest.mark.parametrize("change, title", [
    ({"APP_ENV": "development"}, "Production mode"),
    ({"SOC_LOGIN_EMAILS": ""}, "Who can sign in"),
    ({"SOC_LOGIN_EMAILS": "not-an-address"}, "Who can sign in"),
    ({"SOC_ALLOWED_CIDRS": ""}, "Network allow-list"),
    ({"SOC_ALLOWED_CIDRS": "office-network"}, "Network allow-list"),
])
def test_each_blocker_fails_with_its_fix(env, change, title, capsys):
    for k, v in change.items():
        env.setenv(k, v)
    assert statuses()[title] == pf.FAIL
    assert pf.main(["--env-file", "/nonexistent"]) == 1
    out = capsys.readouterr().out
    assert "problem(s) to fix before going live" in out and "->" in out


@pytest.mark.parametrize("change, title", [
    ({"SOC_ALLOWED_CIDRS": "0.0.0.0/0,::/0"}, "Network allow-list"),
    ({"SOC_PASSKEY": "off"}, "Old passkey setting"),
    ({"SMTP_PASS": ""}, "Email for sign-in codes"),
    ({"APP_URL": "http://hrzest.example"}, "Link in the daily summary"),
    ({"SECURITY_ALERT_WEBHOOK_URL": ""}, "Real-time alerts"),
    ({"TRUSTED_PROXY_HOPS": ""}, "Real visitor addresses"),
    ({"SOC_ADMIN_TOKEN": "old-token"}, "Old SOC dashboard setting"),
    ({"SOC_DASHBOARD_URL": "https://soc.example:4500"}, "Old SOC dashboard setting"),
])
def test_risky_but_working_settings_warn(env, change, title):
    for k, v in change.items():
        env.setenv(k, v)
    assert statuses()[title] == pf.WARN


def test_a_server_the_app_would_refuse_to_start_says_so_instead_of_crashing(env, capsys):
    import sys
    env.setitem(sys.modules, "extensions", None)       # importing it now raises, like a refused start-up
    s = statuses()
    assert s["App starts"] == pf.FAIL and s["Who can sign in"] == pf.PASS       # the rest still runs
    assert pf.main(["--env-file", "/nonexistent"]) == 1


@pytest.mark.parametrize("raw", ["", "on", "off", "OFF", "0", "false", "no", "yes", "garbage"])
def test_reads_the_switches_exactly_as_the_app_does(env, raw):
    from utils import soc_digest
    env.setenv("SOC_DIGEST", raw)
    env.setenv("SOC_DIGEST_HOUR", raw or "6")
    assert pf._digest() == (soc_digest.enabled(), soc_digest.digest_hour())


def test_alerts_by_email_alone_are_enough(env):
    env.setenv("SECURITY_ALERT_WEBHOOK_URL", "")
    env.setenv("SECURITY_ALERT_EMAIL", "ops@example.com")
    assert statuses()["Real-time alerts"] == pf.PASS


def test_never_prints_a_secret_or_an_address(env, capsys):
    env.setenv("SOC_ADMIN_TOKEN", "old-token-789")
    env.setenv("SOC_TEST_ACCOUNTS", "qa.bot@example.com")
    pf.main(["--env-file", "/nonexistent"])
    out = capsys.readouterr().out
    for secret in ("mail-secret-123", "hook-secret-456", "old-token-789", "lead@example.com",
                   "ana@example.com", "qa.bot@example.com", "203.0.113.0/24", "smtp.example.com"):
        assert secret not in out, secret
    assert "2 valid address(es)" in out and "1 account(s)" in out
