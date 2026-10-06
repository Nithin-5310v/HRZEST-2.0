# -*- coding: utf-8 -*-
"""Face-recognition bootstrap + known-encoding cache.

Extracted from app.py -- used by both the employees blueprint (photo
registration/update, which validates a face is detectable in the uploaded
photo) and the attendance blueprint (check-in face-match verification), so
neither can own it exclusively without the other importing across a
blueprint boundary.
"""
import os
import sys
import types
import datetime


def _import_face_recognition():
    """face_recognition_models 0.3.0 (its latest release) does
    `from pkg_resources import resource_filename` at import time, and
    setuptools>=82 no longer ships pkg_resources -- but setuptools<83 is
    vulnerable (PYSEC-2026-3447). Rather than pin the vulnerable version,
    provide just that one function (backed by importlib.resources) for the
    duration of this import only, then remove it again so no other library
    ever sees a partial pkg_resources. No-op when a real pkg_resources is
    installed."""
    injected = False
    try:
        import pkg_resources  # noqa: F401
    except ImportError:
        shim = types.ModuleType("pkg_resources")

        def resource_filename(package, resource):
            from importlib.resources import files
            return str(files(package).joinpath(resource))

        shim.resource_filename = resource_filename
        sys.modules["pkg_resources"] = shim
        injected = True
    try:
        import face_recognition as _fr
        return _fr
    finally:
        if injected:
            sys.modules.pop("pkg_resources", None)


try:
    face_recognition = _import_face_recognition()
    _face_recognition_available = True
except Exception as _fr_err:
    face_recognition = None
    _face_recognition_available = False
    print(f"⚠  face_recognition unavailable ({_fr_err}). Face features disabled.")

# Cache known face encodings by (employee_id, file_mtime) to avoid recomputing on every punch
_face_enc_cache: dict = {}


def _get_known_face_encoding(emp_id: str, face_path: str):
    """Return the cached face encoding for an employee, recomputing only when the file changes."""
    try:
        mtime = os.path.getmtime(face_path)
    except OSError:
        return None
    cached = _face_enc_cache.get(emp_id)
    if cached and cached[0] == mtime:
        return cached[1]
    img = face_recognition.load_image_file(face_path)
    encs = face_recognition.face_encodings(img)
    enc = encs[0] if encs else None
    _face_enc_cache[emp_id] = (mtime, enc)
    return enc


def verify_uploaded_face(emp_id: str, registered_face_path: str, face_photo_storage, save_dir: str):
    """Compare an uploaded photo (werkzeug FileStorage) against an employee's
    on-file registered face. Returns (ok, error_msg). The one place in this
    app that turns a claimed employee_id into a real, server-verified proof
    that the person in front of the camera is actually that employee --
    used both by attendance check-in and by the WebAuthn kiosk-enrollment
    identity gate, since a client-supplied employee_id string alone (QR scan
    content, typed input) proves nothing on its own."""
    if not _face_recognition_available:
        return False, "Face recognition is currently unavailable on this server. Contact your admin."
    if not registered_face_path or not os.path.exists(registered_face_path):
        return False, "No registered face found. Please contact your admin."
    try:
        from PIL import Image as _PILImage
        os.makedirs(save_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        face_path = os.path.join(save_dir, f"{emp_id}_{ts}.jpg")
        img = _PILImage.open(face_photo_storage.stream).convert("RGB")
        img.save(face_path, "JPEG", quality=80)

        known_enc = _get_known_face_encoding(emp_id, registered_face_path)
        test_img_data = face_recognition.load_image_file(face_path)
        test_encs = face_recognition.face_encodings(test_img_data)
        if known_enc is None or not test_encs:
            return False, "Face not detected clearly. Please retake the photo."
        if not face_recognition.compare_faces([known_enc], test_encs[0], tolerance=0.5)[0]:
            return False, "Face did not match. Please try again."
        return True, None
    except Exception:
        return False, "Face verification failed. Please retake the photo."
