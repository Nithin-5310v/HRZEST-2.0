# -*- coding: utf-8 -*-
"""Field-level encryption for PII at rest: AES-256-GCM (authenticated).

Replaces Fernet (AES-128-CBC + HMAC-SHA256) as the format new values are
written in. Fernet values already in the database stay readable, so there
is no flag day: every write produces the new format, and
scripts/rotate_encryption_key.py upgrades existing rows in place.

Keys: no new secret to provision. Each configured ENCRYPTION_KEY /
ENCRYPTION_KEY_PREVIOUS value is a 32-byte urlsafe-base64 key (the format
`Fernet.generate_key()` prints). The AES-256 key is derived from it with
HKDF-SHA-512, so a 256-bit secret yields a full-strength 256-bit AES key.
The same raw key also keeps decrypting legacy Fernet tokens.

Stored format (ASCII, safe for any TEXT column):

    hz2:<urlsafe-b64( key_id[4] | nonce[12] | ciphertext | tag[16] )>

key_id is a public fingerprint of the derived key, so decryption picks
the right key directly and the rotation script can tell "already on the
current key" without trial decryption. The nonce is 96 random bits per
value (os.urandom), the GCM standard.

This module has no Flask/app imports on purpose: scripts import it
directly.
"""
import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet, MultiFernet, InvalidToken as _FernetInvalid
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.exceptions import InvalidTag

PREFIX = "hz2:"
FERNET_PREFIX = "gAAAAA"
_HKDF_INFO = b"hrzest/pii/aes-256-gcm/v2"
_AAD = b"hrzest-pii-v2"
_KID_LEN = 4
_NONCE_LEN = 12
_TAG_LEN = 16
FILE_MAGIC = b"HZF2"            # encrypted private-upload files
_FILE_AAD = b"hrzest-file-v2"


class DecryptionError(Exception):
    """Value looks like ciphertext but no configured key can decrypt it."""


def _raw_key(key: str) -> bytes:
    raw = base64.urlsafe_b64decode(key.strip().encode())
    if len(raw) != 32:
        raise ValueError("encryption key must be 32 bytes of urlsafe base64")
    return raw


def _derive(raw: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA512(), length=32, salt=None, info=_HKDF_INFO).derive(raw)


def _key_id(aes_key: bytes) -> bytes:
    return hmac.new(aes_key, b"hrzest/pii/key-id", hashlib.sha512).digest()[:_KID_LEN]


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def looks_like_fernet(value: str) -> bool:
    """Shaped like a real Fernet token (0x80 version byte, >= 73 bytes) --
    as opposed to legacy plaintext that predates encryption."""
    try:
        raw = base64.urlsafe_b64decode(value.encode())
    except Exception:
        return False
    return len(raw) >= 73 and raw[0:1] == b"\x80"


def looks_like_v2(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    try:
        return len(_b64d(value[len(PREFIX):])) >= _KID_LEN + _NONCE_LEN + _TAG_LEN
    except Exception:
        return False


def looks_encrypted(value) -> bool:
    return isinstance(value, str) and (looks_like_v2(value) or looks_like_fernet(value))


class PiiCipher:
    """keys: primary (encrypts) first, then previous keys (decrypt only)."""

    def __init__(self, keys):
        keys = [k.strip() for k in keys if k and k.strip()]
        if not keys:
            raise ValueError("no encryption key supplied")
        raws = [_raw_key(k) for k in keys]
        self._aes = []                      # [(kid, AESGCM)] primary first
        for raw in raws:
            dk = _derive(raw)
            self._aes.append((_key_id(dk), AESGCM(dk)))
        self._by_kid = {}
        for kid, aes in self._aes:
            self._by_kid.setdefault(kid, aes)
        self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])

    @property
    def primary_kid(self) -> bytes:
        return self._aes[0][0]

    def encrypt(self, plaintext: str) -> str:
        kid, aes = self._aes[0]
        nonce = os.urandom(_NONCE_LEN)
        ct = aes.encrypt(nonce, plaintext.encode("utf-8"), _AAD)
        return PREFIX + _b64e(kid + nonce + ct)

    def decrypt(self, value: str) -> str:
        """Decrypt a v2 or Fernet value. Raises DecryptionError if it is
        ciphertext no configured key opens; ValueError if it is not
        ciphertext at all (caller decides what plaintext means)."""
        if looks_like_v2(value):
            blob = _b64d(value[len(PREFIX):])
            kid, nonce, ct = blob[:_KID_LEN], blob[_KID_LEN:_KID_LEN + _NONCE_LEN], blob[_KID_LEN + _NONCE_LEN:]
            aes = self._by_kid.get(kid)
            if aes is None:
                raise DecryptionError("no configured key matches this value's key id")
            try:
                return aes.decrypt(nonce, ct, _AAD).decode("utf-8")
            except InvalidTag as e:
                raise DecryptionError("authentication failed (tampered or corrupt)") from e
        if looks_like_fernet(value):
            try:
                return self._fernet.decrypt(value.encode()).decode("utf-8")
            except _FernetInvalid as e:
                raise DecryptionError("legacy Fernet token not decryptable with configured keys") from e
        raise ValueError("not an encrypted value")

    # ── binary (private uploads) ────────────────────────────────────────────
    # File layout: FILE_MAGIC | key_id[4] | nonce[12] | ciphertext | tag[16].
    # A distinct AAD keeps a file blob from ever being accepted as a PII
    # field value or vice versa.
    def encrypt_bytes(self, data: bytes) -> bytes:
        kid, aes = self._aes[0]
        nonce = os.urandom(_NONCE_LEN)
        return FILE_MAGIC + kid + nonce + aes.encrypt(nonce, data, _FILE_AAD)

    def decrypt_bytes(self, blob: bytes) -> bytes:
        """Decrypt an encrypt_bytes() blob. Bytes that don't start with
        FILE_MAGIC are legacy plaintext files and come back unchanged."""
        if not blob.startswith(FILE_MAGIC):
            return blob
        h = len(FILE_MAGIC)
        kid, nonce, ct = blob[h:h + _KID_LEN], blob[h + _KID_LEN:h + _KID_LEN + _NONCE_LEN], blob[h + _KID_LEN + _NONCE_LEN:]
        aes = self._by_kid.get(kid)
        if aes is None:
            raise DecryptionError("no configured key matches this file's key id")
        try:
            return aes.decrypt(nonce, ct, _FILE_AAD)
        except InvalidTag as e:
            raise DecryptionError("file authentication failed (tampered or corrupt)") from e

    def file_is_current(self, blob: bytes) -> bool:
        h = len(FILE_MAGIC)
        return blob.startswith(FILE_MAGIC) and blob[h:h + _KID_LEN] == self.primary_kid

    def is_current(self, value: str) -> bool:
        """True if value is v2 under the primary key (nothing to rotate)."""
        if not looks_like_v2(value):
            return False
        return _b64d(value[len(PREFIX):])[:_KID_LEN] == self.primary_kid

    def rotate(self, value: str) -> str:
        """Re-encrypt any decryptable value as v2 under the primary key."""
        return self.encrypt(self.decrypt(value))


def cipher_from_env(environ=None) -> PiiCipher:
    env = os.environ if environ is None else environ
    primary = env.get("ENCRYPTION_KEY", "").strip()
    previous = [k.strip() for k in env.get("ENCRYPTION_KEY_PREVIOUS", "").split(",") if k.strip()]
    return PiiCipher([primary] + previous)
