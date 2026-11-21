"""Security audit P3 (2026-11-20), option B: a self-serve company works in
restricted mode -- no payouts, no bulk email -- until its KYC documents are
approved; and uploading documents no longer approves them on the spot (which
made the automated duplicate GST/PAN/CIN check skip every application).
"""
import pytest

from extensions import app as flask_app
from utils import kyc_gate


@pytest.fixture
def kyc_status(monkeypatch):
    """Set what the master database says about the current company."""
    state = {"status": "pending_review"}
    monkeypatch.setenv("KYC_LIMITED_MODE", "1")
    monkeypatch.setattr(kyc_gate, "_kyc_status_for", lambda _db: state["status"])
    return state


@pytest.mark.parametrize("status, allowed", [
    (None, True),                 # no self-serve application: older / platform-created company
    ("approved", True),
    ("pending_review", False),
    ("not_submitted", False),
    ("rejected", False),
])
def test_company_kyc_verified(kyc_status, status, allowed):
    kyc_status["status"] = status
    with flask_app.test_request_context("/"):
        flask_app.preprocess_request()
        assert kyc_gate.company_kyc_verified() is allowed


def test_status_lookup_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("KYC_LIMITED_MODE", "1")

    def boom(_db):
        raise RuntimeError("master db down")

    monkeypatch.setattr(kyc_gate, "_kyc_status_for", boom)
    with flask_app.test_request_context("/"):
        flask_app.preprocess_request()
        assert kyc_gate.company_kyc_verified() is False


def test_switch_turns_the_restriction_off(kyc_status, monkeypatch):
    monkeypatch.setenv("KYC_LIMITED_MODE", "0")
    with flask_app.test_request_context("/"):
        flask_app.preprocess_request()
        assert kyc_gate.company_kyc_verified() is True


def _admin_session(client, seed_admin):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})


def test_email_blast_closed_until_kyc_approved(client, seed_admin, kyc_status):
    _admin_session(client, seed_admin)
    resp = client.post("/api/admin/email-blast", json={"target_type": "all", "subject": "s", "body": "b"})
    assert resp.status_code == 403
    assert resp.get_json()["kyc_pending"] is True


def test_email_blast_open_once_approved(client, seed_admin, kyc_status):
    kyc_status["status"] = "approved"
    _admin_session(client, seed_admin)
    resp = client.post("/api/admin/email-blast", json={"target_type": "all", "subject": "s", "body": "b"})
    assert resp.status_code != 403


@pytest.mark.parametrize("endpoint", [
    "disbursement.api_save_payout_bank_config",
    "disbursement.approve_disbursement_run",
    "email_blast.api_email_blast",
])
def test_money_and_mass_mail_routes_carry_the_gate(endpoint):
    view, found = flask_app.view_functions[endpoint], False
    while view is not None and not found:
        found = bool(getattr(view, "_hrz_kyc_gate", False))
        view = getattr(view, "__wrapped__", None)
    assert found


def test_document_upload_leaves_review_pending():
    """The upload helper must not approve; the automated worker only acts on
    'pending_review' and skipped every auto-approved application."""
    import ast
    import inspect
    import textwrap
    from blueprints.org import applications
    tree = ast.parse(textwrap.dedent(inspect.getsource(applications._save_application_documents)))
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "_approve_application_reviewed" not in calls
