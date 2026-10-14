"""CONN-01: early returns don't leave a pooled connection checked out.

Two onboarding save routes borrowed a connection, then returned early on a
validation error without closing it. The pool only got it back through
_PooledConnection.__del__ -- a backstop that depends on garbage-collection
timing. The test turns that backstop off and checks the pool.
"""
import pytest

import database


@pytest.fixture
def no_gc_backstop(monkeypatch):
    monkeypatch.setattr(database._PooledConnection, "__del__", lambda self: None)


@pytest.fixture
def admin(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    return client


def _checked_out():
    return len(getattr(database._pool, "_used", {}))


@pytest.mark.parametrize("path, form", [
    ("/onboarding_template_save", {"name": ""}),
    ("/onboarding_task_save", {"template_id": "1", "task_title": ""}),
])
def test_validation_errors_return_the_connection(admin, no_gc_backstop, path, form):
    admin.get("/login")                                  # warm the pool / session
    before = _checked_out()
    resp = admin.post(path, data=form)
    assert resp.status_code == 302
    assert _checked_out() == before
