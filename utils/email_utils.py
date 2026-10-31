# -*- coding: utf-8 -*-
"""Email sending -- SMTP or Microsoft 365 (utils/graph_mail.py) + DB-backed queue with retry worker."""
import os
import re
import ssl
import html as _html
import base64
import datetime as _dt
import smtplib
import threading
import time as _time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate, make_msgid
from database import get_db_connection, get_master_db
from extensions import app, app_log
from utils.helpers import decrypt_pii
from utils import graph_mail


def _mask_email(email):
    """j***@example.com -- enough left to spot-check in a log line
    ("is this even the right domain/user") without putting the full
    address into logs an on-call/log-pipeline audience can read, for an
    error path that doesn't otherwise need the full value."""
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def get_email_config():
    """Return SMTP config dict from DB, falling back to .env values.

    Two bugs fixed here, found by diffing against app.py's copy of this
    same function before either went live in a blueprint:

    1. Wrong column names. email_config's real schema (app.py's init_db())
       is smtp_host/smtp_port/smtp_user/smtp_pass/from_name/from_email --
       this queried host/port/username/password/from_name/from_email,
       none of which exist. Every call would raise "column does not
       exist", get swallowed by the bare except below, and silently fall
       back to .env config -- meaning a DB-configured SMTP setup would
       never actually be honored, with no error surfaced anywhere.
    2. Missing decryption. smtp_pass is stored encrypted at rest
       (encrypt_pii on save) -- this returned the raw ciphertext as the
       password, which would have failed SMTP auth on every send.

    Also added ORDER BY id DESC: LIMIT 1 with no ORDER BY doesn't
    guarantee which row Postgres returns if more than one exists --
    matches app.py's behavior of always using the most recently saved
    config.
    """
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "SELECT smtp_host, smtp_port, smtp_user, smtp_pass, from_name, from_email "
            "FROM email_config ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        cursor.close()
        db.close()
        if row and row[0]:
            return {
                "host": row[0], "port": row[1], "user": row[2], "password": decrypt_pii(row[3]),
                "from_name": row[4], "from_email": row[5] or row[2],
            }
    except Exception as exc:
        # See the docstring above: this exact silent swallow used to be
        # the reason a DB-configured SMTP setup could go unused with zero
        # indication why -- now at least surfaced before falling back to
        # .env config.
        app_log.warning("get_email_config: DB lookup failed, falling back to .env config: %s", exc, exc_info=True)
    # No company SMTP saved: .env's Microsoft 365 app (EMAIL_PROVIDER=
    # microsoft_graph, utils/graph_mail.py) before .env's SMTP_* settings.
    graph_cfg = graph_mail.config_from_env()
    if graph_cfg:
        return graph_cfg
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    if smtp_host and smtp_user and smtp_pass:
        return {
            "host": smtp_host,
            "port": int(os.environ.get("SMTP_PORT", 587)),
            "user": smtp_user,
            "password": smtp_pass,
            "from_name": os.environ.get("SMTP_FROM_NAME", "HRzest.com"),
            "from_email": os.environ.get("SMTP_FROM_EMAIL", smtp_user),
        }
    return None


def support_inbox():
    """Where enquiries go and the address the website shows for contacting
    HRZEST: SUPPORT_EMAIL, else the address mail is sent from
    (EMAIL_FROM_EMAIL / SMTP_FROM_EMAIL), else noreply@hrzest.com."""
    for name in ("SUPPORT_EMAIL", "EMAIL_FROM_EMAIL", "SMTP_FROM_EMAIL"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return "noreply@hrzest.com"


def notify_support(subject, html_body):
    """Queue an email to the support inbox (support_inbox()). Never raises:
    the enquiry is already saved, so a mail problem is only logged."""
    try:
        config = get_email_config()
        if not config:
            app_log.warning("notify_support: no email configured -- enquiry saved but not emailed")
            return
        send_email_async(support_inbox(), subject, html_body, config)
    except Exception as exc:
        app_log.error("notify_support failed: %s", exc)


def get_admin_emails():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT email FROM admin_users WHERE email IS NOT NULL AND email != ''")
    emails = [row[0] for row in cursor.fetchall()]
    cursor.close()
    db.close()
    return emails


def _html_to_plain_text(html_body):
    """Best-effort plain-text fallback for the multipart/alternative part
    below -- not meant to be a faithful rendering, just enough text that
    the message isn't HTML-only, which spam filters penalize on its own."""
    text = re.sub(r"(?i)<(br|/p|/div|/tr|/li)\s*/?>", "\n", html_body)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def send_email_smtp(to_email, subject, html_body, config,
                    attachment_bytes=None, attachment_filename=None):
    """Sends one email with whatever get_email_config() returned: SMTP, or
    Microsoft 365 for a microsoft_graph config. Raises on failure."""
    if config.get("provider") == graph_mail.PROVIDER:
        graph_mail.send_mail(config, to_email, subject, html_body,
                             attachment_bytes=attachment_bytes, attachment_filename=attachment_filename)
        return
    from_addr = config.get("from_email") or config["user"]
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{config['from_name']} <{from_addr}>"
    msg["To"] = to_email
    # Both missing on every email this app sent before -- legitimate mail
    # essentially always carries these, so their absence is itself a spam
    # signal on top of whatever else a filter scores the content on.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1])
    alt = MIMEMultipart("alternative")
    # Plain-text part must come first: multipart/alternative order is
    # least-to-most preferred, and an HTML-only message (no text/plain
    # sibling at all) is another common spam-filter penalty.
    alt.attach(MIMEText(_html_to_plain_text(html_body), "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)
    if attachment_bytes and attachment_filename:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{attachment_filename}"')
        msg.attach(part)
    context = ssl.create_default_context()
    port = int(config.get("port", 587))
    if port == 465:
        with smtplib.SMTP_SSL(config["host"], port, context=context, timeout=20) as server:
            server.login(config["user"], config["password"])
            server.sendmail(from_addr, to_email, msg.as_string())
    else:
        with smtplib.SMTP(config["host"], port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(config["user"], config["password"])
            server.sendmail(from_addr, to_email, msg.as_string())


def send_email_async(to_email, subject, html_body, config,
                     attachment_bytes=None, attachment_filename=None, **_):
    """Enqueue email for reliable delivery via the DB-backed worker."""
    att_b64 = base64.b64encode(attachment_bytes).decode() if attachment_bytes else None
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO email_queue (to_email, subject, html_body, attachment_b64, attachment_filename) "
            "VALUES (%s,%s,%s,%s,%s)",
            (to_email, subject, html_body, att_b64, attachment_filename)
        )
        db.commit()
        cur.close()
        db.close()
    except Exception as e:
        app_log.error("Failed to enqueue email to %s: %s", _mask_email(to_email), e)
        threading.Thread(
            target=lambda: send_email_smtp(to_email, subject, html_body, config,
                                           attachment_bytes=attachment_bytes,
                                           attachment_filename=attachment_filename),
            daemon=True
        ).start()


def _email_queue_worker():
    """Background thread: dequeues and sends emails; retries up to 3 times.

    Multi-tenant aware -- this app is schema-per-tenant, and email_queue is
    a per-schema table (each tenant has its own). This thread has no Flask
    request of its own, so get_db_connection() has nothing to read
    g.tenant_db from and previously always fell through to the "public"
    schema only -- meaning every REAL tenant's queued mail (login OTPs,
    payslips, signup verification, everything) sat at status='pending'
    forever, with the worker never even looking at it. Now drains "public"
    (legacy/single-tenant local-dev deployments) plus every active tenant
    schema each cycle, same app.app_context() + g.tenant_db-per-iteration
    pattern already used by blueprints/billing_dunning.py's
    check_tenant_billing() and blueprints/disbursement.py's
    prepare_pending_disbursements()."""
    while True:
        try:
            with app.app_context():
                _drain_queue_for_schema("public")
                for schema in _active_tenant_schemas():
                    _drain_queue_for_schema(schema)
        except Exception as _we:
            app_log.error("Email queue worker error: %s", _we)
        _time.sleep(15)


def _active_tenant_schemas():
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        # Skips a company whose schema lacks tables: it would drain the
        # shared public queue instead of its own (QA SEC-07).
        cur.execute("SELECT db_name FROM tenants WHERE status='active' AND schema_missing_tables IS NULL")
        schemas = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return schemas
    except Exception as exc:
        app_log.error("Email queue worker: failed to list active tenants: %s", exc)
        return []


# A row still 'sending' after this long belongs to a drainer that died mid-send.
_STALE_CLAIM = _dt.timedelta(minutes=15)


def _drain_queue_for_schema(schema_name):
    from flask import g as _g
    prev_tenant_db = getattr(_g, "tenant_db", None)
    try:
        _g.tenant_db = schema_name
        cfg = get_email_config()
        if not cfg:
            return
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        # Every gunicorn worker runs a drainer, so rows are claimed in one
        # atomic statement (SKIP LOCKED: a row another drainer is claiming is
        # left to it) -- selecting first and marking each row just before
        # sending let two drainers send the same email (QA SCALE-06).
        # claimed_at comes from this process's UTC clock on both sides of
        # the comparison, so it doesn't depend on the DB session timezone.
        now_utc = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        stale_before = now_utc - _STALE_CLAIM
        cur.execute(
            "UPDATE email_queue SET status='pending' WHERE status='sending' AND "
            "(claimed_at < %s OR (claimed_at IS NULL AND created_at < %s))",
            (stale_before, stale_before),
        )
        cur.execute(
            "UPDATE email_queue SET status='sending', attempts=attempts+1, claimed_at=%s "
            "WHERE id IN (SELECT id FROM email_queue WHERE status='pending' AND attempts < 3 "
            "ORDER BY created_at LIMIT 10 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, to_email, subject, html_body, attachment_b64, attachment_filename",
            (now_utc,),
        )
        rows = cur.fetchall()
        db.commit()
        for row in rows:
            eid, to_email, subject, html_body, att_b64, att_name = row
            try:
                att_bytes = base64.b64decode(att_b64) if att_b64 else None
                send_email_smtp(to_email, subject, html_body, cfg,
                                attachment_bytes=att_bytes, attachment_filename=att_name)
                cur.execute(
                    "UPDATE email_queue SET status='done', sent_at=NOW() WHERE id=%s", (eid,)
                )
            except Exception as exc:
                app_log.error("Email queue send failed (id=%s) to %s (schema=%s): %s",
                              eid, _mask_email(to_email), schema_name, exc)
                cur.execute(
                    "UPDATE email_queue SET status='pending', last_error=%s WHERE id=%s",
                    (str(exc)[:500], eid)
                )
            db.commit()
        cur.execute(
            "UPDATE email_queue SET status='failed' WHERE status='pending' AND attempts >= 3"
        )
        db.commit()
        cur.close()
        db.close()
    except Exception as exc:
        # Table may simply not exist yet for this schema (e.g. a brand-new
        # tenant mid-provisioning) -- log and move on to the next schema
        # rather than letting one tenant's hiccup stop the whole cycle.
        app_log.error("Email queue worker: drain failed for schema=%s: %s", schema_name, exc)
    finally:
        _g.tenant_db = prev_tenant_db


def build_new_ip_login_email(display_name, identifier, ip_address, login_time_str):
    """Account-owner-facing notification: 'a sign-in happened from an IP we
    haven't seen before for this account.' All interpolated values are
    escaped -- this reaches the same HTML-email sink that needed retrofitting
    for missing escaping elsewhere in this codebase, so it's done correctly
    here from the start rather than as a later fix."""
    _name = _html.escape(str(display_name))
    _id = _html.escape(str(identifier))
    _ip = _html.escape(str(ip_address))
    _time_s = _html.escape(str(login_time_str))
    return f"""
<div style="font-family:Segoe UI,sans-serif;max-width:540px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #fde68a;">
  <div style="background:#92400e;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">🔐 New Sign-In Detected</div>
    <div style="font-size:13px;opacity:0.8;margin-top:4px;">HRzest.com</div>
  </div>
  <div style="padding:28px;">
    <p style="color:#334155;font-size:14px;">Hi <strong>{_name}</strong>,</p>
    <p style="color:#475569;font-size:14px;">We noticed a sign-in to your account (<strong>{_id}</strong>)
      from an IP address we haven't seen on this account before:</p>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin:16px 0;">
      <tr style="background:#f1f5f9;"><td style="padding:10px 14px;color:#555;font-weight:600;width:120px;">IP Address</td>
          <td style="padding:10px 14px;">{_ip}</td></tr>
      <tr><td style="padding:10px 14px;color:#555;font-weight:600;">Time</td><td style="padding:10px 14px;">{_time_s}</td></tr>
    </table>
    <div style="background:#fef3c7;border-left:4px solid #d97706;border-radius:8px;padding:16px 18px;margin:20px 0;">
      <p style="margin:0;font-size:13px;color:#92400e;font-weight:700;">Was this you?</p>
      <p style="margin:8px 0 0;font-size:13px;color:#78350f;">If yes -- no action needed, you can ignore this email.</p>
      <p style="margin:10px 0 0;font-size:13px;color:#78350f;font-weight:700;">If this wasn't you, please do the following now:</p>
      <ol style="margin:6px 0 0;padding-left:18px;font-size:13px;color:#78350f;">
        <li>Change your password immediately.</li>
        <li>Contact your administrator so they can review your account.</li>
        <li>Check your recent attendance/activity for anything unfamiliar.</li>
      </ol>
    </div>
    <p style="font-size:12px;color:#94a3b8;margin-top:20px;">This is an automated security notification -- replies aren't monitored.</p>
  </div>
</div>"""


def notify_if_new_login_ip(identifier, attempt_type, ip_address, display_name, to_email):
    """Record ip_address for this account; queue a 'new sign-in' security
    email to the account owner only if it's genuinely new against an
    already-established history.

    Deliberately does NOT alert on an account's very first-ever recorded
    login -- there's no baseline yet to compare against, so treating IP #1
    as "new" would email every user on every fresh account's first login.
    Only IP #2-and-onward, the first time each is seen, triggers a mail.

    Best-effort: any failure here is logged and swallowed, never raised --
    this must not be able to block a login that already succeeded.
    """
    if not ip_address or not to_email:
        return
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM known_login_ips WHERE identifier=%s AND attempt_type=%s",
            (identifier, attempt_type)
        )
        existing_count = cur.fetchone()[0]
        cur.execute(
            "SELECT 1 FROM known_login_ips WHERE identifier=%s AND attempt_type=%s AND ip_address=%s",
            (identifier, attempt_type, ip_address)
        )
        already_known = cur.fetchone() is not None
        if not already_known:
            cur.execute(
                "INSERT INTO known_login_ips (identifier, attempt_type, ip_address) VALUES (%s,%s,%s) "
                "ON CONFLICT (identifier, attempt_type, ip_address) DO NOTHING",
                (identifier, attempt_type, ip_address)
            )
            db.commit()
        cur.close()
        db.close()
    except Exception as e:
        app_log.error("notify_if_new_login_ip: known_login_ips check failed for %s: %s", identifier, e)
        return

    is_first_ever_login = (existing_count == 0)
    if already_known or is_first_ever_login:
        return

    try:
        cfg = get_email_config()
        if not cfg:
            return
        login_time_str = _dt.datetime.now().strftime("%d %b %Y, %I:%M %p")
        html_body = build_new_ip_login_email(display_name, identifier, ip_address, login_time_str)
        send_email_async(to_email, "New Sign-In to Your Account", html_body, cfg)
    except Exception as e:
        app_log.error("notify_if_new_login_ip: failed to queue email for %s: %s", identifier, e)
