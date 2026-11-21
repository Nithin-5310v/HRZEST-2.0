"""Server-side data cache for each module's GET JSON routes (utils/data_cache.py).

The suite runs without Redis, so the cache is off by default; these tests turn
it on with an in-memory stand-in for the Redis client.
"""
import ast
import json
import pathlib

import pytest
from flask import jsonify, session

import extensions
from extensions import app as flask_app
from utils import data_cache

BLUEPRINTS = pathlib.Path(__file__).resolve().parent.parent / "blueprints"


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, _ttl, value):
        self.store[key] = value

    def incr(self, key):
        self.store[key] = str(int(self.store.get(key) or 0) + 1)


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(extensions, "redis_client", r)
    monkeypatch.setenv("DATA_CACHE_ENABLED", "1")
    return r


def _counting_view(payload=None, status=200):
    calls = []

    @data_cache.data_cache("test")
    def view():
        calls.append(1)
        return jsonify(payload or {"ok": True, "salary": 123456}), status
    return view, calls


def _get(view, path="/api/x?page=1", user="alice"):
    with flask_app.test_request_context(path):
        session["admin_logged_in"] = True
        session["admin_username"] = user
        session["admin_role"] = "admin"
        return view()


def test_repeat_request_is_served_from_the_cache(fake_redis):
    view, calls = _counting_view()
    first = _get(view)
    second = _get(view)
    assert len(calls) == 1
    assert json.loads(second.get_data()) == json.loads(flask_app.make_response(first).get_data())


def test_entries_are_encrypted_at_rest(fake_redis):
    view, _calls = _counting_view()
    _get(view)
    stored = [v for k, v in fake_redis.store.items() if k.startswith("dcache:v1")]
    assert stored and all("123456" not in str(v) and "salary" not in str(v) for v in stored)


def test_people_never_share_an_entry(fake_redis):
    view, calls = _counting_view()
    _get(view, user="alice")
    _get(view, user="bob")
    assert len(calls) == 2


def test_query_string_is_part_of_the_key(fake_redis):
    view, calls = _counting_view()
    _get(view, path="/api/x?page=1")
    _get(view, path="/api/x?page=2")
    assert len(calls) == 2


def test_a_write_retires_the_company_cache(fake_redis):
    view, calls = _counting_view()
    _get(view)
    with flask_app.test_request_context("/api/anything", method="POST"):
        data_cache._invalidate_after_write(flask_app.make_response(("", 200)))
    _get(view)
    assert len(calls) == 2


def test_failed_writes_do_not_invalidate(fake_redis):
    view, calls = _counting_view()
    _get(view)
    with flask_app.test_request_context("/api/anything", method="POST"):
        data_cache._invalidate_after_write(flask_app.make_response(("", 403)))
    _get(view)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [201, 400, 403, 404, 500])
def test_only_200_responses_are_stored(fake_redis, status):
    view, calls = _counting_view(status=status)
    _get(view)
    _get(view)
    assert len(calls) == 2


def test_cache_is_off_without_redis(monkeypatch):
    monkeypatch.setattr(extensions, "redis_client", None)
    view, calls = _counting_view()
    _get(view)
    _get(view)
    assert len(calls) == 2


def test_switch_turns_the_cache_off(fake_redis, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_ENABLED", "0")
    view, calls = _counting_view()
    _get(view)
    _get(view)
    assert len(calls) == 2


def test_a_corrupt_entry_is_a_miss(fake_redis):
    view, calls = _counting_view()
    _get(view)
    for k in list(fake_redis.store):
        if k.startswith("dcache:v1"):
            fake_redis.store[k] = "not-ciphertext"
    _get(view)
    assert len(calls) == 2


# ── Static checks over every blueprint ─────────────────────────────────────────

GUARDS = {"admin_required", "employee_required", "any_user_required", "role_required", "require_access",
          "api_required", "api_role_required", "api_require_access", "employee_api_required",
          "session_or_bearer_required", "_platform_admin_required", "_admin_session_or_api_role_required"}
UNSAFE = ("commit(", "INSERT ", "UPDATE ", "DELETE ", "audit_read", "_audit(", "audit_changes",
          "send_file", "send_from_directory", "log_security_event(", "session[")


def _deco_name(d):
    node = d.func if isinstance(d, ast.Call) else d
    return getattr(node, "attr", None) or getattr(node, "id", "")


def _cached_views():
    for path in sorted(BLUEPRINTS.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        for fn in ast.walk(ast.parse(src)):
            if isinstance(fn, ast.FunctionDef) and any(_deco_name(d) == "data_cache" for d in fn.decorator_list):
                yield path, fn, src


def test_every_module_has_cached_routes():
    modules = {p.relative_to(BLUEPRINTS).parts[0].replace(".py", "") for p, _f, _s in _cached_views()}
    assert len(modules) >= 15, modules


def test_cache_sits_under_the_auth_guards():
    """data_cache must be the innermost decorator with an auth guard above it,
    so login/role/kill-switch checks run before the cache is consulted."""
    bad = []
    for path, fn, _src in _cached_views():
        names = [_deco_name(d) for d in fn.decorator_list]
        if names[-1] != "data_cache" or not set(names[:-1]) & GUARDS:
            bad.append(f"{path.name}::{fn.name}")
    assert not bad, bad


def test_cached_routes_have_no_side_effects():
    """A cached hit skips the view body: no writes, audit entries or files there."""
    bad = []
    for path, fn, src in _cached_views():
        body = ast.get_source_segment(src, fn) or ""
        hits = [m for m in UNSAFE if m in body]
        if hits:
            bad.append(f"{path.name}::{fn.name}: {hits}")
    assert not bad, bad


# ── End to end on a real route ───────────────────────────────────────────────

def test_real_route_hit_and_invalidation(client, seed_admin, db_engine, fake_redis):
    token = client.post("/api/login", json={"username": seed_admin["username"],
                                            "password": seed_admin["password"]}).get_json().get("token")
    if not token:
        pytest.skip("admin API login needs MFA here")
    hdr = {"Authorization": f"Bearer {token}"}
    first = client.get("/api/holidays", headers=hdr).get_json()
    cur = db_engine.cursor()
    cur.execute("INSERT INTO holidays (date, name) VALUES ('2031-01-01', 'Cache Test Day')")
    cur.close()
    db_engine.commit()
    # Written behind the app's back: the cached answer is still served.
    assert client.get("/api/holidays", headers=hdr).get_json() == first
    # Any successful write through the app retires the company's cache.
    client.post("/api/client-log", json={"type": "js_error", "message": "x"})
    names = [h["name"] for h in client.get("/api/holidays", headers=hdr).get_json()["holidays"]]
    assert "Cache Test Day" in names
