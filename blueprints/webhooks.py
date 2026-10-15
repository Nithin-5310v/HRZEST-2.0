# -*- coding: utf-8 -*-
"""Generic /webhooks/<provider> endpoint -- the shared home for every
server-to-server callback this app receives (Razorpay today, whatever
comes next tomorrow).

Owning this here, once, means a consumer feature (blueprints/auto_debit.py
today) doesn't have to each re-solve: claiming a slice of the shared
/webhooks/ URL namespace (utils/tenant_routing.py's
RESERVED_PATH_SEGMENTS), the CSRF exemption for a route with no session
cookie to carry a token (app.py's _enforce_csrf()), or the
signature-verification boilerplate -- all of that lives here exactly once,
regardless of how many providers/features end up using it.

Features register a handler at *import time* (module level, not inside a
request) via register_webhook_handler(provider, event, handler) -- see
blueprints/auto_debit.py's module-level registration calls for the one
real consumer today. A handler receives the parsed JSON payload and
returns nothing; this module doesn't care what it does with it.
"""
from flask import Blueprint, request, jsonify
from extensions import limiter, log_security_event, app_log
from utils.razorpay_utils import verify_webhook_signature

webhooks_bp = Blueprint("webhooks", __name__)

# {(provider, event): [handler(payload: dict), ...]} -- populated by each
# feature blueprint at import time via register_webhook_handler() below.
# A list, not a single callable, because more than one feature can care
# about the same event (e.g. "payment.captured" fires for a dunning
# repayment, a signup order, AND a seat top-up -- each handler is expected
# to check its own table and no-op on a non-match, per this module's own
# original design intent). A bare dict-of-callables would let a second
# registration for the same key silently replace the first with no error.
_HANDLERS = {}

# provider -> callable(raw_body: bytes, request) -> bool. Each provider
# signs callbacks differently (Razorpay: HMAC of the raw body against a
# dashboard-configured webhook secret, in a fixed header) -- a second
# provider adds one more entry here, not a second route/blueprint.
_SIGNATURE_VERIFIERS = {
    "razorpay": lambda raw_body, req: verify_webhook_signature(
        raw_body, req.headers.get("X-Razorpay-Signature", "")
    ),
}


def register_signature_verifier(provider: str, verifier):
    """Registers `verifier(raw_body: bytes, request) -> bool` for a provider
    whose callbacks this app should accept at /webhooks/<provider>. Same
    import-time registration model as register_webhook_handler(); a
    provider with no verifier 404s, so nothing here is reachable until a
    feature explicitly opts in with a real signature check."""
    _SIGNATURE_VERIFIERS[provider] = verifier


def register_webhook_handler(provider: str, event: str, handler):
    """Registers `handler(payload: dict)` to run when POST /webhooks/<provider>
    receives an `event`-named event, after signature verification passes.
    Call this at blueprint import time (module level) -- see
    blueprints/auto_debit.py. Multiple handlers may register for the same
    (provider, event) pair -- all of them run; each is expected to check
    whether the payload is actually theirs (e.g. by order-id table lookup)
    and no-op if not, same as blueprints/billing_dunning.py's existing
    payment.captured handler already does for orders it doesn't recognize."""
    _HANDLERS.setdefault((provider, event), []).append(handler)


@webhooks_bp.route("/webhooks/<provider>", methods=["POST"])
@limiter.limit("120 per minute")
def receive_webhook(provider):
    """No @admin_required/@api_required -- the caller is a remote
    server with no session cookie or Bearer token, authenticated instead
    by the provider-specific signature check below. Deliberately never
    touches g.tenant_db (this route has no tenant-prefixed URL, same
    posture as blueprints/platform_admin.py's /super_admin/* per that
    module's own docstring) -- registered handlers are expected to work
    entirely off master-schema tables, the same way
    blueprints/auto_debit.py's do."""
    verifier = _SIGNATURE_VERIFIERS.get(provider)
    if not verifier:
        # Unknown provider -- 404 rather than 400, so this doesn't leak
        # which provider names ARE recognized to an unauthenticated caller.
        return jsonify({"ok": False}), 404

    raw_body = request.get_data()
    if not verifier(raw_body, request):
        log_security_event(
            f"webhooks.{provider}_signature_invalid",
            f"{provider} webhook signature verification failed", level="ERROR",
        )
        return jsonify({"ok": False}), 400

    payload = request.get_json(silent=True) or {}
    event = payload.get("event", "")
    handlers = _HANDLERS.get((provider, event), [])
    failed = False
    for handler in handlers:
        try:
            handler(payload)
        except Exception:
            # Previously an unhandled exception here surfaced only as a
            # bare 500 to the provider, with zero signal on our side --
            # log before re-raising so Flask's normal error handling still
            # returns 500 (providers retry on that), but the failure is
            # now visible to monitoring instead of silently disappearing.
            # Every OTHER registered handler for this event still runs
            # first -- one feature's bug shouldn't stop an unrelated
            # feature's handler for the same event from doing its job,
            # and each handler is independently idempotent so a provider
            # retry of the whole event (triggered by the raise below) is
            # always safe even for the handlers that already succeeded.
            app_log.error(
                "webhooks.handler_failed: provider=%s event=%s", provider, event, exc_info=True
            )
            failed = True
    if failed:
        raise RuntimeError(f"webhooks: one or more handlers failed for {provider}.{event}")
    # 200 regardless of whether an event had a registered handler --
    # an unhandled-but-authentic event (one we simply don't act on yet)
    # is not a delivery failure Razorpay should retry.
    return jsonify({"ok": True})
