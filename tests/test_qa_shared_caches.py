"""SCALE-04: per-worker caches that matter go through Redis when it's configured.

The sidebar pending counts and the "has this admin enrolled MFA" flag were
cached in each gunicorn worker's own memory; an invalidation only cleared
the worker that handled it. After an admin's MFA was reset, another worker
could keep a cached "enabled" for up to 60 s and skip the re-enrollment
gate; badges disagreed between workers.

A dict-backed fake Redis stands in for the real one, and a second worker
is simulated by giving it its own copy of the in-process cache.
"""
import fnmatch

import pytest

import extensions
import utils.helpers as helpers
import utils.totp as totp
from extensions import app as flask_app


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value.encode() if isinstance(value, str) else value

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def scan_iter(self, match="*"):
        return [k for k in list(self.store) if fnmatch.fnmatch(k, match)]


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(helpers, "redis_client", fake)
    monkeypatch.setattr(extensions, "redis_client", fake)
    return fake


def test_pending_counts_are_shared_between_workers(redis, monkeypatch):
    monkeypatch.setattr(helpers, "get_pending_action_counts", lambda cursor, *a, **k: (3, 1, 2))
    with flask_app.test_request_context():
        assert helpers.get_pending_counts() == (3, 1, 2)          # worker A computes and caches
    helpers._pending_counts_cache.clear()                          # worker B: empty local memory

    def _no_query(*a, **k):
        raise AssertionError("worker B should read the shared cache")
    monkeypatch.setattr(helpers, "get_pending_action_counts", _no_query)
    with flask_app.test_request_context():
        assert helpers.get_pending_counts() == (3, 1, 2)


def test_invalidating_pending_counts_clears_the_shared_cache(redis, monkeypatch):
    monkeypatch.setattr(helpers, "get_pending_action_counts", lambda cursor, *a, **k: (3, 1, 2))
    with flask_app.test_request_context():
        helpers.get_pending_counts()
    helpers.invalidate_pending_counts_cache()
    assert not redis.scan_iter("settings_pending:*")


def test_an_mfa_reset_is_seen_by_every_worker(redis, db_engine, seed_admin):
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET totp_enabled=1 WHERE username=%s", (seed_admin["username"],))
    with flask_app.test_request_context():
        assert totp.is_totp_enabled_cached(seed_admin["username"]) is True       # both workers cache "enabled"
    worker_b_memory = dict(totp._totp_enabled_cache)

    cur.execute("UPDATE admin_users SET totp_enabled=0 WHERE username=%s", (seed_admin["username"],))
    cur.close()
    with flask_app.test_request_context():
        totp._invalidate_totp_cache(seed_admin["username"])                    # worker A handles the reset

    totp._totp_enabled_cache.clear()
    totp._totp_enabled_cache.update(worker_b_memory)                           # worker B still has its own copy
    with flask_app.test_request_context():
        assert totp.is_totp_enabled_cached(seed_admin["username"]) is False
