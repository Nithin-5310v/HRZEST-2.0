"""Personal-data lifecycle: export, erasure, retention, consent
(utils/privacy.py + blueprints/privacy.py).

Regression coverage for the audit finding that the platform held biometric
and personal data with no retention, no subject-access path, no erasure and
no record of consent.
"""
import os
import time

import pytest

from utils import privacy


def _login_admin(client, seed_admin):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})


def _emp_token(client, seed_employee):
    return client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    }).get_json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def rich_employee(db_engine, seed_employee):
    from utils.helpers import encrypt_pii
    cur = db_engine.cursor()
    cur.execute(
        "UPDATE employees SET phone='9999900000', pan_number=%s, bank_account=%s, dob=%s, address=%s, "
        "face_image=%s, about_me='likes hiking' WHERE employee_id=%s",
        (encrypt_pii("ABCDE1234F"), encrypt_pii("123456789012"), encrypt_pii("1990-04-02"),
         encrypt_pii("12 Park Street"), "/nonexistent/face.jpg", seed_employee["employee_id"]))
    cur.execute("INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s, CURRENT_DATE, "
                "'09:00:00', 'Full Day Login') ON CONFLICT DO NOTHING", (seed_employee["employee_id"],))
    cur.execute("INSERT INTO punch_evidence (employee_id, channel, device_id, ip) VALUES (%s,'mobile','dev-abcdef012345','203.0.113.4')",
                (seed_employee["employee_id"],))
    cur.execute("INSERT INTO push_tokens (owner_type, owner_id, expo_push_token) VALUES ('employee',%s,'ExponentPushToken[xyz]')",
                (seed_employee["employee_id"],))
    cur.close()
    return seed_employee


class TestExport:
    def test_decrypts_and_omits_secrets(self, db_engine, rich_employee):
        cur = db_engine.cursor()
        data = privacy.export_employee_data(cur, rich_employee["employee_id"])
        p = data["profile"]
        assert p["pan_number"] == "ABCDE1234F" and p["bank_account"] == "123456789012"
        assert p["dob"] == "1990-04-02" and p["address"] == "12 Park Street"
        for secret in ("password", "reset_token", "fingerprint_public_key"):
            assert secret not in p
        assert p["face_image"] == "[on file]"        # presence, not the path
        assert len(data["attendance"]) == 1
        assert data["punch_evidence"][0]["device_id"] == "dev-abcdef012345"
        assert "gAAAAA" not in repr(data)            # no ciphertext leaked through

    def test_unknown_employee_gives_empty_profile(self, db_engine):
        data = privacy.export_employee_data(db_engine.cursor(), "NOBODY")
        assert data["profile"] == {} and data["attendance"] == []


class TestErasure:
    def test_anonymises_and_removes_artefacts(self, db_engine, rich_employee, tmp_path):
        emp = rich_employee["employee_id"]
        (tmp_path / "face_logs").mkdir()
        reg = tmp_path / f"{emp}.jpg"
        log1 = tmp_path / "face_logs" / f"{emp}_20260101_000000.jpg"
        other = tmp_path / "face_logs" / "TST0010_20260101_000000.jpg"   # prefix-adjacent id
        stranger = tmp_path / "ZZZ.jpg"
        for f in (reg, log1, other, stranger):
            f.write_bytes(b"x")

        cur = db_engine.cursor()
        manifest = privacy.erase_employee(cur, emp, upload_folder=str(tmp_path))

        assert not reg.exists() and not log1.exists()
        assert other.exists() and stranger.exists()
        assert manifest["files_removed"] == 2

        cur.execute("SELECT name, email, phone, pan_number, bank_account, dob, address, face_image, "
                    "about_me, password, is_active FROM employees WHERE employee_id=%s", (emp,))
        row = cur.fetchone()
        assert row[0] == "Former employee"
        assert row[1:8] == (None,) * 7 and row[8] is None
        assert row[9] == "!erased" and row[10] == 0

        cur.execute("SELECT COUNT(*) FROM punch_evidence WHERE employee_id=%s", (emp,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM push_tokens WHERE owner_id=%s", (emp,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=%s", (emp,))
        assert cur.fetchone()[0] == 1                       # statutory record retained
        assert any("attendance" in k for k in manifest["kept"])

    def test_erased_account_cannot_log_in(self, client, db_engine, rich_employee):
        privacy.erase_employee(db_engine.cursor(), rich_employee["employee_id"])
        r = client.post("/api/employee/login", json={
            "employee_id": rich_employee["employee_id"], "password": rich_employee["password"]})
        assert r.status_code in (401, 403) or not r.get_json().get("ok")

    def test_unknown_employee_raises(self, db_engine):
        with pytest.raises(LookupError):
            privacy.erase_employee(db_engine.cursor(), "NOBODY")


class TestRetention:
    def test_purge_face_logs_only_old_jpgs(self, tmp_path):
        logs = tmp_path / "face_logs"
        logs.mkdir()
        old, fresh, keep_txt, registered = (logs / "a_1.jpg", logs / "a_2.jpg", logs / "note.txt", tmp_path / "a.jpg")
        for f in (old, fresh, keep_txt, registered):
            f.write_bytes(b"x")
        long_ago = time.time() - 40 * 86400
        for f in (old, keep_txt, registered):
            os.utime(f, (long_ago, long_ago))
        removed = privacy.purge_face_logs(str(tmp_path), days=30)
        assert removed == 1
        assert not old.exists() and fresh.exists() and keep_txt.exists() and registered.exists()

    def test_purge_expired_db_rows(self, db_engine, tmp_path):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO punch_evidence (employee_id, channel, created_at) VALUES "
                    "('E','kiosk', NOW() - INTERVAL '400 days'), ('E','kiosk', NOW())")
        cur.execute("INSERT INTO checkin_challenges (id, employee_id, nonce_hash, action, expires_at) VALUES "
                    "('old-challenge-0001','E','h','blink', NOW() - INTERVAL '10 days'),"
                    "('new-challenge-0001','E','h','blink', NOW() + INTERVAL '1 minute')")
        out = privacy.purge_expired(cur, str(tmp_path))
        assert out["punch_evidence"] == 1 and out["checkin_challenges"] == 1
        cur.execute("SELECT COUNT(*) FROM punch_evidence")
        assert cur.fetchone()[0] == 1

    def test_windows_are_configurable_and_sane(self, monkeypatch):
        assert privacy.face_log_retention_days() == 30
        monkeypatch.setenv("FACE_LOG_RETENTION_DAYS", "7")
        assert privacy.face_log_retention_days() == 7
        monkeypatch.setenv("FACE_LOG_RETENTION_DAYS", "junk")
        assert privacy.face_log_retention_days() == 30


class TestConsent:
    def test_latest_row_wins_and_history_is_append_only(self, db_engine):
        import psycopg2
        cur = db_engine.cursor()
        assert privacy.consent_state(cur, "E9", "biometric_attendance")["granted"] is False
        privacy.record_consent(cur, "E9", "biometric_attendance", "v1", True, "203.0.113.2")
        assert privacy.consent_state(cur, "E9", "biometric_attendance")["granted"] is True
        privacy.record_consent(cur, "E9", "biometric_attendance", "v1", False)
        assert privacy.consent_state(cur, "E9", "biometric_attendance")["granted"] is False
        cur.execute("SELECT COUNT(*) FROM consent_records WHERE employee_id='E9'")
        assert cur.fetchone()[0] == 2
        with pytest.raises(psycopg2.Error):
            cur.execute("UPDATE consent_records SET granted=TRUE WHERE employee_id='E9'")
        with pytest.raises(psycopg2.Error):
            cur.execute("DELETE FROM consent_records WHERE employee_id='E9'")

    @pytest.mark.parametrize("purpose,version", [("marketing", "v1"), ("biometric_attendance", "")])
    def test_validation(self, db_engine, purpose, version):
        with pytest.raises(ValueError):
            privacy.record_consent(db_engine.cursor(), "E9", purpose, version, True)

    def test_enforcement_is_opt_in(self, db_engine, monkeypatch):
        from utils import punch_integrity as pi
        cur = db_engine.cursor()
        ctx = pi.PunchContext("E10", "kiosk")
        monkeypatch.delenv("BIOMETRIC_CONSENT_REQUIRED", raising=False)
        assert pi.check_biometric_consent(ctx, cur, lambda *a, **k: None).allow
        monkeypatch.setenv("BIOMETRIC_CONSENT_REQUIRED", "true")
        d = pi.check_biometric_consent(ctx, cur, lambda *a, **k: None)
        assert not d.allow and d.code == "consent_required" and "no_biometric_consent" in ctx.flags
        privacy.record_consent(cur, "E10", "biometric_attendance", "v1", True)
        assert pi.check_biometric_consent(pi.PunchContext("E10", "kiosk"), cur, lambda *a, **k: None).allow


class TestEmployeeEndpoints:
    def test_export_requires_token(self, client):
        assert client.get("/api/employee/privacy/export").status_code == 401

    def test_export_returns_own_data_only(self, client, rich_employee):
        r = client.get("/api/employee/privacy/export", headers=_hdr(_emp_token(client, rich_employee)))
        j = r.get_json()
        assert r.status_code == 200 and j["data"]["employee_id"] == rich_employee["employee_id"]
        assert j["data"]["profile"]["pan_number"] == "ABCDE1234F"

    def test_erasure_request_is_deduplicated(self, client, seed_employee, db_engine):
        h = _hdr(_emp_token(client, seed_employee))
        first = client.post("/api/employee/privacy/erasure_request", json={"note": "leaving"}, headers=h).get_json()
        again = client.post("/api/employee/privacy/erasure_request", json={}, headers=h).get_json()
        assert first["ok"] and first["request_id"] and again["ok"] and "request_id" not in again
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM privacy_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == 1

    def test_consent_roundtrip(self, client, seed_employee):
        h = _hdr(_emp_token(client, seed_employee))
        r = client.post("/api/employee/consent", json={
            "purpose": "biometric_attendance", "policy_version": "2026-10", "granted": True}, headers=h)
        assert r.get_json()["state"]["granted"] is True
        g = client.get("/api/employee/consent", headers=h).get_json()
        assert g["consent"]["biometric_attendance"]["granted"] is True
        bad = client.post("/api/employee/consent", json={"purpose": "x", "policy_version": "1"}, headers=h)
        assert bad.status_code == 400


class TestAdminEndpoints:
    def test_erase_requires_typed_confirmation(self, client, seed_admin, rich_employee):
        _login_admin(client, seed_admin)
        emp = rich_employee["employee_id"]
        r = client.post(f"/api/admin/privacy/employee/{emp}/erase", json={})
        assert r.status_code == 400
        r = client.post(f"/api/admin/privacy/employee/{emp}/erase", json={"confirm": "WRONG"})
        assert r.status_code == 400

    def test_erase_end_to_end_and_closes_request(self, client, seed_admin, rich_employee, db_engine):
        emp = rich_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("INSERT INTO privacy_requests (employee_id, kind) VALUES (%s,'erasure')", (emp,))
        _login_admin(client, seed_admin)
        r = client.post(f"/api/admin/privacy/employee/{emp}/erase", json={"confirm": emp})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"] and j["manifest"]["employee_id"] == emp
        cur.execute("SELECT status, handled_by FROM privacy_requests WHERE employee_id=%s", (emp,))
        assert cur.fetchone() == ("completed", seed_admin["username"])
        cur.execute("SELECT name FROM employees WHERE employee_id=%s", (emp,))
        assert cur.fetchone()[0] == "Former employee"

    def test_refuses_to_erase_an_administrator_account(self, client, seed_admin):
        _login_admin(client, seed_admin)
        u = seed_admin["username"]
        r = client.post(f"/api/admin/privacy/employee/{u}/erase", json={"confirm": u})
        assert r.status_code == 409

    def test_unknown_employee_404(self, client, seed_admin):
        _login_admin(client, seed_admin)
        assert client.get("/api/admin/privacy/employee/NOPE/export").status_code == 404
        assert client.post("/api/admin/privacy/employee/NOPE/erase", json={"confirm": "NOPE"}).status_code == 404

    def test_admin_export_is_audited(self, client, seed_admin, rich_employee, db_engine):
        _login_admin(client, seed_admin)
        r = client.get(f"/api/admin/privacy/employee/{rich_employee['employee_id']}/export")
        assert r.status_code == 200 and r.get_json()["data"]["profile"]["pan_number"] == "ABCDE1234F"
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM audit_logs WHERE action='privacy.export'")
        assert cur.fetchone()[0] >= 1

    def test_requires_admin_session(self, client, rich_employee):
        emp = rich_employee["employee_id"]
        assert client.get(f"/api/admin/privacy/employee/{emp}/export").status_code in (302, 401, 403)
        assert client.post(f"/api/admin/privacy/employee/{emp}/erase",
                           json={"confirm": emp}).status_code in (302, 401, 403)

    def test_resolve_request(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO privacy_requests (employee_id, kind) VALUES ('E1','access') RETURNING id")
        rid = cur.fetchone()[0]
        _login_admin(client, seed_admin)
        assert client.post(f"/api/admin/privacy/requests/{rid}/resolve", json={"status": "bogus"}).status_code == 400
        assert client.post(f"/api/admin/privacy/requests/{rid}/resolve", json={"status": "completed"}).status_code == 200
        assert client.post(f"/api/admin/privacy/requests/{rid}/resolve", json={"status": "completed"}).status_code == 404
