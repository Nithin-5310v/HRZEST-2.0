"""Daily SOC summary email (utils/soc_digest.py).

Why: a small team does not watch the console all day, and Ban review waits for a
person -- if nobody looks, detected attackers are never banned. One email a day
says what happened and what is waiting, using only genuine activity, and without
putting account names or event details into email.
"""
import ast
import pathlib

from tests import test_soc_console as _console
from tests.test_soc_console import PUBLIC_IP, add_event, allow, audit_actions, make_user
from utils import soc_auth, soc_digest
from utils import soc_taxonomy as tax

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches).
_fresh_soc_state = _console._fresh_soc_state

OTHER_IP = "185.220.101.1"


def _insert_review(db, ip, days_ago=0, status="pending"):
    cur = db.cursor()
    cur.execute("INSERT INTO att_master.soc_ban_reviews (ip, status, source, reason, hits, first_seen, last_seen) "
                "VALUES (%s, %s, 'waf', 'SQL injection payloads', 4, NOW() - (%s * INTERVAL '1 day'), NOW()) "
                "ON CONFLICT (ip) DO NOTHING", (ip, status, days_ago))
    cur.close()


class TestContent:
    def test_counts_only_genuine_activity_against_the_day_before(self, db_engine):
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP)
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP)
        add_event(db_engine, "auth.failure", ip=OTHER_IP, identifier="victim@example.com")
        add_event(db_engine, "detection.credential_stuffing", "ERROR", ip=PUBLIC_IP)
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, minutes_ago=60 * 30)          # the day before
        add_event(db_engine, "waf.sqli_blocked", "CRITICAL", ip="10.0.0.2")             # the server's own
        d = soc_digest.build()
        assert d["kpi"]["events"] == 4 and d["kpi"]["critical"] == 0 and d["previous_events"] == 1
        assert d["detections"] == [(tax.DETECTIONS["credential_stuffing"][0], 1)]
        assert [s["ip"] for s in d["sources"]] == [PUBLIC_IP, OTHER_IP]
        assert d["excluded"]["server"] >= 1 and d["threat"]["level"] in ("normal", "elevated", "high")

    def test_a_quiet_day_still_produces_a_summary(self):
        subject, body = soc_digest.render(soc_digest.build())
        assert "threat normal" in subject and "0 awaiting ban review" in subject
        assert "No correlation rule fired." in body and "Nothing is waiting for a decision." in body

    def test_ban_review_waiting_is_reported_with_its_age(self, db_engine, monkeypatch):
        monkeypatch.setenv("SOC_TEST_CIDRS", "151.101.0.0/16")
        _insert_review(db_engine, PUBLIC_IP, days_ago=3)
        _insert_review(db_engine, "151.101.1.10", days_ago=9)          # your own testing: left out
        _insert_review(db_engine, OTHER_IP, status="banned")           # already decided
        d = soc_digest.build()
        assert d["pending"] == 1 and d["pending_oldest"] == "3 days"
        assert [r["ip"] for r in d["reviews"]] == [PUBLIC_IP]
        subject, body = soc_digest.render(d)
        assert "1 awaiting ban review" in subject
        assert "Waiting in Ban review" in body and PUBLIC_IP in body and "waited 3 days" in body
        assert "151.101.1.10" not in body

    def test_email_carries_no_account_names_or_event_details_and_escapes_everything(self, db_engine):
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="victim@example.com", message="SECRET-DETAIL",
                  path="/acme/login")
        add_event(db_engine, "waf.xss_blocked", "ERROR", ip="<script>alert(1)</script>", message="SECRET-DETAIL")
        subject, body = soc_digest.render(soc_digest.build(), "https://hrzest.example/socperson")
        for private in ("victim@example.com", "SECRET-DETAIL", "/acme/login"):
            assert private not in body and private not in subject, private
        assert "<script>" not in body and "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        assert 'href="https://hrzest.example/socperson"' in body

    def test_proxy_warning_reaches_the_email(self, db_engine):
        for _ in range(5):
            add_event(db_engine, "auth.failure", ip="10.0.0.2")
        subject, body = soc_digest.render(soc_digest.build())
        assert "TRUSTED_PROXY_HOPS" in body


class TestSending:
    def test_recipients_are_the_listed_active_addresses(self, monkeypatch):
        allow("lead@example.com")
        allow("ana@example.com", "analyst")
        make_user("dora", "viewer")
        soc_auth.update_user(soc_auth.get_user("dora")["id"], active=False)
        assert soc_digest.recipients() == ["ana@example.com", "lead@example.com"]
        monkeypatch.setenv("SOC_LOGIN_EMAILS", "")
        assert soc_digest.recipients() == []

    def test_one_email_per_recipient_and_the_send_is_audited(self, monkeypatch, db_engine):
        import utils.email_utils as email_utils
        sent = []
        monkeypatch.setattr(email_utils, "get_email_config", lambda: {"smtp_host": "x"})
        monkeypatch.setattr(email_utils, "send_email_async", lambda to, subject, body, cfg: sent.append((to, subject)))
        monkeypatch.setenv("APP_URL", "https://hrzest.example")
        allow("lead@example.com")
        allow("ana@example.com", "analyst")
        assert soc_digest.send_daily_digest() == 2
        assert sorted(to for to, _ in sent) == ["ana@example.com", "lead@example.com"]
        assert all(subject.startswith("SOC daily summary") for _, subject in sent)
        assert ("system", "daily_digest_sent", None) in audit_actions(db_engine)

    def test_nothing_is_sent_when_turned_off_unconfigured_or_unaddressed(self, monkeypatch):
        import utils.email_utils as email_utils
        sent = []
        monkeypatch.setattr(email_utils, "send_email_async", lambda *a, **k: sent.append(a))
        monkeypatch.setattr(email_utils, "get_email_config", lambda: {"smtp_host": "x"})
        assert soc_digest.send_daily_digest() == 0                  # SOC_LOGIN_EMAILS is empty
        allow("lead@example.com")
        monkeypatch.setenv("SOC_DIGEST", "off")
        assert soc_digest.send_daily_digest() == 0
        monkeypatch.setenv("SOC_DIGEST", "on")
        monkeypatch.setattr(email_utils, "get_email_config", lambda: None)
        assert soc_digest.send_daily_digest() == 0
        assert sent == []

    def test_send_hour_setting(self, monkeypatch):
        for raw, hour in (("", 8), ("6", 6), ("25", 23), ("-3", 0), ("seven", 8)):
            monkeypatch.setenv("SOC_DIGEST_HOUR", raw)
            assert soc_digest.digest_hour() == hour, raw

    def test_it_is_scheduled_daily_through_the_once_per_run_claim(self):
        tree = ast.parse(pathlib.Path(__file__).resolve().parents[1].joinpath("wsgi.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_job"
                 and "send_daily_digest" in ast.unparse(n)]
        assert len(calls) == 1
        kw = {k.arg: k.value for k in calls[0].keywords}
        assert ast.unparse(kw["func"]).startswith("_scheduled_job(")
        assert kw["trigger"].value == "cron" and ast.unparse(kw["hour"]) == "digest_hour()"
