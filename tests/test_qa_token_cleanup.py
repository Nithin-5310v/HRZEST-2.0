"""PERF-01: expired API tokens are cleaned up periodically, not on every request.

Every Bearer-token API request ran DELETE FROM api_tokens WHERE
expires_at < NOW() and committed -- a write, and a full scan since
expires_at had no index, on every mobile API call. Expired tokens are
already rejected by each lookup's own expires_at > NOW() check, so the
delete is only housekeeping.
"""
import database


def _count_token_purges(monkeypatch):
    log = []
    real_cursor = database._PooledConnection.cursor

    class _Counting:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, params=None):
            log.append(sql)
            return self._cur.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._cur, name)

    monkeypatch.setattr(database._PooledConnection, "cursor", lambda self, *a, **k: _Counting(real_cursor(self, *a, **k)))
    return lambda: sum(1 for sql in log if "DELETE FROM api_tokens WHERE expires_at" in sql)


def test_repeated_api_calls_purge_expired_tokens_at_most_once(client, seed_admin, monkeypatch):
    token = client.post("/api/login", json={"username": seed_admin["username"],
                                            "password": seed_admin["password"]}).get_json()["token"]
    purges = _count_token_purges(monkeypatch)
    for _ in range(5):
        assert client.get("/api/employees", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert purges() <= 1


def test_expired_tokens_are_still_rejected(client, seed_admin, db_engine):
    import hashlib
    token = client.post("/api/login", json={"username": seed_admin["username"],
                                            "password": seed_admin["password"]}).get_json()["token"]
    cur = db_engine.cursor()
    cur.execute("UPDATE api_tokens SET expires_at = NOW() - INTERVAL '1 hour' WHERE token=%s",
                (hashlib.sha256(token.encode()).hexdigest(),))
    cur.close()
    assert client.get("/api/employees", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_expiry_column_is_indexed(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='api_tokens'")
    assert any("(expires_at)" in r[0] for r in cur.fetchall())
    cur.close()
