"""utils/pii_crypto.py: AES-256-GCM field encryption, with legacy Fernet
(AES-128-CBC) values still readable. Pure unit tests -- no database."""
import pytest
from cryptography.fernet import Fernet

from utils.pii_crypto import PiiCipher, DecryptionError, looks_encrypted, PREFIX


@pytest.fixture
def key():
    return Fernet.generate_key().decode()


def test_round_trip_and_format(key):
    c = PiiCipher([key])
    tok = c.encrypt("ABCDE1234F")
    assert tok.startswith(PREFIX)
    assert "ABCDE1234F" not in tok
    assert c.decrypt(tok) == "ABCDE1234F"
    assert c.is_current(tok)


def test_unicode_and_empty_string(key):
    c = PiiCipher([key])
    for v in ("", "ரஞ்சித் குமார்", "a" * 5000):
        assert c.decrypt(c.encrypt(v)) == v


def test_nonce_is_random_per_value(key):
    c = PiiCipher([key])
    assert c.encrypt("same") != c.encrypt("same")


def test_tampering_is_detected(key):
    c = PiiCipher([key])
    tok = c.encrypt("1234567890")
    flipped = tok[:-3] + ("A" if tok[-3] != "A" else "B") + tok[-2:]
    with pytest.raises(DecryptionError):
        c.decrypt(flipped)


def test_wrong_key_fails(key):
    tok = PiiCipher([key]).encrypt("x")
    with pytest.raises(DecryptionError):
        PiiCipher([Fernet.generate_key().decode()]).decrypt(tok)


def test_legacy_fernet_still_decrypts_and_rotates_to_v2(key):
    legacy = Fernet(key.encode()).encrypt(b"Female").decode()
    c = PiiCipher([key])
    assert c.decrypt(legacy) == "Female"
    assert not c.is_current(legacy)
    upgraded = c.rotate(legacy)
    assert upgraded.startswith(PREFIX) and c.decrypt(upgraded) == "Female"


def test_previous_key_decrypts_but_primary_encrypts(key):
    new = Fernet.generate_key().decode()
    old_tok = PiiCipher([key]).encrypt("v")
    c = PiiCipher([new, key])
    assert c.decrypt(old_tok) == "v"
    assert not c.is_current(old_tok)
    assert c.is_current(c.rotate(old_tok))


def test_plaintext_is_not_ciphertext(key):
    c = PiiCipher([key])
    for v in ("Female", "1988-03-10", "hz2:", "gAAAAA-not-really"):
        assert not looks_encrypted(v)
        with pytest.raises(ValueError):
            c.decrypt(v)


def test_malformed_key_rejected():
    with pytest.raises(Exception):
        PiiCipher(["not-a-key"])
    with pytest.raises(ValueError):
        PiiCipher([""])


# ── private upload files ──────────────────────────────────────────────────
def test_bytes_round_trip_and_legacy_passthrough(key):
    from utils.pii_crypto import FILE_MAGIC
    c = PiiCipher([key])
    data = b"%PDF-1.7 \x00\xff binary"
    blob = c.encrypt_bytes(data)
    assert blob.startswith(FILE_MAGIC) and data not in blob
    assert c.decrypt_bytes(blob) == data and c.file_is_current(blob)
    assert c.decrypt_bytes(data) == data          # legacy plaintext file
    with pytest.raises(DecryptionError):
        c.decrypt_bytes(blob[:-1] + bytes([blob[-1] ^ 1]))


def test_storage_encrypts_on_disk_and_open_private_decrypts(tmp_path):
    import io as _io
    from werkzeug.datastructures import FileStorage
    from utils.storage import save_private, open_private
    data = b"\x89PNG secret aadhaar scan"
    ref, err = save_private(str(tmp_path), FileStorage(stream=_io.BytesIO(data), filename="a.png"), "docs/a.png")
    assert err is None
    with open(ref, "rb") as f:
        assert data not in f.read(), "file on disk must be ciphertext"
    assert open_private(ref) == data
    assert [p.name for p in (tmp_path / "private_uploads" / "docs").iterdir()] == ["a.png"]   # no temp leftovers


def test_encrypt_private_uploads_script(tmp_path, capsys):
    import importlib.util
    import os as _os
    from utils.storage import open_private
    root = tmp_path / "private_uploads" / "x"
    root.mkdir(parents=True)
    (root / "old.pdf").write_bytes(b"legacy plaintext")
    spec = importlib.util.spec_from_file_location(
        "enc_uploads", _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "scripts", "encrypt_private_uploads.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--root", str(tmp_path / "private_uploads")]) == 0
    assert b"legacy plaintext" not in (root / "old.pdf").read_bytes()
    assert open_private(str(root / "old.pdf")) == b"legacy plaintext"
    assert mod.main(["--root", str(tmp_path / "private_uploads")]) == 0
    assert "encrypted=0" in capsys.readouterr().out.splitlines()[-1]
