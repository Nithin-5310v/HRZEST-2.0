# -*- coding: utf-8 -*-
"""Expo push notifications -- token registration + delivery for the mobile
app's remote push (distinct from the on-device local reminder in
mobile/src/utils/localNotifications.js, which needs no backend at all).

Calls Expo's push HTTP API directly over urllib.request (stdlib), same
no-heavy-SDK pattern already used for Razorpay (utils/razorpay_utils.py)
and the Anthropic API (utils/ai_assistant.py) -- no new pip dependency.

NOTE: the mobile app has no EAS project configured yet (no
extra.eas.projectId in mobile/app.json), so Notifications.
getExpoPushTokenAsync() cannot yet obtain a real token on-device --
mobile/src/utils/pushNotifications.js's registration call will no-op until
that one-time `eas init` step is done. This module and its DB table/
endpoints work correctly today regardless; there is simply nothing to
send to until then.
"""
import os
import json
import urllib.request
import urllib.error

from extensions import app_log
from utils.helpers import _db

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_TIMEOUT_SECONDS = 10
# Optional -- Expo's push API works without one; setting it (from an Expo
# access token, unrelated to EAS project setup) raises rate limits and lets
# Expo attribute requests to this account. See .env.example.
_ACCESS_TOKEN = os.environ.get("EXPO_ACCESS_TOKEN", "")


def register_push_token(owner_type: str, owner_id: str, expo_push_token: str, platform: str = None):
    """Upserts one device's push token. Re-registering the same token under
    a different owner (a logout/login as someone else on the same physical
    device) reassigns the existing row rather than erroring or duplicating
    -- see the UNIQUE(expo_push_token) column comment in
    migrations/core_tables.py for why owner isn't part of the key."""
    with _db() as (cursor, db):
        cursor.execute(
            "INSERT INTO push_tokens (owner_type, owner_id, expo_push_token, platform) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (expo_push_token) DO UPDATE SET "
            "owner_type=EXCLUDED.owner_type, owner_id=EXCLUDED.owner_id, platform=EXCLUDED.platform",
            (owner_type, owner_id, expo_push_token, platform),
        )
        db.commit()


_EXPO_MAX_MESSAGES_PER_REQUEST = 100


def _send_expo_push(tokens: list, title: str, body: str):
    """POSTs to Expo's push API in chunks of _EXPO_MAX_MESSAGES_PER_REQUEST
    (Expo's own documented cap per call) -- a single employee-targeted push
    is always one token, well under that, but send_push_notification_bulk()
    below can hand this hundreds at once for a company-wide announcement.
    Returns the concatenated list of per-token result dicts, in the same
    order as `tokens`, so callers can zip() them back together for
    pruning -- one chunk's request-level failure ([] for that chunk) still
    lets every other chunk's results come through rather than discarding
    the whole batch. Never raises -- a push-delivery failure must never
    surface to the caller (_create_notification() already treats this as
    best-effort)."""
    if not tokens:
        return []
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }
    if _ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {_ACCESS_TOKEN}"
    all_results = []
    for i in range(0, len(tokens), _EXPO_MAX_MESSAGES_PER_REQUEST):
        chunk = tokens[i:i + _EXPO_MAX_MESSAGES_PER_REQUEST]
        payload = [{"to": t, "title": title, "body": body, "sound": "default"} for t in chunk]
        req = urllib.request.Request(
            _EXPO_PUSH_URL, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # nosec B310
                result = json.loads(resp.read().decode("utf-8"))
                all_results.extend(result.get("data", []))
        except Exception as exc:
            app_log.warning("Expo push send failed for a %d-token chunk: %s", len(chunk), exc, exc_info=True)
            all_results.extend([None] * len(chunk))
    return all_results


def _prune_stale_tokens(tokens: list, results: list):
    """Expo's per-message result marks a token DeviceNotRegistered once the
    app has been uninstalled or the token otherwise permanently expired --
    deleting those here is the only place that ever happens, so a push
    silently stops trying a dead device instead of erroring on every future
    notification forever."""
    stale = [
        tok for tok, res in zip(tokens, results)
        if isinstance(res, dict) and res.get("details", {}).get("error") == "DeviceNotRegistered"
    ]
    if not stale:
        return
    with _db() as (cursor, db):
        cursor.execute("DELETE FROM push_tokens WHERE expo_push_token = ANY(%s)", (stale,))
        db.commit()


def send_push_notification(owner_type: str, owner_id: str, title: str, body: str):
    """Best-effort push to every device registered for one specific owner
    (e.g. owner_type='employee', owner_id=that employee's ID). Never raises
    -- called from utils.helpers._create_notification()'s own
    try/except-wrapped path, but kept defensive here too since it's also a
    reasonable direct call site for future code."""
    try:
        with _db() as (cursor, _conn):
            cursor.execute(
                "SELECT expo_push_token FROM push_tokens WHERE owner_type=%s AND owner_id=%s",
                (owner_type, owner_id),
            )
            tokens = [r[0] for r in cursor.fetchall()]
        if not tokens:
            return
        results = _send_expo_push(tokens, title, body)
        if results:
            _prune_stale_tokens(tokens, results)
    except Exception as exc:
        app_log.warning(
            "send_push_notification failed (owner_type=%s, owner_id=%s): %s",
            owner_type, owner_id, exc, exc_info=True,
        )


def send_push_notification_bulk(owner_type: str, owner_ids: list, title: str, body: str):
    """Best-effort push to every device registered for ANY of owner_ids --
    one query for every matching token, one (possibly chunked, see
    _send_expo_push) send, rather than looping send_push_notification() per
    owner_id (a separate DB round-trip and Expo request each). The
    company-wide-announcement broadcast path (utils.helpers.
    post_announcement()'s public branch) is the actual reason this exists:
    it fans out to every active employee via one executemany() rather than
    utils.helpers._create_notification() per employee, so it needs this
    same shape to reach every one of them without inverting back into a
    per-employee loop."""
    if not owner_ids:
        return
    try:
        with _db() as (cursor, _conn):
            cursor.execute(
                "SELECT expo_push_token FROM push_tokens WHERE owner_type=%s AND owner_id = ANY(%s)",
                (owner_type, list(owner_ids)),
            )
            tokens = [r[0] for r in cursor.fetchall()]
        if not tokens:
            return
        results = _send_expo_push(tokens, title, body)
        if results:
            _prune_stale_tokens(tokens, results)
    except Exception as exc:
        app_log.warning(
            "send_push_notification_bulk failed (owner_type=%s, %d owner_ids): %s",
            owner_type, len(owner_ids), exc, exc_info=True,
        )


def send_push_notification_broadcast(owner_type: str, title: str, body: str):
    """Best-effort push to every device registered under owner_type,
    regardless of owner_id -- for notifications.recipient_type='admin',
    which (like /api/notifications itself) is one shared inbox across
    every admin/hr/manager session, not scoped to a specific username."""
    try:
        with _db() as (cursor, _conn):
            cursor.execute(
                "SELECT expo_push_token FROM push_tokens WHERE owner_type=%s",
                (owner_type,),
            )
            tokens = [r[0] for r in cursor.fetchall()]
        if not tokens:
            return
        results = _send_expo_push(tokens, title, body)
        if results:
            _prune_stale_tokens(tokens, results)
    except Exception as exc:
        app_log.warning(
            "send_push_notification_broadcast failed (owner_type=%s): %s",
            owner_type, exc, exc_info=True,
        )
