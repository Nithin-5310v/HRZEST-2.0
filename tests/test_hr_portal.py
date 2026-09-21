"""Tests for the 'hr' admin_users role's page-level RBAC scoping.

The standalone HR Portal (its own /hr_login page and /hr dashboard,
blueprints/hr_portal.py) was intentionally removed from the codebase (git
log: "chore: remove HR portal, SuperAdmin, SecOps, Compliance modules &
cleanup duplicates") and, unlike SuperAdmin/SecOps, was never reintroduced.
HR accounts are back, though, via a different path: blueprints/admin_views.py's
/hr_accounts page lets an admin create/terminate/reactivate role='hr' rows
in admin_users, and they log in through the same general /login as admin
(see tests/test_login_mfa.py::TestAdminLoginMfa::test_hr_role_can_use_admin_login_and_completes_mfa),
landing on /employees instead of /admin.

What's tested here: the generic role_required("admin")/admin_required RBAC
machinery correctly scopes an hr-role session (stamped directly via
session_transaction, the same way test_secops.py exercises the soc_analyst
role) away from admin-only surfaces and into the shared employee-lifecycle
ones -- this is what actually keeps an HR account from reaching payroll,
company settings, etc. once logged in.
"""
import pytest


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def hr_admin(seed_admin, db_engine):
    """A seeded admin promoted to the hr role -- same pattern as
    tests/test_secops.py's soc_admin fixture."""
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET role='hr' WHERE username=%s", (seed_admin["username"],))
    db_engine.commit()
    cur.close()
    yield seed_admin
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
    db_engine.commit()
    cur.close()


class TestHrScopeRestrictions:
    """An hr-role session reuses the same admin_logged_in session shape as a
    regular admin (so it can reach the shared employee-lifecycle routes),
    which means the tenant/system-settings routes must explicitly reject it
    via role_required("admin") rather than relying on admin_required alone."""

    @pytest.mark.parametrize("path", [
        "/settings", "/companies", "/analytics", "/admin_tools",
    ])
    def test_hr_role_blocked_from_admin_only_pages(self, client, hr_admin, path):
        _admin_session(client, hr_admin["username"], role="hr")
        resp = client.get(path)
        assert resp.status_code == 403

    @pytest.mark.parametrize("path", [
        "/employees", "/monthly_report", "/leave_holidays", "/overtime",
        "/performance", "/onboarding",
    ])
    def test_hr_role_reaches_shared_employee_lifecycle_pages(self, client, hr_admin, path):
        _admin_session(client, hr_admin["username"], role="hr")
        # /tickets is now a tab inside /leave_holidays, reached via a redirect
        resp = client.get(path, follow_redirects=(path == "/tickets"))
        assert resp.status_code == 200

    def test_hr_role_reaches_tickets_via_redirect(self, client, hr_admin):
        # /tickets redirects to the Leaves & Holidays page's Tickets tab now
        # (blueprints/tickets.py's tickets_view()) instead of rendering
        # inline -- HR still isn't blocked from it (302, not 403).
        _admin_session(client, hr_admin["username"], role="hr")
        resp = client.get("/tickets", follow_redirects=False)
        assert resp.status_code == 302
        assert "/leave_holidays" in resp.headers["Location"]

    def test_hr_role_blocked_from_payroll(self, client, hr_admin):
        _admin_session(client, hr_admin["username"], role="hr")
        resp = client.get("/salary_report")
        assert resp.status_code == 403
