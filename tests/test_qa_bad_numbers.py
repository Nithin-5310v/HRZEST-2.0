"""BUG-08..16: a malformed number in a request is a 400, not a server error.

80 int() calls on request values (?year=abc, page=x, form fields) sat
outside any try, so bad input raised ValueError: a 500 page, a logged
traceback and an error-alert webhook. utils.helpers.int_param() answers
400 Bad Request instead (JSON for API calls, ERR-01).
"""
import pytest

from extensions import app as flask_app


@pytest.fixture
def handled_errors():
    """Let error handlers run, as in production (TESTING propagates exceptions)."""
    previous = flask_app.config.get("PROPAGATE_EXCEPTIONS")
    flask_app.config["PROPAGATE_EXCEPTIONS"] = False
    yield
    flask_app.config["PROPAGATE_EXCEPTIONS"] = previous


@pytest.fixture
def admin(client, seed_admin, handled_errors):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    return client


def _admin_token(client, seed_admin):
    creds = {"username": seed_admin["username"], "password": seed_admin["password"]}
    return client.post("/api/login", json=creds).get_json()["token"]


# ── BUG-08 payroll ───────────────────────────────────────────────────────────

def test_salary_report_with_a_bad_year_is_a_400(admin):
    assert admin.get("/salary_report?year=abc").status_code == 400


# ── BUG-09 performance ───────────────────────────────────────────────────────

def test_performance_with_a_bad_quarter_is_a_400(admin):
    assert admin.get("/performance?quarter=x").status_code == 400


# ── BUG-10 leave ─────────────────────────────────────────────────────────────

def test_leave_balance_with_a_bad_year_is_a_400(admin):
    assert admin.get("/leave_balance?year=abc").status_code == 400
