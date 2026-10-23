# -*- coding: utf-8 -*-
"""Database access helpers for the SOC console.

Every read runs inside an explicit `BEGIN READ ONLY` transaction with a
`SET LOCAL statement_timeout`:

  * READ ONLY makes the guarantee structural -- a query bug or an injection in
    this module cannot modify data, whatever the SQL says.
  * LOCAL scopes the timeout to the transaction, so it vanishes at ROLLBACK
    and can never leak onto the shared connection pool (a plain SET on an
    autocommit pooled connection would).
  * The timeout stops an analyst's wide time-range query from starving the
    request path of the app it is watching.

Writes (bans, cases, session revocation, SOC audit) go through
`write_cursor`, which is an explicit transaction too.
"""
import contextlib

from database import get_master_db, get_tenant_db

DEFAULT_TIMEOUT_MS = 8000


def _connect(schema):
    if schema == "att_master":
        return get_master_db()
    return get_tenant_db(schema)        # validates the schema name


@contextlib.contextmanager
def read_cursor(schema="public", timeout_ms=DEFAULT_TIMEOUT_MS):
    conn = _connect(schema)
    cur = conn.cursor()
    began = False
    try:
        cur.execute("BEGIN READ ONLY")
        began = True
        cur.execute("SET LOCAL statement_timeout = %d" % int(timeout_ms))
        yield cur
    finally:
        try:
            if began:
                cur.execute("ROLLBACK")
        except Exception:       # connection already broken; the pool discards it
            pass
        try:
            cur.close()
        finally:
            conn.close()


@contextlib.contextmanager
def write_cursor(schema="att_master", timeout_ms=DEFAULT_TIMEOUT_MS):
    conn = _connect(schema)
    cur = conn.cursor()
    began = False
    try:
        cur.execute("BEGIN")
        began = True
        cur.execute("SET LOCAL statement_timeout = %d" % int(timeout_ms))
        yield cur
        cur.execute("COMMIT")
        began = False
    finally:
        try:
            if began:
                cur.execute("ROLLBACK")
        except Exception:
            pass
        try:
            cur.close()
        finally:
            conn.close()


def list_tenants(limit=500):
    """Active tenants (id, name, slug, schema, status, billing_state)."""
    with read_cursor("att_master") as cur:
        cur.execute(
            "SELECT id, company_name, subdomain, db_name, status, COALESCE(billing_state, 'ok'), created_at "
            "FROM tenants ORDER BY id LIMIT %s", (int(limit),))
        cols = ("id", "company", "slug", "schema", "status", "billing_state", "created_at")
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def each_tenant(fn, limit=300, timeout_ms=3000):
    """Run `fn(tenant_dict, cursor)` read-only inside each tenant schema.

    A tenant that errors (missing table, timeout) is skipped and reported
    instead of failing the whole view. -> (results:list, errors:list)."""
    results, errors = [], []
    for t in list_tenants(limit):
        if t["status"] not in ("active", "suspended", "locked"):
            continue
        try:
            with read_cursor(t["schema"], timeout_ms) as cur:
                out = fn(t, cur)
            if out is not None:
                results.append(out)
        except Exception as exc:
            errors.append({"tenant": t["slug"], "error": type(exc).__name__})
    return results, errors
