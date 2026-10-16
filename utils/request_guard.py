# -*- coding: utf-8 -*-
"""Per-request cache for the account-state reads every authenticated route makes.

Every web-session guard (admin_required / employee_required / any_user_required /
role_required) asks the database three things before the route body runs:

  1. is this session flagged compromised?        (session_risk)
  2. is the account still active?                (admin_users / employees)
  3. has the password changed since login?       (same row as 2)

They used to be three separate connection borrows -- three queries *and*
three `SET search_path` round-trips -- so a fixed 6 round-trips were spent on
every page before any real work. utils/auth.py now answers all three with ONE
statement (`_prime_guard_state`) and stores the result here; the three
original functions consult this cache first and fall back to their own query
if it is empty, so behaviour (including fail-open on a database error) and
their public names -- which tests patch -- are unchanged.

Scope and safety: the cache lives on flask.g, i.e. for exactly one request.
It is written once, at the top of the guard, from a single consistent
statement; it is never consulted across requests, so a deactivation, password
change or kill-switch flag takes effect on the very next request exactly as
before.
"""

import time

_KEY = "_hrz_guard_state"
# Belt and braces: the state is written at the top of a request's guard and is
# also dropped when that request ends (extensions.py), so it can never be this
# old in normal operation. If a request/app context is ever kept alive longer
# (test clients do this), refuse to serve a stale answer.
_MAX_AGE_SECONDS = 5.0


def _g():
    try:
        from flask import g
        return g
    except Exception:       # no app context (scripts, background threads)
        return None


def _current_request():
    try:
        from flask import request
        return request._get_current_object()
    except Exception:
        return None


def store(state):
    g = _g()
    if g is not None:
        try:
            state = dict(state, _req=_current_request(), _ts=time.monotonic())
            setattr(g, _KEY, state)
        except RuntimeError:
            pass


def _state():
    g = _g()
    if g is None:
        return None
    try:
        st = getattr(g, _KEY, None)
    except RuntimeError:
        return None
    if not st:
        return None
    # Only valid for the very request that wrote it, and only briefly.
    if st.get("_req") is None or st.get("_req") is not _current_request():
        return None
    if time.monotonic() - st.get("_ts", 0.0) > _MAX_AGE_SECONDS:
        return None
    return st


def clear():
    g = _g()
    if g is not None:
        try:
            setattr(g, _KEY, None)
        except RuntimeError:
            pass


def cached_risk_status(sid):
    """-> 'compromised' | other status string | None (unknown / not cached)."""
    st = _state()
    if st and sid and st.get("sid") == sid:
        return st.get("risk") or ""
    return None


def cached_account_active(table, id_column, id_value):
    """-> bool, or None when this account was not primed this request."""
    st = _state()
    if st and st.get("acct") == (table, id_column, id_value):
        return bool(st["exists"]) and bool(st["active"])
    return None


def cached_password_version(table, id_value):
    """-> '' | isoformat string, or None when not primed for this account."""
    st = _state()
    if st and st.get("pwv_key") == (table, id_value):
        return st["pwv"]
    return None
