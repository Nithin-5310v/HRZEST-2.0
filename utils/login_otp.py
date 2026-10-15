# -*- coding: utf-8 -*-
"""Server-side store for emailed login one-time codes.

The code itself used to live in the Flask session -- a signed but NOT
encrypted cookie -- so whoever triggered a login could base64-decode their
own cookie and read the code back without ever seeing the email. Now only
a random, opaque challenge id travels in the session; the code is kept
here as an HMAC, single-use, short-lived and attempt-capped.

Rows live in whichever schema the passed-in connection points at: the
current tenant's schema for tenant logins (utils.helpers._db), att_master
for platform-admin logins (database.get_master_db). A challenge id minted
in one tenant therefore simply doesn't exist in another tenant's schema,
and the schema name is also folded into the HMAC as a second binding.
"""
import hashlib
import hmac
import secrets

from flask import current_app

MAX_ATTEMPTS = 5

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS login_otp_challenges (
        id VARCHAR(64) PRIMARY KEY,
        kind VARCHAR(30) NOT NULL,
        username VARCHAR(150) NOT NULL,
        code_hash VARCHAR(64) NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        attempts INT NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""
CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_login_otp_challenges_user ON login_otp_challenges (kind, username)"
)
PURGE_EXPIRED_SQL = "DELETE FROM login_otp_challenges WHERE expires_at < NOW()"


def _current_schema(cursor) -> str:
    cursor.execute("SELECT current_schema()")
    row = cursor.fetchone()
    return (row[0] if row else "") or ""


def _digest(schema: str, kind: str, username: str, code: str) -> str:
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    msg = f"{schema}|{kind}|{username}|{code}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def issue_challenge(cursor, kind: str, username: str, ttl_sec: int):
    """Create a fresh challenge (replacing any earlier one for the same
    account) and return (challenge_id, plaintext_code). The plaintext code
    must only ever be handed to the email sender -- never stored, logged,
    or returned to the client. Caller commits."""
    code = f"{secrets.randbelow(900000) + 100000}"
    challenge_id = secrets.token_urlsafe(32)
    schema = _current_schema(cursor)
    cursor.execute(PURGE_EXPIRED_SQL)
    cursor.execute("DELETE FROM login_otp_challenges WHERE kind=%s AND username=%s", (kind, username))
    cursor.execute(
        "INSERT INTO login_otp_challenges (id, kind, username, code_hash, expires_at) "
        "VALUES (%s, %s, %s, %s, NOW() + make_interval(secs => %s))",
        (challenge_id, kind, username, _digest(schema, kind, username, code), int(ttl_sec)),
    )
    return challenge_id, code


def verify_challenge(cursor, challenge_id: str, submitted: str, kinds):
    """Check `submitted` against challenge `challenge_id`. Returns
    (kind, username) on success, else None. Every call counts as an
    attempt; the challenge is deleted on success (single use) and once
    MAX_ATTEMPTS is reached. Caller commits -- including on failure, so
    the attempt counter persists."""
    submitted = (submitted or "").strip()
    if not challenge_id or not submitted:
        return None
    cursor.execute(
        "UPDATE login_otp_challenges SET attempts = attempts + 1 "
        "WHERE id=%s AND expires_at > NOW() RETURNING kind, username, code_hash, attempts",
        (challenge_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    kind, username, code_hash, attempts = row
    if attempts > MAX_ATTEMPTS or kind not in kinds:
        cursor.execute("DELETE FROM login_otp_challenges WHERE id=%s", (challenge_id,))
        return None
    expected = _digest(_current_schema(cursor), kind, username, submitted)
    if not hmac.compare_digest(code_hash, expected):
        if attempts >= MAX_ATTEMPTS:
            cursor.execute("DELETE FROM login_otp_challenges WHERE id=%s", (challenge_id,))
        return None
    cursor.execute("DELETE FROM login_otp_challenges WHERE id=%s", (challenge_id,))
    return kind, username


def discard_challenge(cursor, challenge_id: str):
    if challenge_id:
        cursor.execute("DELETE FROM login_otp_challenges WHERE id=%s", (challenge_id,))
