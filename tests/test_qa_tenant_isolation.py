"""SEC-07: a table missing from a company's schema resolved to the shared
public table.

Every tenant connection runs `SET search_path TO "<tenant>", public`, so a
query against a table the tenant schema lacks silently reads and writes
public.<table> -- the default company's rows. The deploy-time migrations
re-applied only the column migrations to existing tenants, never the table
creation, so a table added to core_tables after a company was provisioned
never reached that company's schema.

Owner decision (docs/QA_AUDIT.md §0.1, option A): the deploy-time migrations
create missing tables in every tenant, record any table still missing on
att_master.tenants.schema_missing_tables, and such a company gets a 503
(and is skipped by background jobs) instead of another company's data.
"""
import logging
import os
import secrets

import pytest

from app import app as flask_app
from tests.test_auth_hardening import _make_tenant

MARKER = "SEC07 default-company holiday"


@pytest.fixture
def tenant(db_engine, client):
    return _make_tenant(db_engine, f"sec07{secrets.token_hex(3)}")


def _tables(db_engine, schema):
    cur = db_engine.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema,))
    names = {r[0] for r in cur.fetchall()}
    cur.close()
    return names


def _recorded_missing(db_engine, db_name):
    cur = db_engine.cursor()
    cur.execute("SELECT schema_missing_tables FROM att_master.tenants WHERE db_name=%s", (db_name,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else "no tenant row"


def _flag(db_engine, schema, value):
    cur = db_engine.cursor()
    cur.execute("UPDATE att_master.tenants SET schema_missing_tables=%s WHERE db_name=%s", (value, schema))
    cur.close()


def _run_tenant_migrations():
    from migrations.orchestration import _run_migrations_for_all_tenants
    with flask_app.app_context():
        _run_migrations_for_all_tenants()


def _tenant_holiday_names(schema):
    from database import get_tenant_db
    with flask_app.app_context():
        conn = get_tenant_db(schema)
        cur = conn.cursor()
        cur.execute("SELECT name FROM holidays")
        names = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
    return names


class TestRepair:
    def test_missing_table_is_created_in_the_company_schema(self, tenant, db_engine, caplog):
        _slug, schema = tenant
        cur = db_engine.cursor()
        cur.execute(f'DROP TABLE "{schema}".holidays CASCADE')  # nosec B608 -- test schema name
        cur.execute("INSERT INTO public.holidays (date, name) VALUES ('2031-01-02', %s)", (MARKER,))
        cur.close()
        assert MARKER in _tenant_holiday_names(schema)  # the leak this fixes

        with caplog.at_level(logging.WARNING, logger="attendance"):
            _run_tenant_migrations()

        assert "holidays" in _tables(db_engine, schema)
        assert MARKER not in _tenant_holiday_names(schema)
        assert _recorded_missing(db_engine, schema) is None
        repair_logs = [r.getMessage() for r in caplog.records if "SEC-07" in r.getMessage()]
        assert any(schema in m and "holidays" in m for m in repair_logs)

    def test_complete_company_is_left_unflagged(self, tenant, db_engine):
        _slug, schema = tenant
        _flag(db_engine, schema, "holidays")  # stale flag from an earlier deploy
        _run_tenant_migrations()
        assert _recorded_missing(db_engine, schema) is None

    def test_table_that_cannot_be_created_is_recorded(self, tenant, db_engine, monkeypatch):
        """A table the migrations create but that is absent afterwards
        (creation failed) is recorded, so the router can refuse the company."""
        _slug, schema = tenant
        import migrations.orchestration as orch
        real = orch._init_core_tables

        def init_with_a_table_that_vanishes(cursor, db):
            real(cursor, db)
            cursor.execute("CREATE TABLE IF NOT EXISTS qa_sec07_phantom (id INT)")
            cursor.execute("DROP TABLE qa_sec07_phantom")

        monkeypatch.setattr(orch, "_init_core_tables", init_with_a_table_that_vanishes)
        _run_tenant_migrations()
        assert "qa_sec07_phantom" in (_recorded_missing(db_engine, schema) or "")

    def test_company_whose_schema_does_not_exist_is_recorded(self, db_engine):
        ghost = f"t_sec07ghost{secrets.token_hex(3)}"
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
                    "VALUES ('Ghost Co', %s, %s, 'active')", (ghost.replace("_", "-"), ghost))
        cur.close()
        _run_tenant_migrations()
        assert _recorded_missing(db_engine, ghost)
        assert ghost not in _tables(db_engine, ghost)  # nothing was created on its behalf

    def test_default_company_on_public_is_never_flagged(self, db_engine):
        """The default company (DB_NAME) has no schema of its own -- its
        tables are public's -- so it has nothing to fall back to."""
        _run_tenant_migrations()
        assert _recorded_missing(db_engine, os.environ["DB_NAME"]) is None


class TestRouting:
    def test_company_with_missing_tables_gets_503_html(self, tenant, db_engine):
        slug, schema = tenant
        with flask_app.test_client() as c:
            assert c.get(f"/{slug}/admin_login").status_code == 200
        _flag(db_engine, schema, "holidays")
        with flask_app.test_client() as c:
            resp = c.get(f"/{slug}/admin_login")
        assert resp.status_code == 503

    def test_company_with_missing_tables_gets_503_json(self, tenant, db_engine):
        slug, schema = tenant
        _flag(db_engine, schema, "holidays")
        with flask_app.test_client() as c:
            resp = c.get(f"/{slug}/api/dashboard", headers={"Accept": "application/json"})
        assert resp.status_code == 503
        assert resp.get_json()["ok"] is False

    def test_session_bound_to_an_incomplete_company_gets_503(self, tenant, db_engine):
        """A session already bound to the company (no slug in the URL) is
        refused at its next status recheck."""
        slug, schema = tenant
        _flag(db_engine, schema, "holidays")
        with flask_app.test_client() as c:
            with c.session_transaction() as s:
                s["tenant_db"] = schema
                s["tenant_slug"] = slug
                s["_tenant_status_checked_at"] = 0
            assert c.get("/admin_login").status_code == 503


class TestBackgroundJobs:
    def test_email_queue_skips_an_incomplete_company(self, tenant, db_engine):
        _slug, schema = tenant
        from utils.email_utils import _active_tenant_schemas
        with flask_app.app_context():
            assert schema in _active_tenant_schemas()
        _flag(db_engine, schema, "holidays")
        with flask_app.app_context():
            assert schema not in _active_tenant_schemas()
