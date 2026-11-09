# -*- coding: utf-8 -*-
"""Support tickets a company raises with HRZEST (att_master.tenant_support_tickets):
the files attached to them, and the email to whoever raised one when
HRZEST replies or changes its status.

Used by both sides of a ticket: the company admin's Organization page
(blueprints/company_profile.py) and Platform Admin (blueprints/platform_admin/tickets.py).
"""
import os
import re
import uuid
from html import escape
from io import BytesIO

from flask import current_app, g, send_file
from werkzeug.utils import secure_filename

from database import get_db_connection, get_master_db
from extensions import app_log

# Screenshots, PDFs and office documents -- the same set employee documents
# allow, each checked against its real content by _validate_upload().
ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"}
MAX_FILES = 5
# Shown as a thumbnail and opened in the browser; anything else downloads.
_INLINE_IMAGE_TYPES = ("image/png", "image/jpeg")
ACCEPT_ATTR = ",".join("." + e for e in sorted(ALLOWED_EXT))

_SAFE_SCHEMA_RE = re.compile(r"^att_[a-z0-9_]+$")


def chosen_files(request):
    """The files picked in a form's "attachments" input (an empty picker
    sends one blank entry, which is dropped)."""
    return [f for f in request.files.getlist("attachments") if f and f.filename]


def check_files(files):
    """An error message if any file can't be attached, else None. Checked
    before the ticket or reply is saved, so nothing is half-saved."""
    from utils.helpers import _validate_upload
    if len(files) > MAX_FILES:
        return f"Attach up to {MAX_FILES} files at a time."
    for f in files:
        ok, err = _validate_upload(f, ALLOWED_EXT)
        if not ok:
            return f"{f.filename}: {err}"
    return None


def save_files(files, ticket_id, comment_id, uploaded_by, uploader_kind):
    """Saves already-checked files against a ticket (or one reply on it),
    encrypted at rest under private_uploads/. Returns how many were saved;
    a file that fails to save is logged and skipped."""
    from utils.storage import save_private
    saved = 0
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    try:
        for f in files:
            original = os.path.basename(f.filename)[:255]
            rel_path = f"support_tickets/{int(ticket_id)}/{uuid.uuid4().hex}_{secure_filename(original) or 'file'}"
            f.stream.seek(0, 2)
            size = f.stream.tell()
            f.stream.seek(0)
            try:
                stored_ref, err = save_private(current_app.root_path, f, rel_path)
            except Exception as exc:
                stored_ref, err = None, str(exc)
            if err or not stored_ref:
                app_log.error("support ticket %s: could not save attachment: %s", ticket_id, err)
                continue
            cur.execute(
                "INSERT INTO tenant_ticket_attachments (ticket_id, comment_id, original_name, stored_ref, "
                "content_type, size_bytes, uploaded_by, uploader_kind) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (ticket_id, comment_id, original, stored_ref, (f.mimetype or "")[:100], size,
                 uploaded_by, uploader_kind),
            )
            saved += 1
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return saved


def attachments_for(ticket_id):
    """{comment_id or None: [attachment, ...]} -- None holds the files sent
    with the ticket itself."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, comment_id, original_name, size_bytes, content_type, uploader_kind, created_at "
        "FROM tenant_ticket_attachments WHERE ticket_id=%s ORDER BY created_at, id",
        (ticket_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    grouped = {}
    for r in rows:
        grouped.setdefault(r[1], []).append({
            "id": r[0], "name": r[2], "size": _human_size(r[3]),
            "is_image": r[4] in _INLINE_IMAGE_TYPES, "uploader_kind": r[5], "created_at": r[6],
        })
    return grouped


def send_attachment(ticket_id, attachment_id, inline=False):
    """The file, if it belongs to this ticket, else None. Images may be shown
    inline (for a preview); everything else downloads."""
    from utils.storage import open_private
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT original_name, stored_ref, content_type FROM tenant_ticket_attachments "
                "WHERE id=%s AND ticket_id=%s", (attachment_id, ticket_id))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    name, stored_ref, content_type = row
    try:
        data = open_private(stored_ref)
    except Exception as exc:
        app_log.error("support ticket %s: attachment %s unreadable: %s", ticket_id, attachment_id, exc)
        return None
    is_image = content_type in _INLINE_IMAGE_TYPES
    resp = send_file(BytesIO(data), mimetype=content_type if is_image else "application/octet-stream",
                     as_attachment=not (inline and is_image), download_name=name)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def _human_size(n):
    if not n:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


# ── Email to whoever raised the ticket ──────────────────────────────────────

def _raiser(ticket_id):
    """(email, company slug, subject, name) for the person who raised the
    ticket -- their own admin account's email in the company, else the
    company's registered admin email -- or None."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    try:
        cur.execute(
            "SELECT tk.subject, tk.created_by, t.db_name, t.subdomain, t.admin_email, t.company_name "
            "FROM tenant_support_tickets tk JOIN tenants t ON t.id = tk.tenant_id WHERE tk.id=%s",
            (ticket_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        subject, created_by, db_name, slug, admin_email, company = row
    finally:
        cur.close()
        conn.close()
    email = None
    if created_by and db_name and _SAFE_SCHEMA_RE.match(db_name):
        prev = getattr(g, "tenant_db", None)
        g.tenant_db = db_name
        try:
            db = get_db_connection()
            tcur = db.cursor()
            tcur.execute("SELECT email FROM admin_users WHERE username=%s", (created_by,))
            r = tcur.fetchone()
            tcur.close()
            db.close()
            email = (r[0] or "").strip() if r else None
        except Exception as exc:
            app_log.warning("support ticket %s: raiser lookup failed: %s", ticket_id, exc)
        finally:
            g.tenant_db = prev
    return (email or (admin_email or "").strip() or None), slug, subject, company


def email_raiser(ticket_id, heading, message_html):
    """Emails whoever raised the ticket, from HRZEST's own mail settings.
    Never raises: a mail problem is only logged. True if it was queued."""
    from utils.billing_notices import company_link, email_shell
    from utils.email_utils import get_email_config, send_email_async
    try:
        raiser = _raiser(ticket_id)
        if not raiser or not raiser[0]:
            app_log.info("support ticket %s: nobody to email", ticket_id)
            return False
        to_email, slug, subject, _company = raiser
        link = company_link(slug, f"/profile/tickets/{int(ticket_id)}")
        body = (
            f"<p style=\"color:#64748b;margin:0 0 6px;\">Ticket #{int(ticket_id)} &middot; {escape(subject or '')}</p>"
            f"{message_html}"
            f'<p style="margin-top:20px;"><a href="{escape(link)}" style="background:#1d4ed8;color:#fff;'
            f'padding:10px 18px;border-radius:8px;text-decoration:none;">View the ticket</a></p>'
        )
        prev = getattr(g, "tenant_db", None)
        g.tenant_db = None  # HRZEST's own mail settings, not the company's
        try:
            config = get_email_config()
            if not config:
                app_log.warning("support ticket %s: no email configured -- not sent", ticket_id)
                return False
            send_email_async(to_email, f"{heading} — ticket #{int(ticket_id)}: {subject or ''}"[:200],
                             email_shell(body, "Support ticket update"), config)
        finally:
            g.tenant_db = prev
        return True
    except Exception as exc:
        app_log.error("support ticket %s: email failed: %s", ticket_id, exc)
        return False
