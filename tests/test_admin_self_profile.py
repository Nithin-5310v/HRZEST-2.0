# -*- coding: utf-8 -*-
"""The owner admin's own employee profile (utils/own_profile.py).

The company admin creates it from Organization -> "My employee profile";
it's linked to the admin login (admin_users.linked_employee_id), opens in
My Workspace like an HR's or manager's own record, and is not a paid seat.
"""
import io
import os

import pytest
from PIL import Image

from utils.plan_limits import get_tenant_employee_count

TENANT_SCHEMA = os.environ["DB_NAME"]


def _owner(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"


@pytest.fixture
def owner(db_engine, seed_admin):
    """The seeded admin, with a name (the profile takes it) and no profile."""
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET display_name='Asha Owner', job_title='Founder', mobile='9876543210', "
                "linked_employee_id=NULL WHERE username=%s", (seed_admin["username"],))
    yield seed_admin
    cur.execute("SELECT linked_employee_id FROM admin_users WHERE username=%s", (seed_admin["username"],))
    row = cur.fetchone()
    if row and row[0]:
        cur.execute("DELETE FROM leave_balances WHERE employee_id=%s", (row[0],))
        cur.execute("DELETE FROM employees WHERE employee_id=%s", (row[0],))
    cur.close()


def _linked(db_engine, username):
    cur = db_engine.cursor()
    cur.execute("SELECT linked_employee_id FROM admin_users WHERE username=%s", (username,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def _jpeg(size=(400, 400)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 180, 160)).save(buf, "JPEG")
    buf.seek(0)
    return buf


class TestBeforeTheProfileExists:
    def test_my_workspace_sends_the_owner_to_the_setup_card(self, client, owner):
        _owner(client, owner)
        resp = client.get("/my_workspace")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/profile#my-employee-profile")

    def test_organization_page_offers_to_create_it(self, client, owner):
        _owner(client, owner)
        html = client.get("/profile").get_data(as_text=True)
        assert 'id="my-employee-profile"' in html and "Create my employee profile" in html


class TestCreate:
    def test_creates_a_linked_employee_and_opens_my_workspace(self, client, db_engine, owner):
        _owner(client, owner)
        resp = client.post("/profile/my_employee", data={
            "designation": "Managing Director", "department": "Management",
            "date_of_joining": "2026-11-01", "work_mode": "office",
            "face": (_jpeg(), "me.jpg"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/profile#my-employee-profile")

        emp_id = _linked(db_engine, owner["username"])
        assert emp_id
        cur = db_engine.cursor()
        cur.execute("SELECT name, role, department, work_mode, face_image, force_pin_change FROM employees "
                    "WHERE employee_id=%s", (emp_id,))
        name, role, department, work_mode, face_image, force_pin_change = cur.fetchone()
        cur.close()
        assert (name, role, department, work_mode) == ("Asha Owner", "Managing Director", "Management", "office")
        assert face_image and force_pin_change == 0

        page = client.get("/my_workspace")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert emp_id in html and "Mark Attendance" in html
        # Their sign-in is the admin login: no separate employee password form.
        assert "Update Password" not in html

    def test_not_a_paid_seat(self, client, owner):
        before = get_tenant_employee_count(TENANT_SCHEMA)
        _owner(client, owner)
        client.post("/profile/my_employee", data={"work_mode": "office"})
        assert get_tenant_employee_count(TENANT_SCHEMA) == before

    def test_seat_cap_ignores_it(self, client, db_engine, owner):
        from app import app
        from utils.helpers.gates import add_employee_seat_cap_check
        from utils.helpers import invalidate_settings_cache
        _owner(client, owner)
        client.post("/profile/my_employee", data={"work_mode": "office"})
        cur = db_engine.cursor()
        cur.execute("SELECT paid_employee_slots FROM company_settings ORDER BY id LIMIT 1")
        saved = cur.fetchone()[0]
        billable = get_tenant_employee_count(TENANT_SCHEMA)
        cur.execute("UPDATE company_settings SET paid_employee_slots=%s", (billable + 1,))
        invalidate_settings_cache()
        try:
            with app.test_request_context():
                assert add_employee_seat_cap_check() is None    # one paid seat still free
        finally:
            cur.execute("UPDATE company_settings SET paid_employee_slots=%s", (saved,))
            invalidate_settings_cache()
            cur.close()

    def test_only_once(self, client, db_engine, owner):
        _owner(client, owner)
        client.post("/profile/my_employee", data={"work_mode": "office"})
        first = _linked(db_engine, owner["username"])
        resp = client.post("/profile/my_employee", data={"work_mode": "office"})
        assert resp.headers["Location"].endswith("/profile#my-employee-profile")
        assert _linked(db_engine, owner["username"]) == first
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM employees WHERE name='Asha Owner'")
        assert cur.fetchone()[0] == 1
        cur.close()

    def test_needs_the_admins_name_first(self, client, db_engine, owner):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET display_name=NULL WHERE username=%s", (owner["username"],))
        cur.close()
        _owner(client, owner)
        resp = client.post("/profile/my_employee", data={"work_mode": "office"})
        assert resp.headers["Location"].endswith("/profile#my-employee-profile")
        assert _linked(db_engine, owner["username"]) is None

    def test_rejects_a_bad_photo(self, client, db_engine, owner):
        _owner(client, owner)
        resp = client.post("/profile/my_employee", data={"face": (io.BytesIO(b"not an image"), "x.jpg")},
                           content_type="multipart/form-data")
        assert resp.headers["Location"].endswith("/profile#my-employee-profile")
        assert _linked(db_engine, owner["username"]) is None

    def test_hr_cannot_create_one(self, client, db_engine, owner):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = owner["username"]
            sess["admin_role"] = "hr"
        client.post("/profile/my_employee", data={"work_mode": "office"})
        assert _linked(db_engine, owner["username"]) is None


class TestAfterwards:
    def test_organization_page_shows_the_profile(self, client, db_engine, owner):
        _owner(client, owner)
        client.post("/profile/my_employee", data={"work_mode": "office"})
        emp_id = _linked(db_engine, owner["username"])
        html = client.get("/profile").get_data(as_text=True)
        assert emp_id in html and "Open my employee dashboard" in html and "Create my employee profile" not in html

    def test_self_service_acts_on_the_linked_employee(self, client, db_engine, owner):
        from flask import session
        from utils.helpers.urls import self_service_emp_id
        _owner(client, owner)
        client.post("/profile/my_employee", data={"work_mode": "office"})
        emp_id = _linked(db_engine, owner["username"])
        from app import app
        with app.test_request_context():
            session["admin_logged_in"] = True
            session["admin_username"] = owner["username"]
            session["admin_role"] = "admin"
            assert self_service_emp_id() == emp_id

    def test_unlinked_owner_has_no_self_service_employee(self, owner):
        from flask import session
        from utils.helpers.urls import self_service_emp_id
        from app import app
        with app.test_request_context():
            session["admin_logged_in"] = True
            session["admin_username"] = owner["username"]
            session["admin_role"] = "admin"
            assert self_service_emp_id() is None
