# -*- coding: utf-8 -*-
"""org: public leads and product feedback"""
from extensions import app_log, limiter
from flask import jsonify, redirect, request
from utils.auth import turnstile_enabled, verify_turnstile
from utils.email_utils import notify_support, public_contact_email, render_email, send_template_email
from utils.helpers import _safe_app_url
from . import org_bp
from .validation import _EMAIL_RE


@org_bp.route("/superadmin")
def superadmin_redirect():
    return redirect("/super_admin/login")


@org_bp.route("/api/leads", methods=["POST"])
@limiter.limit("5 per minute")
def submit_lead():
    """"Request info" and "Contact us" forms on the public landing page
    (templates/landing.html) -- for visitors who want more details before
    self-registering via /create_org. Stored in att_master.leads, surfaced
    on the Platform Admin dashboard for manual follow-up, and emailed to the
    support inbox (utils/email_utils.py's support_inbox()). The Contact form
    adds a subject. Deliberately has no admin-facing error detail beyond
    "required" -- this is a public, unauthenticated endpoint."""
    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response") or (request.get_json(silent=True) or {}).get("cf_turnstile_response", "")
        if not verify_turnstile(token, request.remote_addr):
            return jsonify({"ok": False, "msg": "Captcha verification failed. Please try again."}), 400

    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()[:200]
    email = (data.get("email") or "").strip()[:200]
    phone = (data.get("phone") or "").strip()[:30] or None
    company_name = (data.get("company_name") or "").strip()[:200] or None
    subject = (data.get("subject") or "").strip()[:200]
    message = (data.get("message") or "").strip()[:2000] or None
    if subject:
        # leads has no subject column: the Contact form's subject heads the message.
        message = f"{subject}\n\n{message or ''}".strip()

    if not name or not email:
        return jsonify({"ok": False, "msg": "Name and email are required."}), 400
    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "msg": "Enter a valid email address."}), 400

    try:
        from database import get_master_db
        conn = get_master_db()
        cur = conn.cursor()
        # One automatic reply per address per hour: the form is public, so a
        # flood of submissions naming someone else's address mustn't flood them.
        cur.execute(
            "SELECT COUNT(*) FROM leads WHERE LOWER(email) = LOWER(%s) AND created_at > NOW() - INTERVAL '1 hour'",
            (email,)
        )
        send_receipt = cur.fetchone()[0] == 0
        cur.execute(
            "INSERT INTO leads (name, email, phone, company_name, message) VALUES (%s, %s, %s, %s, %s)",
            (name, email, phone, company_name, message)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.error("submit_lead failed: %s", exc)
        return jsonify({"ok": False, "msg": "Could not submit right now. Please try again."}), 500

    notify_support(
        f"New enquiry: {subject or company_name or name}"[:200],
        _enquiry_email("New enquiry from the website", [
            ("Name", name), ("Email", email), ("Phone", phone), ("Company", company_name),
        ], message),
    )
    if send_receipt:
        send_template_email(
            email, "Thanks for contacting HRZest", "enquiry_received.html",
            first_name=_first_name(name), contact_email=public_contact_email(), site_url=_safe_app_url(),
        )
    return jsonify({"ok": True, "msg": "Thanks! We'll be in touch shortly."})


def _first_name(name):
    """The first word of a typed name if it looks like a name (letters, with
    ' - . allowed), else None -- the automatic reply greets with it, and
    must not carry arbitrary text (a URL, an advert) to whoever's address
    was typed in."""
    first = (name.split() or [""])[0]
    if 1 <= len(first) <= 30 and first[0].isalpha() and all(c.isalpha() or c in "'-." for c in first):
        return first
    return None


def _enquiry_email(heading, rows, message):
    """The support inbox's copy of a landing-page form
    (templates/emails/enquiry_notification.html). rows: (label, value)
    pairs; empty values are left out. All of it is the visitor's own input,
    autoescaped by the template."""
    return render_email("enquiry_notification.html", heading=heading,
                        rows=[(label, str(value)) for label, value in rows if value], message=message)


_FEEDBACK_TYPES = frozenset({"Feature Request", "User Experience", "Biometrics", "General Praise"})


@org_bp.route("/api/feedback", methods=["POST"])
@limiter.limit("5 per minute")
def submit_feedback():
    """Product feedback / star-rating form on the public landing page
    (templates/landing.html's #feedback section) -- was previously a pure
    client-side fake-success simulation (the form was hidden and a "Thanks!"
    banner shown on submit with no request ever sent). Stored in
    att_master.feedback, surfaced read-only on the Platform Admin dashboard
    (blueprints/platform_admin.py's _recent_feedback()). Same public/
    unauthenticated/rate-limited/optional-Turnstile posture as submit_lead()
    above -- co-located here rather than in its own module since it's the
    same "public landing page form" family."""
    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response") or (request.get_json(silent=True) or {}).get("cf_turnstile_response", "")
        if not verify_turnstile(token, request.remote_addr):
            return jsonify({"ok": False, "msg": "Captcha verification failed. Please try again."}), 400

    data = request.get_json(silent=True) or request.form
    feedback_type = (data.get("feedback_type") or "").strip()
    if feedback_type not in _FEEDBACK_TYPES:
        feedback_type = "Feature Request"
    email = (data.get("email") or "").strip()[:200] or None
    message = (data.get("message") or "").strip()[:2000]
    rating_raw = data.get("rating")

    if not message:
        return jsonify({"ok": False, "msg": "Please enter your feedback."}), 400
    if email and not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "msg": "Enter a valid email address, or leave it blank."}), 400
    rating = None
    if rating_raw not in (None, "", "0"):
        try:
            rating = int(rating_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "Rating must be a number."}), 400
        if rating < 1 or rating > 5:
            return jsonify({"ok": False, "msg": "Rating must be between 1 and 5."}), 400

    try:
        from database import get_master_db
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO feedback (feedback_type, rating, email, message) VALUES (%s, %s, %s, %s)",
            (feedback_type, rating, email, message)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.error("submit_feedback failed: %s", exc)
        return jsonify({"ok": False, "msg": "Could not submit right now. Please try again."}), 500

    notify_support(
        f"New feedback: {feedback_type}" + (f" ({rating}/5)" if rating else ""),
        _enquiry_email("New product feedback from the website", [
            ("Type", feedback_type), ("Rating", f"{rating} of 5" if rating else None), ("Email", email),
        ], message),
    )
    return jsonify({"ok": True, "msg": "Thanks for the feedback! It goes straight to our product team."})
