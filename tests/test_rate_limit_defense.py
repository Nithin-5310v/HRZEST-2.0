"""Tests for the 429 breach-handling path (app.py's rate_limit_exceeded
errorhandler) and the auto-ban escalation it shares with utils/waf.py's
WAF-block counter. Rate limiting is disabled globally in conftest.py (same
reasoning as the mandatory-MFA gate) since most of the suite doesn't care
about it — re-enabled locally here, following test_mandatory_admin_mfa.py's
pattern for re-enabling a globally-disabled gate."""
import datetime
import pytest
import utils.waf as waf_module


def _unban(db_engine, ip):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM banned_ips WHERE ip=%s", (ip,))
    cur.execute("DELETE FROM att_master.soc_ban_reviews WHERE ip=%s", (ip,))
    db_engine.commit()
    cur.close()


@pytest.fixture
def rate_limiting_enabled(client):
    import app as app_module
    client.application.config["RATELIMIT_ENABLED"] = True
    app_module.limiter.enabled = True
    yield
    client.application.config["RATELIMIT_ENABLED"] = False
    app_module.limiter.enabled = False


class TestRateLimitBreachResponse:
    def test_breaching_login_limit_returns_429(self, client, rate_limiting_enabled, db_engine):
        ip = "203.0.113.201"
        _unban(db_engine, ip)
        try:
            # /login is limited to 20/minute (blueprints/auth.py) —
            # raised from 5/minute because rootless Podman's port-forwarding
            # proxy collapses every visitor to one apparent IP in production
            # (see blueprints/auth.py's comment on admin_login), so a tight
            # per-IP limit there was shared by the whole site's traffic.
            last = None
            for _ in range(25):
                last = client.post("/login", data={"username": "nobody", "password": "wrong"},
                                   environ_overrides={"REMOTE_ADDR": ip})
            assert last.status_code == 429
        finally:
            waf_module._breach_log.pop(ip, None)
            _unban(db_engine, ip)

    def test_breach_is_logged(self, client, rate_limiting_enabled, monkeypatch, db_engine):
        ip = "203.0.113.202"
        _unban(db_engine, ip)
        calls = []
        import app as app_module
        monkeypatch.setattr(app_module, "log_security_event",
                            lambda event_type, message, level="WARNING", **f: calls.append(event_type))
        try:
            for _ in range(25):
                client.post("/login", data={"username": "nobody", "password": "wrong"},
                            environ_overrides={"REMOTE_ADDR": ip})
            assert "ratelimit.exceeded" in calls
        finally:
            waf_module._breach_log.pop(ip, None)
            _unban(db_engine, ip)


def _repeated_429s(client, ip):
    # Each burst of >5/min triggers one 429; repeat past _BREACH_THRESHOLD
    # 429s to cross the breach counter.
    for _ in range((waf_module._BREACH_THRESHOLD + 2) * 6):
        client.post("/login", data={"username": "nobody", "password": "wrong"},
                    environ_overrides={"REMOTE_ADDR": ip})


class TestProgressiveBanFromRepeatedBreaches:
    def test_repeated_429s_raise_a_review_not_a_ban(self, client, rate_limiting_enabled, db_engine, monkeypatch):
        """A shift-start login rush from one customer office (many employees,
        one shared address) looks exactly like this. It stays rate-limited,
        but is no longer locked out: the address waits for an analyst."""
        monkeypatch.delenv("SOC_AUTO_BAN", raising=False)
        ip = "203.0.113.203"
        waf_module._breach_log.pop(ip, None)
        _unban(db_engine, ip)
        try:
            _repeated_429s(client, ip)
            cur = db_engine.cursor()
            cur.execute("SELECT 1 FROM banned_ips WHERE ip=%s", (ip,))
            assert cur.fetchone() is None
            cur.execute("SELECT status, source FROM att_master.soc_ban_reviews WHERE ip=%s", (ip,))
            assert cur.fetchone() == ("pending", "ratelimit")
            cur.close()
            assert client.get("/login", environ_overrides={"REMOTE_ADDR": ip}).status_code != 403
        finally:
            waf_module._breach_log.pop(ip, None)
            _unban(db_engine, ip)

    def test_repeated_429s_ban_with_soc_auto_ban(self, client, rate_limiting_enabled, db_engine, monkeypatch):
        monkeypatch.setenv("SOC_AUTO_BAN", "true")
        ip = "203.0.113.204"
        waf_module._breach_log.pop(ip, None)
        _unban(db_engine, ip)
        try:
            _repeated_429s(client, ip)
            cur = db_engine.cursor()
            cur.execute("SELECT expires_at FROM banned_ips WHERE ip=%s", (ip,))
            row = cur.fetchone()
            cur.close()
            assert row is not None
            assert row[0] > datetime.datetime.now()

            # Once banned, _enforce_ip_ban blocks the very next request outright.
            blocked = client.get("/login", environ_overrides={"REMOTE_ADDR": ip})
            assert blocked.status_code == 403
        finally:
            waf_module._breach_log.pop(ip, None)
            _unban(db_engine, ip)
