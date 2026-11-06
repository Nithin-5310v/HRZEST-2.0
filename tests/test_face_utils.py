"""Tests for utils/face_utils.py — the mtime-keyed face-encoding cache.

face_recognition.load_image_file/face_encodings are monkeypatched
throughout: this module's own logic under test is the caching/invalidation
behavior, not the third-party face-detection pipeline itself (that's
exercised for real elsewhere via actual check-in flows, not unit tests)."""
import os
import pytest
import utils.face_utils as face_utils


@pytest.fixture(autouse=True)
def _clear_cache():
    face_utils._face_enc_cache.clear()
    yield
    face_utils._face_enc_cache.clear()


@pytest.fixture
def face_file(tmp_path):
    p = tmp_path / "emp1.jpg"
    p.write_bytes(b"fake-image-bytes")
    return str(p)


class TestGetKnownFaceEncoding:
    def test_missing_file_returns_none(self, tmp_path):
        result = face_utils._get_known_face_encoding("EMP1", str(tmp_path / "does_not_exist.jpg"))
        assert result is None

    def test_computes_and_caches_encoding(self, face_file, monkeypatch):
        calls = []

        def _fake_load(path):
            calls.append(path)
            return "fake-image-array"

        monkeypatch.setattr(face_utils.face_recognition, "load_image_file", _fake_load)
        monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: ["encoding-1"])

        result = face_utils._get_known_face_encoding("EMP1", face_file)
        assert result == "encoding-1"
        assert len(calls) == 1
        assert face_file in face_utils._face_enc_cache

    def test_second_call_with_unchanged_mtime_uses_cache(self, face_file, monkeypatch):
        calls = []
        monkeypatch.setattr(face_utils.face_recognition, "load_image_file",
                            lambda path: calls.append(path) or "img")
        monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: ["encoding-1"])

        first = face_utils._get_known_face_encoding("EMP1", face_file)
        second = face_utils._get_known_face_encoding("EMP1", face_file)

        assert first == second == "encoding-1"
        assert len(calls) == 1  # only computed once — second call hit the cache

    def test_file_change_invalidates_cache(self, face_file, monkeypatch):
        encodings_returned = iter(["encoding-1", "encoding-2"])
        monkeypatch.setattr(face_utils.face_recognition, "load_image_file", lambda path: "img")
        monkeypatch.setattr(face_utils.face_recognition, "face_encodings",
                            lambda img: [next(encodings_returned)])

        first = face_utils._get_known_face_encoding("EMP1", face_file)
        assert first == "encoding-1"

        # Bump the mtime forward to simulate a re-uploaded photo.
        newer = os.path.getmtime(face_file) + 5
        os.utime(face_file, (newer, newer))

        second = face_utils._get_known_face_encoding("EMP1", face_file)
        assert second == "encoding-2"

    def test_no_face_detected_returns_none_and_caches_none(self, face_file, monkeypatch):
        calls = []
        monkeypatch.setattr(face_utils.face_recognition, "load_image_file",
                            lambda path: calls.append(path) or "img")
        monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: [])

        result = face_utils._get_known_face_encoding("EMP1", face_file)
        assert result is None

        # A second call with the same mtime should still hit the cache
        # (not recompute) even though the cached value is None.
        result2 = face_utils._get_known_face_encoding("EMP1", face_file)
        assert result2 is None
        assert len(calls) == 1

    def test_different_employees_cached_independently(self, tmp_path, monkeypatch):
        f1 = tmp_path / "emp1.jpg"
        f2 = tmp_path / "emp2.jpg"
        f1.write_bytes(b"a")
        f2.write_bytes(b"b")
        # load_image_file receives a decrypted BytesIO, not a path. Key the fake
        # encoding on the bytes: the stream's repr holds a memory address CPython
        # can reuse once the first stream is freed, so two encodings could collide.
        monkeypatch.setattr(face_utils.face_recognition, "load_image_file", lambda f: f.read())
        monkeypatch.setattr(face_utils.face_recognition, "face_encodings",
                            lambda img: [f"enc-for-{img}"])

        r1 = face_utils._get_known_face_encoding("EMP1", str(f1))
        r2 = face_utils._get_known_face_encoding("EMP2", str(f2))
        assert r1 != r2
        assert str(f1) in face_utils._face_enc_cache
        assert str(f2) in face_utils._face_enc_cache

    def test_same_employee_id_in_two_companies_cached_separately(self, tmp_path, monkeypatch):
        """Keyed by file, not employee ID: two companies' EMP1 have different
        reference photos and must never share one cached encoding."""
        a = tmp_path / "a" / "EMP1.jpg"
        b = tmp_path / "b" / "EMP1.jpg"
        for f, data in ((a, b"a"), (b, b"b")):
            f.parent.mkdir()
            f.write_bytes(data)
        monkeypatch.setattr(face_utils.face_recognition, "load_image_file", lambda f: f.read())
        monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: [f"enc-for-{img}"])
        assert face_utils._get_known_face_encoding("EMP1", str(a)) != face_utils._get_known_face_encoding("EMP1", str(b))


class TestModuleAvailabilityFlag:
    def test_face_recognition_available_flag_matches_import_state(self):
        # In this environment face_recognition imports successfully, so the
        # module-level flag should reflect that.
        assert face_utils._face_recognition_available is True
        assert face_utils.face_recognition is not None
