# -*- coding: utf-8 -*-
"""Every error says what went wrong and why -- never HTML or code (27 Nov 2026).

Attendance check-ins showed "Unexpected token '<', "<!DOCTYPE "... is not
valid JSON" whenever the server, a proxy or an expired session answered
with a web page instead of JSON, and photo uploads gave no size guidance.
Now:

- JSON error replies carry "Type: reason" text and an error code;
- every page loads static/hz-errors.js, which turns non-JSON replies,
  network drops and browser errors into plain "Type: reason" messages;
- photos of people must be readable and 200 x 200 px to 5 MB, checked on the
  server with the photo's actual size in the message (logos are exempt from
  the minimum), and the admin's own photo pickers state the rules and check
  a photo as soon as it is chosen;
- the browser, app and server use the same photo rules.
"""
import io
import os
import re

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

import blueprints.core as core
from extensions import app as flask_app
from utils.helpers import PHOTO_MIN_PX, PHOTO_RULES_HINT, _MAX_PHOTO_SIZE_MB, _validate_image_file
from tests.test_admin_self_profile import _owner, owner  # noqa: F401 (fixture)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _image(size, fmt="JPEG"):
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 110, 100)).save(buf, fmt)
    buf.seek(0)
    return buf


def _upload(buf, name="face.jpg", content_type="image/jpeg"):
    return FileStorage(stream=buf, filename=name, content_type=content_type)


def _flashes(client):
    with client.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


# ── JSON error replies ──────────────────────────────────────────────────────

class TestJsonErrorsGiveTypeAndReason:
    def test_404(self, client):
        body = client.get("/api/no-such-endpoint").get_json()
        assert body["ok"] is False and body["error"] == "page_not_found"
        assert body["msg"].startswith("Page Not Found: ") and len(body["msg"]) > len("Page Not Found: ") + 10

    def test_405_explains_itself(self, client):
        body = client.delete("/api/session/risk-stream").get_json()
        assert body["error"] == "method_not_allowed"
        assert body["msg"].startswith("Method Not Allowed: This action isn't allowed here.")

    def test_fetch_upload_to_a_page_route_gets_json_not_html(self, client):
        # The page script marks fetch() calls with X-Requested-With, so a
        # multipart upload's error comes back as JSON the page can show.
        resp = client.post("/no-such-upload", data={"photo": (io.BytesIO(b"x"), "a.jpg")},
                           content_type="multipart/form-data", headers={"X-Requested-With": "XMLHttpRequest"})
        # 403 when the CSRF check answers first, else 404 -- JSON either way.
        assert resp.status_code in (403, 404) and resp.mimetype == "application/json"
        assert "<" not in resp.get_json()["msg"]

    @pytest.fixture
    def handled_exceptions(self):
        previous = flask_app.config.get("PROPAGATE_EXCEPTIONS")
        flask_app.config["PROPAGATE_EXCEPTIONS"] = False   # let the 500 handler run, as in production
        yield
        flask_app.config["PROPAGATE_EXCEPTIONS"] = previous

    def test_500_says_why_without_details(self, client, monkeypatch, handled_exceptions):
        def _boom(sid):
            raise RuntimeError("psycopg2.errors.UndefinedTable: relation \"secret_table\" does not exist")

        monkeypatch.setattr(core, "is_session_compromised", _boom)
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["_sid"] = "clear-err-sid"
        resp = client.get("/api/session/risk-stream")
        body = resp.get_json()
        assert resp.status_code == 500 and body["error"] == "internal_server_error"
        assert body["msg"] == "Internal Server Error: An unexpected error occurred. The team has been notified."
        assert "secret_table" not in str(body) and "psycopg2" not in str(body)


# ── Photo rules on the server ───────────────────────────────────────────────

class TestPhotoValidation:
    def test_a_normal_photo_passes(self):
        assert _validate_image_file(_upload(_image((640, 480))), person_photo=True) == (True, "")

    def test_too_small_says_its_size_and_the_minimum(self):
        ok, msg = _validate_image_file(_upload(_image((150, 120))), person_photo=True)
        assert not ok
        assert msg.startswith("Photo too small: this photo is 150 × 120 pixels (")
        assert f"at least {PHOTO_MIN_PX} × {PHOTO_MIN_PX} pixels" in msg

    def test_too_large_says_its_size_and_the_limit(self):
        big = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\0" * (6 * 1024 * 1024))
        ok, msg = _validate_image_file(_upload(big), person_photo=True)
        assert not ok
        assert msg.startswith("File too large: this photo is 6.0 MB; the maximum is 5 MB.")

    def test_unreadable_photo(self):
        ok, msg = _validate_image_file(_upload(io.BytesIO(b"\xff\xd8\xff" + b"garbage" * 50)), person_photo=True)
        assert not ok and msg.startswith("Unreadable photo: ")

    def test_wrong_type_names_the_reason(self):
        ok, msg = _validate_image_file(_upload(_image((300, 300), "GIF"), "face.gif", "image/gif"), person_photo=True)
        assert not ok and msg.startswith("File type not allowed: .gif files can't be used.")

    def test_logos_may_be_small(self):
        assert _validate_image_file(_upload(_image((64, 32), "PNG"), "logo.png", "image/png")) == (True, "")

    def test_stream_is_rewound_for_saving(self):
        f = _upload(_image((640, 480)))
        _validate_image_file(f, person_photo=True)
        assert f.stream.tell() == 0


class TestPhotoRoutes:
    def test_admin_face_photo_too_small_is_refused_with_the_reason(self, client, db_engine, owner):  # noqa: F811
        _owner(client, owner)
        resp = client.post("/profile/my_employee", data={"work_mode": "office", "face": (_image((120, 90)), "me.jpg")},
                           content_type="multipart/form-data")
        assert resp.status_code == 302
        messages = _flashes(client)
        assert any(m.startswith("Photo too small: this photo is 120 × 90 pixels") for m in messages), messages

    def test_employee_photo_update_too_small(self, client, seed_admin, seed_employee):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        client.post(f"/update_employee_photo/{seed_employee['employee_id']}",
                    data={"face": (_image((100, 100)), "face.jpg")}, content_type="multipart/form-data",
                    headers={"Referer": "http://localhost/employees"})
        assert any(m.startswith("Photo too small: ") for m in _flashes(client))

    @pytest.mark.parametrize("code, text", [
        ("too_small", f"Photo too small: it must be at least {PHOTO_MIN_PX} &times; {PHOTO_MIN_PX} pixels"),
        ("too_large", f"File too large: the photo is over {_MAX_PHOTO_SIZE_MB} MB"),
        ("unreadable", "Unreadable photo: the file is damaged"),
        ("no_face", "No face detected: the photo must show your face clearly"),
    ])
    def test_portal_banner_says_why(self, client, seed_employee, code, text):
        with client.session_transaction() as sess:
            sess["employee_logged_in"] = True
            sess["employee_id"] = seed_employee["employee_id"]
        html = client.get(f"/employee_portal?photo_error={code}").get_data(as_text=True)
        assert text in html


class TestAdminPhotoPickers:
    def test_profile_and_face_pickers_state_the_rules_and_check_in_the_browser(self, client, db_engine,
                                                                               owner):  # noqa: F811
        _owner(client, owner)
        html = client.get("/profile").get_data(as_text=True)
        assert "js/photo-check.js" in html
        assert 'id="p-photo-input"' in html and 'id="own-face"' in html
        assert len(re.findall(r"data-photo-check(?=[\s>])", html)) == 2
        assert html.count(PHOTO_RULES_HINT) == 2   # under the profile photo and the face photo


# ── Every page gets the error helper ────────────────────────────────────────

def test_pages_load_hz_errors_before_hz_actions(client):
    html = client.get("/login").get_data(as_text=True)
    assert "/static/hz-errors.js" in html
    assert html.index("/static/hz-errors.js") < html.index("/static/hz-actions.js")


def test_hz_errors_never_writes_html():
    src = open(os.path.join(ROOT, "static", "hz-errors.js"), encoding="utf-8").read()
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src
    for status in ("400", "401", "403", "404", "413", "429", "500", "502", "503", "504"):
        assert re.search(rf"\b{status}: ['\"][A-Z][^:'\"]{{2,40}}: ", src), status


# ── One set of photo rules ──────────────────────────────────────────────────

def _number(path, pattern):
    src = open(os.path.join(ROOT, *path.split("/")), encoding="utf-8").read()
    match = re.search(pattern, src)
    assert match, (path, pattern)
    return int(match.group(1))


def test_browser_and_app_use_the_servers_photo_rules():
    assert _number("static/js/photo-check.js", r"var MAX_MB = (\d+);") == _MAX_PHOTO_SIZE_MB
    assert _number("static/js/photo-check.js", r"var MIN_PX = (\d+);") == PHOTO_MIN_PX
    assert _number("mobile/src/utils/photoRules.js", r"PHOTO_MAX_MB = (\d+);") == _MAX_PHOTO_SIZE_MB
    assert _number("mobile/src/utils/photoRules.js", r"PHOTO_MIN_PX = (\d+);") == PHOTO_MIN_PX
