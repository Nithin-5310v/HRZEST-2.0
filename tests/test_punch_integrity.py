"""Attendance-punch integrity (utils/punch_integrity.py + the hooks in
blueprints/attendance.py and blueprints/employee_portal.py).

Regression coverage for the audit finding that a face check-in only asked
"does this JPEG encode close to the registered face?" -- a replayed or
photographed image passed, a captured request could be resent, a client
could skip the geofence by omitting coordinates, and nothing recorded the
evidence behind a punch.

dlib is not needed: the face_recognition module the routes talk to is
replaced with a scripted fake whose landmark sequence stands in for what a
camera saw.
"""
import base64
import datetime
import io
import os

import pytest

from utils import punch_integrity as pi
from tests.test_liveness import lm   # synthetic landmark builder


# ── helpers ──────────────────────────────────────────────────────────────────
def _jpeg_b64(size=(64, 64)):
    """A unique JPEG every call (random pixels) so replay detection only
    fires where a test deliberately resubmits the same bytes."""
    from PIL import Image
    im = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


BLINK = [0.30, 0.31, 0.30, 0.09, 0.29, 0.30, 0.31, 0.30]
STATIC = [0.30, 0.301, 0.299, 0.30, 0.30, 0.30, 0.302, 0.30]


class FakeFR:
    """Stands in for the face_recognition module. `landmarks` is consumed one
    entry per face_landmarks() call (one per liveness frame)."""

    def __init__(self, ears=BLINK, match=True):
        self.landmarks = [lm(ear=e) for e in ears]
        self.i = 0
        self.match = match

    def face_locations(self, img):
        return [(0, 10, 10, 0)]

    def face_encodings(self, img, locs=None):
        return [object()]

    def compare_faces(self, known, enc, tolerance=0.5):
        return [self.match]

    def face_landmarks(self, img):
        item = self.landmarks[self.i % len(self.landmarks)]
        self.i += 1
        return [item]


@pytest.fixture
def kiosk(client, db_engine, seed_employee, signed_qr, mocker, tmp_path, monkeypatch):
    """Everything a /attendance qr_face call needs, with face recognition
    faked. Returns a namespace with .post(**overrides) and the fake."""
    face_file = tmp_path / "registered.jpg"
    face_file.write_bytes(b"x")
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s",
                (str(face_file), seed_employee["employee_id"]))
    cur.execute("DELETE FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.close()

    fake = FakeFR()
    mocker.patch("blueprints.attendance.face_recognition", fake)
    mocker.patch("blueprints.attendance._face_recognition_available", True)
    mocker.patch("blueprints.attendance._get_known_face_encoding", return_value=object())
    monkeypatch.setenv("LIVENESS_MODE", "enforce")
    monkeypatch.setenv("PHOTO_REPLAY_MODE", "block")
    monkeypatch.setenv("MOCK_LOCATION_POLICY", "block")

    class K:
        employee_id = seed_employee["employee_id"]
        qr = signed_qr(seed_employee["employee_id"])
        fr = fake

        @staticmethod
        def challenge():
            r = client.post("/api/attendance/challenge", json={"employee_id": K.qr})
            assert r.status_code == 200, r.get_data(as_text=True)
            return r.get_json()

        @staticmethod
        def post(face=None, liveness="auto", **extra):
            body = {"employee_id": K.qr, "auth_combo": "qr_face",
                    "face_image": face or _jpeg_b64(), "lat": 12.97, "lon": 77.59}
            if liveness == "auto":
                ch = K.challenge()
                body["liveness"] = {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"],
                                    "frames": [_jpeg_b64() for _ in range(8)]}
            elif liveness is not None:
                body["liveness"] = liveness
            ip = extra.pop("_ip", None)
            body.update(extra)
            kw = {"environ_overrides": {"REMOTE_ADDR": ip}} if ip else {}
            return client.post("/attendance", json=body, **kw)

        @staticmethod
        def evidence():
            c = db_engine.cursor()
            c.execute("SELECT liveness, liveness_action, flags, outcome, channel, device_id, "
                      "photo_sha256 FROM punch_evidence WHERE employee_id=%s ORDER BY id",
                      (K.employee_id,))
            rows = c.fetchall()
            c.close()
            return rows

        @staticmethod
        def attendance_rows():
            c = db_engine.cursor()
            c.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=%s", (K.employee_id,))
            n = c.fetchone()[0]
            c.close()
            return n

    return K


def _cur(db_engine):
    return db_engine.cursor()


# ── challenge store ──────────────────────────────────────────────────────────
class TestChallengeStore:
    def test_single_use(self, db_engine):
        cur = _cur(db_engine)
        ch = pi.issue_challenge(cur, "TST001", "203.0.113.5")
        assert pi.consume_challenge(cur, ch["challenge_id"], ch["nonce"], "TST001") == ch["action"]
        assert pi.consume_challenge(cur, ch["challenge_id"], ch["nonce"], "TST001") is None

    def test_bound_to_the_employee_it_was_issued_to(self, db_engine):
        cur = _cur(db_engine)
        ch = pi.issue_challenge(cur, "TST001", "203.0.113.5")
        assert pi.consume_challenge(cur, ch["challenge_id"], ch["nonce"], "TST002") is None
        # ...and a failed attempt must not burn it for the rightful owner.
        assert pi.consume_challenge(cur, ch["challenge_id"], ch["nonce"], "TST001") is not None

    def test_wrong_nonce_rejected(self, db_engine):
        cur = _cur(db_engine)
        ch = pi.issue_challenge(cur, "TST001", None)
        assert pi.consume_challenge(cur, ch["challenge_id"], "n" * 32, "TST001") is None

    def test_expired_rejected(self, db_engine):
        cur = _cur(db_engine)
        ch = pi.issue_challenge(cur, "TST001", None)
        cur.execute("UPDATE checkin_challenges SET expires_at = NOW() - INTERVAL '1 second' WHERE id=%s",
                    (ch["challenge_id"],))
        assert pi.consume_challenge(cur, ch["challenge_id"], ch["nonce"], "TST001") is None

    @pytest.mark.parametrize("cid,nonce", [(None, None), ("", ""), ("short", "short"), (1, 2)])
    def test_malformed_inputs_rejected(self, db_engine, cid, nonce):
        assert pi.consume_challenge(_cur(db_engine), cid, nonce, "TST001") is None

    def test_nonce_is_not_stored_in_clear(self, db_engine):
        cur = _cur(db_engine)
        ch = pi.issue_challenge(cur, "TST001", None)
        cur.execute("SELECT nonce_hash FROM checkin_challenges WHERE id=%s", (ch["challenge_id"],))
        stored = cur.fetchone()[0]
        assert stored != ch["nonce"] and len(stored) == 64

    def test_actions_are_server_chosen_and_vary(self, db_engine):
        cur = _cur(db_engine)
        seen = {pi.issue_challenge(cur, "TST001", None)["action"] for _ in range(60)}
        assert seen <= set(pi.liveness.ACTIONS) and len(seen) >= 2


class TestChallengeEndpoint:
    def test_issues_for_a_known_employee(self, client, seed_employee, signed_qr):
        r = client.post("/api/attendance/challenge", json={"employee_id": signed_qr(seed_employee["employee_id"])})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"]
        assert j["action"] in pi.liveness.ACTIONS and j["instruction"]
        assert len(j["nonce"]) >= 24 and j["min_frames"] >= 5

    def test_unknown_employee_gets_same_shape(self, client, signed_qr):
        r = client.post("/api/attendance/challenge", json={"employee_id": signed_qr("NOPE999")})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"] and set(j) >= {"challenge_id", "nonce", "action"}

    def test_missing_id_is_400(self, client):
        assert client.post("/api/attendance/challenge", json={}).status_code == 400


# ── liveness through the real /attendance route ──────────────────────────────
class TestKioskLiveness:
    def test_valid_blink_burst_is_accepted(self, kiosk, monkeypatch):
        # the challenge picks a random action; script the fake for whichever
        # one it picked by making every action's signal present at once.
        kiosk.fr.landmarks = [lm(ear=e, yaw=y, smile=s) for e, y, s in zip(
            BLINK, [0, .1, .2, .3, .2, .1, 0, 0], [.8, .8, .85, .95, .98, .95, .9, .85])]
        r = kiosk.post()
        assert r.get_json()["ok"] is True, r.get_json()
        ev = kiosk.evidence()
        assert ev[-1][0] == "passed" and ev[-1][3] == "accepted"
        assert kiosk.attendance_rows() == 1

    def test_static_photo_fails_in_enforce(self, kiosk):
        kiosk.fr.landmarks = [lm(ear=e) for e in STATIC]
        r = kiosk.post()
        j = r.get_json()
        assert r.status_code == 403 and j["ok"] is False
        assert j["code"].startswith("liveness_")
        assert kiosk.attendance_rows() == 0
        ev = kiosk.evidence()
        assert ev[-1][0] == "failed" and ev[-1][3] == "rejected"

    def test_missing_liveness_blocked_in_enforce(self, kiosk):
        r = kiosk.post(liveness=None)
        assert r.status_code == 403 and r.get_json()["code"] == "liveness_required"
        assert kiosk.attendance_rows() == 0

    def test_missing_liveness_flagged_but_allowed_in_warn(self, kiosk, monkeypatch):
        monkeypatch.setenv("LIVENESS_MODE", "warn")
        r = kiosk.post(liveness=None)
        assert r.get_json()["ok"] is True
        ev = kiosk.evidence()
        assert ev[-1][0] == "missing" and "liveness_missing" in ev[-1][2]

    def test_off_mode_skips_everything(self, kiosk, monkeypatch):
        monkeypatch.setenv("LIVENESS_MODE", "off")
        r = kiosk.post(liveness=None)
        assert r.get_json()["ok"] is True
        assert kiosk.evidence()[-1][0] == "not_required"

    def test_replaying_a_captured_request_fails(self, kiosk):
        kiosk.fr.landmarks = [lm(ear=e, yaw=y, smile=s) for e, y, s in zip(
            BLINK, [0, .1, .2, .3, .2, .1, 0, 0], [.8, .8, .85, .95, .98, .95, .9, .85])]
        ch = kiosk.challenge()
        live = {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"],
                "frames": [_jpeg_b64() for _ in range(8)]}
        first = kiosk.post(liveness=live)
        assert first.get_json()["ok"] is True
        # Same challenge again with a fresh photo (so only the nonce differs).
        second = kiosk.post(liveness=live)
        assert second.status_code == 403
        assert second.get_json()["code"] == "liveness_challenge_invalid"

    def test_challenge_for_another_employee_cannot_be_used(self, kiosk, db_engine):
        cur = db_engine.cursor()
        other = pi.issue_challenge(cur, "SOMEONE_ELSE", None)
        cur.close()
        r = kiosk.post(liveness={"challenge_id": other["challenge_id"], "nonce": other["nonce"],
                                 "frames": [_jpeg_b64() for _ in range(8)]})
        assert r.status_code == 403
        assert r.get_json()["code"] == "liveness_challenge_invalid"

    def test_live_frames_of_someone_else_are_refused_even_in_warn(self, kiosk, monkeypatch):
        """Accomplice performs the action live while the victim's photo
        supplies identity: the frames themselves must match the employee."""
        monkeypatch.setenv("LIVENESS_MODE", "warn")
        kiosk.fr.landmarks = [lm(ear=e, yaw=y, smile=s) for e, y, s in zip(
            BLINK, [0, .1, .2, .3, .2, .1, 0, 0], [.8, .8, .85, .95, .98, .95, .9, .85])]
        calls = {"n": 0}
        real = kiosk.fr.compare_faces

        def compare(known, enc, tolerance=0.5):
            calls["n"] += 1
            # 1st call is the legacy face_image identity match -> passes;
            # later calls are the liveness frames -> a different person.
            return [True] if calls["n"] == 1 else [False]

        kiosk.fr.compare_faces = compare
        r = kiosk.post()
        assert r.status_code == 403
        assert r.get_json()["code"] == "liveness_identity_mismatch"
        assert kiosk.attendance_rows() == 0
        kiosk.fr.compare_faces = real


# ── photo replay ─────────────────────────────────────────────────────────────
class TestPhotoReplay:
    def test_same_photo_twice_is_blocked(self, kiosk, monkeypatch):
        monkeypatch.setenv("LIVENESS_MODE", "off")
        photo = _jpeg_b64()
        first = kiosk.post(face=photo, liveness=None)
        assert first.get_json()["ok"] is True
        second = kiosk.post(face=photo, liveness=None)
        assert second.status_code == 403
        assert second.get_json()["code"] == "photo_replay"
        ev = kiosk.evidence()
        assert "photo_exact_replay" in ev[-1][2]

    def test_flag_mode_allows_but_records(self, kiosk, monkeypatch):
        monkeypatch.setenv("LIVENESS_MODE", "off")
        monkeypatch.setenv("PHOTO_REPLAY_MODE", "flag")
        photo = _jpeg_b64()
        kiosk.post(face=photo, liveness=None)
        second = kiosk.post(face=photo, liveness=None)
        assert second.get_json()["ok"] is True
        assert "photo_exact_replay" in kiosk.evidence()[-1][2]

    def test_a_different_photo_is_fine(self, kiosk, monkeypatch):
        monkeypatch.setenv("LIVENESS_MODE", "off")
        assert kiosk.post(liveness=None).get_json()["ok"] is True
        assert kiosk.post(liveness=None).get_json()["ok"] is True

    def test_dhash_properties(self):
        raw = base64.b64decode(_jpeg_b64((96, 96)))
        a = pi.dhash_256(raw)
        assert len(a) == 64 and pi.hamming_hex(a, a) == 0
        other = pi.dhash_256(base64.b64decode(_jpeg_b64((96, 96))))
        assert pi.hamming_hex(a, other) > pi.NEAR_DUPLICATE_MAX_HAMMING   # unrelated noise is far apart

    def test_near_duplicate_is_flagged_not_blocked(self, db_engine):
        cur = _cur(db_engine)
        dh = "ab" * 32
        near = f"{int(dh, 16) ^ 0b101:064x}"                  # 2 bits different
        cur.execute("INSERT INTO punch_evidence (employee_id, channel, photo_sha256, photo_dhash) "
                    "VALUES ('TST001','kiosk',%s,%s)", ("1" * 64, dh))
        ctx = pi.PunchContext("TST001", "kiosk", photo_sha256="2" * 64, photo_dhash=near)
        d = pi.check_photo_replay(ctx, cur, lambda *a, **k: None)
        assert d.allow and "photo_near_duplicate" in ctx.flags


# ── environment checks ───────────────────────────────────────────────────────
class TestMockLocation:
    def test_blocked_by_default(self, kiosk):
        r = kiosk.post(mocked=True)
        assert r.status_code == 403 and r.get_json()["code"] == "mock_location"
        assert kiosk.attendance_rows() == 0
        assert "mock_location" in kiosk.evidence()[-1][2]

    def test_flag_policy_allows_but_records(self, kiosk, monkeypatch):
        monkeypatch.setenv("MOCK_LOCATION_POLICY", "flag")
        monkeypatch.setenv("LIVENESS_MODE", "off")      # isolate the mock-location policy
        r = kiosk.post(mocked=True, liveness=None)
        assert r.get_json()["ok"] is True
        assert "mock_location" in kiosk.evidence()[-1][2]

    @pytest.mark.parametrize("raw,expected", [(True, True), ("true", True), ("1", True),
                                              (False, False), ("false", False), (None, False), ("x", False)])
    def test_parse_bool(self, raw, expected):
        assert pi.parse_bool(raw) is expected


class TestOfficeNetwork:
    @staticmethod
    def _configure(db_engine, cidrs, require=1):
        cur = db_engine.cursor()
        cur.execute("UPDATE company_settings SET office_ip_cidrs=%s, require_office_network=%s", (cidrs, require))
        cur.close()

    def _check(self, db_engine, ip, work_mode="office"):
        cur = db_engine.cursor()
        ctx = pi.PunchContext("TST001", "kiosk", ip=ip)
        d = pi.check_office_network(ctx, cur, work_mode, lambda *a, **k: None)
        cur.close()
        return d, ctx

    def test_inside_allowed(self, db_engine):
        self._configure(db_engine, "198.51.100.0/24")
        d, _ = self._check(db_engine, "198.51.100.77")
        assert d.allow

    def test_outside_denied_and_flagged(self, db_engine):
        self._configure(db_engine, "198.51.100.0/24")
        d, ctx = self._check(db_engine, "203.0.113.9")
        assert not d.allow and d.code == "off_network" and "off_network" in ctx.flags

    def test_wfh_exempt(self, db_engine):
        self._configure(db_engine, "198.51.100.0/24")
        d, _ = self._check(db_engine, "203.0.113.9", work_mode="wfh")
        assert d.allow

    def test_unverifiable_source_is_flagged_never_blocked(self, db_engine):
        """Container gateway hiding client IPs must not lock the company out."""
        self._configure(db_engine, "198.51.100.0/24")
        d, ctx = self._check(db_engine, "10.0.2.2")
        assert d.allow and "network_unverifiable" in ctx.flags

    def test_disabled_when_not_required(self, db_engine):
        self._configure(db_engine, "198.51.100.0/24", require=0)
        d, _ = self._check(db_engine, "203.0.113.9")
        assert d.allow

    def test_ipv6_ranges(self, db_engine):
        self._configure(db_engine, "2001:db8::/32")
        assert self._check(db_engine, "2001:db8::5")[0].allow
        assert not self._check(db_engine, "2001:db9::5")[0].allow

    def test_kiosk_enforces_it_end_to_end(self, kiosk, db_engine, monkeypatch):
        monkeypatch.setenv("LIVENESS_MODE", "off")
        self._configure(db_engine, "198.51.100.0/24")
        blocked = kiosk.post(liveness=None, _ip="203.0.113.9")
        assert blocked.status_code == 403 and blocked.get_json()["code"] == "off_network"
        assert kiosk.attendance_rows() == 0
        allowed = kiosk.post(liveness=None, _ip="198.51.100.20")
        assert allowed.get_json()["ok"] is True


class TestOfficeNetworkAdminEndpoint:
    def _login(self, client, seed_admin):
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})

    def test_roundtrip_and_validation(self, client, seed_admin):
        self._login(client, seed_admin)
        r = client.post("/api/admin/attendance/office_networks",
                        json={"cidrs": ["198.51.100.0/24", "203.0.113.5"], "require": True})
        assert r.status_code == 200 and r.get_json()["require"] is True
        g = client.get("/api/admin/attendance/office_networks").get_json()
        assert "198.51.100.0/24" in g["cidrs"] and g["require"] is True

    def test_rejects_catch_all_and_garbage(self, client, seed_admin):
        self._login(client, seed_admin)
        assert client.post("/api/admin/attendance/office_networks",
                           json={"cidrs": ["0.0.0.0/0"], "require": True}).status_code == 400
        assert client.post("/api/admin/attendance/office_networks",
                           json={"cidrs": ["not-a-cidr"], "require": True}).status_code == 400
        assert client.post("/api/admin/attendance/office_networks",
                           json={"cidrs": ["1.1.1.1"] * 51}).status_code == 400

    def test_requires_admin(self, client):
        r = client.get("/api/admin/attendance/office_networks")
        assert r.status_code in (401, 302, 403)


class TestFlaggedPunchesEndpoint:
    def test_lists_rejected_and_flagged(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO punch_evidence (employee_id, channel, flags, outcome) "
                    "VALUES ('TST001','kiosk','mock_location','rejected')")
        cur.execute("INSERT INTO punch_evidence (employee_id, channel, flags, outcome) "
                    "VALUES ('TST001','kiosk',NULL,'accepted')")
        cur.close()
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
        j = client.get("/api/admin/attendance/flagged?days=7").get_json()
        assert j["ok"]
        outcomes = [p["outcome"] for p in j["punches"]]
        assert "rejected" in outcomes and "accepted" not in outcomes


# ── the GPS-only mobile routes ───────────────────────────────────────────────
class TestMobileCheckin:
    @staticmethod
    def _token(client, seed_employee):
        return client.post("/api/employee/login", json={
            "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
        }).get_json()["token"]

    def _post(self, client, seed_employee, **body):
        tok = self._token(client, seed_employee)
        return client.post("/api/employee/checkin", json=body,
                           headers={"Authorization": f"Bearer {tok}"})

    def test_omitting_coordinates_no_longer_skips_the_geofence(self, client, seed_employee):
        r = self._post(client, seed_employee)
        assert r.status_code == 400 and r.get_json()["code"] == "location_required"

    def test_opt_out_env_restores_old_behaviour(self, client, seed_employee, monkeypatch):
        monkeypatch.setenv("MOBILE_REQUIRE_LOCATION", "false")
        assert self._post(client, seed_employee).get_json()["ok"] is True

    def test_with_coordinates_succeeds_and_records_evidence(self, client, seed_employee, db_engine):
        r = self._post(client, seed_employee, lat=12.97, lon=77.59, device_id="dev-abcdef012345")
        assert r.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("SELECT channel, device_id, outcome FROM punch_evidence WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        assert cur.fetchone() == ("mobile", "dev-abcdef012345", "accepted")

    def test_mocked_location_refused(self, client, seed_employee):
        r = self._post(client, seed_employee, lat=12.97, lon=77.59, mocked=True)
        assert r.status_code == 403 and r.get_json()["code"] == "mock_location"

    def test_old_offline_timestamp_is_flagged(self, client, seed_employee, db_engine):
        past = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).isoformat() + "Z"
        r = self._post(client, seed_employee, lat=12.97, lon=77.59, punched_at=past)
        assert r.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("SELECT flags FROM punch_evidence WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert "offline_punch" in (cur.fetchone()[0] or "")

    def test_sync_rejects_mocked_and_location_less_punches(self, client, seed_employee):
        tok = self._token(client, seed_employee)
        now = datetime.datetime.utcnow().isoformat() + "Z"
        r = client.post("/api/employee/sync_punches", headers={"Authorization": f"Bearer {tok}"}, json={
            "punches": [
                {"id": "a", "punched_at": now, "lat": 12.97, "lon": 77.59, "mocked": True},
                {"id": "b", "punched_at": now},
            ]})
        res = {x["id"]: x for x in r.get_json()["results"]}
        assert res["a"]["ok"] is False
        assert res["b"]["ok"] is False and "Location" in res["b"]["msg"]


class TestSanitizers:
    @pytest.mark.parametrize("raw,ok", [("dev-abcdef012345", True), ("a" * 64, True),
                                        ("short", False), ("a" * 65, False), ("bad id!", False),
                                        (None, False), (123, False), ("'; DROP TABLE x;--", False)])
    def test_device_id(self, raw, ok):
        assert (pi.sanitize_device_id(raw) is not None) is ok

    def test_cidr_parser(self):
        assert [str(n) for n in pi.parse_cidrs("10.0.0.0/8, 192.0.2.1\n2001:db8::/32")] == \
            ["10.0.0.0/8", "192.0.2.1/32", "2001:db8::/32"]
        with pytest.raises(ValueError):
            pi.parse_cidrs("10.0.0.0/8, nope")


class TestChallengeCaseInsensitivity:
    def test_issued_and_redeemed_ids_match_regardless_of_case(self, db_engine):
        cur = db_engine.cursor()
        ch = pi.issue_challenge(cur, "emp-0042", "203.0.113.5")
        assert pi.consume_challenge(cur, ch["challenge_id"], ch["nonce"], "EMP-0042") == ch["action"]
