# -*- coding: utf-8 -*-
"""Passkeys for the SOC console: the step after the emailed code.

The code proves access to the inbox; the passkey proves possession of the
person's own device (Touch ID, Windows Hello, a phone or a security key), so a
stolen inbox alone does not open the console. The first sign-in after the code
creates a passkey; every later one asks for it.

* Only public keys are stored (att_master.soc_passkeys); the private key never
  leaves the device. Verification is the real signature check of py_webauthn,
  with user verification (fingerprint / face / device PIN) required.
* A failed passkey counts toward the same lockout as a wrong code.
* Lost device: another SOC admin uses Users & audit -> Reset passkey; the person
  creates a new one at their next sign-in. A sole admin who is locked out sets
  SOC_PASSKEY=off on the server for that sign-in, then turns it back on.
* The site identity (RP ID) is the app's own domain (utils.webauthn_utils:
  ALLOWED_ORIGINS[0]); passkeys need HTTPS outside localhost.
"""
import base64
import json
import os

from utils.soc_db import read_cursor, write_cursor
from utils.webauthn_utils import (
    AttestationConveyancePreference, AuthenticatorSelectionCriteria, COSEAlgorithmIdentifier,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement,
    _wa_b64url_decode, _wa_b64url_encode, _wa_check_rp_id, _wa_origins, _wa_rp_id, _webauthn_available, webauthn,
)

_ALGS = (-7, -257)          # ES256, RS256: what every platform authenticator offers
RP_NAME = "HRzest Security Operations"


def required():
    """On unless SOC_PASSKEY=off (the documented recovery switch)."""
    return os.environ.get("SOC_PASSKEY", "on").strip().lower() not in ("0", "off", "false", "no")


def available():
    return _webauthn_available and webauthn is not None


def rp_problem():
    """A reason passkeys cannot work on this address (an IP as the site), or None."""
    return _wa_check_rp_id(_wa_rp_id())


def credentials(uid):
    with read_cursor("att_master") as cur:
        cur.execute("SELECT credential_id, public_key, sign_count FROM soc_passkeys WHERE user_id=%s ORDER BY id", (uid,))
        return cur.fetchall()


def counts():
    """{user_id: number of passkeys} for the Users list."""
    with read_cursor("att_master") as cur:
        cur.execute("SELECT user_id, COUNT(*) FROM soc_passkeys GROUP BY user_id")
        return dict(cur.fetchall())


def reset(uid):
    with write_cursor("att_master") as cur:
        cur.execute("DELETE FROM soc_passkeys WHERE user_id=%s", (uid,))
        return cur.rowcount


def registration_options(user, existing):
    """-> (options dict for navigator.credentials.create, challenge b64url)."""
    options = webauthn.generate_registration_options(
        rp_id=_wa_rp_id(),
        rp_name=RP_NAME,
        user_id=f"soc:{user['id']}".encode(),
        user_name=user.get("email") or user["username"],
        user_display_name=user["username"],
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=_wa_b64url_decode(c)) for c, _, _ in existing],
        supported_pub_key_algs=[COSEAlgorithmIdentifier(a) for a in _ALGS],
        attestation=AttestationConveyancePreference.NONE,
    )
    return json.loads(webauthn.options_to_json(options)), _wa_b64url_encode(options.challenge)


def authentication_options(existing):
    """-> (options dict for navigator.credentials.get, challenge b64url)."""
    options = webauthn.generate_authentication_options(
        rp_id=_wa_rp_id(),
        allow_credentials=[PublicKeyCredentialDescriptor(id=_wa_b64url_decode(c)) for c, _, _ in existing],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return json.loads(webauthn.options_to_json(options)), _wa_b64url_encode(options.challenge)


def verify_registration(user, credential, challenge_b64):
    """Check the new passkey's attestation and store its public key. -> bool."""
    verified = webauthn.verify_registration_response(
        credential=credential,
        expected_challenge=_wa_b64url_decode(challenge_b64),
        expected_rp_id=_wa_rp_id(),
        expected_origin=_wa_origins(),
        require_user_verification=True,
        supported_pub_key_algs=[COSEAlgorithmIdentifier(a) for a in _ALGS],
    )
    with write_cursor("att_master") as cur:
        cur.execute("INSERT INTO soc_passkeys (user_id, credential_id, public_key, sign_count, last_used_at) "
                    "VALUES (%s,%s,%s,%s,NOW()) ON CONFLICT (credential_id) DO NOTHING RETURNING id",
                    (user["id"], _wa_b64url_encode(verified.credential_id),
                     base64.b64encode(verified.credential_public_key).decode(), verified.sign_count))
        return cur.fetchone() is not None


def verify_authentication(user, credential, challenge_b64, existing):
    """Check the assertion against the stored public key of one of THIS user's
    passkeys, and record the new signature counter. -> bool."""
    cred_id = credential.get("id") if isinstance(credential, dict) else None
    match = next((row for row in existing if row[0] == cred_id), None)
    if match is None:
        return False
    _, public_key, sign_count = match
    verified = webauthn.verify_authentication_response(
        credential=credential,
        expected_challenge=_wa_b64url_decode(challenge_b64),
        expected_rp_id=_wa_rp_id(),
        expected_origin=_wa_origins(),
        credential_public_key=base64.b64decode(public_key),
        # A counter that goes backwards means a cloned key; authenticators that
        # never count (0) skip the check (py_webauthn, W3C WebAuthn 7.2 step 21).
        credential_current_sign_count=int(sign_count or 0),
        require_user_verification=True,
    )
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_passkeys SET sign_count=%s, last_used_at=NOW() WHERE user_id=%s AND credential_id=%s",
                    (verified.new_sign_count, user["id"], cred_id))
    return True
