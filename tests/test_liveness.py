"""Decision logic of utils/liveness.py, driven by synthetic landmark geometry
so it needs neither dlib nor a camera. What these pin down is the contract
the audit finding called for: a static photo must not satisfy a challenge,
and each challenge must be satisfied only by the matching real movement.
"""
import pytest

from utils import liveness as lv


def _eye(cx, cy, width, ear):
    """dlib six-point eye whose EAR equals `ear`."""
    h = ear * width / 2.0          # EAR = (2h + 2h) / (2w) = 2h / w
    return [
        (cx - width / 2, cy),
        (cx - width / 6, cy - h), (cx + width / 6, cy - h),
        (cx + width / 2, cy),
        (cx + width / 6, cy + h), (cx - width / 6, cy + h),
    ]


def lm(ear=0.30, yaw=0.0, smile=0.80, iod=60.0, eye_w=24.0):
    """One frame's landmarks in face_recognition's dict shape."""
    lcx, rcx, cy = 100.0, 100.0 + iod, 100.0
    mid = (lcx + rcx) / 2
    nose_x = mid + yaw * iod
    half = smile * iod / 2
    return {
        "left_eye": _eye(lcx, cy, eye_w, ear),
        "right_eye": _eye(rcx, cy, eye_w, ear),
        "nose_tip": [(nose_x - 3, cy + 30), (nose_x, cy + 32), (nose_x + 3, cy + 30)],
        "top_lip": [(mid - half, cy + 60), (mid, cy + 56), (mid + half, cy + 60)],
        "bottom_lip": [(mid + half, cy + 60), (mid, cy + 66), (mid - half, cy + 60)],
    }


def seq(**per_frame):
    """seq(ear=[...], yaw=[...], smile=[...]) -> list of frame_metrics()."""
    n = max(len(v) for v in per_frame.values())
    out = []
    for i in range(n):
        kw = {k: v[i] for k, v in per_frame.items() if i < len(v)}
        out.append(lv.frame_metrics(lm(**kw)))
    return out


class TestGeometry:
    def test_ear_matches_construction(self):
        assert lv.eye_aspect_ratio(_eye(0, 0, 30, 0.31)) == pytest.approx(0.31, abs=1e-6)

    def test_metrics_shape(self):
        m = lv.frame_metrics(lm(ear=0.28, yaw=0.1, smile=0.9, iod=70))
        assert m["ear"] == pytest.approx(0.28, abs=1e-3)
        assert m["yaw"] == pytest.approx(0.1, abs=1e-3)
        assert m["iod"] == pytest.approx(70)

    @pytest.mark.parametrize("bad", [{}, {"left_eye": []}, None])
    def test_unusable_landmarks_give_none(self, bad):
        assert lv.frame_metrics(bad) is None


class TestBlink:
    def test_real_blink_passes(self):
        ms = seq(ear=[0.30, 0.31, 0.29, 0.10, 0.28, 0.30, 0.31, 0.30])
        r = lv.evaluate(ms, "blink")
        assert r.ok, r.reason

    def test_partial_blink_within_noise_passes(self):
        ms = seq(ear=[0.30, 0.30, 0.31, 0.19, 0.29, 0.30, 0.31])      # 63% of baseline
        assert lv.evaluate(ms, "blink").ok

    def test_static_photo_fails(self):
        ms = seq(ear=[0.30, 0.301, 0.299, 0.30, 0.30, 0.302, 0.30, 0.30])
        r = lv.evaluate(ms, "blink")
        assert not r.ok and r.reason == "blink_not_detected"

    def test_eyes_closed_throughout_fails(self):
        ms = seq(ear=[0.08, 0.09, 0.08, 0.08, 0.09, 0.08, 0.08])
        r = lv.evaluate(ms, "blink")
        assert not r.ok and r.reason == "eyes_not_open_at_baseline"

    def test_closure_at_the_very_start_is_not_a_blink(self):
        ms = seq(ear=[0.08, 0.30, 0.30, 0.31, 0.30, 0.30, 0.30])
        r = lv.evaluate(ms, "blink")
        assert not r.ok and r.reason == "blink_not_transient"

    def test_small_flicker_is_not_a_blink(self):
        ms = seq(ear=[0.30, 0.30, 0.27, 0.30, 0.30, 0.30, 0.30])
        assert not lv.evaluate(ms, "blink").ok


class TestTurnHead:
    def test_turn_passes_either_direction(self):
        left = seq(yaw=[0.0, -0.05, -0.15, -0.25, -0.12, 0.0, 0.01])
        right = seq(yaw=[0.0, 0.05, 0.15, 0.25, 0.12, 0.0, 0.01])
        assert lv.evaluate(left, "turn_head").ok
        assert lv.evaluate(right, "turn_head").ok

    def test_flat_photo_does_not_produce_depth_parallax(self):
        ms = seq(yaw=[0.01, 0.02, 0.0, 0.015, 0.01, 0.02, 0.0])
        r = lv.evaluate(ms, "turn_head")
        assert not r.ok and r.reason == "head_turn_not_detected"

    def test_blinking_does_not_satisfy_a_turn_challenge(self):
        ms = seq(ear=[0.3, 0.3, 0.3, 0.1, 0.3, 0.3, 0.3], yaw=[0.0] * 7)
        assert not lv.evaluate(ms, "turn_head").ok


class TestSmile:
    def test_smile_passes(self):
        ms = seq(smile=[0.80, 0.80, 0.83, 0.90, 0.93, 0.92, 0.90])
        assert lv.evaluate(ms, "smile").ok

    def test_neutral_face_fails(self):
        ms = seq(smile=[0.80, 0.805, 0.80, 0.795, 0.80, 0.80, 0.80])
        assert not lv.evaluate(ms, "smile").ok


class TestGuards:
    def test_action_must_match_the_challenge(self):
        blink = seq(ear=[0.3, 0.3, 0.3, 0.1, 0.3, 0.3, 0.3])
        assert lv.evaluate(blink, "blink").ok
        assert not lv.evaluate(blink, "smile").ok
        assert not lv.evaluate(blink, "turn_head").ok

    def test_too_few_usable_frames(self):
        ms = seq(ear=[0.3, 0.1, 0.3])
        r = lv.evaluate(ms, "blink")
        assert not r.ok and r.reason == "too_few_faces"

    def test_frames_without_a_face_do_not_count(self):
        ms = seq(ear=[0.3, 0.3, 0.1, 0.3, 0.3, 0.3]) + [None, None, None]
        assert lv.evaluate(ms, "blink").ok          # six real frames is enough
        assert lv.evaluate([None] * 9, "blink").reason == "too_few_faces"

    def test_face_too_small_is_refused(self):
        ms = [lv.frame_metrics(lm(ear=e, iod=15, eye_w=6)) for e in [0.3, 0.3, 0.1, 0.3, 0.3, 0.3, 0.3]]
        r = lv.evaluate(ms, "blink")
        assert not r.ok and r.reason == "face_too_small"

    def test_unknown_action(self):
        assert lv.evaluate(seq(ear=[0.3] * 7), "wink").reason == "unknown_action"

    def test_actions_all_have_instructions_and_checks(self):
        for a in lv.ACTIONS:
            assert a in lv.INSTRUCTIONS and a in lv._CHECKS


class TestAnalyzeFrames:
    """The dlib-facing wrapper, driven with a fake face_recognition module."""

    class FakeFR:
        def __init__(self, per_frame):
            self.per_frame = list(per_frame)
            self.i = 0

        def face_landmarks(self, img):
            item = self.per_frame[self.i]
            self.i += 1
            if item is None:
                return []
            if item == "two":
                return [lm(), lm()]
            return [item]

    @staticmethod
    def _imgs(n):
        import numpy as np
        return [np.zeros((48, 64, 3), dtype="uint8") for _ in range(n)]

    def test_end_to_end_blink(self):
        per = [lm(ear=e) for e in [0.3, 0.3, 0.3, 0.1, 0.3, 0.3, 0.3, 0.3]]
        r = lv.analyze_frames(self._imgs(8), "blink", self.FakeFR(per))
        assert r.ok

    def test_second_face_in_any_frame_fails_the_burst(self):
        per = [lm(), lm(), "two", lm(), lm(), lm(), lm()]
        r = lv.analyze_frames(self._imgs(7), "blink", self.FakeFR(per))
        assert not r.ok and r.reason == "multiple_faces"

    @pytest.mark.parametrize("n", [0, 4, 17])
    def test_frame_count_bounds(self, n):
        r = lv.analyze_frames(self._imgs(n), "blink", self.FakeFR([lm()] * max(n, 1)))
        assert not r.ok and r.reason == "bad_frame_count"
