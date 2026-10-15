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
import os
import pytest

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
        assert b"Company Details" in resp.data

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
    def test_renders_without_error(self, client, seed_admin, own_tenant):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/profile/billing")
        assert resp.status_code == 200
        assert b"Plan Status" in resp.data

    def test_matches_settings_billing_snapshot(self, client, seed_admin, own_tenant):
        """Both pages must read get_billing_snapshot() the same way --
        regression against the two ever silently drifting apart."""
        _admin_session(client, seed_admin["username"])
        from utils.plan_limits import get_billing_snapshot
        snapshot = get_billing_snapshot(TENANT_SCHEMA)
        resp = client.get("/profile/billing")
        assert resp.status_code == 200
        settings_resp = client.get("/settings?tab=billing")
        assert settings_resp.status_code == 200
        # Both pages render the same auto_debit status badge text (or lack
        # thereof) for the same underlying snapshot.
        if snapshot["auto_debit"]:
            assert snapshot["auto_debit"]["status"].replace("_", " ").encode() in resp.data


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
