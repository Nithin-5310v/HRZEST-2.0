"""Public and platform routes with no route-level test: the app's signup
follow-ups (/api/create_org/resend_otp, /upload_documents, /status/<id>),
the web /create_org/pending page, /security_lockout, the old /superadmin
link, every HTTP honeypot decoy path, and the platform admin's rate form
(/super_admin/rate)."""
import io
import time

import pytest

from tests.signup_flow import FAKE_PDF, FAKE_PNG, MOBILE, capture_codes

_SIGNUP = {"company_name": "Route Signup Co", "admin_email": "owner@routesignup.test", "admin_mobile": MOBILE,
           "admin_password": "password-1234"}


def _start(client, monkeypatch):
    codes = capture_codes(monkeypatch)
    body = client.post("/api/create_org", json=_SIGNUP).get_json()
    assert body["ok"] and body["step"] == "otp", body
    return body, codes


def _application(db_engine, application_id):
    cur = db_engine.cursor()
    cur.execute("SELECT status, otp_code_hash, otp_attempts, doc_review_status, doc_registration_cert "
                "FROM att_master.tenant_applications WHERE id=%s", (application_id,))
    row = cur.fetchone()
    cur.close()
    return row


def _verified_without_a_company(client, db_engine, monkeypatch):
    """An application at 'otp_verified' (the older flow, whose code step
    comes before the company exists) -- ready for documents without
    provisioning a whole tenant schema."""
    body, codes = _start(client, monkeypatch)
    cur = db_engine.cursor()
    cur.execute("UPDATE att_master.tenant_applications SET company_name='' WHERE id=%s", (body["application_id"],))
    cur.close()
    verified = client.post("/api/create_org/verify_otp", json={
        "application_id": body["application_id"], "access_token": body["access_token"], "otp_code": codes[-1]})
    assert verified.get_json()["step"] == "profile"
    assert _application(db_engine, body["application_id"])[0] == "otp_verified"
    return body


def _documents(**overrides):
    files = {"registration_cert": (io.BytesIO(FAKE_PDF), "cert.pdf"),
             "address_proof": (io.BytesIO(FAKE_PDF), "address.pdf"),
             "visiting_card": (io.BytesIO(FAKE_PNG), "card.png"),
             "name_board_photo": (io.BytesIO(FAKE_PNG), "board.png")}
    files.update(overrides)
    return {k: v for k, v in files.items() if v is not None}


# ── POST /api/create_org/resend_otp ─────────────────────────────────────────

class TestResendSignupCode:
    URL = "/api/create_org/resend_otp"

    def test_sends_a_fresh_code(self, client, db_engine, monkeypatch):
        body, codes = _start(client, monkeypatch)
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.tenant_applications SET otp_attempts=3 WHERE id=%s", (body["application_id"],))
        cur.close()
        old_hash = _application(db_engine, body["application_id"])[1]
        resp = client.post(self.URL, json={"application_id": body["application_id"],
                                           "access_token": body["access_token"]})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert len(codes) == 2
        _status, new_hash, attempts, _review, _doc = _application(db_engine, body["application_id"])
        assert new_hash != old_hash and attempts == 0

    @pytest.mark.parametrize("token", ["", "not-the-token"])
    def test_needs_the_applications_token(self, client, db_engine, monkeypatch, token):
        body, codes = _start(client, monkeypatch)
        resp = client.post(self.URL, json={"application_id": body["application_id"], "access_token": token})
        assert resp.status_code == 400 and len(codes) == 1

    def test_no_new_code_once_verified(self, client, db_engine, monkeypatch):
        body = _verified_without_a_company(client, db_engine, monkeypatch)
        resp = client.post(self.URL, json={"application_id": body["application_id"],
                                           "access_token": body["access_token"]})
        assert resp.status_code == 400


# ── GET /api/create_org/status/<id> ─────────────────────────────────────────

class TestSignupStatus:
    def test_reports_the_applications_own_status(self, client, monkeypatch):
        body, _codes = _start(client, monkeypatch)
        resp = client.get(f"/api/create_org/status/{body['application_id']}?access_token={body['access_token']}")
        assert resp.get_json() == {"ok": True, "status": "started", "company_name": "Route Signup Co",
                                   "rejection_reason": None, "portal_url": None}

    def test_wrong_or_missing_token_reveals_nothing(self, client, monkeypatch):
        body, _codes = _start(client, monkeypatch)
        for query in ("", "?access_token=guess"):
            resp = client.get(f"/api/create_org/status/{body['application_id']}{query}")
            assert resp.status_code == 404 and "status" not in resp.get_json()
        assert client.get("/api/create_org/status/999999999?access_token=x").status_code == 404


# ── POST /api/create_org/upload_documents ───────────────────────────────────

class TestSignupDocuments:
    URL = "/api/create_org/upload_documents"

    def _post(self, client, body, **files):
        data = {"application_id": str(body["application_id"]), "access_token": body["access_token"]}
        data.update(_documents(**files))
        return client.post(self.URL, data=data, content_type="multipart/form-data")

    def test_documents_go_to_review(self, client, db_engine, monkeypatch):
        body = _verified_without_a_company(client, db_engine, monkeypatch)
        resp = self._post(client, body)
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["step"] == "pending_review"
        status, _hash, _attempts, review, cert = _application(db_engine, body["application_id"])
        assert (status, review) == ("pending_review", "pending_review") and cert

    def test_a_missing_document_is_refused(self, client, db_engine, monkeypatch):
        body = _verified_without_a_company(client, db_engine, monkeypatch)
        resp = self._post(client, body, visiting_card=None)
        assert resp.status_code == 400 and "visiting card" in resp.get_json()["msg"]
        assert _application(db_engine, body["application_id"])[0] == "otp_verified"

    def test_not_before_the_email_is_verified(self, client, db_engine, monkeypatch):
        body, _codes = _start(client, monkeypatch)
        resp = self._post(client, body)
        assert resp.status_code == 400 and "isn't ready" in resp.get_json()["msg"]
        assert _application(db_engine, body["application_id"])[4] is None

    def test_needs_the_applications_token(self, client, db_engine, monkeypatch):
        body = _verified_without_a_company(client, db_engine, monkeypatch)
        resp = self._post(client, dict(body, access_token="guess"))
        assert resp.status_code == 404
        assert _application(db_engine, body["application_id"])[0] == "otp_verified"


# ── Small public pages ───────────────────────────────────────────────────────

class TestPublicPages:
    def test_security_lockout_is_a_403_page(self, client):
        resp = client.get("/security_lockout")
        assert resp.status_code == 403 and b"<html" in resp.data.lower()

    def test_old_superadmin_link_goes_to_the_operator_login(self, client):
        resp = client.get("/superadmin")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/super_admin/login")

    def test_create_org_pending_links_to_the_status_page(self, client):
        resp = client.get("/create_org/pending?application_id=42")
        assert resp.status_code == 200 and b'href="/create_org/status/42"' in resp.data

    def test_create_org_pending_escapes_the_id(self, client):
        resp = client.get('/create_org/pending?application_id="><b>x')
        assert resp.status_code == 200 and b'"><b>x' not in resp.data


# ── HTTP honeypot decoys ─────────────────────────────────────────────────────

DECOYS = ["/.env", "/.git/config", "/wp-admin/", "/wp-login.php", "/api/v1/internal/admin_dump",
          "/actuator/health", "/phpmyadmin", "/backup.sql", "/server-status"]


@pytest.mark.parametrize("path", DECOYS)
def test_every_decoy_answers_404_and_records_the_hit(client, db_engine, path):
    resp = client.get(path, environ_overrides={"REMOTE_ADDR": "10.99.0.77"})   # private: never proposed for a ban
    assert resp.status_code == 404 and resp.get_json() == {"error": "Resource not found", "status": 404}
    cur = db_engine.cursor()
    cur.execute("SELECT level FROM security_events WHERE event_type='secops.honeypot_triggered' AND path=%s "
                "AND ip='10.99.0.77'", (path,))
    assert [r[0] for r in cur.fetchall()] == ["CRITICAL"]
    cur.close()


# ── POST /super_admin/rate ───────────────────────────────────────────────────

def _platform_admin(client):
    with client.session_transaction() as sess:
        sess["platform_admin_logged_in"] = True
        sess["platform_admin_username"] = "route_platform_admin"
        sess["platform_admin_last_activity"] = time.time()


def _costs(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT per_employee_paise, base_fee_paise, minimum_monthly_paise FROM att_master.platform_costs "
                "WHERE id=1")
    row = cur.fetchone()
    cur.close()
    return row


class TestPlatformRate:
    URL = "/super_admin/rate"

    def test_hidden_without_a_platform_admin_session(self, client, db_engine, seed_admin):
        before = _costs(db_engine)
        assert client.post(self.URL, data={"per_employee_rupees": "1"}).status_code == 404
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = "test_admin"
            sess["admin_role"] = "admin"
        assert client.post(self.URL, data={"per_employee_rupees": "1"}).status_code == 404
        assert _costs(db_engine) == before

    def test_sets_the_rate_and_optional_fees(self, client, db_engine, razorpay_demo_mode):
        from utils.plan_limits import get_per_employee_paise
        _, base_before, _ = _costs(db_engine)
        _platform_admin(client)
        resp = client.post(self.URL, data={"per_employee_rupees": "123.45", "base_fee_rupees": "",
                                           "minimum_monthly_rupees": "500"})
        assert resp.status_code == 302 and resp.headers["Location"] == "/super_admin"
        assert _costs(db_engine) == (12345, base_before, 50000)
        assert get_per_employee_paise() == 12345, "the cached rate is refreshed at once"

    @pytest.mark.parametrize("form", [
        {"per_employee_rupees": ""}, {"per_employee_rupees": "abc"}, {"per_employee_rupees": "0"},
        {"per_employee_rupees": "-5"}, {"per_employee_rupees": "99", "base_fee_rupees": "lots"},
        {"per_employee_rupees": "99", "minimum_monthly_rupees": "1,000"},
    ])
    def test_bad_values_change_nothing(self, client, db_engine, razorpay_demo_mode, form):
        before = _costs(db_engine)
        _platform_admin(client)
        resp = client.post(self.URL, data=form)
        assert resp.status_code == 302 and resp.headers["Location"] == "/super_admin"
        assert _costs(db_engine) == before

    # Regression: platform_admin_set_rate accepted float('nan') ('nan' passes the <= 0 check), then
    # round(nan * 100) raised and the form answered 500 instead of the 'Enter a valid rate' message.
    @pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
    def test_nan_is_refused_like_any_other_bad_rate(self, client, db_engine, razorpay_demo_mode, value):
        before = _costs(db_engine)
        _platform_admin(client)
        resp = client.post(self.URL, data={"per_employee_rupees": value})
        assert resp.status_code == 302
        assert _costs(db_engine) == before
