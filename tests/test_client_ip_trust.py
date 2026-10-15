"""Shared-IP safety net: a request source that can't identify one client
(container gateway, LAN, CGNAT, allow-listed probe, unparseable) must never
be IP-banned, and the WAF must not treat ordinary prose as an injection.

Regression coverage for the audit finding where, behind rootless Podman's
port forwarder (every visitor appears as one private IP), five hostile
requests -- or a single scanner hit on /.env -- banned the whole site.
"""
import datetime

import pytest

import utils.waf as waf_module
from utils import client_ip


def _banned_row(db_engine, ip):
    cur = db_engine.cursor()
    cur.execute("SELECT expires_at, banned_by FROM banned_ips WHERE ip=%s", (ip,))
    row = cur.fetchone()
    cur.close()
    return row


def _unban(db_engine, ip):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM banned_ips WHERE ip=%s", (ip,))
    db_engine.commit()
    cur.close()


class TestIsProtectedIp:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1", "10.4.0.7", "172.16.5.9", "172.31.255.254", "192.168.1.50",
        "100.64.0.1", "169.254.1.1", "::1", "fe80::1", "fd00::5",
        "::ffff:10.0.0.9",      # IPv4-mapped private
        "0.0.0.0",
    ])
    def test_non_routable_is_protected(self, ip):
        assert client_ip.is_protected_ip(ip) is True

    @pytest.mark.parametrize("ip", ["", None, "unknown", "not-an-ip", "999.1.1.1"])
    def test_unidentifiable_is_protected(self, ip):
        assert client_ip.is_protected_ip(ip) is True

    @pytest.mark.parametrize("ip", [
        "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111",
        # Documentation ranges stand in for real clients in the test suite
        # and staging labs -- they must stay bannable.
        "198.51.100.7", "203.0.113.7", "192.0.2.7",
        "172.15.0.1", "172.32.0.1",   # just outside RFC1918 172.16/12
    ])
    def test_routable_is_not_protected(self, ip):
        assert client_ip.is_protected_ip(ip) is False

    def test_operator_allowlist_extends_protection(self, monkeypatch):
        assert client_ip.is_protected_ip("203.0.113.50") is False
        monkeypatch.setenv("WAF_NEVER_BAN_CIDRS", "203.0.113.0/28, 198.18.0.0/15, junk")
        assert client_ip.is_protected_ip("203.0.113.5") is True
        assert client_ip.is_protected_ip("198.19.4.4") is True
        assert client_ip.is_protected_ip("203.0.113.200") is False


class TestTrustedProxyHops:
    def test_default_is_one(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        assert client_ip.trusted_proxy_hops() == 1

    @pytest.mark.parametrize("raw,expected", [("2", 2), ("0", 0), ("-3", 0), ("99", 5), ("x", 1)])
    def test_parsing_is_clamped(self, monkeypatch, raw, expected):
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", raw)
        assert client_ip.trusted_proxy_hops() == expected


class TestBanIsWithheldForSharedAddresses:
    def test_breach_threshold_does_not_ban_private_ip(self, db_engine):
        ip = "10.99.0.1"
        _unban(db_engine, ip)
        waf_module._breach_log.pop(ip, None)
        for _ in range(waf_module._BREACH_THRESHOLD * 3):
            waf_module.record_breach_and_maybe_ban(ip, "hostile request")
        assert _banned_row(db_engine, ip) is None
        assert ip not in waf_module._breach_log   # not even counted

    def test_ban_ip_refuses_protected_addresses(self, db_engine):
        for ip in ("127.0.0.1", "192.168.50.2", "unknown"):
            assert waf_module.ban_ip(ip, "x", minutes=5) is False
            assert _banned_row(db_engine, ip) is None

    def test_ban_ip_bans_routable_and_sets_expiry(self, db_engine):
        ip = "198.51.100.230"
        _unban(db_engine, ip)
        try:
            assert waf_module.ban_ip(ip, "test", banned_by="system:test", minutes=30) is True
            exp, by = _banned_row(db_engine, ip)
            assert by == "system:test"
            assert exp > datetime.datetime.now() + datetime.timedelta(minutes=20)
        finally:
            _unban(db_engine, ip)

    def test_automated_ban_never_shortens_a_longer_or_permanent_ban(self, db_engine):
        ip = "198.51.100.231"
        _unban(db_engine, ip)
        try:
            waf_module.ban_ip(ip, "manual permanent", banned_by="analyst", minutes=None)
            assert _banned_row(db_engine, ip)[0] is None
            waf_module.ban_ip(ip, "auto", banned_by="system:auto", minutes=15)
            assert _banned_row(db_engine, ip)[0] is None   # still permanent

            _unban(db_engine, ip)
            waf_module.ban_ip(ip, "long", minutes=24 * 60)
            long_exp = _banned_row(db_engine, ip)[0]
            waf_module.ban_ip(ip, "short", minutes=5)
            assert _banned_row(db_engine, ip)[0] >= long_exp
        finally:
            _unban(db_engine, ip)

    def test_existing_ban_row_on_private_ip_is_not_enforced(self, client, db_engine):
        """A stale ban written by an older build (or by hand) for the
        gateway address must not lock everyone out."""
        ip = "10.99.0.2"
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO banned_ips (ip, reason, banned_by, expires_at) VALUES (%s,%s,%s,NULL) "
            "ON CONFLICT (ip) DO NOTHING", (ip, "stale", "legacy"))
        db_engine.commit()
        cur.close()
        try:
            resp = client.get("/healthz")
            resp = client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": ip})
            assert resp.status_code != 403
        finally:
            _unban(db_engine, ip)

    def test_routable_banned_ip_is_still_blocked(self, client, db_engine):
        ip = "198.51.100.232"
        _unban(db_engine, ip)
        waf_module.ban_ip(ip, "test", minutes=10)
        try:
            resp = client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": ip})
            assert resp.status_code == 403
        finally:
            _unban(db_engine, ip)


class TestWafFalsePositivesFixed:
    """Ordinary prose containing a typed double-hyphen used to be blocked
    on every non-login form/JSON field (and counted toward the ban)."""

    @pytest.mark.parametrize("prose", [
        "Great work this quarter -- keep it up",
        "Leave needed for family function -- will be back Monday",
        "Printer broken -- please fix",
        "Reimbursement for client lunch (approx 1200) -- receipt attached",
        'He said "ok" -- fine',
        "see the /* TODO */ note in the policy",
        "Rated 'A' #1 in the region",
        "Q3 -- Q4 transition plan",
    ])
    def test_prose_is_not_flagged(self, prose):
        assert waf_module._sqli_signature(prose) is None

    @pytest.mark.parametrize("attack", [
        "admin'--",
        "admin' --",
        "admin' -- -",
        "x') --",
        "1; -- drop the rest",
        "' OR 1=1 --",
        "foo' or 'a'='a' --",
        "id=1 AND 1=1 --",
        "UN/**/ION SELECT 1",
        "SELECT/**/password FROM users",
        "1 /*!50000 UNION*/ SELECT 1",
        "' UNION SELECT username,password FROM admin_users--",
        "'; DROP TABLE employees;--",
        "/* comment */ UNION SELECT NULL--",
    ])
    def test_injection_shapes_still_detected(self, attack):
        assert waf_module._sqli_signature(attack) is not None


class TestHttpHoneypotBanPolicy:
    def test_scanner_hit_on_public_ip_bans_with_expiry(self, client, db_engine):
        ip = "198.51.100.240"
        _unban(db_engine, ip)
        try:
            resp = client.get("/.env", environ_overrides={"REMOTE_ADDR": ip})
            assert resp.status_code == 404
            row = _banned_row(db_engine, ip)
            assert row is not None
            assert row[1] == "system:honeypot"
            assert row[0] is not None            # time-limited, not permanent
            assert row[0] > datetime.datetime.now() + datetime.timedelta(days=1)
        finally:
            _unban(db_engine, ip)

    def test_scanner_hit_from_shared_private_ip_does_not_ban(self, client, db_engine):
        ip = "10.99.0.3"
        _unban(db_engine, ip)
        resp = client.get("/.env", environ_overrides={"REMOTE_ADDR": ip})
        assert resp.status_code == 404
        assert _banned_row(db_engine, ip) is None
        # ...and the site still serves the next request from that address.
        follow = client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": ip})
        assert follow.status_code != 403


class TestRateLimitKey:
    def test_routable_ip_is_the_key(self, client):
        with client.application.test_request_context("/", environ_overrides={"REMOTE_ADDR": "8.8.8.8"}):
            assert client_ip.rate_limit_key() == "8.8.8.8"

    def test_shared_ip_anonymous_keeps_single_bucket(self, client):
        with client.application.test_request_context("/", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}):
            assert client_ip.rate_limit_key() == "10.0.0.5"

    def test_shared_ip_authenticated_gets_own_bucket(self, client):
        app = client.application
        with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}):
            from flask import session
            session["employee_id"] = "EMP042"
            assert client_ip.rate_limit_key() == "10.0.0.5|EMP042"
        with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}):
            from flask import session
            session["admin_username"] = "hr_alice"
            assert client_ip.rate_limit_key() == "10.0.0.5|hr_alice"

    def test_routable_ip_ignores_session_identity(self, client):
        with client.application.test_request_context("/", environ_overrides={"REMOTE_ADDR": "8.8.4.4"}):
            from flask import session
            session["employee_id"] = "EMP042"
            assert client_ip.rate_limit_key() == "8.8.4.4"


class TestCollapseWarning:
    """The app logger has propagate=False (extensions.py), so caplog never
    sees it -- capture the .critical() call directly instead."""

    def test_warns_once_in_production(self, monkeypatch):
        client_ip._reset_warning_for_tests()
        calls = []
        monkeypatch.setattr(client_ip._log, "critical", lambda *a, **k: calls.append(a))
        client_ip.warn_if_client_ip_collapsed("10.0.2.2", app_env="production")
        client_ip.warn_if_client_ip_collapsed("10.0.2.2", app_env="production")
        assert len(calls) == 1
        assert "non-routable address" in calls[0][0]
        client_ip._reset_warning_for_tests()

    def test_silent_in_development(self, monkeypatch):
        client_ip._reset_warning_for_tests()
        calls = []
        monkeypatch.setattr(client_ip._log, "critical", lambda *a, **k: calls.append(a))
        client_ip.warn_if_client_ip_collapsed("127.0.0.1", app_env="development")
        assert calls == []
        client_ip._reset_warning_for_tests()
