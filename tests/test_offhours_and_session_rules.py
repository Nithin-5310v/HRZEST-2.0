"""Two detections from the SOC/HRMS gap plan, item 4:

* payroll_off_hours: pay, payslip or bank data used outside the company's
  working hours, working days or holidays (company_settings, its time zone).
* session_browser_switch / session_network_hopping: a signed-in session used
  from another browser, or moving across several networks
  (utils/session_context.py records the changes, app_setup/session_watch.py
  calls it on every signed-in request).
"""
import datetime
import json

import pytest

from utils import detections
from utils import session_context as sc

RULES = {r.name: r for r in detections.RULES}
CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
CHROME_WIN_NEWER = CHROME_WIN.replace("131.0.0.0", "132.0.0.0")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"


def _fire(db_engine, name, scope_label=None):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM detection_state WHERE rule=%s", (name,))
    events = []
    fired = detections.evaluate(cur, RULES[name], scope_label, lambda et, msg, **f: events.append((et, msg, f)))
    cur.close()
    return fired, events


class TestBrowserAndNetwork:
    @pytest.mark.parametrize("ua, family", [
        (CHROME_WIN, "Chrome on Windows"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 Edg/131.0",
         "Edge on Windows"),
        (FIREFOX_LINUX, "Firefox on Linux"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
         "Mobile/15E148 Safari/604.1", "Safari on iOS"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0 "
         "Mobile/15E148 Safari/604.1", "Chrome on iOS"),
        ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36",
         "Chrome on Android"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
         "Safari on macOS"),
        ("curl/8.5.0", "curl"), ("python-requests/2.32.3", "python-requests"), ("", "no User-Agent"),
    ])
    def test_browser_family_leaves_the_version_out(self, ua, family):
        assert sc.browser_of(ua) == family

    def test_a_browser_update_is_not_a_change(self):
        assert sc.browser_of(CHROME_WIN) == sc.browser_of(CHROME_WIN_NEWER)

    @pytest.mark.parametrize("ip, net", [("203.0.113.77", "203.0.113.0/24"), ("::ffff:198.51.100.9", "198.51.100.0/24"),
                                         ("2001:db8:abcd:12::1", "2001:db8:abcd::/48"), ("not-an-ip", "not-an-ip")])
    def test_network_of(self, ip, net):
        assert sc.network_of(ip) == net

    def test_observe_records_each_change_once(self):
        s = {"_sid": "a" * 32}
        assert sc.observe(s, "203.0.113.5", CHROME_WIN) == []                         # where it started
        assert sc.observe(s, "203.0.113.99", CHROME_WIN_NEWER) == []                  # same /24, same family
        [(what, before, after, extra)] = sc.observe(s, "203.0.113.5", FIREFOX_LINUX)
        assert (what, before, after) == ("browser", "Chrome on Windows", "Firefox on Linux")
        assert sc.observe(s, "203.0.113.5", FIREFOX_LINUX) == []                      # not again
        [(what, before, after, extra)] = sc.observe(s, "198.51.100.1", FIREFOX_LINUX)
        assert (what, before, after, extra["returning"]) == ("network", "203.0.113.0/24", "198.51.100.0/24", False)
        [(_, _, _, extra)] = sc.observe(s, "203.0.113.7", FIREFOX_LINUX)
        assert extra["returning"] is True and extra["login_network"] == "203.0.113.0/24"

    def test_a_new_session_id_starts_over(self):
        s = {"_sid": "a" * 32}
        sc.observe(s, "203.0.113.5", CHROME_WIN)
        s["_sid"] = "b" * 32                                       # e.g. rotated after a password change
        assert sc.observe(s, "198.51.100.1", FIREFOX_LINUX) == []
        assert s["_ctx"]["ua0"] == "Firefox on Linux"

    def test_no_session_id_no_tracking(self):
        s = {}
        assert sc.observe(s, "203.0.113.5", CHROME_WIN) == [] and "_ctx" not in s


class TestSignedInRequests:
    def _events(self, db_engine, identifier, mark):
        cur = db_engine.cursor()
        cur.execute("SELECT event_type, level, ip, extra_json FROM security_events WHERE identifier=%s AND id > %s "
                    "AND event_type LIKE 'session.%%_changed' ORDER BY id", (identifier, mark))
        rows = [(et, lvl, ip, json.loads(extra)) for et, lvl, ip, extra in cur.fetchall()]
        cur.close()
        return rows

    def test_a_session_moving_browser_and_network_is_recorded(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM security_events")
        mark = cur.fetchone()[0]
        cur.close()
        home = {"REMOTE_ADDR": "203.0.113.5", "HTTP_USER_AGENT": CHROME_WIN}
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]},
                    environ_base=home)
        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") and sess.get("_sid")
        client.get("/admin", environ_base=home)
        client.get("/admin", environ_base=home)
        assert self._events(db_engine, seed_admin["username"], mark) == []
        client.get("/admin", environ_base={"REMOTE_ADDR": "198.51.100.23", "HTTP_USER_AGENT": "curl/8.5.0"})
        events = self._events(db_engine, seed_admin["username"], mark)
        assert [(e[0], e[1], e[2]) for e in events] == [
            ("session.browser_changed", "WARNING", "198.51.100.23"),
            ("session.network_changed", "INFO", "198.51.100.23"),
        ]
        browser, network = events[0][3], events[1][3]
        assert (browser["from_browser"], browser["to_browser"]) == ("Chrome on Windows", "curl")
        assert (network["from_network"], network["to_network"]) == ("203.0.113.0/24", "198.51.100.0/24")
        assert len(browser["session_ref"]) == 12 and browser["session_ref"] == network["session_ref"]
        # the raw session id never leaves the cookie
        with client.session_transaction() as sess:
            assert sess["_sid"] not in json.dumps(events)

    def test_anonymous_requests_are_not_tracked(self, client):
        client.get("/login", environ_base={"REMOTE_ADDR": "203.0.113.5", "HTTP_USER_AGENT": CHROME_WIN})
        client.get("/login", environ_base={"REMOTE_ADDR": "198.51.100.1", "HTTP_USER_AGENT": FIREFOX_LINUX})
        with client.session_transaction() as sess:
            assert "_ctx" not in sess


class TestSessionRules:
    def _event(self, db_engine, event_type, identifier, ip):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO security_events (event_type, level, message, identifier, ip) VALUES (%s,'WARNING','t',%s,%s)",
                    (event_type, identifier, ip))
        cur.close()

    def test_browser_switch_fires_per_account(self, db_engine):
        self._event(db_engine, "session.browser_changed", "victim_one", "198.51.100.23")
        fired, events = _fire(db_engine, "session_browser_switch")
        assert "victim_one" in [f["key"] for f in fired]
        assert any(f["identifier"] == "victim_one" and "another browser" in msg for _, msg, f in events)

    def test_network_hopping_needs_several_networks(self, db_engine):
        for ip in ("198.51.100.1", "203.0.113.1", "192.0.2.1"):
            self._event(db_engine, "session.network_changed", "commuter_two", ip)
        fired, _ = _fire(db_engine, "session_network_hopping")
        assert "commuter_two" not in [f["key"] for f in fired]          # three is a normal day on a phone
        self._event(db_engine, "session.network_changed", "commuter_two", "100.64.7.1")
        fired, _ = _fire(db_engine, "session_network_hopping")
        assert "commuter_two" in [f["key"] for f in fired]


class TestPayrollOffHours:
    @pytest.fixture
    def company(self, db_engine):
        """Make company_settings say UTC, and hand the test a setter for hours and days."""
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM company_settings")
        if not cur.fetchone()[0]:
            cur.execute("INSERT INTO company_settings (company_name) VALUES ('Test Co')")
        cur.execute("SELECT (NOW() AT TIME ZONE 'UTC')")
        now = cur.fetchone()[0]

        def set_hours(start_hour, end_hour, days="Mon,Tue,Wed,Thu,Fri,Sat,Sun"):
            cur.execute("UPDATE company_settings SET timezone='UTC', shift_start=%s, shift_end=%s, working_days=%s",
                        (datetime.time(start_hour % 24), datetime.time(end_hour % 24), days))
        yield now, set_hours
        cur.close()

    def _access(self, db_engine, actor, action="view_payslip", actor_type="admin"):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO audit_logs (actor, actor_type, action, target_id) VALUES (%s, %s, %s, 'TST001:2025-01')",
                    (actor, actor_type, action))
        cur.close()

    def test_access_far_outside_the_shift_fires(self, db_engine, company):
        now, set_hours = company
        set_hours(now.hour + 8, now.hour + 9)                    # the shift is 8 hours away: well beyond the grace
        self._access(db_engine, "night_owl")
        self._access(db_engine, "night_owl", "export_salary_report")
        self._access(db_engine, "scheduler", "unlock_payroll", actor_type="system")
        fired, events = _fire(db_engine, "payroll_off_hours", "public")
        keys = [f["key"] for f in fired]
        assert "public:night_owl" in keys and "public:scheduler" not in keys
        msg = next(m for _, m, f in events if f["identifier"] == "night_owl")
        assert "2 time(s) outside working hours" in msg and "export_salary_report" in msg and "view_payslip" in msg

    def test_access_within_the_shift_does_not_fire(self, db_engine, company):
        now, set_hours = company
        set_hours(now.hour - 1, now.hour + 1)                     # wraps past midnight near 00:00 / 23:00 too
        self._access(db_engine, "day_worker")
        fired, _ = _fire(db_engine, "payroll_off_hours", "public")
        assert "public:day_worker" not in [f["key"] for f in fired]

    def test_a_non_working_day_fires_even_within_hours(self, db_engine, company):
        now, set_hours = company
        others = ",".join(d for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") if d != now.strftime("%a"))
        set_hours(now.hour - 1, now.hour + 1, days=others)
        self._access(db_engine, "weekend_worker")
        fired, _ = _fire(db_engine, "payroll_off_hours", "public")
        assert "public:weekend_worker" in [f["key"] for f in fired]

    def test_a_holiday_fires_even_within_hours(self, db_engine, company):
        now, set_hours = company
        set_hours(now.hour - 1, now.hour + 1)
        cur = db_engine.cursor()
        cur.execute("INSERT INTO holidays (date, name) VALUES (%s, 'Test holiday') ON CONFLICT (date) DO NOTHING",
                    (now.date(),))
        cur.close()
        self._access(db_engine, "holiday_worker", "update_bank_details", actor_type="employee")
        fired, _ = _fire(db_engine, "payroll_off_hours", "public")
        assert "public:holiday_worker" in [f["key"] for f in fired]

    def test_other_actions_are_not_payroll(self, db_engine, company):
        now, set_hours = company
        set_hours(now.hour + 8, now.hour + 9)
        self._access(db_engine, "leave_approver", "approve_leave")
        fired, _ = _fire(db_engine, "payroll_off_hours", "public")
        assert "public:leave_approver" not in [f["key"] for f in fired]
