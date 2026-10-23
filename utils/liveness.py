# -*- coding: utf-8 -*-
"""Active liveness check for face check-in: server-chosen challenge, verified
from a short burst of frames.

Why this exists
---------------
verify_uploaded_face()/the /attendance route only ask "does this JPEG
contain a face that encodes close to the registered one?". A photo of the
employee on a phone, or one stolen from a profile, passes. This module adds
the missing question: "is a live, three-dimensional person responding to
something the server just asked for?"

Mechanism
---------
The server picks one action at random per check-in (utils/punch_integrity.py
issues it inside a single-use challenge); the client captures ~1.5-3 s of
frames while the person performs it; this module measures facial-landmark
geometry across the frames and decides whether the action happened.

  blink      eye-aspect-ratio (EAR) dips well below its own baseline and
             recovers -- a printed/static photo cannot do this and a rigidly
             moved photo changes EAR very little.
  turn_head  the nose tip moves relative to the eye line (normalised by
             inter-ocular distance). That offset depends on depth, so a
             flat photo tilted or rotated barely changes it.
  smile      mouth width grows relative to inter-ocular distance.

What this does NOT stop (be honest about it)
--------------------------------------------
* A pre-recorded VIDEO of the real employee performing the requested action
  played to the camera. Randomising the action per attempt and requiring a
  specific one raises the cost (the attacker needs footage of that person
  doing it), but it is not presentation-attack-detection hardware.
* A real-time deepfake/face-swap driven by the attacker's own face.
* A client that hooks the camera API and feeds synthetic frames.
For those, you need device attestation (Play Integrity / App Attest) and/or
a certified PAD SDK -- see SECURITY_HARDENING.md. This is a meaningful
raise-the-bar control, not a guarantee.

The decision logic below is pure Python over landmark dicts so it can be
unit-tested without dlib; only extract_landmarks() needs face_recognition.
"""
import math
from dataclasses import dataclass, field
from statistics import median

ACTIONS = ("blink", "turn_head", "smile")

# Instruction text shown to the person (kept here so server and tests agree).
INSTRUCTIONS = {
    "blink": "Look at the camera and blink once.",
    "turn_head": "Look at the camera, then slowly turn your head to one side and back.",
    "smile": "Look at the camera and smile.",
}

MIN_FRAMES = 5
MAX_FRAMES = 16
MIN_INTER_OCULAR_PX = 22.0      # smaller faces give unreliable landmark geometry
MAX_FRAME_WIDTH = 480           # frames are downscaled to this before dlib
MAX_FRAME_BYTES = 1_500_000     # per decoded frame

# Thresholds were chosen from the usual published EAR/pose ranges and are
# deliberately conservative to avoid locking out honest users; tune with real
# kiosk captures via the LIVENESS_* env overrides in punch_integrity.
BLINK_REL_DROP = 0.70           # min EAR must fall to <= 70% of baseline
BLINK_MIN_BASELINE = 0.18       # eyes must be demonstrably open at baseline
BLINK_MIN_ABS_DROP = 0.045
TURN_MIN_YAW_SPAN = 0.20        # nose offset range / inter-ocular distance
SMILE_MIN_REL_GROWTH = 0.10


@dataclass
class LivenessResult:
    ok: bool
    reason: str
    action: str = ""
    frames_used: int = 0
    metrics: dict = field(default_factory=dict)


# ── geometry ────────────────────────────────────────────────────────────────
def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _center(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def eye_aspect_ratio(eye):
    """Soukupova & Cech (2016). `eye` is dlib's six-point ordering: p1 outer
    corner, p2/p3 upper lid, p4 inner corner, p5/p6 lower lid."""
    if len(eye) != 6:
        return None
    horizontal = _dist(eye[0], eye[3])
    if horizontal < 1e-6:
        return None
    return (_dist(eye[1], eye[5]) + _dist(eye[2], eye[4])) / (2.0 * horizontal)


def frame_metrics(lm):
    """Landmark dict (face_recognition.face_landmarks shape) ->
    {'ear','yaw','smile','iod'} or None when the landmarks are unusable."""
    try:
        le, re_ = lm["left_eye"], lm["right_eye"]
        ear_l, ear_r = eye_aspect_ratio(le), eye_aspect_ratio(re_)
        if ear_l is None or ear_r is None:
            return None
        lc, rc = _center(le), _center(re_)
        iod = _dist(lc, rc)
        if iod < 1e-6:
            return None
        nose = _center(lm["nose_tip"])
        mid_x = (lc[0] + rc[0]) / 2.0
        lips = list(lm["top_lip"]) + list(lm["bottom_lip"])
        mouth_w = max(p[0] for p in lips) - min(p[0] for p in lips)
        return {
            "ear": (ear_l + ear_r) / 2.0,
            "yaw": (nose[0] - mid_x) / iod,
            "smile": mouth_w / iod,
            "iod": iod,
        }
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


# ── decision ────────────────────────────────────────────────────────────────
def _check_blink(ms):
    ears = [m["ear"] for m in ms]
    base = median(ears)
    lo = min(ears)
    i = ears.index(lo)
    if base < BLINK_MIN_BASELINE:
        return False, "eyes_not_open_at_baseline"
    if not (lo <= BLINK_REL_DROP * base and (base - lo) >= BLINK_MIN_ABS_DROP):
        return False, "blink_not_detected"
    # Must be a transient: open eyes both before and after the closure.
    before = ears[:i]
    after = ears[i + 1:]
    if not before or not after or max(before) < 0.85 * base or max(after) < 0.85 * base:
        return False, "blink_not_transient"
    return True, "ok"


def _check_turn(ms):
    yaws = [m["yaw"] for m in ms]
    span = max(yaws) - min(yaws)
    if span < TURN_MIN_YAW_SPAN:
        return False, "head_turn_not_detected"
    return True, "ok"


def _check_smile(ms):
    sm = [m["smile"] for m in ms]
    lo = min(sm)
    if lo <= 0:
        return False, "smile_not_detected"
    if (max(sm) - lo) / lo < SMILE_MIN_REL_GROWTH:
        return False, "smile_not_detected"
    return True, "ok"


_CHECKS = {"blink": _check_blink, "turn_head": _check_turn, "smile": _check_smile}


def evaluate(metrics_seq, action):
    """metrics_seq: list of frame_metrics() dicts (None entries = frames where
    no usable face was found). Returns LivenessResult."""
    if action not in _CHECKS:
        return LivenessResult(False, "unknown_action", action)
    usable = [m for m in metrics_seq if m]
    if len(usable) < MIN_FRAMES:
        return LivenessResult(False, "too_few_faces", action, len(usable))
    if median(m["iod"] for m in usable) < MIN_INTER_OCULAR_PX:
        return LivenessResult(False, "face_too_small", action, len(usable))
    ok, reason = _CHECKS[action](usable)
    summary = {
        "ear_min": round(min(m["ear"] for m in usable), 3),
        "ear_median": round(median(m["ear"] for m in usable), 3),
        "yaw_span": round(max(m["yaw"] for m in usable) - min(m["yaw"] for m in usable), 3),
        "smile_span": round(max(m["smile"] for m in usable) - min(m["smile"] for m in usable), 3),
    }
    return LivenessResult(ok, reason, action, len(usable), summary)


# ── dlib-backed extraction (only part needing face_recognition) ─────────────
def _downscale(img_array):
    from PIL import Image
    import numpy as np
    h, w = img_array.shape[:2]
    if w <= MAX_FRAME_WIDTH:
        return img_array
    scale = MAX_FRAME_WIDTH / float(w)
    im = Image.fromarray(img_array).resize((MAX_FRAME_WIDTH, max(1, int(h * scale))))
    return np.array(im)


def extract_landmarks(img_array, face_recognition_mod):
    """-> (landmark_dict | None, face_count). Downscales for speed; with more
    than one face the caller treats the frame as unusable (a second person or
    a photo held beside the face is itself suspicious)."""
    small = _downscale(img_array)
    lms = face_recognition_mod.face_landmarks(small)
    if not lms:
        return None, 0
    if len(lms) > 1:
        return None, len(lms)
    return lms[0], 1


def analyze_frames(frames, action, face_recognition_mod):
    """frames: list of RGB numpy arrays. Runs landmark extraction and the
    action decision. Rejects any burst that contains a multi-face frame."""
    if not (MIN_FRAMES <= len(frames) <= MAX_FRAMES):
        return LivenessResult(False, "bad_frame_count", action, len(frames))
    metrics = []
    for img in frames:
        lm, count = extract_landmarks(img, face_recognition_mod)
        if count > 1:
            return LivenessResult(False, "multiple_faces", action, len(metrics))
        metrics.append(frame_metrics(lm) if lm else None)
    return evaluate(metrics, action)
