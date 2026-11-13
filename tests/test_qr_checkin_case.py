"""QR check-in failed with "Invalid or unrecognized QR code" for employees
whose ID has lower-case letters (e.g. "Sales-007").

The badge payload is "<employee_id>.<hmac>", signed over the ID exactly as
stored. The website kiosk (templates/index.html) upper-cased the whole
scanned text before sending it, so "Sales-007.<sig>" arrived as
"SALES-007.<SIG>": the signature part is compared case-insensitively, but
the ID part no longer matched what was signed.

Fixed twice over: the kiosk sends a signed payload exactly as scanned, and
the server, when a signature fails, retries with the employee's ID as
stored (matched case-insensitively, only when exactly one employee
matches). Rejections are logged with a reason.
"""
import logging
import pathlib
import re
import secrets

from tests.test_auth_hardening import _make_tenant

ROOT = pathlib.Path(__file__).resolve().parents[1]
QR_ERR = "Invalid or unrecognized QR code"


def _add_employee(db_engine, schema, emp_id):
    cur = db_engine.cursor()
    cur.execute(f'INSERT INTO "{schema}".employees (employee_id, name, password) VALUES (%s,%s,%s)',  # nosec B608
                (emp_id, "QR Person", "x"))
    cur.close()


def _badge(flask_app, schema, emp_id):
    """The payload My QR / ID cards encode for this employee in this company."""
    from flask import g
    from qr_generator import _qr_signature
    with flask_app.test_request_context():
        g.tenant_db = schema
        return f"{emp_id}.{_qr_signature(emp_id)}"


def _kiosk_post(flask_app, slug, payload):
    kiosk = flask_app.test_client()
    kiosk.get(f"/{slug}/")                       # the kiosk page is opened at /<company>/
    r = kiosk.post("/attendance", json={"employee_id": payload, "auth_combo": "qr_face"})
    return (r.get_json() or {}).get("msg", "")


def test_uppercased_badge_of_mixed_case_employee_is_accepted(client, db_engine):
    from extensions import app as flask_app
    slug, schema = _make_tenant(db_engine, f"qrcase{secrets.token_hex(3)}")
    _add_employee(db_engine, schema, "Sales-007")
    badge = _badge(flask_app, schema, "Sales-007")
    assert QR_ERR not in _kiosk_post(flask_app, slug, badge)
    # What the old kiosk page sent -- rejected before this fix.
    assert QR_ERR not in _kiosk_post(flask_app, slug, badge.upper())


def test_verify_returns_the_stored_spelling(client, db_engine):
    from flask import g
    from extensions import app as flask_app
    from qr_generator import verify_qr_value
    slug, schema = _make_tenant(db_engine, f"qrcase{secrets.token_hex(3)}")
    _add_employee(db_engine, schema, "Sales-007")
    badge = _badge(flask_app, schema, "Sales-007")
    with flask_app.test_request_context():
        g.tenant_db = schema
        assert verify_qr_value(badge.upper()) == ("Sales-007", True)


def test_case_rescue_never_accepts_another_employees_signature(client, db_engine, caplog):
    from flask import g
    from extensions import app as flask_app
    from qr_generator import verify_qr_value
    slug, schema = _make_tenant(db_engine, f"qrcase{secrets.token_hex(3)}")
    _add_employee(db_engine, schema, "Sales-007")
    _add_employee(db_engine, schema, "Sales-008")
    sig_007 = _badge(flask_app, schema, "Sales-007").rpartition(".")[2]
    with flask_app.test_request_context(), caplog.at_level(logging.WARNING, logger="attendance"):
        g.tenant_db = schema
        assert verify_qr_value(f"SALES-008.{sig_007}") == (None, False)
    assert "attendance.qr_rejected: reason=bad_signature" in caplog.text


def test_badge_from_another_company_is_still_rejected(client, db_engine):
    from extensions import app as flask_app
    slug_a, schema_a = _make_tenant(db_engine, f"qra{secrets.token_hex(3)}")
    slug_b, schema_b = _make_tenant(db_engine, f"qrb{secrets.token_hex(3)}")
    _add_employee(db_engine, schema_a, "Sales-007")
    _add_employee(db_engine, schema_b, "Sales-007")
    badge_from_a = _badge(flask_app, schema_a, "Sales-007")
    assert QR_ERR in _kiosk_post(flask_app, slug_b, badge_from_a)
    assert QR_ERR in _kiosk_post(flask_app, slug_b, badge_from_a.upper())


def test_no_rescue_when_the_case_insensitive_match_is_ambiguous(client, db_engine):
    from flask import g
    from extensions import app as flask_app
    from qr_generator import verify_qr_value
    slug, schema = _make_tenant(db_engine, f"qrcase{secrets.token_hex(3)}")
    _add_employee(db_engine, schema, "abc1")
    _add_employee(db_engine, schema, "ABC1")
    badge = _badge(flask_app, schema, "abc1")
    with flask_app.test_request_context():
        g.tenant_db = schema
        assert verify_qr_value(badge) == ("abc1", True)          # exact spelling still works
        assert verify_qr_value(badge.upper()) == (None, False)   # ambiguous: no guess


def test_malformed_scan_is_logged(caplog):
    from extensions import app as flask_app
    from qr_generator import verify_qr_value
    with flask_app.test_request_context(), caplog.at_level(logging.WARNING, logger="attendance"):
        assert verify_qr_value("not-a-badge") == (None, False)
    assert "attendance.qr_rejected: reason=malformed" in caplog.text


def test_kiosk_page_sends_signed_badges_unchanged():
    src = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    step = src[src.index("async function doQRStep()"):]
    step = step[:step.index("\n      }\n")]
    assert "gEmployeeId = empId.trim().toUpperCase()" not in step
    assert re.search(r"\[0-9a-f\]\{16\}", step), "kiosk must recognise a signed badge and keep it verbatim"
