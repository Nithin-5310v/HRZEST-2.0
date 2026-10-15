# -*- coding: utf-8 -*-
"""Razorpay REST integration -- one-time Orders (paid signup, seat top-ups)
and recurring Subscriptions (monthly per-employee auto-debit).

Calls the REST API directly over urllib.request (stdlib) rather than the
`razorpay` SDK -- same no-heavy-SDK pattern already used for the Anthropic
API in utils/ai_assistant.py and webhook delivery in utils/alerts.py, so
this needs no new pip dependency.
"""
import os
import json
import base64
import hmac
import hashlib
import secrets
import urllib.request
import urllib.error

from extensions import app_log

_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
# Signs incoming webhook payloads (blueprints/auto_debit.py's /webhooks/razorpay)
# -- configured separately from _KEY_ID/_KEY_SECRET in the Razorpay dashboard's
# Webhooks section, since it authenticates Razorpay -> us, not us -> Razorpay.
_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# Sandbox-vs-live is entirely a function of which key prefix is configured
# (Razorpay itself distinguishes rzp_test_... from rzp_live_...) -- there is
# no separate "mode" flag to get out of sync. The one real risk is a
# production deploy accidentally still carrying a test key (e.g. a copied
# .env, or a developer never swapping .env.example's placeholder), which
# would silently run every "live" signup through the sandbox. Fail fast
# instead, same fail-secure posture as utils/helpers.py's ENCRYPTION_KEY
# check above it in the import order.
if os.environ.get("APP_ENV") == "production" and _KEY_ID.startswith("rzp_test_"):
    app_log.critical(
        "FATAL: APP_ENV=production but RAZORPAY_KEY_ID is a test-mode key "
        "(rzp_test_...) -- refusing to start rather than silently taking real "
        "signups through the Razorpay sandbox. Set RAZORPAY_KEY_ID/"
        "RAZORPAY_KEY_SECRET to live (rzp_live_...) keys."
    )
    raise RuntimeError(
        "RAZORPAY_KEY_ID is a test-mode key under APP_ENV=production -- refusing to start (fail-secure)."
    )
# The check above only catches a LEFTOVER test key -- it says nothing about
# the keys being MISSING entirely (a broken secret-manager injection, a
# typo'd env var name, a .env that never got the production secrets added).
# In that case razorpay_configured() below is simply False, and every
# payment flow (utils/razorpay_utils.create_id_or_demo()/verify_or_demo(),
# used throughout blueprints/seats.py, billing.py, auto_debit.py, org.py,
# billing_dunning.py) transparently falls into its demo branch: orders/
# subscriptions get fabricated demo_* ids, and verify_or_demo() accepts
# them with NO signature check and no real charge -- silently provisioning
# tenants, crediting seats, and activating mandates for zero real money.
# Refuse to start rather than let that happen unnoticed.
if os.environ.get("APP_ENV") == "production" and not (_KEY_ID and _KEY_SECRET):
    app_log.critical(
        "FATAL: APP_ENV=production but RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET are not "
        "configured -- refusing to start rather than silently running every payment "
        "flow through the no-charge demo path. Set both to live (rzp_live_...) keys."
    )
    raise RuntimeError(
        "RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET are unset under APP_ENV=production -- refusing to start (fail-secure)."
    )

_API_BASE = "https://api.razorpay.com/v1"
_TIMEOUT_SECONDS = 15


def razorpay_configured() -> bool:
    return bool(_KEY_ID and _KEY_SECRET)


def key_id() -> str:
    """Public key ID -- safe to hand to the frontend (Checkout needs it),
    unlike _KEY_SECRET which never leaves this module."""
    return _KEY_ID


def _auth_header() -> str:
    token = base64.b64encode(f"{_KEY_ID}:{_KEY_SECRET}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _request(path: str, body: dict = None, method: str = "POST"):
    """Shared REST call against the Razorpay API. Returns (data, error) --
    exactly one is None, same convention as utils/ai_assistant.py's
    _call_claude(). Every Orders/Plans/Customers/Subscriptions call below
    goes through this so the HTTPError/URLError handling only lives once."""
    if not razorpay_configured():
        return None, "Payments are not configured yet. Contact support."
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "Authorization": _auth_header()}
    req = urllib.request.Request(f"{_API_BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            msg = err_body.get("error", {}).get("description", str(e))
        except Exception:
            msg = str(e)
        return None, f"Razorpay request failed: {msg}"
    except urllib.error.URLError as e:
        return None, f"network error contacting Razorpay: {e.reason}"
    except Exception as e:
        return None, f"unexpected error calling Razorpay: {e}"


def create_order(amount_paise: int, receipt_id: str):
    """POST /v1/orders -- a one-time charge (paid signup, seat top-ups).
    Returns (order_id, error)."""
    data, error = _request("/orders", {
        "amount": amount_paise, "currency": "INR", "receipt": receipt_id, "payment_capture": 1,
    })
    if error:
        return None, error
    order_id = data.get("id")
    if not order_id:
        return None, "Razorpay did not return an order ID."
    return order_id, None


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """HMAC-SHA256 of "order_id|payment_id" with the key secret -- Razorpay's
    documented Checkout success-callback verification contract for a
    one-time Order (blueprints/billing.py, blueprints/seats.py). NOT valid
    for a Subscription authorization callback -- see
    verify_subscription_signature() below, which uses a different field
    order. Constant-time compare (hmac.compare_digest), never a plain ==
    on a MAC."""
    if not razorpay_configured() or not (order_id and payment_id and signature):
        return False
    expected = hmac.new(
        _KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_subscription_signature(subscription_id: str, payment_id: str, signature: str) -> bool:
    """HMAC-SHA256 of "payment_id|subscription_id" -- Razorpay's documented
    verification contract for a Subscription authorization callback
    (blueprints/auto_debit.py's confirm()). Deliberately NOT the same field
    order as verify_payment_signature()'s "order_id|payment_id" for a
    one-time Order -- Razorpay swaps the order for subscriptions, and using
    the Order formula here would make every real (non-demo) auto-debit
    enrollment fail verification even though the mandate was genuinely
    authorized. Constant-time compare, same as verify_payment_signature."""
    if not razorpay_configured() or not (subscription_id and payment_id and signature):
        return False
    expected = hmac.new(
        _KEY_SECRET.encode("utf-8"),
        f"{payment_id}|{subscription_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def create_plan(amount_paise: int, item_name: str, period: str = "monthly", interval: int = 1):
    """POST /v1/plans -- a recurring-billing template (amount charged each
    cycle, before any per-subscription `quantity` multiplier). Returns
    (plan_id, error). Created once and cached (blueprints/auto_debit.py
    stores the id in att_master.billing_config) rather than re-created per
    tenant -- one Plan ("₹99/employee/month") is shared by every tenant's
    own Subscription, which is what actually carries that tenant's
    `quantity` (= their employee count)."""
    data, error = _request("/plans", {
        "period": period, "interval": interval,
        "item": {"name": item_name, "amount": amount_paise, "currency": "INR"},
    })
    if error:
        return None, error
    plan_id = data.get("id")
    if not plan_id:
        return None, "Razorpay did not return a plan ID."
    return plan_id, None


def create_customer(name: str, email: str, fail_if_exists: bool = False):
    """POST /v1/customers. Returns (customer_id, error). fail_if_exists=False
    (default) lets Razorpay return the existing customer for a duplicate
    email instead of erroring -- fine here since we key our own
    auto_debit_mandates row by tenant_schema, not by trusting this id to be
    unique across tenants."""
    data, error = _request("/customers", {
        "name": name, "email": email, "fail_existing": "1" if fail_if_exists else "0",
    })
    if error:
        return None, error
    customer_id = data.get("id")
    if not customer_id:
        return None, "Razorpay did not return a customer ID."
    return customer_id, None


def create_subscription(plan_id: str, customer_id: str, quantity: int, total_count: int = 1200):
    """POST /v1/subscriptions. `quantity` is the seat-based-billing lever
    Razorpay documents for exactly this case (variable per-unit recurring
    charge): total billed each cycle = plan.item.amount * quantity.
    total_count=1200 (100 years of monthly cycles) stands in for
    "indefinite" -- Razorpay requires a finite cycle count, and
    cancel_subscription() below is the real way this ever stops. Returns
    (subscription_id, error)."""
    data, error = _request("/subscriptions", {
        "plan_id": plan_id, "customer_id": customer_id, "quantity": max(1, quantity),
        "total_count": total_count, "customer_notify": 1,
    })
    if error:
        return None, error
    subscription_id = data.get("id")
    if not subscription_id:
        return None, "Razorpay did not return a subscription ID."
    return subscription_id, None


def update_subscription_quantity(subscription_id: str, quantity: int):
    """PATCH /v1/subscriptions/{id} -- resyncs the billed quantity to a
    tenant's current headcount (blueprints/auto_debit.py's daily sync job).
    schedule_change_at="now" applies immediately with proration, matching
    Razorpay's documented seat-based-billing guidance (an employee added
    mid-cycle should start being billed for right away, not silently free
    until the next renewal). Returns (ok, error)."""
    data, error = _request(f"/subscriptions/{subscription_id}", {
        "quantity": max(1, quantity), "schedule_change_at": "now",
    }, method="PATCH")
    if error:
        return False, error
    return True, None


def list_subscription_invoices(subscription_id: str, count: int = 5):
    """GET /v1/invoices?subscription_id={id} -- Razorpay's own authoritative
    billing history for one subscription, most recent first. Used by
    blueprints/auto_debit.py's nightly reconciliation job to fill in a
    monthly_invoices row for a cycle whose subscription.charged webhook
    never arrived (a network blip, an outage during the delivery window --
    Razorpay retries webhooks, but not forever). Returns (items, error)
    where items is the raw list of invoice objects (each carrying at least
    id/status/amount_paid/date/payment_id) -- deliberately not reshaped
    here, since _record_charge() (auto_debit.py) already owns the mapping
    from "Razorpay's fields" to "this app's monthly_invoices columns" for
    the webhook path, and the reconciliation job reuses that exact
    function rather than a second, parallel mapping that could drift from
    it."""
    data, error = _request(
        f"/invoices?subscription_id={subscription_id}&count={count}", method="GET"
    )
    if error:
        return None, error
    return data.get("items", []), None


def cancel_subscription(subscription_id: str, cancel_at_cycle_end: bool = False):
    """POST /v1/subscriptions/{id}/cancel. Returns (ok, error)."""
    data, error = _request(f"/subscriptions/{subscription_id}/cancel", {
        "cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0,
    })
    if error:
        return False, error
    return True, None


def demo_mode_allowed() -> bool:
    """Demo (no-charge) payment ids are only ever honoured on a local
    development box. Read per call, not at import, and keyed on an exact
    APP_ENV match so staging/unset/typo'd environments fail closed."""
    return os.environ.get("APP_ENV", "production") == "development"


def create_id_or_demo(demo_prefix: str, create_fn):
    """Shared "demo vs. live" branch for minting a Razorpay object id.
    blueprints/billing.py (Orders), blueprints/seats.py (Orders), and
    blueprints/auto_debit.py (Subscriptions) each independently
    reimplemented this exact branch -- "if Razorpay isn't configured, mint
    a demo_prefix-tagged random id instead of calling the real API" --
    with only the prefix and the real creation call differing. That
    triplication is exactly how utils/razorpay_utils.py ended up with an
    inconsistent demo-detection rule in one caller (fixed separately) --
    consolidating here means there's only one place left to get it right.

    create_fn: a zero-arg callable returning (real_id, error) -- callers
    close over whatever real-mode-only arguments (amount, receipt id, plan
    id, customer id, ...) they need. Never called at all when Razorpay
    isn't configured.
    Returns (id, is_demo, error) -- error is None on success."""
    if not razorpay_configured():
        if not demo_mode_allowed():
            app_log.error("Razorpay keys are not configured outside development -- refusing demo payment id")
            return None, False, "Payments are temporarily unavailable. Please contact support."
        return demo_prefix + secrets.token_hex(12), True, None
    real_id, error = create_fn()
    if error:
        return None, False, error
    return real_id, False, None


def verify_or_demo(id_value: str, demo_prefix: str, payment_id: str, signature: str, verify_fn):
    """Shared "demo vs. live" branch for the verify/confirm side, mirroring
    create_id_or_demo above. If id_value carries demo_prefix, honors it as
    a demo completion UNLESS real Razorpay keys are configured NOW -- a
    demo id minted before configuration can never be honored once real
    payments are live, so that this never becomes a bypass in production.
    Otherwise runs verify_fn(id_value, payment_id, signature) (either
    verify_payment_signature for an Order, or verify_subscription_signature
    for a Subscription -- callers pass whichever applies).
    Returns (ok, is_demo, error_msg) -- error_msg is None when ok is True."""
    is_demo = (id_value or "").startswith(demo_prefix)
    if is_demo:
        if razorpay_configured() or not demo_mode_allowed():
            return False, True, "This was created in demo/test mode. Please start again."
        return True, True, None
    if not verify_fn(id_value, payment_id, signature):
        return False, False, "Payment verification failed."
    return True, False, None


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 of the raw request body with RAZORPAY_WEBHOOK_SECRET --
    Razorpay's documented webhook authenticity check (X-Razorpay-Signature
    header). Deliberately independent of _KEY_SECRET: the webhook secret is
    configured separately in the Razorpay dashboard's Webhooks section.
    Constant-time compare, same as verify_payment_signature above."""
    if not _WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
