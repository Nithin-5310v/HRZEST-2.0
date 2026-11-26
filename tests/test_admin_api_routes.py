"""Admin-side routes the app (Bearer token) and the admin pages' fetch()
calls use that had no route-level test: the mobile Settings saves
(/api/settings/company, /geo_radius, /toggle_feature), the announcement
broadcast, departments, comp-off balances, the support chat with the
platform operator, and the payout step-up lock."""
import hashlib
import secrets
import time

import pytest


def _bearer(db_engine, identity, token_type="admin"):
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s, %s, %s, NOW() + INTERVAL '1 hour')",
        (identity, hashlib.sha256(raw.encode()).hexdigest(), token_type),
    )
    cur.close()
    return {"Authorization": f"Bearer {raw}"}


def _session(client, username="test_admin", role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _settings(db_engine, *columns):
    cur = db_engine.cursor()
    cur.execute(f"SELECT {', '.join(columns)} FROM company_settings ORDER BY id LIMIT 1")  # nosec B608 -- test literals
    row = cur.fetchone()
    cur.close()
    return row


@pytest.fixture
def admin_headers(db_engine, seed_admin):
    return _bearer(db_engine, seed_admin["username"])


@pytest.fixture
def hr_headers(db_engine, seed_hr_admin):
    return _bearer(db_engine, seed_hr_admin["username"])


@pytest.fixture
def team_scoped_hr(db_engine, seed_hr_admin):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO roles (role_key, display_name, base_type, is_system, data_scope) "
        "VALUES ('hr', 'HR', 'hr', FALSE, 'team') ON CONFLICT (role_key) DO UPDATE SET data_scope='team'"
    )
    cur.close()
    return _bearer(db_engine, seed_hr_admin["username"])


# ── Owner-admin-only settings saves (mobile Settings) ────────────────────────

@pytest.mark.parametrize("url", ["/api/settings/company", "/api/settings/geo_radius", "/api/settings/toggle_feature"])
class TestSettingsApiGuards:
    def test_needs_an_admin_token(self, client, db_engine, seed_employee, url):
        assert client.post(url, json={}).status_code == 401
        emp = _bearer(db_engine, seed_employee["employee_id"], "employee")
        assert client.post(url, json={}, headers=emp).status_code == 401

    def test_hr_is_refused(self, client, hr_headers, url):
        assert client.post(url, json={}, headers=hr_headers).status_code == 403


class TestCompanySettingsApi:
    URL = "/api/settings/company"

    def test_saves_the_company_info(self, client, db_engine, admin_headers):
        resp = client.post(self.URL, json={"company_name": "  Route Test Pvt Ltd ", "company_code": "rtc-longer-than-ten",
                                           "timezone": "Asia/Dubai", "working_days": ["Mon", "Tue", "Sat"]},
                           headers=admin_headers)
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert _settings(db_engine, "company_name", "company_code", "timezone", "working_days") == (
            "Route Test Pvt Ltd", "RTC-LONGER", "Asia/Dubai", "Mon,Tue,Sat")

    def test_working_days_as_text(self, client, db_engine, admin_headers):
        client.post(self.URL, json={"company_name": "Route Co", "working_days": "Mon, Wed,Fri"},
                    headers=admin_headers)
        assert _settings(db_engine, "working_days") == ("Mon,Wed,Fri",)

    def test_no_working_days_means_monday_to_friday(self, client, db_engine, admin_headers):
        client.post(self.URL, json={"company_name": "Route Co", "working_days": []}, headers=admin_headers)
        assert _settings(db_engine, "working_days") == ("Mon,Tue,Wed,Thu,Fri",)

    @pytest.mark.parametrize("body,needle", [
        ({"company_name": "Route Co", "timezone": "Mars/Olympus_Mons"}, "timezone"),
        ({"company_name": "Route Co", "working_days": ["Mon", "Funday"]}, "working days"),
    ])
    def test_bad_values_change_nothing(self, client, db_engine, admin_headers, body, needle):
        before = _settings(db_engine, "company_name", "timezone", "working_days")
        resp = client.post(self.URL, json=body, headers=admin_headers)
        assert resp.status_code == 400 and needle in resp.get_json()["msg"]
        assert _settings(db_engine, "company_name", "timezone", "working_days") == before


class TestGeoRadiusApi:
    URL = "/api/settings/geo_radius"

    def test_saves_radius_and_office(self, client, db_engine, admin_headers):
        resp = client.post(self.URL, json={"geo_radius": "250", "office_lat": "12.9716", "office_lon": 77.5946},
                           headers=admin_headers)
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        radius, lat, lon = _settings(db_engine, "geo_radius", "office_lat", "office_lon")
        assert int(radius) == 250 and float(lat) == pytest.approx(12.9716) and float(lon) == pytest.approx(77.5946)

    def test_radius_alone_keeps_the_office(self, client, db_engine, admin_headers):
        _lat, _lon = _settings(db_engine, "office_lat", "office_lon")
        assert client.post(self.URL, json={"geo_radius": 500}, headers=admin_headers).status_code == 200
        assert _settings(db_engine, "office_lat", "office_lon") == (_lat, _lon)
        assert int(_settings(db_engine, "geo_radius")[0]) == 500

    @pytest.mark.parametrize("body", [
        {"geo_radius": 10}, {"geo_radius": 6000}, {"geo_radius": "wide"},
        {"geo_radius": 200, "office_lat": "12.97"},                       # half a coordinate
        {"geo_radius": 200, "office_lat": "95", "office_lon": "77"},      # off the globe
        {"geo_radius": 200, "office_lat": "north", "office_lon": "east"},
    ])
    def test_bad_values_change_nothing(self, client, db_engine, admin_headers, body):
        before = _settings(db_engine, "geo_radius", "office_lat", "office_lon")
        assert client.post(self.URL, json=body, headers=admin_headers).status_code == 400
        assert _settings(db_engine, "geo_radius", "office_lat", "office_lon") == before


class TestToggleFeatureApi:
    URL = "/api/settings/toggle_feature"

    @pytest.mark.parametrize("feature", ["notify_leave", "notify_doc_expiry", "pin_enabled"])
    def test_turns_a_feature_on_and_off(self, client, db_engine, admin_headers, feature):
        assert client.post(self.URL, json={"feature": feature, "value": True}, headers=admin_headers).get_json() == {
            "ok": True}
        assert int(_settings(db_engine, feature)[0]) == 1
        client.post(self.URL, json={"feature": feature, "value": False}, headers=admin_headers)
        assert int(_settings(db_engine, feature)[0]) == 0

    @pytest.mark.parametrize("feature", ["", "employee_password_auth", "company_name", "geo_enabled_extra"])
    def test_only_listed_features(self, client, admin_headers, feature):
        resp = client.post(self.URL, json={"feature": feature, "value": True}, headers=admin_headers)
        assert resp.status_code == 400 and resp.get_json()["msg"] == "Unknown feature."


# ── POST /api/notifications/broadcast ────────────────────────────────────────

class TestBroadcastApi:
    URL = "/api/notifications/broadcast"

    @pytest.fixture(autouse=True)
    def _no_push(self, monkeypatch):
        monkeypatch.setattr("utils.push_utils._send_expo_push", lambda *a, **k: [])

    def _notified(self, db_engine, title):
        cur = db_engine.cursor()
        cur.execute("SELECT employee_id FROM notifications WHERE recipient_type='employee' AND title=%s",
                    (f"📢 {title}",))
        out = sorted(r[0] for r in cur.fetchall())
        cur.close()
        return out

    def _announcement(self, db_engine, title):
        cur = db_engine.cursor()
        cur.execute("SELECT content, visibility, target_employee_id FROM announcements WHERE title=%s", (title,))
        row = cur.fetchall()
        cur.close()
        return row

    def test_guards(self, client, db_engine, seed_employee, hr_headers):
        body = {"title": "Route hello", "message": "Hi"}
        assert client.post(self.URL, json=body).status_code == 401
        assert client.post(self.URL, json=body, headers=_bearer(db_engine, "TST001", "employee")).status_code == 401
        assert client.post(self.URL, json=body, headers=hr_headers).status_code == 403
        assert self._announcement(db_engine, "Route hello") == []

    def test_everyone_gets_a_public_announcement(self, client, db_engine, admin_headers, seed_employee,
                                                 seed_assigned_employee):
        cur = db_engine.cursor()
        cur.execute("SELECT employee_id FROM employees WHERE is_active=1")
        active = sorted(r[0] for r in cur.fetchall())
        cur.close()
        resp = client.post(self.URL, json={"title": "Route all-hands", "message": "Town hall at 4"},
                           headers=admin_headers)
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert self._announcement(db_engine, "Route all-hands") == [("Town hall at 4", "public", None)]
        assert self._notified(db_engine, "Route all-hands") == active and "TST001" in active

    def test_one_employee_gets_a_private_one(self, client, db_engine, admin_headers, seed_employee,
                                             seed_assigned_employee):
        resp = client.post(self.URL, json={"title": "Route private", "message": "See me", "audience": "TST002"},
                           headers=admin_headers)
        assert resp.status_code == 200
        assert self._announcement(db_engine, "Route private") == [("See me", "private", "TST002")]
        assert self._notified(db_engine, "Route private") == ["TST002"]

    @pytest.mark.parametrize("body,needle", [
        ({"title": "", "message": "x"}, "required"),
        ({"title": "Route t", "message": "  "}, "required"),
        ({"title": "Route t", "message": "x", "audience": "NOPE99"}, "Unknown employee_id"),
    ])
    def test_refusals_post_nothing(self, client, db_engine, admin_headers, body, needle):
        resp = client.post(self.URL, json=body, headers=admin_headers)
        assert resp.status_code == 400 and needle in resp.get_json()["msg"]
        assert self._announcement(db_engine, "Route t") == []


# ── GET /api/departments and /api/compoff ────────────────────────────────────

def _set(db_engine, sql, params):
    cur = db_engine.cursor()
    cur.execute(sql, params)
    cur.close()


class TestDepartmentsApi:
    def test_needs_an_admin_token(self, client, db_engine, seed_employee):
        assert client.get("/api/departments").status_code == 401
        assert client.get("/api/departments", headers=_bearer(db_engine, "TST001", "employee")).status_code == 401

    def test_counts_active_employees_per_department(self, client, db_engine, admin_headers, seed_employee,
                                                    seed_assigned_employee, seed_direct_report):
        _set(db_engine, "UPDATE employees SET department='Route QA', is_active=1 WHERE employee_id IN ('TST001','TST002')", ())
        _set(db_engine, "UPDATE employees SET department='Route Gone', is_active=0 WHERE employee_id='TST003'", ())
        body = client.get("/api/departments", headers=admin_headers).get_json()
        assert body["ok"] is True
        counts = {d["name"]: d["count"] for d in body["departments"]}
        assert counts["Route QA"] == 2
        assert "Route Gone" not in counts and "" not in counts


class TestCompoffApi:
    def test_needs_an_admin_token(self, client, db_engine, seed_employee):
        assert client.get("/api/compoff").status_code == 401
        assert client.get("/api/compoff", headers=_bearer(db_engine, "TST001", "employee")).status_code == 401

    def test_balances_in_days(self, client, db_engine, admin_headers, seed_employee):
        _set(db_engine, "UPDATE company_settings SET compoff_minutes_per_day=480", ())
        _set(db_engine, "INSERT INTO compoff_balance (employee_id, earned_minutes, used_minutes) VALUES (%s, 960, 240) "
                        "ON CONFLICT (employee_id) DO UPDATE SET earned_minutes=960, used_minutes=240", ("TST001",))
        balances = {b["employee_id"]: b for b in client.get("/api/compoff", headers=admin_headers).get_json()["balances"]}
        mine = balances["TST001"]
        assert (mine["earned_days"], mine["used_days"], mine["balance_days"]) == (2.0, 0.5, 1.5)
        assert mine["name"] == "Test Employee"

    def test_a_team_scoped_token_sees_only_its_team(self, client, db_engine, team_scoped_hr, seed_employee,
                                                    seed_assigned_employee):
        ids = {b["employee_id"] for b in client.get("/api/compoff", headers=team_scoped_hr).get_json()["balances"]}
        assert "TST002" in ids and "TST001" not in ids


# ── Support chat with the platform operator (admin session) ──────────────────

class TestAdminChat:
    def test_needs_an_admin_session(self, client, seed_employee):
        assert client.get("/api/admin/chat/messages").status_code in (302, 401)
        assert client.post("/api/admin/chat/send", json={"message": "hi"}).status_code in (302, 401)
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        assert client.post("/api/admin/chat/send", json={"message": "hi"}).status_code in (302, 401)

    def test_send_then_read_the_thread(self, client, db_engine, seed_admin):
        _session(client)
        assert client.post("/api/admin/chat/send", json={"message": "  Route: need help with billing  "}).get_json() == {
            "ok": True}
        cur = db_engine.cursor()
        cur.execute("SELECT tenant_schema, sender_kind, message, read_by_company, read_by_platform "
                    "FROM att_master.chat_messages WHERE sender_name='test_admin'")
        [(tenant_schema, kind, message, by_company, by_platform)] = cur.fetchall()
        assert (kind, message, by_company, by_platform) == ("admin", "Route: need help with billing", 1, 0)
        # The operator answers; opening the thread marks it read on the company side.
        cur.execute("INSERT INTO att_master.chat_messages (tenant_schema, sender_kind, sender_name, message, "
                    "read_by_platform, read_by_company) VALUES (%s, 'platform_admin', 'ops', 'Route: on it', 1, 0)",
                    (tenant_schema,))
        body = client.get("/api/admin/chat/messages").get_json()
        assert [m["message"] for m in body["messages"]][-2:] == ["Route: need help with billing", "Route: on it"]
        cur.execute("SELECT read_by_company FROM att_master.chat_messages WHERE sender_name='ops' AND tenant_schema=%s",
                    (tenant_schema,))
        assert cur.fetchone()[0] == 1
        cur.close()

    def test_hr_messages_are_marked_as_hr(self, client, db_engine, seed_hr_admin):
        _session(client, seed_hr_admin["username"], "hr")
        assert client.post("/api/admin/chat/send", json={"message": "Route from HR"}).status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT sender_kind FROM att_master.chat_messages WHERE message='Route from HR'")
        assert cur.fetchone()[0] == "hr"
        cur.close()

    @pytest.mark.parametrize("body", [{}, {"message": "   "}, {"message": None}])
    def test_an_empty_message_is_refused(self, client, db_engine, seed_admin, body):
        _session(client)
        resp = client.post("/api/admin/chat/send", json=body)
        assert resp.status_code == 400
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM att_master.chat_messages WHERE sender_name='test_admin'")
        assert cur.fetchone()[0] == 0
        cur.close()


# ── POST /api/payout/lock ────────────────────────────────────────────────────

class TestPayoutLock:
    def test_clears_the_payout_step_up(self, client, seed_admin):
        _session(client)
        with client.session_transaction() as sess:
            sess["payout_2fa_verified_at"] = time.time()
            sess["email_2fa_verified_at"] = time.time()
        assert client.post("/api/payout/lock", json={}).get_json() == {"ok": True}
        with client.session_transaction() as sess:
            assert "payout_2fa_verified_at" not in sess
            assert "email_2fa_verified_at" in sess, "the email-settings step-up is a separate gate"

    def test_owner_admin_only(self, client, seed_hr_admin):
        assert client.post("/api/payout/lock", json={}).status_code == 401
        _session(client, seed_hr_admin["username"], "hr")
        assert client.post("/api/payout/lock", json={}).status_code == 403
