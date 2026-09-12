"""Startup DB migration package -- split out of app.py (Wave 3 item 19).

Custom, hand-rolled, idempotent migrations (CREATE TABLE IF NOT EXISTS /
ALTER TABLE ADD COLUMN IF NOT EXISTS), not Alembic/Flask-Migrate -- safe to
re-run on every boot. See orchestration.py's init_db() docstring for the
three-phase order (table creation -> column migrations -> seeding) that
every module here assumes.
"""
from .orchestration import init_db, _run_migrations_for_all_tenants, init_tenant_db
from .master import init_master_db

__all__ = ["init_db", "_run_migrations_for_all_tenants", "init_tenant_db", "init_master_db"]
