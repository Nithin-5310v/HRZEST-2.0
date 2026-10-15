"""ERR-01: API errors are JSON, pages stay HTML.

403/404/405/500 always answered with the HTML error page, even for /api/*
and AJAX requests (only 429 and 503 had a JSON branch), so the mobile app
and the pages' fetch() calls couldn't read the error and showed a generic
"something went wrong".
"""
import pytest

import blueprints.core as core
from extensions import app as flask_app


def _json_error(resp, status):
    assert resp.status_code == status
    assert resp.mimetype == "application/json", resp.mimetype
    body = resp.get_json()
    assert body["ok"] is False and body["msg"]
    return body


def test_unknown_api_route_is_a_json_404(client):
    _json_error(client.get("/api/no-such-endpoint"), 404)


def test_wrong_method_on_an_api_route_is_a_json_405(client):
    _json_error(client.delete("/api/session/risk-stream"), 405)


def test_ajax_request_to_a_page_gets_json(client):
    _json_error(client.get("/no-such-page", headers={"X-Requested-With": "XMLHttpRequest"}), 404)


def test_browser_page_still_gets_the_html_error_page(client):
    resp = client.get("/no-such-page")
    assert resp.status_code == 404 and resp.mimetype == "text/html"
    assert b"Page Not Found" in resp.data


@pytest.fixture
def handled_exceptions():
    previous = flask_app.config.get("PROPAGATE_EXCEPTIONS")
    flask_app.config["PROPAGATE_EXCEPTIONS"] = False   # let the 500 handler run, as in production
    yield
    flask_app.config["PROPAGATE_EXCEPTIONS"] = previous


def test_server_error_in_an_api_route_is_a_json_500_without_details(client, monkeypatch, handled_exceptions):
    def _boom(sid):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(core, "is_session_compromised", _boom)
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["_sid"] = "qa-err-sid"
    body = _json_error(client.get("/api/session/risk-stream"), 500)
    assert "secret internal detail" not in str(body)
