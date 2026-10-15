# -*- coding: utf-8 -*-
"""Attendance-punch integrity: everything between "a client says it is
employee X at place Y" and "write the punch".

Layers (each independently switchable, each recorded as evidence):

  challenge   server-issued, single-use, employee-bound, short-lived nonce +
              a randomly chosen liveness action. A captured request cannot be
              replayed, and the person has to do something the server just
              asked for. (checkin_challenges table)
  liveness    utils/liveness.py -- multi-frame landmark check of that action,
              plus a same-person binding of the frames to the registered face.
  replay      exact-bytes SHA-256 and a 256-bit difference hash of the
              submitted photo, compared with earlier punches (punch_evidence).
              A camera never emits byte-identical JPEGs twice; a replayed
              upload does.
  environment client-reported `mocked` location flag (Android), and an
              optional per-company office-network allow-list (public IP CIDRs).
  evidence    one append-only row per punch attempt that reached the policy
              layer: channel, device id, ip, coordinates, liveness verdict,
              photo fingerprints, flags. The detection rules in
              utils/detections.py (impossible travel, shared device, ...) and
              HR's "flagged punches" view are built on it.

Modes (env, evaluated per call so they can be flipped without a restart of
the module -- but a deploy is still needed to change the environment):

  LIVENESS_MODE        off | warn | enforce      (default warn)
  PHOTO_REPLAY_MODE    off | flag | block        (default block exact copies;
                                                  near-duplicates only flag)
  MOCK_LOCATION_POLICY flag | block              (default block)

`warn` records a failed/missing liveness verdict as a flagged, alerting
event but still lets the punch through, so the clients can be rolled out
(web kiosk + portal ship with this change; update the mobile app, then set
LIVENESS_MODE=enforce).

Honest limits: a client that lies about `mocked`/`device_id`, a video
replay of the real employee doing the requested action, or a real-time
face swap, are not defeated here -- see utils/liveness.py and
SECURITY_HARDENING.md ("Attendance integrity") for what covers those.
"""
import hashlib
import io
import ipaddress
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Optional

from utils import liveness
from utils.client_ip import is_protected_ip

CHALLENGE_TTL_SECONDS = 120
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{8,64}$")
EXACT_REPLAY_WINDOW_DAYS = 90
NEAR_DUPLICATE_WINDOW_DAYS = 7
NEAR_DUPLICATE_MAX_HAMMING = 3          # of 256 bits
FACE_TOLERANCE = 0.5                    # same as every other compare_faces() site


def _mode(name, default, allowed):
    v = os.environ.get(name, default).strip().lower()
    return v if v in allowed else default


def liveness_mode():
    return _mode("LIVENESS_MODE", "warn", ("off", "warn", "enforce"))


def replay_mode():
    return _mode("PHOTO_REPLAY_MODE", "block", ("off", "flag", "block"))


def mock_location_policy():
    return _mode("MOCK_LOCATION_POLICY", "block", ("flag", "block"))


@dataclass
class Decision:
    allow: bool = True
    status: int = 200
    msg: str = ""
    code: str = ""
    count_as_failure: bool = False      # feeds the per-day attendance lockout


ALLOW = Decision()


@dataclass
class PunchContext:
    employee_id: str
    channel: str                         # kiosk | portal | mobile
    auth_combo: str = ""
    ip: Optional[str] = None
    device_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    mocked: bool = False
    flags: list = field(default_factory=list)
    liveness_state: str = "not_required"    # not_required|passed|failed|missing|challenge_invalid
    liveness_action: str = ""
    photo_sha256: Optional[str] = None
    photo_dhash: Optional[str] = None

    def flag(self, name):
        if name not in self.flags:
            self.flags.append(name)


def sanitize_device_id(raw):
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    return raw if _DEVICE_ID_RE.match(raw) else None


def _to_float(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if -1e6 < f < 1e6 else None


def parse_bool(v):
    if isinstance(v, bool):
        return v
    return isinstance(v, str) and v.strip().lower() in ("1", "true", "yes")


def build_context(employee_id, channel, auth_combo, ip, payload):
    """payload: dict-like (request JSON or request.form) carrying optional
    device_id / mocked / lat / lon."""
    return PunchContext(
        employee_id=employee_id, channel=channel, auth_combo=auth_combo or "", ip=ip,
        device_id=sanitize_device_id(payload.get("device_id")),
        lat=_to_float(payload.get("lat")), lon=_to_float(payload.get("lon")),
        mocked=parse_bool(payload.get("mocked")),
    )


# ── environment policy ──────────────────────────────────────────────────────
def check_mock_location(ctx: PunchContext, log_event) -> Decision:
    if not ctx.mocked:
        return ALLOW
    ctx.flag("mock_location")
    log_event("attendance.mock_location",
              "Client reported a mocked/simulated location for a punch",
              level="WARNING", identifier=ctx.employee_id, channel=ctx.channel)
    if mock_location_policy() == "block":
        return Decision(False, 403,
                        "Mock/simulated location detected. Turn off any fake-location app and try again.",
                        "mock_location", count_as_failure=True)
    return ALLOW


def parse_cidrs(text):
    """'a, b\\nc' -> list[ip_network]; raises ValueError naming the bad token."""
    nets = []
    for tok in re.split(r"[,\s]+", text or ""):
        tok = tok.strip()
        if not tok:
            continue
        try:
            nets.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            raise ValueError(f"not a valid IP/CIDR: {tok!r}")
    return nets


def check_office_network(ctx: PunchContext, cursor, work_mode, log_event) -> Decision:
    """If the company configured office networks and requires them, an
    on-site (non-WFH) punch must originate from one. Cannot be evaluated
    when the source address is itself non-routable/shared (container gateway
    hiding client IPs): that is flagged, never blocked, so a deployment
    problem can't lock the whole company out."""
    if work_mode == "wfh":
        return ALLOW
    try:
        cursor.execute("SELECT office_ip_cidrs, require_office_network FROM company_settings LIMIT 1")
        row = cursor.fetchone()
    except Exception:
        return ALLOW        # columns not migrated yet -- feature simply off
    if not row or not row[1]:
        return ALLOW
    try:
        nets = parse_cidrs(row[0] or "")
    except ValueError:
        return ALLOW
    if not nets:
        return ALLOW
    if is_protected_ip(ctx.ip):
        ctx.flag("network_unverifiable")
        return ALLOW
    addr = ipaddress.ip_address(ctx.ip.split("%")[0])
    if any(addr.version == n.version and addr in n for n in nets):
        return ALLOW
    ctx.flag("off_network")
    log_event("attendance.off_network", "Punch from outside the configured office networks",
              level="WARNING", identifier=ctx.employee_id, channel=ctx.channel)
    return Decision(False, 403, "Check-in is only allowed from the office network.", "off_network")


def biometric_consent_required():
    return os.environ.get("BIOMETRIC_CONSENT_REQUIRED", "false").strip().lower() in ("1", "true", "yes")


def check_biometric_consent(ctx: PunchContext, cursor, log_event) -> Decision:
    """Face recognition is biometric processing; when the deployment requires
    a recorded, un-withdrawn consent (BIOMETRIC_CONSENT_REQUIRED=true), a face
    check-in is refused without one. Off by default: the consent ledger and
    endpoints exist (blueprints/privacy.py), but the clients must offer the
    consent screen before this can be switched on without locking people out."""
    if not biometric_consent_required():
        return ALLOW
    from utils import privacy
    try:
        state = privacy.consent_state(cursor, ctx.employee_id, "biometric_attendance")
    except Exception:
        return ALLOW        # ledger not migrated yet -- never lock a company out over it
    if state["granted"]:
        return ALLOW
    ctx.flag("no_biometric_consent")
    log_event("attendance.consent_missing", "Face check-in refused: no biometric consent on record",
              level="WARNING", identifier=ctx.employee_id, channel=ctx.channel)
    return Decision(False, 403,
                    "Your consent for face recognition is required before you can check in with your face.",
                    "consent_required")


# ── challenge store ─────────────────────────────────────────────────────────
def issue_challenge(cursor, employee_id, ip):
    # Ids are compared case-insensitively end to end: the kiosk upper-cases the
    # scanned QR text and the portal endpoint upper-cases the verified id, so
    # a challenge bound to the raw casing would never be redeemable there.
    employee_id = (employee_id or "").upper()
    cid = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(24)
    action = secrets.choice(liveness.ACTIONS)
    cursor.execute(
        "INSERT INTO checkin_challenges (id, employee_id, nonce_hash, action, ip, expires_at) "
        "VALUES (%s,%s,%s,%s,%s, NOW() + (%s * INTERVAL '1 second'))",
        (cid, employee_id, hashlib.sha256(nonce.encode()).hexdigest(), action, (ip or "")[:64],
         CHALLENGE_TTL_SECONDS),
    )
    if secrets.randbelow(50) == 0:      # opportunistic purge, no scheduler needed
        cursor.execute("DELETE FROM checkin_challenges WHERE expires_at < NOW() - INTERVAL '1 day'")
    return {
        "challenge_id": cid, "nonce": nonce, "action": action,
        "instruction": liveness.INSTRUCTIONS[action], "expires_in": CHALLENGE_TTL_SECONDS,
        "min_frames": liveness.MIN_FRAMES, "max_frames": liveness.MAX_FRAMES,
    }


def consume_challenge(cursor, challenge_id, nonce, employee_id):
    """Atomically marks the challenge used and returns its action, or None
    when it is unknown, expired, already used, or issued to someone else."""
    if not (isinstance(challenge_id, str) and isinstance(nonce, str)
            and 8 <= len(challenge_id) <= 64 and 8 <= len(nonce) <= 128):
        return None
    employee_id = (employee_id or "").upper()
    cursor.execute(
        "UPDATE checkin_challenges SET used_at = NOW() "
        "WHERE id=%s AND employee_id=%s AND nonce_hash=%s AND used_at IS NULL AND expires_at > NOW() "
        "RETURNING action",
        (challenge_id, employee_id, hashlib.sha256(nonce.encode()).hexdigest()),
    )
    row = cursor.fetchone()
    return row[0] if row else None


# ── photo fingerprints / replay ─────────────────────────────────────────────
def dhash_256(image_bytes):
    """256-bit difference hash as 64 hex chars (16 rows x 16 comparisons)."""
    from PIL import Image
    im = Image.open(io.BytesIO(image_bytes)).convert("L").resize((17, 16))
    px = list(im.getdata())
    bits = 0
    for row in range(16):
        for col in range(16):
            bits = (bits << 1) | (1 if px[row * 17 + col] > px[row * 17 + col + 1] else 0)
    return f"{bits:064x}"


def hamming_hex(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def fingerprint_photo(image_bytes):
    return hashlib.sha256(image_bytes).hexdigest(), dhash_256(image_bytes)


def check_photo_replay(ctx: PunchContext, cursor, log_event) -> Decision:
    mode = replay_mode()
    if mode == "off" or not ctx.photo_sha256:
        return ALLOW
    cursor.execute(
        "SELECT employee_id FROM punch_evidence WHERE photo_sha256=%s "
        "AND created_at > NOW() - (%s * INTERVAL '1 day') LIMIT 1",
        (ctx.photo_sha256, EXACT_REPLAY_WINDOW_DAYS))
    hit = cursor.fetchone()
    if hit:
        ctx.flag("photo_exact_replay")
        log_event("attendance.photo_replay", "A previously submitted check-in photo was submitted again",
                  level="ERROR", identifier=ctx.employee_id, reason=f"first_seen_for={hit[0]}",
                  channel=ctx.channel)
        if mode == "block":
            return Decision(False, 403, "This photo has already been used. Please retake it.",
                            "photo_replay", count_as_failure=True)
        return ALLOW
    if ctx.photo_dhash:
        cursor.execute(
            "SELECT photo_dhash FROM punch_evidence WHERE employee_id=%s AND photo_dhash IS NOT NULL "
            "AND created_at > NOW() - (%s * INTERVAL '1 day') ORDER BY id DESC LIMIT 200",
            (ctx.employee_id, NEAR_DUPLICATE_WINDOW_DAYS))
        for (prev,) in cursor.fetchall():
            if prev and len(prev) == 64 and hamming_hex(prev, ctx.photo_dhash) <= NEAR_DUPLICATE_MAX_HAMMING:
                ctx.flag("photo_near_duplicate")      # flag only: a still subject can legitimately look alike
                break
    return ALLOW


# ── face + liveness ─────────────────────────────────────────────────────────
def check_liveness(ctx: PunchContext, cursor, *, frames, challenge_id, nonce,
                   known_encoding, face_recognition_mod, log_event) -> Decision:
    """frames: list of RGB numpy arrays (already decoded) or [] when the
    client sent none. known_encoding: the employee's registered encoding."""
    mode = liveness_mode()
    if mode == "off":
        ctx.liveness_state = "not_required"
        return ALLOW

    enforce = mode == "enforce"

    def _fail(state, code, msg, count=False, always=False):
        ctx.liveness_state = state
        ctx.flag(f"liveness_{state}")
        log_event(f"attendance.liveness_{state}", "Liveness check did not pass",
                  level="WARNING", identifier=ctx.employee_id, reason=code, channel=ctx.channel,
                  attempt_type=ctx.liveness_action)
        if enforce or always:
            return Decision(False, 403, msg, code, count_as_failure=count)
        return ALLOW

    if not frames or not challenge_id or not nonce:
        return _fail("missing", "liveness_required",
                     "Liveness check required. Please update the app / reload the page and try again.")

    action = consume_challenge(cursor, challenge_id, nonce, ctx.employee_id)
    if not action:
        return _fail("challenge_invalid", "liveness_challenge_invalid",
                     "Your check-in session expired. Please start again.", count=True)
    ctx.liveness_action = action

    result = liveness.analyze_frames(frames, action, face_recognition_mod)
    if not result.ok:
        return _fail("failed", f"liveness_{result.reason}",
                     "Liveness check failed (" + liveness.INSTRUCTIONS[action].rstrip('.') + "). Please try again.")

    # Same-person binding: the frames that proved liveness must also be the
    # registered employee, otherwise a live accomplice + a stolen photo of
    # the victim would pass both checks independently.
    if known_encoding is not None:
        n = len(frames)
        for idx in sorted({0, n // 2, n - 1}):
            encs = face_recognition_mod.face_encodings(liveness._downscale(frames[idx]))
            if not encs or not any(
                    True in face_recognition_mod.compare_faces([known_encoding], e, tolerance=FACE_TOLERANCE)
                    for e in encs):
                # Identity, not policy: refused even in warn mode.
                return _fail("failed", "liveness_identity_mismatch",
                             "Face did not match. Please try again.", count=True, always=True)
    ctx.liveness_state = "passed"
    return ALLOW


# ── frame decoding ──────────────────────────────────────────────────────────
def decode_frame_blobs(blobs):
    """list[bytes] -> (list[np.ndarray RGB], list[bytes]). Drops anything
    malformed/oversized/excess: a client sending garbage simply has no valid
    liveness evidence, which the policy then treats as `missing`."""
    import numpy as np
    from PIL import Image
    arrays, kept = [], []
    for b in (blobs or [])[:liveness.MAX_FRAMES]:
        try:
            if not b or len(b) > liveness.MAX_FRAME_BYTES:
                continue
            arrays.append(np.array(Image.open(io.BytesIO(b)).convert("RGB")))
            kept.append(b)
        except Exception:  # nosec B112 -- a bad frame is simply skipped; policy treats missing evidence as missing
            continue
    return arrays, kept


def decode_b64_frames(items):
    import base64
    if not isinstance(items, list):
        return [], []
    blobs = []
    for it in items[:liveness.MAX_FRAMES]:
        if isinstance(it, str) and len(it) <= liveness.MAX_FRAME_BYTES * 4 // 3 + 8:
            try:
                blobs.append(base64.b64decode(it, validate=False))
            except Exception:  # nosec B112 -- malformed base64 is simply no evidence
                continue
    return decode_frame_blobs(blobs)


# ── evidence ────────────────────────────────────────────────────────────────
def record_evidence(ctx: PunchContext, cursor, outcome="accepted"):
    """Append one punch_evidence row. Never raises: evidence must not be able
    to break attendance."""
    try:
        cursor.execute(
            "INSERT INTO punch_evidence (employee_id, channel, auth_combo, ip, device_id, lat, lon, "
            "mocked, liveness, liveness_action, photo_sha256, photo_dhash, flags, outcome) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ctx.employee_id, ctx.channel, ctx.auth_combo[:30], (ctx.ip or "")[:64], ctx.device_id,
             ctx.lat, ctx.lon, bool(ctx.mocked), ctx.liveness_state, ctx.liveness_action or None,
             ctx.photo_sha256, ctx.photo_dhash, ",".join(ctx.flags)[:200] or None, outcome))
    except Exception:
        from extensions import app_log
        app_log.warning("punch_evidence insert failed for %s", ctx.employee_id, exc_info=True)
