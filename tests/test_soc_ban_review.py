"""Ban review queue (utils/soc_ban_review.py): automated detections propose a
ban, a SOC analyst decides -- ban / dismiss / trust -- in the console.

Why: a per-IP threshold can't tell an attacker from a customer office whose
employees share one address. A shift-start login rush, one infected office
PC, or the customer's own security scan used to lock the whole office out
(up to 7 days for a honeypot hit) with nobody deciding.
"""
import pytest

from extensions import app as flask_app
from utils import soc_ban_review
from tests import test_soc_console as _console
from tests.test_soc_console import PUBLIC_IP, audit_actions

# The console tests' fixtures: a signed-in SOC session, and its autouse reset.
soc = _console.soc
_fresh_soc_state = _console._fresh_soc_state

IP = "198.51.100.150"
OTHER = "198.51.100.151"


@pytest.fixture(autouse=True)
def _clean(db_engine, monkeypatch):
    monkeypatch.delenv("SOC_AUTO_BAN", raising=False)

    def clear():
        cur = db_engine.cursor()
        for ip in (IP, OTHER, PUBLIC_IP):
            cur.execute("DELETE FROM public.banned_ips WHERE ip=%s", (ip,))
            cur.execute("DELETE FROM att_master.soc_ban_reviews WHERE ip=%s", (ip,))
        db_engine.commit()
        cur.close()

    clear()
    yield
    clear()


def _row(db_engine, ip):
    cur = db_engine.cursor()
    cur.execute("SELECT status, hits, source, suggested_minutes, decided_by, trusted_until "
                "FROM att_master.soc_ban_reviews WHERE ip=%s", (ip,))
    row = cur.fetchone()
    cur.close()
    return row


def _banned(db_engine, ip):
    cur = db_engine.cursor()
    cur.execute("SELECT reason, banned_by, expires_at FROM public.banned_ips WHERE ip=%s", (ip,))
    row = cur.fetchone()
    cur.close()
    return row


class TestRaiseReview:
    def test_detection_queues_without_banning(self, db_engine):
        assert soc_ban_review.raise_review(IP, "honeypot", "Honeypot (/.env)", suggested_minutes=10080) == "pending"
        status, hits, source, minutes, _, _ = _row(db_engine, IP)
        assert (status, hits, source, minutes) == ("pending", 1, "honeypot", 10080)
        assert _banned(db_engine, IP) is None

    def test_repeat_hits_count_and_keep_the_strongest_suggestion(self, db_engine):
        soc_ban_review.raise_review(IP, "ratelimit", "429s", suggested_minutes=15)
        soc_ban_review.raise_review(IP, "honeypot", "Honeypot (/.git/config)", suggested_minutes=10080)
        soc_ban_review.raise_review(IP, "waf", "WAF blocks", suggested_minutes=15)
        status, hits, source, minutes, _, _ = _row(db_engine, IP)
        assert (status, hits, source, minutes) == ("pending", 3, "waf", 10080)

    def test_shared_and_private_addresses_are_never_proposed(self, db_engine):
        for ip in ("10.1.2.3", "127.0.0.1", "192.168.0.9", "not-an-ip"):
            assert soc_ban_review.raise_review(ip, "honeypot", "x") is None
            assert _row(db_engine, ip) is None

    def test_dismissed_address_reopens_on_the_next_detection(self, db_engine):
        soc_ban_review.raise_review(IP, "waf", "first")
        assert soc_ban_review.decide(IP, "dismiss", "ann", OTHER, note="our own test") == (True, "dismissed")
        assert _row(db_engine, IP)[0] == "dismissed"
        assert soc_ban_review.raise_review(IP, "waf", "again") == "pending"
        status, _, _, _, decided_by, _ = _row(db_engine, IP)
        assert status == "pending" and decided_by is None

    def test_trusted_address_stays_trusted_and_only_counts(self, db_engine):
        soc_ban_review.raise_review(IP, "ratelimit", "login rush")
        assert soc_ban_review.decide(IP, "trust", "ann", OTHER, days=30, note="Acme office") == (True, "trusted")
        assert soc_ban_review.raise_review(IP, "ratelimit", "login rush") == "trusted"
        status, hits, _, _, _, until = _row(db_engine, IP)
        assert status == "trusted" and hits == 2 and until is not None
        assert _banned(db_engine, IP) is None

    def test_expired_trust_reopens(self, db_engine):
        soc_ban_review.raise_review(IP, "waf", "x")
        soc_ban_review.decide(IP, "trust", "ann", OTHER, days=30)
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.soc_ban_reviews SET trusted_until = NOW() - INTERVAL '1 day' WHERE ip=%s", (IP,))
        db_engine.commit()
        cur.close()
        assert soc_ban_review.raise_review(IP, "waf", "x") == "pending"

    def test_soc_auto_ban_bans_and_records_the_decision(self, db_engine, monkeypatch):
        monkeypatch.setenv("SOC_AUTO_BAN", "true")
        assert soc_ban_review.raise_review(IP, "honeypot", "Honeypot (/.env)", suggested_minutes=60) == "banned"
        reason, by, expires = _banned(db_engine, IP)
        assert by == "system:auto" and expires is not None
        assert _row(db_engine, IP)[0] == "banned" and _row(db_engine, IP)[4] == "system:auto"

    def test_auto_ban_never_overrides_trust(self, db_engine, monkeypatch):
        soc_ban_review.raise_review(IP, "waf", "x")
        soc_ban_review.decide(IP, "trust", "ann", OTHER, days=0)
        monkeypatch.setenv("SOC_AUTO_BAN", "true")
        assert soc_ban_review.raise_review(IP, "waf", "x") == "trusted"
        assert _banned(db_engine, IP) is None

    def test_a_storage_failure_never_raises(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(soc_ban_review, "write_cursor", boom)
        assert soc_ban_review.raise_review(IP, "waf", "x") is None


class TestDecide:
    def test_ban_uses_the_soc_ban_path(self, db_engine):
        soc_ban_review.raise_review(IP, "honeypot", "Honeypot (/.env)", suggested_minutes=10080)
        assert soc_ban_review.decide(IP, "ban", "ann", OTHER, minutes=60, note="confirmed scanner") == (True, "banned")
        reason, by, expires = _banned(db_engine, IP)
        assert by == "soc:ann" and expires is not None
        assert "Honeypot (/.env)" in reason and "confirmed scanner" in reason
        assert _row(db_engine, IP)[:1] == ("banned",)
        # ...and the app refuses it.
        assert flask_app.test_client().get("/healthz2", environ_overrides={"REMOTE_ADDR": IP}).status_code == 403

    def test_ban_keeps_the_soc_guards(self, db_engine):
        soc_ban_review.raise_review(IP, "waf", "x")
        ok, msg = soc_ban_review.decide(IP, "ban", "ann", IP, minutes=60)     # analyst's own address
        assert not ok and "connected from" in msg
        assert _row(db_engine, IP)[0] == "pending"

    @pytest.mark.parametrize("ip, action", [("not-an-ip", "ban"), (IP, "explode"), (OTHER, "dismiss")])
    def test_rejects_bad_input_and_unknown_addresses(self, ip, action):
        soc_ban_review.raise_review(IP, "waf", "x")
        ok, _ = soc_ban_review.decide(ip, action, "ann", "203.0.113.250")
        assert not ok

    def test_trust_until_revoked(self, db_engine):
        soc_ban_review.raise_review(IP, "waf", "x")
        soc_ban_review.decide(IP, "trust", "ann", OTHER, days=0)
        assert _row(db_engine, IP)[5] is None


class TestConsoleApi:
    def test_viewer_can_list_but_not_decide(self, soc):
        soc_ban_review.raise_review(PUBLIC_IP, "honeypot", "Honeypot (/.env)", suggested_minutes=10080)
        c, _ = soc("vic", "viewer")
        d = c.get("/soc/api/ban-reviews").get_json()
        assert d["ok"] and d["status"] == "pending" and d["counts"]["pending"] >= 1
        assert any(r["ip"] == PUBLIC_IP and r["source"] == "honeypot" for r in d["reviews"])
        assert c.post("/soc/api/ban-reviews/decide", json={"ip": PUBLIC_IP, "action": "dismiss"}).status_code == 403

    def test_analyst_decisions_are_audited(self, soc, db_engine):
        soc_ban_review.raise_review(PUBLIC_IP, "ratelimit", "Repeated rate-limit breaches", suggested_minutes=15)
        c, _ = soc("ann", "analyst")
        r = c.post("/soc/api/ban-reviews/decide", json={"ip": PUBLIC_IP, "action": "trust", "days": 30, "note": "Acme office"})
        assert r.status_code == 200 and r.get_json()["message"] == "trusted"
        assert c.get("/soc/api/ban-reviews?status=trusted").get_json()["reviews"][0]["decided_by"] == "ann"
        r = c.post("/soc/api/ban-reviews/decide", json={"ip": PUBLIC_IP, "action": "ban", "minutes": 60})
        assert r.status_code == 200
        acts = [(a, t) for _, a, t in audit_actions(db_engine)]
        assert ("ban_review_trust", PUBLIC_IP) in acts and ("ban_ip", PUBLIC_IP) in acts

    def test_bad_decision_is_a_400_not_a_500(self, soc):
        c, _ = soc("ann", "analyst")
        for body in ({"ip": PUBLIC_IP, "action": "ban"}, {"ip": 5, "action": "trust"}, {"action": ["x"]}, {}):
            assert c.post("/soc/api/ban-reviews/decide", json=body).status_code == 400

    def test_view_script_is_served_only_when_signed_in(self, soc):
        assert flask_app.test_client().get("/soc/assets/soc-banreview.js").status_code == 404
        c, _ = soc("vic", "viewer")
        r = c.get("/soc/assets/soc-banreview.js")
        assert r.status_code == 200 and b"SOC.register" in r.data
        assert b"/soc/assets/soc-banreview.js" in c.get("/soc").data
