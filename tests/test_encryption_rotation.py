"""Encryption-key rotation: ENCRYPTION_KEY encrypts, ENCRYPTION_KEY_PREVIOUS
only decrypts (utils/helpers.py), and scripts/rotate_encryption_key.py moves
stored values onto the new key.

Regression coverage for the audit finding that PII encryption used a single
Fernet key with no rotation path -- a key leak or a policy-mandated rotation
meant re-encrypting by hand or losing the data.
"""
import importlib.util
import os
import subprocess
import sys

import pytest
from cryptography.fernet import Fernet, InvalidToken

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "rotate_encryption_key", os.path.join(REPO_ROOT, "scripts", "rotate_encryption_key.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHelpersAcceptPreviousKeys:
    CODE = """
import sys, os
sys.path.insert(0, {root!r})
from cryptography.fernet import Fernet, InvalidToken
from utils.helpers import encrypt_pii, decrypt_pii

old = Fernet(os.environ["OLD_KEY"].encode())
legacy = old.encrypt(b"ABCDE1234F").decode()
assert decrypt_pii(legacy) == "ABCDE1234F", "previous key must still decrypt"

fresh = encrypt_pii("ABCDE1234F")
assert fresh != legacy
Fernet(os.environ["ENCRYPTION_KEY"].encode()).decrypt(fresh.encode())
try:
    old.decrypt(fresh.encode())
except InvalidToken:
    pass
else:
    raise SystemExit("new data must NOT be readable with the old key")
print("rotation-ok")
"""

    def test_new_key_encrypts_old_key_still_decrypts(self):
        new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        env = dict(os.environ, ENCRYPTION_KEY=new, ENCRYPTION_KEY_PREVIOUS=old, OLD_KEY=old,
                   APP_ENV="development", SECRET_KEY="x" * 32)
        out = subprocess.run([sys.executable, "-c", self.CODE.format(root=REPO_ROOT)],
                             cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "rotation-ok" in out.stdout

    def test_without_previous_key_old_data_is_unreadable_not_crashing(self):
        new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        code = (
            f"import sys; sys.path.insert(0, {REPO_ROOT!r});"
            "import os; from cryptography.fernet import Fernet;"
            "from utils.helpers import decrypt_pii;"
            "tok = Fernet(os.environ['OLD_KEY'].encode()).encrypt(b'secret').decode();"
            "print(decrypt_pii(tok))"
        )
        env = dict(os.environ, ENCRYPTION_KEY=new, OLD_KEY=old, APP_ENV="development", SECRET_KEY="x" * 32)
        env.pop("ENCRYPTION_KEY_PREVIOUS", None)
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().splitlines()[-1] == "[unable to decrypt]"


class TestRotationScript:
    def _put(self, db_engine, emp_id, token):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO employees (employee_id, name, pan_number) VALUES (%s,'Rot Test',%s) "
                    "ON CONFLICT (employee_id) DO UPDATE SET pan_number=EXCLUDED.pan_number", (emp_id, token))
        cur.close()

    def _get(self, db_engine, emp_id):
        cur = db_engine.cursor()
        cur.execute("SELECT pan_number FROM employees WHERE employee_id=%s", (emp_id,))
        v = cur.fetchone()[0]
        cur.close()
        return v

    def test_rotates_values_onto_the_new_key(self, db_engine, monkeypatch, capsys):
        script = _load_script()
        old = Fernet.generate_key()
        new = Fernet.generate_key()
        current = os.environ["ENCRYPTION_KEY"].encode()      # the suite's own key (other rows may use it)
        self._put(db_engine, "ROT001", Fernet(old).encrypt(b"ABCDE1234F").decode())
        monkeypatch.setenv("ENCRYPTION_KEY", new.decode())
        monkeypatch.setenv("ENCRYPTION_KEY_PREVIOUS", f"{old.decode()},{current.decode()}")

        # dry run changes nothing
        assert script.main(["--dry-run", "--schema", "public"]) == 0
        assert "would rotate" in capsys.readouterr().out
        with pytest.raises(InvalidToken):
            Fernet(new).decrypt(self._get(db_engine, "ROT001").encode())

        assert script.main(["--schema", "public"]) == 0
        stored = self._get(db_engine, "ROT001")
        assert Fernet(new).decrypt(stored.encode()) == b"ABCDE1234F"
        with pytest.raises(InvalidToken):
            Fernet(old).decrypt(stored.encode())

        # idempotent: a second pass finds everything already current
        assert script.main(["--schema", "public"]) == 0
        assert "rotated=0" in capsys.readouterr().out

    def test_reports_values_it_cannot_decrypt(self, db_engine, monkeypatch, capsys):
        script = _load_script()
        stranger = Fernet.generate_key()
        new = Fernet.generate_key()
        current = os.environ["ENCRYPTION_KEY"].encode()
        self._put(db_engine, "ROT002", Fernet(stranger).encrypt(b"x").decode())
        monkeypatch.setenv("ENCRYPTION_KEY", new.decode())
        monkeypatch.setenv("ENCRYPTION_KEY_PREVIOUS", current.decode())   # but not `stranger`
        assert script.main(["--schema", "public"]) == 2
        assert "undecryptable=" in capsys.readouterr().out

    def test_refuses_to_run_without_a_key(self, monkeypatch):
        script = _load_script()
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        with pytest.raises(SystemExit):
            script.main(["--dry-run"])
