# -*- coding: utf-8 -*-
"""helpers: field encryption at rest (PII, pay amounts, email/phone) -- fail-secure key bootstrap"""
import datetime
import os
from extensions import app_log
from utils import pii_crypto as _pii_crypto


# ── PII encryption (AES-256-GCM) -- fail-secure bootstrap ────────────────────────────
# Canonical location for this check. app.py used to carry a second,
# stricter copy (hard-fail in production, silent plaintext fallback in
# development) while this file's copy silently no-op'd in every
# environment -- a real gap: any future caller of THIS copy would have
# stored PAN/UAN/bank-account numbers in plaintext with no warning
# louder than a log line nobody was necessarily watching.
#
# Policy is now unconditional: missing or invalid ENCRYPTION_KEY is a hard
# abort in every environment, including local dev and CI, no exception.
# The previous "allow it in development" carve-out was itself the
# mechanism that let this exact class of bug hide -- a working-in-dev,
# broken-in-prod bootstrap teaches nobody to notice until it's live.
# Every environment that runs this code now needs a real key; generate
# one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
_ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "").strip()
if not _ENCRYPTION_KEY:
    app_log.critical(
        "FATAL: ENCRYPTION_KEY is not set. PAN, UAN, and bank account numbers "
        "require encryption at rest in every environment this application runs "
        "in -- refusing to start rather than silently storing PII as plaintext. "
        "Generate a key: python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\""
    )
    raise RuntimeError("ENCRYPTION_KEY is not set -- refusing to start (fail-secure).")
try:
    # Key rotation: ENCRYPTION_KEY always encrypts. ENCRYPTION_KEY_PREVIOUS
    # (comma-separated, newest first) is accepted for DECRYPTION only, so a
    # rotation is: set the new ENCRYPTION_KEY, move the old one into
    # ENCRYPTION_KEY_PREVIOUS, deploy, run scripts/rotate_encryption_key.py
    # to re-encrypt stored values, then drop the old key.
    #
    # Cipher: AES-256-GCM with an HKDF-SHA-512-derived key (utils/pii_crypto.py).
    # Values written under the old Fernet (AES-128-CBC) format still decrypt;
    # every new write uses AES-256-GCM, and the rotation script upgrades the
    # rest in place.
    _pii_cipher = _pii_crypto.cipher_from_env()
except Exception as _key_err:
    app_log.critical(
        "FATAL: ENCRYPTION_KEY is set but malformed (%s) -- refusing to start "
        "rather than silently storing PII as plaintext. Regenerate with: "
        "python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"",
        type(_key_err).__name__,
    )
    raise RuntimeError("ENCRYPTION_KEY is malformed -- refusing to start (fail-secure).") from _key_err


def encrypt_pii(value: str) -> str:
    if not value:
        return value
    return _pii_cipher.encrypt(value)


def encrypt_amount(value):
    """Encrypt a pay amount for a pay column (salary_config, offer_letters.
    monthly_ctc, employee_incentives.amount, overtime_records.ot_pay,
    salary_disbursement_*). Reads need nothing: the value comes back as a
    Decimal. None/"" -> None; raises ValueError for a non-number. The
    columns carry a CHECK that rejects anything not encrypted, so a write
    that skips this fails instead of storing pay in plaintext."""
    return _pii_cipher.encrypt_number(value)


def amount(value, default=0):
    """A pay column value as read back (Decimal, or None for NULL) -> Decimal,
    with `default` for NULL. Replaces SQL `COALESCE(col, 0)`, which no longer
    type-checks on the TEXT pay columns. Anything unreadable (a value that
    failed to decrypt, already logged by database.py) also gives `default`."""
    from decimal import Decimal, InvalidOperation
    if value is None:
        return Decimal(str(default))
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return Decimal(str(default))


def encrypt_contact(kind: str, value):
    """Encrypt an employee email ("email") or phone ("phone") for storage.
    Returns (stored_value, blind_index): write both, into e.g. email and
    email_bidx. Empty input stays empty with a NULL index."""
    if value is None:
        return None, None
    value = str(value).strip()
    if not value:
        return value, None
    return _pii_cipher.encrypt(value), _pii_cipher.blind_index(kind, value)


def contact_lookup_keys(kind: str, value):
    """Blind indexes to match with `<kind>_bidx = ANY(%s)` -- one per
    configured key, so rows indexed before a key rotation still match."""
    return _pii_cipher.blind_index_candidates(kind, value)


def decrypt_pii(value: str) -> str:
    if not value:
        return value
    if not isinstance(value, str):
        return value
    try:
        return _pii_crypto.decode_plaintext(_pii_cipher.decrypt(value))
    except ValueError:
        # Not ciphertext at all: legacy plaintext written before PII
        # encryption existed (e.g. a bare "Female" seeded by an old
        # migration or a test fixture) -- pass it through unchanged.
        return value
    except _pii_crypto.DecryptionError:
        # Shaped like real ciphertext but failed to decrypt -- almost
        # always ENCRYPTION_KEY rotated without keeping the old key in
        # ENCRYPTION_KEY_PREVIOUS, or tampered/corrupted data (GCM's tag
        # check catches both). Log it and return an unambiguous placeholder
        # rather than rendering the raw blob in the UI.
        app_log.warning("decrypt_pii: failed to decrypt a PII value (wrong/rotated ENCRYPTION_KEY or corrupted data) -- returning placeholder")
        return "[unable to decrypt]"


def decrypt_pii_date(value):
    """decrypt_pii() for employees.dob: that column used to be a native DATE
    and callers throughout the app call .strftime() on what it returns --
    widening it to TEXT so ciphertext fits (see app.py's
    employee_pii_columns_to_text_v1 migration) would otherwise silently
    turn every one of those call sites into an AttributeError. Returns a
    datetime.date (or None), never a bare string, so existing .strftime()
    call sites keep working unchanged."""
    if not value:
        return None
    decrypted = decrypt_pii(value)
    if isinstance(decrypted, datetime.date):
        return decrypted
    try:
        return datetime.datetime.strptime(str(decrypted), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
