"""SCALE-02: startup migrations run once per code version, one worker at a time.

Every gunicorn worker boot -- including the recycle every ~1,000 requests
(max_requests) -- ran init_master_db + init_db + the migrations for every
tenant schema. Boot time grew with the tenant count, workers migrated in
parallel, and ALTER TABLE ... ADD COLUMN IF NOT EXISTS takes an ACCESS
EXCLUSIVE lock on live tables even when the column already exists.
"""
import threading
import time

import pytest

from extensions import app as flask_app


@pytest.fixture
def runner(db_engine, monkeypatch):
    import migrations.runner as runner
    cur = db_engine.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS att_master")
    cur.execute("DROP TABLE IF EXISTS att_master.schema_migrations")
    monkeypatch.setattr(runner, "migrations_version", lambda: "qa-v1")
    monkeypatch.delenv("MIGRATIONS_FORCE", raising=False)
    calls = []

    def step(name, delay=0.0, fail=False):
        def _run():
            time.sleep(delay)
            calls.append(name)
            if fail:
                raise RuntimeError(f"{name} failed")
        return _run

    def boot(**steps):
        with flask_app.app_context():
            return runner.run_startup_migrations(
                steps.get("master", step("master")), steps.get("tenant", step("init_db")),
                steps.get("tenants", step("all_tenants")))

    yield runner, calls, step, boot
    cur.execute("DROP TABLE IF EXISTS att_master.schema_migrations")
    cur.close()


def test_first_boot_migrates_later_boots_skip(runner):
    _runner, calls, _step, boot = runner
    assert boot() is True
    assert calls == ["master", "init_db", "all_tenants"]
    assert boot() is False and boot() is False      # recycles / other workers
    assert calls == ["master", "init_db", "all_tenants"]


def test_new_code_version_migrates_again(runner, monkeypatch):
    mod, calls, _step, boot = runner
    boot()
    monkeypatch.setattr(mod, "migrations_version", lambda: "qa-v2")
    assert boot() is True
    assert calls.count("init_db") == 2


def test_failed_migration_is_retried_on_the_next_boot(runner):
    _runner, calls, step, boot = runner
    with pytest.raises(RuntimeError):
        boot(tenant=step("init_db", fail=True))
    assert boot() is True                            # version wasn't recorded; lock was released
    assert calls.count("init_db") == 2


def test_force_flag_reruns(runner, monkeypatch):
    _runner, calls, _step, boot = runner
    boot()
    monkeypatch.setenv("MIGRATIONS_FORCE", "1")
    assert boot() is True
    assert calls.count("init_db") == 2


def test_workers_booting_together_migrate_once(runner):
    _runner, calls, step, boot = runner
    results = []
    threads = [threading.Thread(target=lambda: results.append(boot(tenant=step("init_db", delay=0.5))))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [False, False, True]
    assert calls.count("init_db") == 1


def test_version_covers_every_migration_module():
    import migrations.runner as runner
    assert runner.migrations_version() == runner.migrations_version()
    assert len(runner.migrations_version()) >= 12
