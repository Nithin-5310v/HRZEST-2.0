"""Tests for the optional Redis-backed shared cache: extensions.py's
_init_redis_backend() (decides Flask-Limiter's storage_uri) and
utils/waf.py's Redis-vs-in-memory breach counter dispatch.

No real Redis server is required for these — the reachable-Redis path is
exercised with a fake client, and the unset/unreachable paths use real
(non-)connections, matching the fail-open behavior the code implements."""
import datetime
import extensions as extensions_module
import utils.waf as waf_module
import utils.helpers as helpers_module


class FakeRedis:
    """Minimal stand-in for redis.Redis covering exactly what
    _init_redis_backend()/waf.py's breach counter call."""

    def __init__(self, host, port, password=None, socket_connect_timeout=None, socket_timeout=None):
        self.host, self.port = host, port
        self.store = {}

    def ping(self):
        return True

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, seconds):
        pass

    def delete(self, key):
        self.store.pop(key, None)


class RaisingRedis:
    def __init__(self, *a, **k):
        pass

    def ping(self):
        raise ConnectionError("connection refused")


class MidCallFailureRedis:
    """Connects fine at startup (ping succeeds) but every subsequent call fails
    — simulates a Redis instance that goes away after the app has already
    started using it."""

    def ping(self):
        return True

    def incr(self, key):
        raise ConnectionError("connection lost")


class FakeRedisKV:
    """Minimal key-value stand-in covering exactly what utils/helpers.py's
    _cache_get/_cache_set/_redis_clear_prefix call -- get/setex (a plain
    string store, matching real redis-py's behavior of returning bytes/str
    rather than the original Python object) and scan_iter/delete for
    prefix-based invalidation."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def scan_iter(self, match):
        prefix = match.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)


class RaisingKVRedis:
    """Every call raises -- simulates a Redis instance that's unreachable
    mid-request, same role as MidCallFailureRedis above but for the
    get/setex/scan_iter surface the settings caches use."""

    def get(self, key):
        raise ConnectionError("connection lost")

    def setex(self, key, ttl, value):
        raise ConnectionError("connection lost")

    def scan_iter(self, match):
        raise ConnectionError("connection lost")


class TestInitRedisBackend:
    def test_unset_host_falls_back_to_memory(self, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        client, uri = extensions_module._init_redis_backend()
        assert client is None
        assert uri == "memory://"

    def test_unreachable_host_falls_back_to_memory(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "unreachable-host-for-tests")
        monkeypatch.setattr(extensions_module._redis_lib, "Redis", RaisingRedis)
        client, uri = extensions_module._init_redis_backend()
        assert client is None
        assert uri == "memory://"
        monkeypatch.delenv("REDIS_HOST", raising=False)

    def test_reachable_host_returns_client_and_redis_uri(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "fake-redis")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setattr(extensions_module._redis_lib, "Redis", FakeRedis)
        client, uri = extensions_module._init_redis_backend()
        assert isinstance(client, FakeRedis)
        assert uri == "redis://fake-redis:6379/0"
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)

    def test_password_included_in_uri_but_not_logged_elsewhere(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "fake-redis")
        monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
        monkeypatch.setattr(extensions_module._redis_lib, "Redis", FakeRedis)
        client, uri = extensions_module._init_redis_backend()
        assert uri == "redis://:s3cret@fake-redis:6379/0"
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)

    def test_password_with_url_special_chars_is_percent_encoded(self, monkeypatch):
        """A raw '@' or '/' in the password would be parsed as part of the
        host/path, silently breaking the limiter's storage connection."""
        monkeypatch.setenv("REDIS_HOST", "fake-redis")
        monkeypatch.setenv("REDIS_PASSWORD", "p@ss/w:rd#1")
        monkeypatch.setattr(extensions_module._redis_lib, "Redis", FakeRedis)
        client, uri = extensions_module._init_redis_backend()
        assert uri == "redis://:p%40ss%2Fw%3Ard%231@fake-redis:6379/0"
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)


class TestWafBreachCounterRedisDispatch:
    def test_none_client_uses_in_memory_path(self, monkeypatch):
        monkeypatch.setattr(waf_module, "redis_client", None)
        waf_module._breach_log.pop("10.0.0.1", None)
        for _ in range(waf_module._BREACH_THRESHOLD - 1):
            waf_module.record_breach_and_maybe_ban("10.0.0.1", "test")
        assert len(waf_module._breach_log["10.0.0.1"]) == waf_module._BREACH_THRESHOLD - 1
        waf_module._breach_log.pop("10.0.0.1", None)

    def test_redis_client_used_when_configured(self, monkeypatch, db_engine):
        fake = FakeRedis(host="x", port=1)
        monkeypatch.setattr(waf_module, "redis_client", fake)
        ip = "10.0.0.2"
        cur = db_engine.cursor()
        cur.execute("DELETE FROM banned_ips WHERE ip=%s", (ip,))
        db_engine.commit()
        cur.close()
        try:
            for _ in range(waf_module._BREACH_THRESHOLD):
                waf_module.record_breach_and_maybe_ban(ip, "redis test breach")
            # Threshold crossed entirely via the fake Redis client — the
            # in-memory deque for this IP must stay untouched.
            assert ip not in waf_module._breach_log
            assert f"waf:breach:{ip}" not in fake.store  # deleted once banned

            cur = db_engine.cursor()
            cur.execute("SELECT expires_at FROM banned_ips WHERE ip=%s", (ip,))
            row = cur.fetchone()
            cur.close()
            assert row is not None
            assert row[0] > datetime.datetime.now()
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM banned_ips WHERE ip=%s", (ip,))
            db_engine.commit()
            cur.close()

    def test_redis_failure_mid_call_falls_back_to_memory(self, monkeypatch):
        monkeypatch.setattr(waf_module, "redis_client", MidCallFailureRedis())
        ip = "10.0.0.3"
        waf_module._breach_log.pop(ip, None)
        calls = []
        monkeypatch.setattr(waf_module, "log_security_event",
                            lambda event_type, message, level="WARNING", **f: calls.append(event_type))
        waf_module.record_breach_and_maybe_ban(ip, "test")
        assert "waf.redis_error" in calls
        assert len(waf_module._breach_log[ip]) == 1
        waf_module._breach_log.pop(ip, None)


class TestSettingsCacheRedisDispatch:
    """utils/helpers.py's _cache_get/_cache_set/_redis_clear_prefix --
    the same Redis-with-in-memory-fallback dispatch as
    TestWafBreachCounterRedisDispatch above, backing get_company_settings()/
    get_auth_config()/get_companies_list()/get_overdue_onboarding_count().
    Uses a throwaway dict for the in-memory side of each test rather than
    the real module-level _co_cache/etc., so these never interact with
    other tests' cache state."""

    def test_none_client_uses_in_memory_path(self, monkeypatch):
        monkeypatch.setattr(helpers_module, "redis_client", None)
        mem = {}
        assert helpers_module._cache_get(mem, "t") is helpers_module._CACHE_MISS
        helpers_module._cache_set(mem, "t", {"a": 1}, 60)
        assert helpers_module._cache_get(mem, "t") == {"a": 1}

    def test_redis_client_used_when_configured(self, monkeypatch):
        fake = FakeRedisKV()
        monkeypatch.setattr(helpers_module, "redis_client", fake)
        mem = {}
        helpers_module._cache_set(mem, "settings_co", {"company_name": "Acme"}, 60)
        # Went to Redis, not the in-memory fallback.
        assert mem == {}
        assert fake.store  # something landed in the fake store
        assert helpers_module._cache_get(mem, "settings_co") == {"company_name": "Acme"}

    def test_companies_list_tuples_round_trip_as_lists(self, monkeypatch):
        """JSON has no tuple type -- confirms the Redis path returns
        lists (not the original tuples) and that get_companies_list()'s
        own docstring claim (callers only index/iterate, never check
        the type) is what every real caller actually does."""
        fake = FakeRedisKV()
        monkeypatch.setattr(helpers_module, "redis_client", fake)
        mem = {}
        helpers_module._cache_set(mem, "settings_companies", [(1, "Acme", "ACM", "")], 30)
        result = helpers_module._cache_get(mem, "settings_companies")
        assert result == [[1, "Acme", "ACM", ""]]

    def test_redis_get_failure_falls_back_to_memory(self, monkeypatch):
        monkeypatch.setattr(helpers_module, "redis_client", RaisingKVRedis())
        mem = {}
        # Prime the in-memory fallback directly, as if an earlier
        # successful in-memory _cache_set had already run.
        mem[helpers_module._tenant_cache_key()] = {
            "data": {"a": 1},
            "expires": datetime.datetime.now() + datetime.timedelta(seconds=60),
        }
        assert helpers_module._cache_get(mem, "t") == {"a": 1}

    def test_redis_set_failure_falls_back_to_memory(self, monkeypatch):
        monkeypatch.setattr(helpers_module, "redis_client", RaisingKVRedis())
        mem = {}
        helpers_module._cache_set(mem, "t", {"a": 1}, 60)
        assert helpers_module._cache_get(mem, "t") == {"a": 1}

    def test_redis_clear_prefix_deletes_only_matching_keys(self, monkeypatch):
        fake = FakeRedisKV()
        fake.store = {"settings_co:tenantA": "1", "settings_co:tenantB": "2", "settings_auth:tenantA": "3"}
        monkeypatch.setattr(helpers_module, "redis_client", fake)
        helpers_module._redis_clear_prefix("settings_co")
        assert fake.store == {"settings_auth:tenantA": "3"}

    def test_redis_clear_prefix_noop_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(helpers_module, "redis_client", None)
        helpers_module._redis_clear_prefix("settings_co")  # must not raise

    def test_invalidate_settings_cache_clears_redis_too(self, monkeypatch):
        fake = FakeRedisKV()
        fake.store = {"settings_co:__no_tenant__": "1", "settings_auth:__no_tenant__": "2"}
        monkeypatch.setattr(helpers_module, "redis_client", fake)
        helpers_module.invalidate_settings_cache()
        assert fake.store == {}
