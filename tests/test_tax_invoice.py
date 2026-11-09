# -*- coding: utf-8 -*-
"""GST tax invoices (utils/tax_invoice.py) and the plan details card:

- amounts: the GST-inclusive split, Indian digit grouping, amount in words;
- issuing: one invoice per paid payment, numbered without gaps per
  financial year, buyer/seller details frozen in, nothing for unpaid ones;
- after a payment: the PDF goes to the company's admins and the platform
  admins, and each payment path (Auto-Debit charge, overdue bill) invoices;
- the company admin's Payment history / dashboard and the Super Admin's
  download route.

Works on the suite's own tenant row (conftest.py) and puts back what it
changes.
"""
import datetime
import os
import time

import pytest

import utils.email_utils as email_utils
import utils.tax_invoice as ti

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = os.environ["DB_NAME"]


@pytest.fixture(autouse=True)
def _app_context():
    from extensions import app as flask_app
    with flask_app.app_context():
        yield


@pytest.fixture
def tenant(db_engine):
    """The suite's tenant row (its id), with its GSTIN and the company's
    address put back afterwards and every payment/invoice made here removed."""
    cur = db_engine.cursor()
    cur.execute("SELECT id, gst_number, subscription_status, payment_option FROM att_master.tenants "
                "WHERE db_name=%s", (TENANT_SCHEMA,))
    tenant_id, gst, sub_status, payment_option = cur.fetchone()
    cur.execute("SELECT address, city, state, postal_code FROM company_settings LIMIT 1")
    address = cur.fetchone()
    cur.execute("UPDATE att_master.tenants SET subscription_status='active', payment_option='online' WHERE id=%s",
                (tenant_id,))
    db_engine.commit()
    cur.close()
    yield tenant_id
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.tax_invoices WHERE tenant_schema=%s OR source_id IN "
                "(SELECT id FROM att_master.payment_orders WHERE razorpay_order_id LIKE 'demo_order_ti_%%')",
                (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.payment_orders WHERE razorpay_order_id LIKE 'demo_order_ti_%%'")
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("UPDATE att_master.tenants SET gst_number=%s, subscription_status=%s, payment_option=%s WHERE id=%s",
                (gst, sub_status, payment_option, tenant_id))
    if address:
        cur.execute("UPDATE company_settings SET address=%s, city=%s, state=%s, postal_code=%s", address)
    db_engine.commit()
    cur.close()


def _paid_signup(db_engine, tenant_id, tag, amount_paise=59400, employees=6, status="paid"):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.payment_orders (razorpay_order_id, razorpay_payment_id, plan, employee_count, "
        "amount_paise, company_name, subdomain, admin_username, admin_email, status, tenant_id, paid_at) "
        "VALUES (%s,%s,'per_employee',%s,%s,'Invoice Test Co','invoice-test-co','a','a@test.local',%s,%s,NOW()) "
        "RETURNING id",
        (f"demo_order_ti_{tag}", f"pay_ti_{tag}", employees, amount_paise, status, tenant_id))
    row_id = cur.fetchone()[0]
    db_engine.commit()
    cur.close()
    return row_id


def _set_buyer(db_engine, gstin=None, state=None):
    cur = db_engine.cursor()
    cur.execute("UPDATE att_master.tenants SET gst_number=%s WHERE db_name=%s", (gstin, TENANT_SCHEMA))
    cur.execute("UPDATE company_settings SET address='301 Test Arcade', city='Hyderabad', state=%s, "
                "postal_code='500072'", (state,))
    db_engine.commit()
    cur.close()


class TestAmounts:
    @pytest.mark.parametrize("total", [99, 9900, 59400, 363800, 100001])
    @pytest.mark.parametrize("intra", [True, False])
    def test_gst_split_adds_back_up(self, total, intra):
        taxable, cgst, sgst, igst = ti.split_gst(total, 18, intra)
        assert taxable + cgst + sgst + igst == total
        assert abs(taxable * 0.18 - (cgst + sgst + igst)) <= 1
        assert (igst == 0) if intra else (cgst == sgst == 0)

    def test_indian_grouping_and_words(self):
        assert ti.rupees(363800) == "3,638.00"
        assert ti.rupees(12345678901) == "12,34,56,789.01"
        assert ti.amount_in_words(363800) == "Three Thousand Six Hundred Thirty Eight Rupees Only"
        assert ti.amount_in_words(10000005) == "One Lakh Rupees and Five Paise Only"

    def test_financial_year_turns_in_april(self):
        assert ti.financial_year(datetime.date(2027, 3, 31)) == "2026-27"
        assert ti.financial_year(datetime.date(2027, 4, 1)) == "2027-28"


class TestIssuing:
    def test_one_invoice_per_payment_numbered_in_sequence(self, db_engine, tenant):
        first = ti.issue_invoice("signup", _paid_signup(db_engine, tenant, "a"))
        again = ti.issue_invoice("signup", first["source_id"])
        second = ti.issue_invoice("signup", _paid_signup(db_engine, tenant, "b"))
        assert again["invoice_no"] == first["invoice_no"]
        prefix, fy, n1 = first["invoice_no"].split("/")
        assert prefix == "HRZ" and fy == ti.financial_year(datetime.date.fromisoformat(first["invoice_date"]))
        assert second["invoice_no"] == f"{prefix}/{fy}/{int(n1) + 1:05d}"

    def test_unpaid_payment_gets_no_invoice(self, db_engine, tenant):
        assert ti.issue_invoice("signup", _paid_signup(db_engine, tenant, "pending", status="created")) is None
        assert ti.issue_invoice("nonsense", 1) is None

    def test_same_state_buyer_pays_cgst_and_sgst(self, db_engine, tenant):
        _set_buyer(db_engine, state="Telangana")
        inv = ti.issue_invoice("signup", _paid_signup(db_engine, tenant, "tg"))
        t = inv["totals"]
        assert inv["intra_state"] and inv["place_of_supply"] == "Telangana (36)"
        assert t["cgst_paise"] + t["sgst_paise"] == t["tax_paise"] and t["igst_paise"] == 0
        assert t["taxable_paise"] + t["tax_paise"] == 59400
        assert "301 Test Arcade" in inv["buyer"]["address_lines"]

    def test_other_state_gstin_means_igst(self, db_engine, tenant):
        _set_buyer(db_engine, gstin="29AAAAA0000A1Z5", state=None)
        inv = ti.issue_invoice("signup", _paid_signup(db_engine, tenant, "ka"))
        assert not inv["intra_state"] and inv["buyer"]["state"] == "Karnataka"
        assert inv["totals"]["igst_paise"] == inv["totals"]["tax_paise"] > 0

    def test_seller_details_frozen_into_the_invoice(self, db_engine, tenant, monkeypatch):
        monkeypatch.setenv("INVOICE_SELLER_NAME", "Magzest Test Pvt Ltd")
        monkeypatch.setenv("INVOICE_SELLER_GSTIN", "36abcde1234f1z5")
        inv = ti.issue_invoice("signup", _paid_signup(db_engine, tenant, "seller"))
        monkeypatch.setenv("INVOICE_SELLER_NAME", "Someone Else")
        again = ti.issue_invoice("signup", inv["source_id"])
        assert again["seller"]["name"] == "Magzest Test Pvt Ltd"
        assert again["seller"]["gstin"] == "36ABCDE1234F1Z5"
        assert again["seller"]["signatory"] == "Peddala Ranjith Kumar"

    def test_default_seller_is_hrzest(self, monkeypatch):
        for name in ("INVOICE_SELLER_NAME", "INVOICE_SELLER_ADDRESS", "INVOICE_SELLER_GSTIN"):
            monkeypatch.delenv(name, raising=False)
        seller = ti.seller_details()
        assert seller["name"] == "HRZEST" and seller["gstin"] == "36AALCG8469R1ZE"
        assert seller["address_lines"][-1] == "Telangana - 500072"
        assert not any("sample" in str(v).lower() for v in seller.values())

    def test_pdf_renders(self, db_engine, tenant):
        pdf = ti.build_tax_invoice_pdf(ti.issue_invoice("signup", _paid_signup(db_engine, tenant, "pdf")))
        assert pdf[:4] == b"%PDF" and len(pdf) > 5000


class TestAfterPayment:
    @pytest.fixture
    def sent(self, monkeypatch):
        sent = []
        monkeypatch.setattr(email_utils, "get_email_config", lambda: {"host": "smtp.test"})
        monkeypatch.setattr(email_utils, "get_billing_emails", lambda: ["owner@company.test"])
        monkeypatch.setattr(email_utils, "send_email_async",
                            lambda to, subject, body, cfg, **kw: sent.append((to, subject, kw)))
        monkeypatch.setattr(ti, "_platform_admin_emails", lambda: ["ops@hrzest.test"])
        return sent

    def test_admins_and_platform_admins_get_the_pdf(self, db_engine, tenant, sent):
        inv = ti.on_payment_completed("signup", _paid_signup(db_engine, tenant, "mail"))
        assert {to for to, _, _ in sent} == {"owner@company.test", "ops@hrzest.test"}
        for _, subject, kw in sent:
            assert inv["invoice_no"] in subject
            assert kw["attachment_bytes"][:4] == b"%PDF"
            assert kw["attachment_filename"] == ti.invoice_filename(inv)

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(ti, "issue_invoice", lambda *a: 1 / 0)
        assert ti.on_payment_completed("signup", 1) is None

    def test_auto_debit_charge_is_invoiced(self, db_engine, tenant, monkeypatch):
        from blueprints import auto_debit
        calls = []
        monkeypatch.setattr(auto_debit, "on_payment_completed", lambda *a: calls.append(a))
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, "
                    "razorpay_subscription_id, quantity_synced, status) VALUES (%s,'Test Co','sub_ti',1,'active')",
                    (TENANT_SCHEMA,))
        db_engine.commit()
        cur.close()
        auto_debit._record_charge("sub_ti", 9900, "pay_ti_charge", "paid")
        auto_debit._record_charge("sub_ti", 9900, "pay_ti_charge", "paid")   # webhook redelivered
        auto_debit._record_charge("sub_ti", 9900, "pay_ti_failed", "failed", "card declined")
        assert len(calls) == 1 and calls[0][0] == "invoice"
        invoice = ti.issue_invoice("invoice", calls[0][1])
        assert invoice["payment"]["mode"] == "Auto-Debit (Razorpay)"
        assert invoice["period"]["start"].endswith("-01")

    def test_paid_overdue_bill_is_invoiced(self, db_engine, tenant, monkeypatch):
        from blueprints import billing_dunning
        calls = []
        monkeypatch.setattr(billing_dunning, "on_payment_completed", lambda *a: calls.append(a))
        monkeypatch.setattr(billing_dunning, "_notify_tenant", lambda *a: None)
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.monthly_invoices (tenant_schema, company_name, employee_count, "
                    "amount_paise, status, billing_period, razorpay_order_id) "
                    "VALUES (%s,'Test Co',1,9900,'pending',%s,'demo_order_ti_overdue') RETURNING id",
                    (TENANT_SCHEMA, datetime.date.today().replace(day=1)))
        invoice_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()
        assert billing_dunning._mark_invoice_paid_and_unlock("demo_order_ti_overdue", "pay_ti_overdue")
        assert calls == [("invoice", invoice_id)]


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


class TestPages:
    def test_admin_downloads_own_invoice(self, client, db_engine, tenant, seed_admin):
        row_id = _paid_signup(db_engine, tenant, "dl")
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/profile/payments/signup/{row_id}/invoice.pdf")
        assert resp.status_code == 200 and resp.mimetype == "application/pdf"
        assert resp.data[:4] == b"%PDF"
        assert "Invoice_HRZ-" in resp.headers["Content-Disposition"]
        page = client.get("/profile/payments").get_data(as_text=True)
        assert "Your HRZEST plan" in page
        assert ti.get_invoice("signup", row_id)["invoice_no"] in page

    def test_no_invoice_for_an_unpaid_payment(self, client, db_engine, tenant, seed_admin):
        row_id = _paid_signup(db_engine, tenant, "dl_pending", status="created")
        _admin_session(client, seed_admin["username"])
        assert client.get(f"/profile/payments/signup/{row_id}/invoice.pdf").status_code == 404

    def test_dashboard_plan_card_for_admins_only(self, client, db_engine, tenant, seed_admin):
        _paid_signup(db_engine, tenant, "dash")
        _admin_session(client, seed_admin["username"])
        page = client.get("/admin").get_data(as_text=True)
        assert "Your HRZEST plan" in page and "Invoice PDF" in page and "Renews on" in page
        _admin_session(client, seed_admin["username"], role="hr")
        assert "Your HRZEST plan" not in client.get("/admin").get_data(as_text=True)

    def test_super_admin_invoice_download(self, client, db_engine, tenant):
        row_id = _paid_signup(db_engine, tenant, "sa")
        assert client.get(f"/super_admin/payments/signup/{row_id}/invoice.pdf").status_code in (302, 303, 401, 403)
        with client.session_transaction() as sess:
            sess["platform_admin_logged_in"] = True
            sess["platform_admin_username"] = "root"
            sess["platform_admin_last_activity"] = time.time()
        resp = client.get(f"/super_admin/payments/signup/{row_id}/invoice.pdf")
        assert resp.status_code == 200 and resp.data[:4] == b"%PDF"
        assert client.get("/super_admin/payments/signup/999999999/invoice.pdf").status_code == 404
