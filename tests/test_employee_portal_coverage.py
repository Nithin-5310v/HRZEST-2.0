"""Coverage tests for blueprints/employee_portal.py.
Targets: employee_portal, my_qr, my_id_card, update_my_profile,
experience, education, api_employee_login, api_employee_portal,
api_employee_auth_config, api_employee_logout.
"""
import hashlib
import datetime
import secrets


def _emp_session(client, seed_employee):
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    return client


def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    return client


def _make_employee_token(db_engine, emp_id):
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s,%s,'employee',%s)",
        (emp_id, token_hash, expiry)
    )
    cur.close()

    def cleanup():
        c = db_engine.cursor()
        c.execute("DELETE FROM api_tokens WHERE token=%s", (token_hash,))
        c.close()
    return raw, cleanup


# ── employee_portal ───────────────────────────────────────────────────────────

class TestEmployeePortal:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/employee_portal")
        assert rv.status_code == 302

    def test_renders_for_employee(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/employee_portal")
        assert rv.status_code == 200

    def test_renders_with_month_year_params(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/employee_portal?year=2025&month=6")
        assert rv.status_code == 200

    def test_force_pin_change_redirects(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
            sess["employee_name"] = seed_employee["name"]
            sess["_fpc"] = True
        rv = client.get("/employee_portal")
        assert rv.status_code == 302
        assert "force_change_pin" in rv.headers["Location"]


class TestEmployeePortalStoredXssEscaping:
    """Finding #7 (Medium): renderBreakSchedule()'s break name (admin-set
    free text) and the WFH check-in result panel's employee name used to
    be concatenated straight into el.innerHTML/resBox.innerHTML with no
    escaping -- a stored-XSS sink reachable by any admin setting a break
    name, or via an employee's own (admin-editable) display name. Fixed
    by adding an escHtml() helper (mirroring admin.html's existing one)
    and applying it at every server-sourced innerHTML interpolation in
    this template. These are static-source regression checks (this
    codebase has no browser/JS test harness) -- they verify the escaping
    call is actually present at each fixed sink so a future edit can't
    silently drop it back to raw concatenation."""

    def test_break_schedule_name_is_escaped(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/employee_portal")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        # The innerHTML sink specifically -- NOT the only other b.name
        # reference in the file (a browser Notification() call's `body`
        # text, which renders as plain text, not HTML, so it's not a sink).
        assert "escHtml(b.name)" in body
        assert "'<div><div class=\"u-fs-13 u-fw-700 u-c-1e293b\">' + b.name +" not in body

    def test_checkin_result_fields_are_escaped(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/employee_portal")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        assert "escHtml(result.name)" in body
        assert "+ result.name +" not in body

    def test_incentive_details_are_escaped(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/employee_portal")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        assert "escHtml(i.title)" in body
        assert "escHtml(i.notes)" in body


class TestShowViewClosesSidebarSafely:
    """showView()'s closeSidebar() call used to be unconditional, but
    closeSidebar() is only defined in static/employee_sidebar.js, which
    this template loads via a plain <script src> placed AFTER the inline
    <script> block that defines showView() -- and that same inline block
    calls showView() once, synchronously, during its own page-load-time
    "restore last active view" IIFE, well before the browser ever reaches
    the later <script src> tag. That threw an uncaught ReferenceError on
    every single page load, which aborted the rest of that script block's
    top-level execution -- silently leaving every var declared later in
    the block (attCfg, myEmpId) unset, which is what made the "Mark
    Attendance" button (openWfhScanner() -> pickAuthCombo() -> attCfg.*)
    appear to do nothing when clicked. Static-source regression check
    (this codebase has no browser/JS test harness) confirming the guard
    is in place so a future edit can't silently drop it back to an
    unconditional call."""

    def test_close_sidebar_call_is_guarded(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/employee_portal")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        assert "if (typeof closeSidebar === 'function') closeSidebar();" in body
        assert "\n        closeSidebar();" not in body


# ── my_qr ─────────────────────────────────────────────────────────────────────

class TestMyQr:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/my_qr")
        assert rv.status_code == 302

    def test_renders_for_employee(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_qr")
        assert rv.status_code == 200


# ── my_id_card ────────────────────────────────────────────────────────────────

class TestMyIdCard:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/my_id_card")
        assert rv.status_code == 302

    def test_renders_for_employee(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_id_card")
        assert rv.status_code in (200, 500)


# ── my_attendance / my_leaves / my_salary / my_profile (tab deep links) ────────

class TestPortalTabDeepLinks:

    def test_my_attendance_unauthenticated_redirects(self, client):
        rv = client.get("/my_attendance")
        assert rv.status_code == 302

    def test_my_attendance_redirects_to_portal_tab(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_attendance")
        assert rv.status_code == 302
        assert rv.headers["Location"].endswith("/employee_portal#tab-attendance")

    def test_my_leaves_unauthenticated_redirects(self, client):
        rv = client.get("/my_leaves")
        assert rv.status_code == 302

    def test_my_leaves_redirects_to_portal_tab(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_leaves")
        assert rv.status_code == 302
        assert rv.headers["Location"].endswith("/employee_portal#tab-leaves")

    def test_my_salary_unauthenticated_redirects(self, client):
        rv = client.get("/my_salary")
        assert rv.status_code == 302

    def test_my_salary_redirects_to_portal_tab(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_salary")
        assert rv.status_code == 302
        assert rv.headers["Location"].endswith("/employee_portal#tab-salary")

    def test_my_profile_unauthenticated_redirects(self, client):
        rv = client.get("/my_profile")
        assert rv.status_code == 302

    def test_my_profile_redirects_to_portal_tab(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_profile")
        assert rv.status_code == 302
        assert rv.headers["Location"].endswith("/employee_portal#tab-profile")


# ── update_my_profile ─────────────────────────────────────────────────────────

class TestUpdateMyProfile:

    def test_unauthenticated_redirects(self, client):
        rv = client.post("/update_my_profile", data={"about_me": "hello"})
        assert rv.status_code == 302

    def test_updates_profile(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/update_my_profile", data={
            "about_me": "CI test about me",
            "phone":    "9999999999",
        })
        assert rv.status_code == 302

    def test_updates_bank_details(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/update_my_bank_details", data={
            "bank_name":    "CI Bank",
            "bank_account": "123456789",
            "bank_ifsc":    "CIBN0000001",
            "uan_number":   "100123456789",
        })
        assert rv.status_code == 302


# ── add/delete experience ─────────────────────────────────────────────────────

class TestExperience:

    def test_unauthenticated_redirects(self, client):
        rv = client.post("/add_experience", data={})
        assert rv.status_code == 302

    def test_adds_experience(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        rv = client.post("/add_experience", data={
            "company":    "CI Corp",
            "role":       "Tester",
            "start_date": "2020-01-01",
            "end_date":   "2022-12-31",
            "description": "CI testing role",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute(
            "DELETE FROM employee_experience WHERE employee_id=%s AND company='CI Corp'",
            (seed_employee["employee_id"],)
        )
        cur.close()

    def test_delete_nonexistent_experience(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/delete_experience/99999999")
        assert rv.status_code == 302


# ── add/delete education ──────────────────────────────────────────────────────

class TestEducation:

    def test_unauthenticated_redirects(self, client):
        rv = client.post("/add_education_entry", data={})
        assert rv.status_code == 302

    def test_adds_education(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        rv = client.post("/add_education_entry", data={
            "degree":      "B.Tech",
            "institution": "CI University",
            "year":        "2019",
            "grade":       "8.5 CGPA",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute(
            "DELETE FROM employee_education WHERE employee_id=%s AND institution='CI University'",
            (seed_employee["employee_id"],)
        )
        cur.close()

    def test_delete_nonexistent_education(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/delete_education_entry/99999999")
        assert rv.status_code == 302


# ── api_employee_login ────────────────────────────────────────────────────────

class TestApiEmployeeLogin:

    def test_missing_employee_id_returns_400(self, client):
        rv = client.post("/api/employee/login", json={"employee_id": "", "password": "x"})
        assert rv.status_code == 400

    def test_missing_password_returns_400(self, client, seed_employee):
        rv = client.post("/api/employee/login",
                         json={"employee_id": seed_employee["employee_id"], "password": ""})
        assert rv.status_code in (400, 401)

    def test_wrong_password_returns_401(self, client, seed_employee):
        rv = client.post("/api/employee/login",
                         json={"employee_id": seed_employee["employee_id"],
                               "password": "WrongPassword!"})
        assert rv.status_code == 401

    def test_unknown_employee_returns_401(self, client):
        rv = client.post("/api/employee/login",
                         json={"employee_id": "GHOST_99", "password": "any"})
        assert rv.status_code == 401

    def test_valid_login_returns_token(self, client, seed_employee, db_engine):
        rv = client.post("/api/employee/login",
                         json={"employee_id": seed_employee["employee_id"],
                               "password": seed_employee["password"]})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert "token" in data
        cur = db_engine.cursor()
        cur.execute("DELETE FROM api_tokens WHERE identity=%s", (seed_employee["employee_id"],))
        cur.close()


# ── api_employee_portal ───────────────────────────────────────────────────────

class TestApiEmployeePortal:

    def test_unauthenticated_returns_401(self, client):
        rv = client.get("/api/employee/portal")
        assert rv.status_code == 401

    def test_returns_data_with_token(self, client, seed_employee, db_engine):
        token, cleanup = _make_employee_token(db_engine, seed_employee["employee_id"])
        try:
            rv = client.get("/api/employee/portal",
                            headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert "employee" in data or "emp_id" in data or "ok" in data
        finally:
            cleanup()


# ── api_employee_auth_config ──────────────────────────────────────────────────

class TestApiEmployeeAuthConfig:

    def test_returns_auth_config_json(self, client):
        rv = client.get("/api/employee/auth-config")
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, dict)


# ── api_employee_logout ───────────────────────────────────────────────────────

class TestApiEmployeeLogout:

    def test_logout_without_token_returns_ok(self, client):
        rv = client.post("/api/employee/logout")
        assert rv.status_code == 200
        assert rv.get_json()["ok"] is True

    def test_logout_with_token_deletes_it(self, client, seed_employee, db_engine):
        token, cleanup = _make_employee_token(db_engine, seed_employee["employee_id"])
        try:
            rv = client.post("/api/employee/logout",
                             headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            assert rv.get_json()["ok"] is True
        finally:
            cleanup()
