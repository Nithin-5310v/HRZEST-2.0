# -*- coding: utf-8 -*-
"""The session cookie carries the __Host- prefix, so no other *.hrzest.com
subdomain can plant (toss) a session cookie that this host would accept."""


def test_session_cookie_is_host_prefixed_and_meets_its_rules(client, monkeypatch):
    from extensions import app as flask_app
    # conftest turns Secure off for the plain-http test client; production
    # (extensions.py) always sets it, and __Host- requires it.
    monkeypatch.setitem(flask_app.config, "SESSION_COOKIE_SECURE", True)
    with client.session_transaction() as sess:
        sess["probe"] = 1
    resp = client.get("/")
    cookies = [h for h in resp.headers.getlist("Set-Cookie") if h.startswith("__Host-session=")]
    assert cookies, resp.headers.getlist("Set-Cookie")
    attrs = cookies[0].lower()
    # Browsers reject a __Host- cookie unless all three hold.
    assert "; secure" in attrs
    assert "; path=/" in attrs
    assert "domain=" not in attrs


def test_old_unprefixed_cookie_is_ignored(client):
    client.set_cookie("session", "forged-value", domain="localhost")
    resp = client.get("/")
    assert resp.status_code == 200
