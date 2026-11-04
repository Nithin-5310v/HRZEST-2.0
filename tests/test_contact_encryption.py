"""Employee email/phone encrypted at rest (AES-256-GCM) with HMAC-SHA-512
blind indexes for exact-match lookup, decrypted transparently on fetch by
database.py's text caster."""
import pytest

from database import get_db_connection, raw_text
from utils.helpers import encrypt_contact, contact_lookup_keys
from utils.pii_crypto import normalize


def _raw(db_engine, emp_id, *cols):
    cur = db_engine.cursor()
    cur.execute(f"SELECT {', '.join(cols)} FROM employees WHERE employee_id=%s", (emp_id,))  # nosec B608
    row = cur.fetchone()
    cur.close()
    return row


def _employee_session(client, employee_id):
    with client.session_transaction() as sess:
        sess["employee_id"] = employee_id
        sess["role"] = "employee"


class TestPrimitives:
    def test_encrypt_contact_returns_ciphertext_and_index(self):
        enc, bidx = encrypt_contact("email", "  Asha@Example.COM ")
        assert enc.startswith("hz2:") and "asha" not in enc.lower()
        assert len(bidx) == 128                          # HMAC-SHA-512 hex
        assert bidx in contact_lookup_keys("email", "asha@example.com")

    def test_empty_values_stay_empty(self):
        assert encrypt_contact("email", None) == (None, None)
        assert encrypt_contact("phone", "  ") == ("", None)

    @pytest.mark.parametrize("a,b", [("+91 98765-43210", "9876543210"), ("098765 43210", "9876543210")])
    def test_phone_normalisation(self, a, b):
        assert normalize("phone", a) == normalize("phone", b)
        assert contact_lookup_keys("phone", a) == contact_lookup_keys("phone", b)

    def test_index_differs_by_kind(self):
        assert contact_lookup_keys("email", "1234567890") != contact_lookup_keys("phone", "1234567890")


class TestTransparentDecryption:
    def test_app_reads_plaintext_db_holds_ciphertext(self, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        enc, bidx = encrypt_contact("email", "secret.person@test.local")
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=%s, email_bidx=%s WHERE employee_id=%s", (enc, bidx, emp_id))
        cur.close()

        assert _raw(db_engine, emp_id, "email")[0].startswith("hz2:")
        db = get_db_connection()
        try:
            c = db.cursor()
            c.execute("SELECT email FROM employees WHERE employee_id=%s", (emp_id,))
            assert c.fetchone()[0] == "secret.person@test.local"
            raw_text(c)
            c.execute("SELECT email FROM employees WHERE employee_id=%s", (emp_id,))
            assert c.fetchone()[0].startswith("hz2:")
            c.close()
        finally:
            db.close()


class TestMigrationSweep:
    def test_encrypts_existing_plaintext_rows(self, seed_employee, db_engine):
        from migrations.column_migrations import encrypt_plaintext_contacts
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email='plain@test.local', phone='+91 98765 43210', "
                    "email_bidx=NULL, phone_bidx=NULL WHERE employee_id=%s", (emp_id,))
        cur.close()

        db = get_db_connection()
        try:
            c = db.cursor()
            assert encrypt_plaintext_contacts(c) >= 1
            assert encrypt_plaintext_contacts(c) == 0          # idempotent
            c.close()
        finally:
            db.close()

        email, phone, ebidx, pbidx = _raw(db_engine, emp_id, "email", "phone", "email_bidx", "phone_bidx")
        assert email.startswith("hz2:") and phone.startswith("hz2:")
        assert ebidx in contact_lookup_keys("email", "PLAIN@test.local")
        assert pbidx in contact_lookup_keys("phone", "9876543210")


class TestWritePaths:
    def test_profile_update_encrypts_phone(self, client, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        _employee_session(client, emp_id)
        resp = client.post("/update_my_profile", data={"phone": "9123456780"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        phone, pbidx = _raw(db_engine, emp_id, "phone", "phone_bidx")
        assert phone.startswith("hz2:") and "9123456780" not in phone
        assert pbidx in contact_lookup_keys("phone", "+91 91234 56780")


class TestLookups:
    def _admin(self, client, seed_admin):
        resp = client.post("/login", data={"identifier": seed_admin["username"],
                                           "password": seed_admin["password"]}, follow_redirects=True)
        assert resp.status_code == 200

    def test_admin_search_finds_encrypted_email_and_phone_exactly(self, client, seed_admin, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        e, eb = encrypt_contact("email", "findme@test.local")
        p, pb = encrypt_contact("phone", "9988776655")
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=%s, email_bidx=%s, phone=%s, phone_bidx=%s WHERE employee_id=%s",
                    (e, eb, p, pb, emp_id))
        cur.close()
        self._admin(client, seed_admin)
        for q in ("FindMe@test.local", "+91 99887 76655"):
            results = client.get("/api/admin/search", query_string={"q": q}).get_json()["results"]
            assert any(r.get("url", "").endswith(emp_id) for r in results), q


class TestKeyRotation:
    def test_rotation_reencrypts_and_reindexes(self, seed_employee, db_engine, monkeypatch, capsys):
        import importlib.util
        import os
        from cryptography.fernet import Fernet
        import database
        from utils.pii_crypto import PiiCipher
        emp_id = seed_employee["employee_id"]
        old = os.environ["ENCRYPTION_KEY"]
        e, eb = encrypt_contact("email", "rotate.me@test.local")
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=%s, email_bidx=%s WHERE employee_id=%s", (e, eb, emp_id))
        cur.close()

        new = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", new)
        monkeypatch.setenv("ENCRYPTION_KEY_PREVIOUS", old)
        monkeypatch.setattr(database, "_hz_cipher", None)      # fresh process would re-read the env
        spec = importlib.util.spec_from_file_location(
            "rot", os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "rotate_encryption_key.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.main(["--schema", "public"]) == 0
        assert "reindexed=" in capsys.readouterr().out

        email, ebidx = _raw(db_engine, emp_id, "email", "email_bidx")
        c_new = PiiCipher([new])
        assert c_new.decrypt(email) == "rotate.me@test.local"
        assert ebidx == c_new.blind_index("email", "rotate.me@test.local")
        monkeypatch.setattr(database, "_hz_cipher", None)
