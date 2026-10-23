"""SEC-14: request data passed straight into redirect() (Semgrep
python.flask.security.open-redirect, blocking in ci-cd.yml).

Every flagged redirect went to a fixed path on this site with a form value
inside it (a year, a template or application id, an employee id), or
through the _safe_redirect/_safe_referrer_redirect helpers, so none sent a
user off-site. But the values themselves were never checked: a year of
"x" or an employee id of "../a" was put into the URL as sent. Each value
is now validated before use (int_param, emp_id_param, the helpers' result
held in a variable, or the application id from the database row).
"""
import ast
import pathlib

import pytest

from tests.test_admin_views_routes import _admin_session

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCANNED = sorted([ROOT / "app.py", *(ROOT / "blueprints").glob("*.py")])


def _rooted_at_request(node):
    """request.X, request.X.get(...), request.X[...], request.X(...)"""
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return isinstance(node, ast.Name) and node.id == "request"


def _is_direct_request_value(expr):
    """The exact shapes the Semgrep rule treats as unvalidated."""
    if isinstance(expr, ast.Call):           # request.X.get(...) / request.X(...)
        f = expr.func
        return (isinstance(f, ast.Attribute) and isinstance(f.value, (ast.Attribute, ast.Name))
                and _rooted_at_request(f.value))
    if isinstance(expr, (ast.Attribute, ast.Subscript)):
        return _rooted_at_request(expr)
    return False


def _is_request_path(expr):
    return isinstance(expr, ast.Attribute) and expr.attr == "path" and _rooted_at_request(expr)


def _violations():
    found = []
    for path in SCANNED:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route" for d in fn.decorator_list):
                continue
            tainted = {t.id for node in ast.walk(fn) if isinstance(node, ast.Assign)
                       and _is_direct_request_value(node.value)
                       for t in node.targets if isinstance(t, ast.Name)}
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "redirect" and call.args):
                    continue
                arg = call.args[0]
                if _is_request_path(arg):
                    continue
                for sub in ast.walk(arg):
                    if (isinstance(sub, ast.Name) and sub.id in tainted) or (
                            isinstance(sub, (ast.Attribute, ast.Subscript)) and _rooted_at_request(sub)
                            and not _is_request_path(sub)):
                        found.append(f"{path.relative_to(ROOT)}:{call.lineno} {fn.name}")
                        break
    return sorted(set(found))


def test_no_route_redirects_with_an_unvalidated_request_value():
    assert _violations() == []


# ── behaviour of the fixed routes ────────────────────────────────────────────
def test_clear_company_never_leaves_the_site(client, seed_admin):
    _admin_session(client, seed_admin["username"])
    resp = client.post("/clear_company", data={"next": "https://evil.example/x"})
    assert resp.status_code == 302 and "evil.example" not in resp.headers["Location"]


@pytest.mark.parametrize("year", ["x", "2026x"])
def test_delete_holiday_refuses_a_non_numeric_year(client, seed_admin, year):
    _admin_session(client, seed_admin["username"])
    resp = client.post("/delete_holiday/999999", data={"year": year})
    assert resp.status_code == 400


def test_onboarding_task_save_refuses_a_non_numeric_template(client, seed_admin):
    _admin_session(client, seed_admin["username"])
    resp = client.post("/onboarding_task_save", data={"template_id": "abc", "task_title": ""})
    assert resp.status_code == 400


def test_performance_review_refuses_a_malformed_employee_id(client, seed_admin):
    _admin_session(client, seed_admin["username"])
    resp = client.post("/performance_save_review", data={"employee_id": "bad.id", "quarter": "1", "year": "2026"})
    assert resp.status_code == 400
