"""utils/storage.py biometric image helpers: face photos / face logs are
AES-256-GCM on disk, decrypted in memory, and legacy plaintext files keep
working. Pure filesystem tests -- no database."""
import io

import pytest
from PIL import Image

from utils import storage


def _jpeg_bytes(color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def img():
    return Image.new("RGB", (32, 24), (10, 120, 200))


def test_save_image_is_ciphertext_on_disk(tmp_path, img):
    p = str(tmp_path / "EMP001.jpg")
    storage.save_image(img, p, quality=90)
    raw = open(p, "rb").read()
    assert raw.startswith(b"HZF2") and b"JFIF" not in raw
    with pytest.raises(Exception):
        Image.open(p)                       # unreadable without the key


def test_open_image_and_load_face_image_round_trip(tmp_path, img):
    p = str(tmp_path / "EMP001.jpg")
    storage.save_image(img, p)
    back = storage.open_image(p)
    assert back.size == (32, 24)
    arr = storage.load_face_image(p)
    assert arr.shape == (24, 32, 3)


def test_legacy_plaintext_photo_still_reads(tmp_path):
    p = tmp_path / "OLD.jpg"
    p.write_bytes(_jpeg_bytes())
    assert storage.open_image(str(p)).size == (32, 24)
    assert storage.read_file(str(p)) == p.read_bytes()


def test_save_upload_encrypts(tmp_path):
    from werkzeug.datastructures import FileStorage
    data = _jpeg_bytes()
    p = str(tmp_path / "up.jpg")
    storage.save_upload(FileStorage(stream=io.BytesIO(data), filename="up.jpg"), p)
    assert open(p, "rb").read()[:4] == b"HZF2"
    assert storage.read_file(p) == data


def test_send_image_serves_decrypted_bytes(tmp_path, img):
    from extensions import app
    p = str(tmp_path / "s.jpg")
    storage.save_image(img, p)
    with app.test_request_context():
        resp = storage.send_image(p)
        resp.direct_passthrough = False
        assert resp.mimetype == "image/jpeg"
        assert resp.get_data()[:2] == b"\xff\xd8"      # JPEG SOI marker, not ciphertext
