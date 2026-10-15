# -*- coding: utf-8 -*-
"""Billing blueprint tests -- /api/billing/create_order and
/api/billing/verify_payment (blueprints/billing.py).

This is the tenant-application payment-after-approval flow: an application
in att_master.tenant_applications must already be status='approved_pending_payment'
(the end of blueprints/org.py's OTP -> KYC-upload -> platform-admin-approve
pipeline, exercised end-to-end in tests/test_org.py) before create_order()
will even stage a payment_orders row for it.

Razorpay is NOT configured in this environment (no RAZORPAY_KEY_ID/SECRET),
so utils.razorpay_utils.razorpay_configured() is False by default here --
create_id_or_demo()/verify_or_demo() take the "demo" branch, which never
calls the real API and never checks a signature. Tests that need to
exercise the real signature-verification path instead monkeypatch
utils.razorpay_utils.razorpay_configured to True (so verify_or_demo takes
the live branch) and blueprints.billing.verify_payment_signature (the name
billing.py actually calls, bound at import time) to control the outcome --
mocking only the Razorpay boundary, never the database.

provision_tenant() itself (real schema creation) is exercised end-to-end
already by tests/test_org.py; here it's monkeypatched out (blueprints.org.provision_tenant,
patched where it's defined since billing.py imports it locally inside the
request handler) so these tests stay fast and focus on billing.py's own
wiring: does it call provision_tenant with the right arguments, and does it
correctly record the resulting tenant_id / status transitions.

Run with:
    python -m pytest tests/test_billing.py -v
"""
import secrets

import pytest

from utils.auth import _hash_token, generate_password_hash
from utils.plan_limits import calculate_price, format_price_inr

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")


def _insert_application(db_engine, status="approved_pending_payment", **overrides):
    suffix = secrets.token_hex(4)
    access_token = secrets.token_urlsafe(32)
    fields = {
        "company_name": overrides.pop("company_name", f"Billing Test Co {suffix}"),
        "subdomain": overrides.pop("subdomain", f"billing-test-{suffix}"),
        "admin_username": overrides.pop("admin_username", f"billing_admin_{suffix}"),
        "admin_email": overrides.pop("admin_email", f"billing-{suffix}@test.local"),
        "admin_password_hash": overrides.pop("admin_password_hash", generate_password_hash("Test@1234")),
        "email_domain": overrides.pop("email_domain", "test.local"),
        "access_token": access_token,
        "access_token_hash": overrides.pop("access_token_hash", _hash_token(access_token)),
        "status": status,
    }
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.tenant_applications "
        "(company_name, subdomain, admin_username, admin_email, admin_password_hash, "
        "email_domain, access_token_hash, status) "
        "VALUES (%(company_name)s, %(subdomain)s, %(admin_username)s, %(admin_email)s, "
        "%(admin_password_hash)s, %(email_domain)s, %(access_token_hash)s, %(status)s) RETURNING id",
        fields,
    )
    application_id = cur.fetchone()[0]
    cur.close()
    return application_id, fields


def _insert_payment_order(db_engine, application_id, fields, order_id, status="created", employee_count=5):
    amount_paise = calculate_price(employee_count)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.payment_orders (razorpay_order_id, plan, employee_count, amount_paise, "
        "company_name, subdomain, admin_username, admin_email, email_domain, application_id, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (order_id, "per_employee", employee_count, amount_paise, fields["company_name"], fields["subdomain"],
         fields["admin_username"], fields["admin_email"], fields["email_domain"], application_id, status),
    )
    cur.close()
    return amount_paise


def _cleanup(db_engine, application_id=None, subdomain=None, order_ids=()):
    cur = db_engine.cursor()
    for order_id in order_ids:
        cur.execute("DELETE FROM att_master.payment_orders WHERE razorpay_order_id=%s", (order_id,))
    if application_id:
        cur.execute("DELETE FROM att_master.payment_orders WHERE application_id=%s", (application_id,))
        cur.execute("DELETE FROM att_master.tenant_applications WHERE id=%s", (application_id,))
    if subdomain:
        cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
    cur.close()


def _prove_access(client, application_id, fields):
    """What a real applicant does before checkout: open the emailed pay
    link. The page checks the token and remembers only the application id
    in the session, which is what create_order() then accepts."""
    resp = client.get(f"/create_org/pay/{application_id}?token={fields['access_token']}")
    assert resp.status_code == 302


def _staged_order_count(db_engine, application_id):
    cur = db_engine.cursor()
    cur.execute("SELECT COUNT(*) FROM att_master.payment_orders WHERE application_id=%s", (application_id,))
    count = cur.fetchone()[0]
    cur.close()
    return count


class TestCreateOrderAccessToken:
    """Same rule as the pay/trial pages (blueprints/org.py's
    _verify_application_access): a sequential application_id alone must
    not be enough to stage an order or learn an application's status."""

    def test_no_proof_404s_and_stages_nothing(self, client, db_engine):
        application_id, _fields = _insert_application(db_engine)
        try:
            resp = client.post("/api/billing/create_order", json={
                "application_id": application_id, "employee_count": 5,
            })
            assert resp.status_code == 404
            assert resp.get_json() == {"ok": False, "msg": "Application not found."}
            assert _staged_order_count(db_engine, application_id) == 0
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_wrong_token_404s(self, client, db_engine):
        application_id, _fields = _insert_application(db_engine)
        try:
            resp = client.post("/api/billing/create_order?token=wrong-" + secrets.token_hex(8), json={
                "application_id": application_id, "employee_count": 5,
            })
            assert resp.status_code == 404
            assert _staged_order_count(db_engine, application_id) == 0
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_status_not_revealed_without_proof(self, client, db_engine):
        application_id, _fields = _insert_application(db_engine, status="pending_review")
        try:
            resp = client.post("/api/billing/create_order", json={
                "application_id": application_id, "employee_count": 5,
            })
            assert resp.status_code == 404
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_proof_for_another_application_does_not_carry_over(self, client, db_engine):
        app_a, fields_a = _insert_application(db_engine)
        app_b, _fields_b = _insert_application(db_engine)
        try:
            _prove_access(client, app_a, fields_a)
            resp = client.post("/api/billing/create_order", json={"application_id": app_b, "employee_count": 5})
            assert resp.status_code == 404
            assert _staged_order_count(db_engine, app_b) == 0
        finally:
            _cleanup(db_engine, application_id=app_a)
            _cleanup(db_engine, application_id=app_b)


class TestCreateOrder:
    def test_missing_application_id_rejected(self, client):
        resp = client.post("/api/billing/create_order", json={})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_non_numeric_application_id_rejected(self, client):
        resp = client.post("/api/billing/create_order", json={"application_id": "not-a-number"})
        assert resp.status_code == 400

    def test_unknown_application_rejected(self, client):
        resp = client.post("/api/billing/create_order", json={"application_id": 999999999})
        assert resp.status_code == 404

    def test_wrong_status_rejected(self, client, db_engine):
        application_id, fields = _insert_application(db_engine, status="pending_review")
        try:
            _prove_access(client, application_id, fields)
            resp = client.post("/api/billing/create_order", json={
                "application_id": application_id, "employee_count": 5,
            })
            assert resp.status_code == 400
            assert "isn't ready" in resp.get_json()["msg"].lower()
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_zero_employee_count_silently_becomes_one(self, client, db_engine):
        # NOTE: create_order() computes `int(data.get("employee_count") or 1)`
        # -- since 0 is falsy in Python, an explicit 0 is indistinguishable
        # from a missing field and silently becomes 1 rather than tripping
        # the "must be at least 1" check below it (which only a *negative*
        # value can ever reach, since negative ints are truthy). Harmless in
        # practice (worst case: billed for 1 employee instead of rejected),
        # but documented here as the actual behavior rather than asserting
        # the 400 a naive reading of the code would expect.
        application_id, fields = _insert_application(db_engine)
        try:
            _prove_access(client, application_id, fields)
            resp = client.post("/api/billing/create_order", json={
                "application_id": application_id, "employee_count": 0,
            })
            assert resp.status_code == 200
            assert resp.get_json()["amount_paise"] == calculate_price(1)
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_negative_employee_count_rejected(self, client, db_engine):
        application_id, fields = _insert_application(db_engine)
        try:
            _prove_access(client, application_id, fields)
            resp = client.post("/api/billing/create_order", json={
                "application_id": application_id, "employee_count": -3,
            })
            assert resp.status_code == 400
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_non_numeric_employee_count_rejected(self, client, db_engine):
        application_id, fields = _insert_application(db_engine)
        try:
            _prove_access(client, application_id, fields)
            resp = client.post("/api/billing/create_order", json={
                "application_id": application_id, "employee_count": "lots",
            })
            assert resp.status_code == 400
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_happy_path_stages_demo_order(self, client, db_engine):
        application_id, fields = _insert_application(db_engine)
        try:
            _prove_access(client, application_id, fields)
            resp = client.post("/api/billing/create_order", json={
                "application_id": application_id, "employee_count": 10,
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            # Razorpay isn't configured in this environment -- create_id_or_demo()
            # must take the demo branch, never a real Razorpay call.
            assert data["order_id"].startswith("demo_order_")
            assert data["demo"] is True
            assert data["amount_paise"] == calculate_price(10)
            assert data["amount_display"] == format_price_inr(calculate_price(10))

            cur = db_engine.cursor()
            cur.execute(
                "SELECT employee_count, amount_paise, status, application_id, subdomain "
                "FROM att_master.payment_orders WHERE razorpay_order_id=%s",
                (data["order_id"],),
            )
            row = cur.fetchone()
            cur.close()
            assert row == (10, calculate_price(10), "created", application_id, fields["subdomain"])
        finally:
            _cleanup(db_engine, application_id=application_id)


class TestVerifyPayment:
    def test_unknown_order_rejected(self, client):
        resp = client.post("/api/billing/verify_payment", json={
            "razorpay_order_id": "demo_order_" + secrets.token_hex(8),
            "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
        })
        assert resp.status_code == 404

    def test_real_mode_invalid_signature_rejected(self, client, db_engine, monkeypatch):
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.billing.verify_payment_signature", lambda *a, **k: False)

        application_id, fields = _insert_application(db_engine)
        order_id = "order_real_" + secrets.token_hex(6)
        _insert_payment_order(db_engine, application_id, fields, order_id)
        try:
            resp = client.post("/api/billing/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_bad", "razorpay_signature": "bad-sig",
            })
            assert resp.status_code == 400
            assert resp.get_json()["ok"] is False

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.payment_orders WHERE razorpay_order_id=%s", (order_id,))
            assert cur.fetchone()[0] == "created"  # untouched by a failed verification
            cur.close()
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_demo_order_rejected_once_real_keys_configured(self, client, db_engine, monkeypatch):
        # verify_or_demo()'s documented rule: a demo-prefixed id minted
        # before Razorpay was configured must never be honored once real
        # keys ARE configured, so this can never become a bypass in prod.
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)

        application_id, fields = _insert_application(db_engine)
        order_id = "demo_order_" + secrets.token_hex(6)
        _insert_payment_order(db_engine, application_id, fields, order_id)
        try:
            resp = client.post("/api/billing/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
            })
            assert resp.status_code == 400
            assert "demo" in resp.get_json()["msg"].lower()
        finally:
            _cleanup(db_engine, application_id=application_id)

    def test_valid_signature_provisions_and_is_idempotent(self, client, db_engine, monkeypatch):
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.billing.verify_payment_signature", lambda *a, **k: True)

        provision_calls = []

        def fake_provision_tenant(company_name, subdomain, admin_username, admin_password_hash, admin_email, **kw):
            provision_calls.append({
                "company_name": company_name, "subdomain": subdomain, "admin_username": admin_username,
                "admin_password_hash": admin_password_hash, "admin_email": admin_email, **kw,
            })
            return True, None, f"http://testserver/{subdomain}/login", f"http://testserver/{subdomain}/checkin"

        email_calls = []
        monkeypatch.setattr("blueprints.org.provision_tenant", fake_provision_tenant)
        monkeypatch.setattr("blueprints.org.send_payment_confirmation_email",
                            lambda *a, **k: email_calls.append((a, k)))

        application_id, fields = _insert_application(db_engine)
        order_id = "order_real_" + secrets.token_hex(6)
        _insert_payment_order(db_engine, application_id, fields, order_id, employee_count=7)

        # provision_tenant() is mocked out above (its real schema-creation
        # behavior is already covered end-to-end by tests/test_org.py), so
        # pre-seed the tenants row it would have created -- this isolates
        # billing.py's OWN tenant_id-lookup/status-transition wiring from
        # provisioning itself.
        db_name = "att_" + fields["subdomain"].replace("-", "_")
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, plan, status) "
            "VALUES (%s,%s,%s,%s,'per_employee','active') RETURNING id",
            (fields["company_name"], fields["subdomain"], db_name, fields["admin_email"]),
        )
        tenant_id = cur.fetchone()[0]
        cur.close()

        try:
            resp = client.post("/api/billing/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "good-sig",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "already_provisioned" not in data

            assert len(provision_calls) == 1
            call = provision_calls[0]
            assert call["company_name"] == fields["company_name"]
            assert call["subdomain"] == fields["subdomain"]
            assert call["admin_username"] == fields["admin_username"]
            # The password hash already on file from the gated-signup
            # application, NOT a freshly generated random one -- this is
            # the whole point of the docstring's "no separate set-password
            # step needed" design.
            assert call["admin_password_hash"] == fields["admin_password_hash"]
            assert call["admin_email"] == fields["admin_email"]
            assert call["employee_count"] == 7

            assert len(email_calls) == 1

            cur = db_engine.cursor()
            cur.execute(
                "SELECT status, tenant_id FROM att_master.payment_orders WHERE razorpay_order_id=%s", (order_id,)
            )
            row = cur.fetchone()
            assert row == ("provisioned", tenant_id)

            cur.execute(
                "SELECT status, tenant_id FROM att_master.tenant_applications WHERE id=%s", (application_id,)
            )
            arow = cur.fetchone()
            assert arow == ("provisioned", tenant_id)
            cur.close()

            # Idempotent replay: a retried client POST for the now-provisioned
            # order must short-circuit without calling provision_tenant again.
            resp2 = client.post("/api/billing/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "good-sig",
            })
            assert resp2.status_code == 200
            data2 = resp2.get_json()
            assert data2["ok"] is True
            assert data2["already_provisioned"] is True
            assert len(provision_calls) == 1  # not called a second time
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
            cur.close()
            _cleanup(db_engine, application_id=application_id)

    def test_concurrent_verify_never_provisions_twice(self, client, db_engine, monkeypatch):
        """Finding #18 (Low): the UPDATE used to have no idempotency guard
        of its own (unlike seats.py's equivalent) -- two near-simultaneous
        requests for the same order could both pass the status=='created'
        check above and both go on to call provision_tenant() for the same
        subdomain. Simulates the race's mid-flight state directly (order
        already flipped to 'paid' by a "concurrent" request, not yet
        'provisioned') rather than real threading, since the point is the
        UPDATE's own atomicity, not thread scheduling."""
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.billing.verify_payment_signature", lambda *a, **k: True)
        provision_calls = []
        monkeypatch.setattr(
            "blueprints.org.provision_tenant",
            lambda *a, **k: provision_calls.append(1) or (True, None, "http://x/login", "http://x/checkin"),
        )
        monkeypatch.setattr("blueprints.org.send_payment_confirmation_email", lambda *a, **k: None)

        application_id, fields = _insert_application(db_engine)
        order_id = "order_race_" + secrets.token_hex(6)
        _insert_payment_order(db_engine, application_id, fields, order_id)
        cur = db_engine.cursor()
        # Pre-flip to 'paid' -- the state a "winning" concurrent request
        # would have already committed by the time this one's UPDATE runs.
        cur.execute("UPDATE att_master.payment_orders SET status='paid' WHERE razorpay_order_id=%s", (order_id,))
        cur.close()
        try:
            resp = client.post("/api/billing/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "good-sig",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data.get("already_paid") is True
            assert not provision_calls, "the loser of the race must never call provision_tenant"
        finally:
            _cleanup(db_engine, application_id=application_id)


class TestSignupPaymentCapturedWebhook:
    """_handle_signup_payment_captured() -- the server-side safety net for
    when a browser's post-checkout callback to /api/billing/verify_payment
    never reaches the server (closed tab, network blip, ad-blocker) even
    though Razorpay itself captured the payment. Dispatched via the shared
    POST /webhooks/razorpay receiver (blueprints/webhooks.py), signature
    already verified upstream by that route -- these tests mock that
    verification the same way tests/test_webhooks.py does, and never touch
    blueprints.billing.verify_payment_signature (irrelevant to this path)."""

    def _post_payment_captured(self, client, monkeypatch, order_id, payment_id="pay_webhook_ok"):
        monkeypatch.setattr("blueprints.webhooks.verify_webhook_signature", lambda *a, **k: True)
        return client.post("/webhooks/razorpay", json={
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"order_id": order_id, "id": payment_id}}},
        })

    def test_provisions_an_order_the_browser_callback_never_reached(self, client, db_engine, monkeypatch):
        provision_calls = []

        def fake_provision_tenant(company_name, subdomain, admin_username, admin_password_hash, admin_email, **kw):
            provision_calls.append({"subdomain": subdomain})
            return True, None, f"http://testserver/{subdomain}/login", f"http://testserver/{subdomain}/checkin"

        monkeypatch.setattr("blueprints.org.provision_tenant", fake_provision_tenant)
        monkeypatch.setattr("blueprints.org.send_payment_confirmation_email", lambda *a, **k: None)

        application_id, fields = _insert_application(db_engine)
        order_id = "order_webhook_" + secrets.token_hex(6)
        # Left at 'created' -- simulates the browser's callback never having
        # arrived at all, unlike every verify_payment() test above.
        _insert_payment_order(db_engine, application_id, fields, order_id, status="created")

        db_name = "att_" + fields["subdomain"].replace("-", "_")
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, plan, status) "
            "VALUES (%s,%s,%s,%s,'per_employee','active') RETURNING id",
            (fields["company_name"], fields["subdomain"], db_name, fields["admin_email"]),
        )
        tenant_id = cur.fetchone()[0]
        cur.close()

        try:
            resp = self._post_payment_captured(client, monkeypatch, order_id)
            assert resp.status_code == 200
            assert len(provision_calls) == 1
            assert provision_calls[0]["subdomain"] == fields["subdomain"]

            cur = db_engine.cursor()
            cur.execute(
                "SELECT status, tenant_id FROM att_master.payment_orders WHERE razorpay_order_id=%s", (order_id,)
            )
            assert cur.fetchone() == ("provisioned", tenant_id)
            cur.close()
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
            cur.close()
            _cleanup(db_engine, application_id=application_id)

    def test_unrecognized_order_id_noops_without_error(self, client, monkeypatch):
        """An order id belonging to a seat top-up or dunning payment (a
        different order-id space entirely) must not raise -- same
        no-op-on-mismatch contract billing_dunning.py's own handler for
        this event already has."""
        resp = self._post_payment_captured(client, monkeypatch, "order_not_a_signup_" + secrets.token_hex(6))
        assert resp.status_code == 200

    def test_noop_once_already_provisioned_by_the_browser_callback(self, client, db_engine, monkeypatch):
        """The normal case -- verify_payment() already finished the job
        before the webhook arrives. Must not attempt to re-provision."""
        provision_calls = []
        monkeypatch.setattr(
            "blueprints.org.provision_tenant",
            lambda *a, **k: provision_calls.append(1) or (True, None, "http://x/login", "http://x/checkin"),
        )

        application_id, fields = _insert_application(db_engine)
        order_id = "order_already_done_" + secrets.token_hex(6)
        _insert_payment_order(db_engine, application_id, fields, order_id, status="provisioned")

        try:
            resp = self._post_payment_captured(client, monkeypatch, order_id)
            assert resp.status_code == 200
            assert not provision_calls
        finally:
            _cleanup(db_engine, application_id=application_id)
