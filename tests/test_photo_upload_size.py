# -*- coding: utf-8 -*-
"""Photo uploads that are too large or awkward fail with a message, not an
error page:

- an upload over MAX_CONTENT_LENGTH goes back to the page it came from with
  a flash message (JSON for API calls) instead of "Request Entity Too Large";
- face detection runs on an upright copy no bigger than DETECTION_MAX_SIDE,
  so a full-size phone photo doesn't time out the request;
- the photo pickers state the size rules and load static/js/photo-check.js,
  which checks a chosen photo in the browser before it is uploaded.
"""
import io

from PIL import Image

from extensions import app as flask_app
from utils.face_utils import DETECTION_MAX_SIDE, detection_array


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


def _jpeg(size, exif_orientation=None):
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=(50, 60, 70))
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


class TestTooLargeUpload:
    def test_goes_back_to_the_page_with_a_message(self, client, seed_admin, monkeypatch):
        monkeypatch.setitem(flask_app.config, "MAX_CONTENT_LENGTH", 1024 * 1024)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/update_employee_photo/EMP1",
                           data={"face": (io.BytesIO(b"x" * (2 * 1024 * 1024)), "big.jpg")},
                           content_type="multipart/form-data",
                           headers={"Referer": "http://localhost/employees"})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/employees")
        with client.session_transaction() as sess:
            messages = [m for _, m in sess.get("_flashes", [])]
        assert any("too large to upload (the limit is 1 MB)" in m for m in messages)

    def test_never_redirects_off_site(self, client, seed_admin, monkeypatch):
        monkeypatch.setitem(flask_app.config, "MAX_CONTENT_LENGTH", 1024 * 1024)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/update_employee_photo/EMP1",
                           data={"face": (io.BytesIO(b"x" * (2 * 1024 * 1024)), "big.jpg")},
                           content_type="multipart/form-data",
                           headers={"Referer": "https://evil.example/phish"})
        assert resp.status_code == 302 and "evil.example" not in resp.headers["Location"]

    def test_api_callers_get_json(self, client, monkeypatch):
        monkeypatch.setitem(flask_app.config, "MAX_CONTENT_LENGTH", 1024)
        resp = client.post("/api/employees", data=b"x" * 4096, content_type="application/octet-stream")
        assert resp.status_code == 413 and "too large" in resp.get_json()["msg"]


class TestDetectionArray:
    def test_large_photo_is_scaled_down(self):
        arr = detection_array(_jpeg((4000, 3000)))
        assert arr.shape == (DETECTION_MAX_SIDE * 3 // 4, DETECTION_MAX_SIDE, 3)

    def test_small_photo_is_left_alone(self):
        assert detection_array(_jpeg((640, 480))).shape == (480, 640, 3)

    def test_sideways_phone_photo_is_turned_upright(self):
        # EXIF orientation 6: stored landscape, shown rotated 90 degrees.
        assert detection_array(_jpeg((800, 600), exif_orientation=6)).shape == (800, 600, 3)

    def test_accepts_a_pil_image(self):
        assert detection_array(Image.new("RGB", (2000, 2000))).shape == (DETECTION_MAX_SIDE, DETECTION_MAX_SIDE, 3)


class TestPickersStateTheRules:
    def test_employees_page(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        html = client.get("/employees").get_data(as_text=True)
        assert "js/photo-check.js" in html
        assert html.index("js/photo-check.js") < html.index("js/employees.js")
        # Update Photo modal and Add Employee form
        assert html.count("up to 5 MB &middot; at least 200 &times; 200 px") == 2

    def test_employee_portal(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_logged_in"] = True
            sess["employee_id"] = seed_employee["employee_id"]
        html = client.get("/employee_portal").get_data(as_text=True)
        assert "js/photo-check.js" in html
        assert "JPG or PNG &middot; up to 5 MB" in html

    def test_portal_explains_a_rejected_format(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_logged_in"] = True
            sess["employee_id"] = seed_employee["employee_id"]
        html = client.get("/employee_portal?photo_error=bad_format").get_data(as_text=True)
        assert "Please choose a JPG or PNG photo of up to 5 MB." in html
