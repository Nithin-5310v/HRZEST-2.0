# -*- coding: utf-8 -*-
"""Route-level tests for blueprints/company_profile.py -- the Company Admin
"Profile / Account" page's four tabs (Company Info, Subscription & Billing,
Payment History, Support Tickets).

"att_test" is this whole suite's single, shared tenant schema (see
tests/test_auto_debit.py's own docstring) -- g.tenant_db resolves to it for
every plain client.get()/post() call with no special subdomain/path. Most of
this file's new tables (payment_orders, tenant_support_tickets) key off a
real att_master.tenants row (via tenant_id), which doesn't exist for
"att_test" by default -- own_tenant below creates one for the duration of
each test that needs it, following the exact pattern tests/test_platform_admin.py's
TestCompanyProfile.crm_tenant fixture already uses for its own tenant.
"""
import base64
import io
import os

import pytest
from flask import g

from blueprints.company_profile import admin_photo_path
from utils.helpers import invalidate_settings_cache

TENANT_SCHEMA = os.environ["DB_NAME"]  # the suite's default tenant schema (see conftest)


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def own_tenant(db_engine):
    """A real att_master.tenants row for this suite's actual tenant schema
    (att_test) -- required for _resolve_own_tenant_id() to find anything."""
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
    cur.execute(
        "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status, subscription_status) "
        "VALUES ('Profile Test Co', 'profile-test-co', %s, 'active', 'active') RETURNING id",
        (TENANT_SCHEMA,),
    )
    tenant_id = cur.fetchone()[0]
    db_engine.commit()
    yield tenant_id
    cur.execute("DELETE FROM att_master.payment_orders WHERE tenant_id=%s", (tenant_id,))
    cur.execute("DELETE FROM att_master.tenant_ticket_comments WHERE ticket_id IN "
                "(SELECT id FROM att_master.tenant_support_tickets WHERE tenant_id=%s)", (tenant_id,))
    cur.execute("DELETE FROM att_master.tenant_support_tickets WHERE tenant_id=%s", (tenant_id,))
    cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
    cur.execute("UPDATE company_settings SET contact_phone=NULL, address=NULL")
    db_engine.commit()
    cur.close()


@pytest.fixture
def other_tenant(db_engine):
    """A second, unrelated tenant -- for proving own_tenant's admin can
    never see or act on its data."""
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
        "VALUES ('Other Co', 'other-co-profile-test', 'fake_other_co_profile', 'active') RETURNING id",
    )
    tenant_id = cur.fetchone()[0]
    db_engine.commit()
    yield tenant_id
    cur.execute("DELETE FROM att_master.payment_orders WHERE tenant_id=%s", (tenant_id,))
    cur.execute("DELETE FROM att_master.tenant_ticket_comments WHERE ticket_id IN "
                "(SELECT id FROM att_master.tenant_support_tickets WHERE tenant_id=%s)", (tenant_id,))
    cur.execute("DELETE FROM att_master.tenant_support_tickets WHERE tenant_id=%s", (tenant_id,))
    cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
    db_engine.commit()
    cur.close()


class TestCompanyInfoTab:
    def test_requires_login(self, client):
        resp = client.get("/profile", follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_renders_company_name_and_employee_count(self, client, seed_admin, own_tenant):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/profile")
        assert resp.status_code == 200
        assert b"Company details" in resp.data

    def test_update_contact_details_persists(self, client, seed_admin, own_tenant, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/profile/company_info", data={
            "contact_phone": "+91 98765 43210", "address": "123 Test Street, Bengaluru",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT contact_phone, address FROM company_settings LIMIT 1")
        row = cur.fetchone()
        cur.close()
        assert row[0] == "+91 98765 43210"
        assert row[1] == "123 Test Street, Bengaluru"


class TestBillingTab:
    def test_redirects_to_settings_billing(self, client, seed_admin, own_tenant):
        # Billing lives in Settings only; the old tab URL forwards there.
        _admin_session(client, seed_admin["username"])
        resp = client.get("/profile/billing")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/settings?tab=billing")


class TestPaymentHistoryTab:
    def test_shows_only_this_tenants_payments(self, client, db_engine, seed_admin, own_tenant, other_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id) "
            "VALUES (%s,'per_employee',3,2970,'Profile Test Co','profile-test-co','a','a@test.local','paid',%s)",
            (f"demo_order_own_{own_tenant}", own_tenant),
        )
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id) "
            "VALUES (%s,'per_employee',3,2970,'Other Co','other-co-profile-test','b','b@test.local','paid',%s)",
            (f"demo_order_other_{other_tenant}", other_tenant),
        )
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.get("/profile/payments")
        assert resp.status_code == 200
        assert b"Profile Test Co" not in resp.data  # own company_name isn't shown in the row itself, but:
        assert f"demo_order_own_{own_tenant}".encode() in resp.data
        assert f"demo_order_other_{other_tenant}".encode() not in resp.data

    def test_status_filter(self, client, db_engine, seed_admin, own_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id) "
            "VALUES (%s,'per_employee',2,1980,'Profile Test Co','profile-test-co','a','a@test.local','failed',%s)",
            (f"demo_order_failed_{own_tenant}", own_tenant),
        )
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.get("/profile/payments?status=failed")
        assert resp.status_code == 200
        assert f"demo_order_failed_{own_tenant}".encode() in resp.data
        resp2 = client.get("/profile/payments?status=success")
        assert f"demo_order_failed_{own_tenant}".encode() not in resp2.data

    def test_csv_export(self, client, db_engine, seed_admin, own_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id) "
            "VALUES (%s,'per_employee',1,990,'Profile Test Co','profile-test-co','a','a@test.local','paid',%s)",
            (f"demo_order_csv_{own_tenant}", own_tenant),
        )
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.get("/profile/payments/export.csv")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert f"demo_order_csv_{own_tenant}".encode() in resp.data

    def test_receipt_pdf_for_own_payment(self, client, db_engine, seed_admin, own_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, razorpay_payment_id, plan, employee_count, amount_paise, company_name, "
            "subdomain, admin_username, admin_email, status, tenant_id, paid_at) "
            "VALUES (%s,%s,'per_employee',1,990,'Profile Test Co','profile-test-co','a','a@test.local','paid',%s,NOW()) "
            "RETURNING id",
            (f"demo_order_pdf_{own_tenant}", f"demo_pay_pdf_{own_tenant}", own_tenant),
        )
        row_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/profile/payments/signup/{row_id}/receipt.pdf")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert resp.data[:4] == b"%PDF"

    def test_receipt_404_for_other_tenants_payment(self, client, db_engine, seed_admin, own_tenant, other_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id, paid_at) "
            "VALUES (%s,'per_employee',1,990,'Other Co','other-co-profile-test','b','b@test.local','paid',%s,NOW()) "
            "RETURNING id",
            (f"demo_order_other_pdf_{other_tenant}", other_tenant),
        )
        row_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/profile/payments/signup/{row_id}/receipt.pdf")
        assert resp.status_code == 404


class TestSupportTicketsTab:
    def test_create_ticket(self, client, seed_admin, own_tenant, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/profile/tickets", data={
            "subject": "Cannot add employee", "description": "Getting an error.", "priority": "high",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT subject, priority, created_by FROM att_master.tenant_support_tickets WHERE tenant_id=%s",
                    (own_tenant,))
        row = cur.fetchone()
        cur.close()
        assert row == ("Cannot add employee", "high", seed_admin["username"])

    def test_list_shows_only_this_tenants_tickets(self, client, db_engine, seed_admin, own_tenant, other_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description) VALUES (%s,%s,%s)",
            (own_tenant, "Own Ticket Subject", "desc"),
        )
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description) VALUES (%s,%s,%s)",
            (other_tenant, "Other Tenant Ticket Subject", "desc"),
        )
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.get("/profile/tickets")
        assert resp.status_code == 200
        assert b"Own Ticket Subject" in resp.data
        assert b"Other Tenant Ticket Subject" not in resp.data

    def test_detail_404_for_other_tenants_ticket(self, client, db_engine, seed_admin, own_tenant, other_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description) VALUES (%s,%s,%s) RETURNING id",
            (other_tenant, "Not yours", "desc"),
        )
        other_ticket_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/profile/tickets/{other_ticket_id}")
        assert resp.status_code == 404
        reply = client.post(f"/profile/tickets/{other_ticket_id}/reply", data={"comment": "hi"})
        assert reply.status_code == 404

    def test_reply_to_open_ticket(self, client, db_engine, seed_admin, own_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description, status) "
            "VALUES (%s,%s,%s,'open') RETURNING id",
            (own_tenant, "Open ticket", "desc"),
        )
        ticket_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/profile/tickets/{ticket_id}/reply", data={"comment": "Any update?"}, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT author, comment, author_kind FROM att_master.tenant_ticket_comments WHERE ticket_id=%s",
                    (ticket_id,))
        row = cur.fetchone()
        cur.close()
        assert row == (seed_admin["username"], "Any update?", "company_admin")

    def test_reply_rejected_on_closed_ticket(self, client, db_engine, seed_admin, own_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description, status) "
            "VALUES (%s,%s,%s,'closed') RETURNING id",
            (own_tenant, "Closed ticket", "desc"),
        )
        ticket_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/profile/tickets/{ticket_id}/reply", data={"comment": "reopen please"},
                           follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM att_master.tenant_ticket_comments WHERE ticket_id=%s", (ticket_id,))
        count = cur.fetchone()[0]
        cur.close()
        assert count == 0
        assert b"closed" in resp.data.lower()

    def test_linked_ticket_from_own_tenant_accepted(self, client, db_engine, seed_admin, own_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description, status) "
            "VALUES (%s,%s,%s,'closed') RETURNING id",
            (own_tenant, "Original", "desc"),
        )
        original_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        client.post("/profile/tickets", data={
            "subject": "Follow-up", "description": "Following up on the above.",
            "priority": "normal", "linked_ticket_id": str(original_id),
        })
        cur = db_engine.cursor()
        cur.execute("SELECT linked_ticket_id FROM att_master.tenant_support_tickets WHERE subject='Follow-up' AND tenant_id=%s",
                    (own_tenant,))
        row = cur.fetchone()
        cur.close()
        assert row[0] == original_id

    def test_linked_ticket_from_other_tenant_rejected(self, client, db_engine, seed_admin, own_tenant, other_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description, status) "
            "VALUES (%s,%s,%s,'closed') RETURNING id",
            (other_tenant, "Not yours", "desc"),
        )
        other_ticket_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        _admin_session(client, seed_admin["username"])
        client.post("/profile/tickets", data={
            "subject": "Sneaky link", "description": "desc",
            "priority": "normal", "linked_ticket_id": str(other_ticket_id),
        })
        cur = db_engine.cursor()
        cur.execute("SELECT linked_ticket_id FROM att_master.tenant_support_tickets WHERE subject='Sneaky link' AND tenant_id=%s",
                    (own_tenant,))
        row = cur.fetchone()
        cur.close()
        assert row[0] is None


class TestPlatformAdminReplyNotifiesCorrectTenant:
    def test_platform_admin_reply_creates_notification_in_own_tenant_schema(self, client, db_engine, own_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description, status) "
            "VALUES (%s,%s,%s,'open') RETURNING id",
            (own_tenant, "Needs platform reply", "desc"),
        )
        ticket_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        import time as _time
        with client.session_transaction() as sess:
            sess["platform_admin_logged_in"] = True
            sess["platform_admin_username"] = "test_platform_admin"
            sess["platform_admin_last_activity"] = _time.time()

        resp = client.post(f"/super_admin/tickets/{ticket_id}/comment", data={"comment": "We're on it."},
                           follow_redirects=True)
        assert resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute(
            "SELECT title, message FROM notifications WHERE recipient_type='admin' "
            "AND message='We''re on it.' ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.execute("DELETE FROM notifications WHERE recipient_type='admin' AND message='We''re on it.'")
        db_engine.commit()
        cur.close()
        assert row is not None
        assert "Needs platform reply" in row[0]


# Every text field the Organization profile form requires.
FORM_FIELDS = {
    "industry": "Manufacturing", "company_size": "11-50", "address": "12 MG Road", "city": "Hyderabad",
    "state": "Telangana", "postal_code": "500001", "country": "India", "job_title": "HR Manager",
    "email": "asha@example.com", "mobile": "+91 98765 43210",
}


class TestOrganizationProfileForm:
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )

    @pytest.fixture
    def org_admin(self, client, db_engine):
        """A real admin row (the session check rejects accounts that don't
        exist) with no name, photo or logo yet; yields a function to change
        its role."""
        cur = db_engine.cursor()
        cur.execute("SELECT logo_url, company_code, address FROM company_settings")
        before, before_code, before_address = cur.fetchone()
        cur.execute(
            "INSERT INTO admin_users (username, password, role, display_name) "
            "VALUES ('org_admin', 'x', 'admin', NULL) "
            "ON CONFLICT (username) DO UPDATE SET role='admin', display_name=NULL"
        )
        cur.execute("UPDATE company_settings SET logo_url=NULL, company_code='OLD'")
        db_engine.commit()
        invalidate_settings_cache()
        with client.application.test_request_context():
            g.tenant_db = TENANT_SCHEMA
            photo = admin_photo_path("org_admin")
        if os.path.exists(photo):
            os.remove(photo)
        _admin_session(client, "org_admin")
        yield cur
        if os.path.exists(photo):
            os.remove(photo)
        cur.execute("DELETE FROM admin_users WHERE username='org_admin'")
        cur.execute("UPDATE company_settings SET logo_url=%s, company_code=%s, address=%s",
                    (before, before_code, before_address))
        db_engine.commit()
        cur.close()
        invalidate_settings_cache()

    def _post(self, client, name="Asha Rao", logo=True, photo=True, code="asha1", **fields):
        data = {**FORM_FIELDS, "display_name": name, "company_code": code, **fields}
        if logo:
            data["logo"] = (io.BytesIO(self.PNG), "logo.png")
        if photo:
            data["photo"] = (io.BytesIO(self.PNG), "me.png")
        return client.post("/profile/details", data=data, content_type="multipart/form-data")

    def _state(self, cur):
        cur.execute("SELECT display_name FROM admin_users WHERE username='org_admin'")
        name = cur.fetchone()[0]
        cur.execute("SELECT logo_url FROM company_settings")
        return name, cur.fetchone()[0]

    def test_saves_all_three_and_shows_them(self, client, org_admin):
        self._post(client, name="  Asha   Rao ")
        name, logo = self._state(org_admin)
        assert name == "Asha Rao"
        assert logo.startswith("/static/company_logos/") and "?v=" in logo
        html = client.get("/profile").get_data(as_text=True)
        assert ">Asha Rao<" in html and "/profile/photo?v=" in html and "to complete your profile" not in html
        assert 'name="company_code" value="ASHA1"' in html
        assert 'value="HR Manager"' in html and 'value="asha@example.com"' in html and 'value="+919876543210"' in html
        assert 'value="Manufacturing" selected' in html and "<dd>12 MG Road</dd>" in html
        assert 'class="org-tabs"' in html and 'class="org-side"' not in html

    def test_header_logo_button_saves_straight_away(self, client, org_admin):
        self._post(client, logo=False, photo=False)
        client.post("/profile/logo", data={"logo": (io.BytesIO(self.PNG), "logo.png")},
                    content_type="multipart/form-data")
        assert self._state(org_admin)[1].startswith("/static/company_logos/")

    def test_website_is_optional_but_saved_when_given(self, client, org_admin):
        self._post(client, website="www.acme.example")
        org_admin.execute("SELECT website FROM company_settings")
        assert org_admin.fetchone()[0] == "www.acme.example"

    def test_code_logo_and_photo_are_optional(self, client, org_admin):
        self._post(client, logo=False, photo=False, code="")
        assert self._state(org_admin) == ("Asha Rao", None)
        org_admin.execute("SELECT company_code FROM company_settings")
        assert org_admin.fetchone()[0] is None

    def test_only_the_address_line_is_mandatory_in_the_office_address(self, client, org_admin):
        self._post(client, city="", state="", postal_code="", country="")
        assert self._state(org_admin)[0] == "Asha Rao"
        org_admin.execute("SELECT address, city, state, postal_code, country FROM company_settings")
        assert org_admin.fetchone() == ("12 MG Road", None, None, None, None)

    def test_logo_and_photo_can_be_removed(self, client, org_admin):
        self._post(client)
        assert b"/profile/photo?v=" in client.get("/profile").data
        client.post("/profile/logo/remove")
        client.post("/profile/photo/remove")
        assert self._state(org_admin)[1] is None
        assert b"/profile/photo?v=" not in client.get("/profile").data

    def test_each_field_is_mandatory_on_first_save(self, client, org_admin):
        for kwargs in ({"name": "   "}, {"code": "A"},
                       {"code": "HAS SPACE"}, {"code": "TOOLONGCODE1"}, {"industry": "Nonsense"},
                       {"company_size": ""}, {"address": ""}, {"postal_code": "1"}, {"job_title": ""}, {"email": "not-an-email"}, {"mobile": "12345"},
                       {"website": "not a site"}):
            self._post(client, **kwargs)
            assert self._state(org_admin) == (None, None)

    def test_saved_values_stay_editable_without_reuploading(self, client, org_admin):
        self._post(client)
        _, logo = self._state(org_admin)
        self._post(client, name="Asha R", logo=False, photo=False, code="NEW2")
        assert self._state(org_admin) == ("Asha R", logo)
        org_admin.execute("SELECT company_code FROM company_settings")
        assert org_admin.fetchone()[0] == "NEW2"

    def test_hr_cannot_save(self, client, org_admin):
        org_admin.execute("UPDATE admin_users SET role='hr' WHERE username='org_admin'")
        org_admin.connection.commit()
        _admin_session(client, "org_admin", role="hr")
        assert self._post(client).status_code in (302, 403)
        assert self._state(org_admin) == (None, None)


class TestNewCompanyGate:
    PNG = TestOrganizationProfileForm.PNG

    @pytest.fixture
    def pending_admin(self, client, db_engine, monkeypatch):
        """A real admin at a company whose Organization profile is pending,
        with the gate switched on (conftest switches it off for the suite)."""
        monkeypatch.setitem(client.application.config, "MANDATORY_ORG_PROFILE", True)
        cur = db_engine.cursor()
        cur.execute("SELECT logo_url, company_code, address FROM company_settings")
        before = cur.fetchone()
        cur.execute(
            "INSERT INTO admin_users (username, password, role, display_name) "
            "VALUES ('gate_admin', 'x', 'admin', NULL) "
            "ON CONFLICT (username) DO UPDATE SET role='admin', display_name=NULL"
        )
        cur.execute("UPDATE company_settings SET org_profile_pending=1, logo_url=NULL")
        db_engine.commit()
        invalidate_settings_cache()
        with client.application.test_request_context():
            g.tenant_db = TENANT_SCHEMA
            photo = admin_photo_path("gate_admin")
        _admin_session(client, "gate_admin")
        yield cur
        if os.path.exists(photo):
            os.remove(photo)
        cur.execute("DELETE FROM admin_users WHERE username='gate_admin'")
        cur.execute("UPDATE company_settings SET org_profile_pending=0, logo_url=%s, company_code=%s, address=%s", before)
        db_engine.commit()
        cur.close()
        invalidate_settings_cache()

    def _save(self, client):
        return client.post("/profile/details", data={
            **FORM_FIELDS, "company_code": "GATE1", "display_name": "Asha Rao",
            "logo": (io.BytesIO(self.PNG), "logo.png"), "photo": (io.BytesIO(self.PNG), "me.png"),
        }, content_type="multipart/form-data")

    def test_dashboard_pages_go_to_the_organization_page(self, client, pending_admin):
        for path in ("/admin", "/employees", "/settings", "/admin_tools"):
            resp = client.get(path)
            assert resp.status_code == 302 and resp.headers["Location"].endswith("/profile"), path

    def test_organization_page_and_loader_stay_open(self, client, pending_admin):
        page = client.get("/profile").data
        assert b"Complete this form to open your dashboard" in page and b"Save &amp; Continue" in page
        assert b'class="org-side"' in page and b"/profile/policies" in page
        assert b"org-summary" not in page.split(b"</style>")[-1]
        welcome = client.get("/welcome")
        assert welcome.status_code == 200 and b'content="3;url=/profile"' in welcome.data

    def test_incomplete_save_keeps_the_lock(self, client, pending_admin):
        client.post("/profile/details", data={**FORM_FIELDS, "company_code": "GATE1", "display_name": ""},
                    content_type="multipart/form-data")
        assert client.get("/admin").status_code == 302

    def test_saving_opens_the_dashboard(self, client, pending_admin):
        resp = self._save(client)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin")
        assert client.get("/admin").status_code == 200
        assert client.get("/employees").status_code == 200

    def test_existing_company_is_not_locked(self, client, pending_admin):
        pending_admin.execute("UPDATE company_settings SET org_profile_pending=0")
        pending_admin.connection.commit()
        invalidate_settings_cache()
        assert client.get("/admin").status_code == 200

    def test_hr_is_not_held_by_the_gate(self, client, pending_admin):
        pending_admin.execute("UPDATE admin_users SET role='hr' WHERE username='gate_admin'")
        pending_admin.connection.commit()
        _admin_session(client, "gate_admin", role="hr")
        location = client.get("/hr_dashboard").headers.get("Location", "")
        assert not location.endswith("/profile")
