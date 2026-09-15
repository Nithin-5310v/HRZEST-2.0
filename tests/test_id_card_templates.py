"""Tests for per-company logo upload and custom ID card templates:
- blueprints/admin_views.py's add_company/edit_company logo handling and the
  new id_card_template upload/editor/save_positions/reset routes
- blueprints/employees.py's _build_id_card_buf dispatch between the default
  generated design and an admin-uploaded custom template
- blueprints/employee_portal.py's /my_id_card producing identical output to
  the admin download route (de-duplication regression guard)
"""
import io
import os
import glob
import json

import pytest
from PIL import Image

from extensions import app as flask_app


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _png_bytes(size=(120, 120), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _cleanup_company_static_files(cid):
    root = flask_app.root_path
    for pattern in (f"static/company_logos/co_{cid}_*", f"static/id_card_templates/co_{cid}_*"):
        for path in glob.glob(os.path.join(root, pattern)):
            try:
                os.remove(path)
            except OSError:
                pass


@pytest.fixture
def temp_company(db_engine):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO companies (name, code) VALUES ('IDC Test Co', 'IDC') RETURNING id")
    cid = cur.fetchone()[0]
    try:
        yield cid
    finally:
        cur.execute("DELETE FROM id_card_templates WHERE company_id=%s", (cid,))
        cur.execute("DELETE FROM company_feature_settings WHERE company_id=%s", (cid,))
        cur.execute("DELETE FROM employees WHERE company_id=%s", (cid,))
        cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
        cur.close()
        _cleanup_company_static_files(cid)


@pytest.fixture
def company_employee(db_engine, temp_company):
    """A real employee assigned to temp_company, with cleanup of its dataset/qrcode files."""
    from utils.auth import generate_password_hash
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO employees (employee_id, name, email, password, force_pin_change, company_id, "
        "blood_group, phone, date_of_joining) "
        "VALUES (%s,%s,%s,%s,0,%s,%s,%s,%s)",
        ("IDC001", "Card Holder", "card@test.local", generate_password_hash("EmpPass@1"),
         temp_company, "O+", "9999999999", "2022-03-15"),
    )
    cur.close()
    yield {"employee_id": "IDC001", "password": "EmpPass@1"}
    cur = db_engine.cursor()
    cur.execute("DELETE FROM employees WHERE employee_id='IDC001'")
    cur.close()
    jpg = os.path.join(flask_app.config["UPLOAD_FOLDER"], "IDC001.jpg")
    qr = os.path.join(flask_app.root_path, "private_uploads", "qrcodes", "IDC001.png")
    if os.path.exists(jpg):
        os.remove(jpg)
    if os.path.exists(qr):
        os.remove(qr)


class TestCompanyLogoUpload:
    def test_add_company_with_logo(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/companies/add", data={
            "name": "Logo Co", "redirect_to": "settings",
            "logo": (io.BytesIO(_png_bytes()), "logo.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id, logo_path FROM companies WHERE name='Logo Co'")
        row = cur.fetchone()
        cur.close()
        assert row is not None
        cid, logo_path = row
        try:
            assert logo_path and logo_path.startswith("company_logos/")
            assert os.path.exists(os.path.join(flask_app.root_path, "static", logo_path))
        finally:
            db_engine.cursor().execute("DELETE FROM companies WHERE id=%s", (cid,))
            _cleanup_company_static_files(cid)

    def test_add_company_rejects_invalid_logo(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/companies/add", data={
            "name": "Bad Logo Co", "redirect_to": "settings",
            "logo": (io.BytesIO(b"not-an-image"), "logo.txt"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM companies WHERE name='Bad Logo Co'")
        row = cur.fetchone()
        cur.close()
        assert row is None  # rejected before the company was even created

    def test_edit_company_replaces_logo(self, client, seed_admin, db_engine, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/edit", data={
            "name": "IDC Test Co", "redirect_to": "settings",
            "logo": (io.BytesIO(_png_bytes(color=(10, 200, 10))), "logo2.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT logo_path FROM companies WHERE id=%s", (temp_company,))
        logo_path = cur.fetchone()[0]
        cur.close()
        assert logo_path and os.path.exists(os.path.join(flask_app.root_path, "static", logo_path))


class TestCompanyAddress:
    def test_add_company_with_address(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/companies/add", data={
            "name": "Address Co", "redirect_to": "settings",
            "address": "221B Baker Street, London",
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id, address FROM companies WHERE name='Address Co'")
        row = cur.fetchone()
        assert row is not None
        cid, address = row
        assert address == "221B Baker Street, London"
        cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
        cur.close()

    def test_edit_company_updates_address(self, client, seed_admin, db_engine, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/edit", data={
            "name": "IDC Test Co", "redirect_to": "settings",
            "address": "42 Wallaby Way, Sydney",
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT address FROM companies WHERE id=%s", (temp_company,))
        assert cur.fetchone()[0] == "42 Wallaby Way, Sydney"
        cur.close()

    def test_add_company_with_website_email_phone(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/companies/add", data={
            "name": "Contact Co", "redirect_to": "settings",
            "website": "contactco.com", "email": "info@contactco.com", "phone": "+91 98765 43210",
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id, website, email, phone FROM companies WHERE name='Contact Co'")
        row = cur.fetchone()
        assert row is not None
        cid, website, email, phone = row
        assert website == "contactco.com"
        assert email == "info@contactco.com"
        assert phone == "+91 98765 43210"
        cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
        cur.close()

    def test_edit_company_updates_website_email_phone(self, client, seed_admin, db_engine, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/edit", data={
            "name": "IDC Test Co", "redirect_to": "settings",
            "website": "idctest.com", "email": "hello@idctest.com", "phone": "022-1234567",
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT website, email, phone FROM companies WHERE id=%s", (temp_company,))
        website, email, phone = cur.fetchone()
        assert website == "idctest.com"
        assert email == "hello@idctest.com"
        assert phone == "022-1234567"
        cur.close()


class TestIdCardTemplateUpload:
    def test_upload_requires_at_least_one_image(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/id_card_template/upload",
                           data={}, content_type="multipart/form-data", follow_redirects=True)
        assert b"Upload at least a front or back" in resp.data

    def test_upload_rejects_non_image(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/id_card_template/upload", data={
            "front_image": (io.BytesIO(b"not-an-image"), "front.txt"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert b"Front template" in resp.data

    def test_upload_front_and_back(self, client, seed_admin, db_engine, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/id_card_template/upload", data={
            "front_image": (io.BytesIO(_png_bytes((300, 500))), "front.png"),
            "back_image": (io.BytesIO(_png_bytes((300, 500))), "back.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT front_image, back_image FROM id_card_templates WHERE company_id=%s", (temp_company,))
        front_image, back_image = cur.fetchone()
        cur.close()
        assert front_image and back_image
        assert os.path.exists(os.path.join(flask_app.root_path, "static", front_image))
        assert os.path.exists(os.path.join(flask_app.root_path, "static", back_image))

    def test_editor_page_renders(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/companies/{temp_company}/id_card_template/editor")
        assert resp.status_code == 200

    def test_editor_unknown_company_404s(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/companies/999999999/id_card_template/editor")
        assert resp.status_code == 404


class TestSavePositionsAndReset:
    def _upload(self, client, cid):
        return client.post(f"/companies/{cid}/id_card_template/upload", data={
            "front_image": (io.BytesIO(_png_bytes((300, 500))), "front.png"),
        }, content_type="multipart/form-data")

    def test_save_positions_persists_valid_fields(self, client, seed_admin, db_engine, temp_company):
        _admin_session(client, seed_admin["username"])
        self._upload(client, temp_company)
        positions = {
            "name": {"side": "front", "x": 0.1, "y": 0.5, "w": 0.8, "h": 0.1, "font_size": 20},
            "photo": {"side": "front", "x": 0.3, "y": 0.1, "w": 0.4, "h": 0.3},
            "ignored_bad_key": {"side": "front", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1},
        }
        resp = client.post(f"/companies/{temp_company}/id_card_template/save_positions",
                           data={"positions_json": json.dumps(positions)}, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT fields FROM id_card_templates WHERE company_id=%s", (temp_company,))
        saved = json.loads(cur.fetchone()[0])
        cur.close()
        assert "name" in saved and saved["name"]["font_size"] == 20
        assert "photo" in saved
        assert "ignored_bad_key" not in saved

    def test_save_positions_rejects_out_of_range(self, client, seed_admin, db_engine, temp_company):
        _admin_session(client, seed_admin["username"])
        self._upload(client, temp_company)
        positions = {"name": {"side": "front", "x": 1.5, "y": 0.1, "w": 0.1, "h": 0.1}}
        client.post(f"/companies/{temp_company}/id_card_template/save_positions",
                    data={"positions_json": json.dumps(positions)})
        cur = db_engine.cursor()
        cur.execute("SELECT fields FROM id_card_templates WHERE company_id=%s", (temp_company,))
        saved = json.loads(cur.fetchone()[0])
        cur.close()
        assert "name" not in saved

    def test_reset_clears_template(self, client, seed_admin, db_engine, temp_company):
        _admin_session(client, seed_admin["username"])
        self._upload(client, temp_company)
        resp = client.post(f"/companies/{temp_company}/id_card_template/reset", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT * FROM id_card_templates WHERE company_id=%s", (temp_company,))
        assert cur.fetchone() is None
        cur.close()


class TestIdCardRendering:
    def test_default_card_no_company_branding(self, seed_employee):
        from blueprints.employees import _build_id_card_buf
        buf = _build_id_card_buf(seed_employee["employee_id"])
        assert buf is not None
        img = Image.open(buf)
        # default front (500x820) + gap(40) + default back (500x820) + label strip (24)
        assert img.size == (500 * 2 + 40, 820 + 24)

    def test_company_name_changes_default_header(self, company_employee):
        from blueprints.employees import _render_default_front
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        plain = _render_default_front("IDC001", row, company_name=None, logo_path=None)
        branded = _render_default_front("IDC001", row, company_name="Acme Corp", logo_path=None)
        assert plain.size == branded.size == (500, 820)
        assert plain.tobytes() != branded.tobytes()

    def test_default_front_shows_company_address(self, company_employee):
        from blueprints.employees import _render_default_front
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        no_addr = _render_default_front("IDC001", row, company_name="Acme Corp", logo_path=None, company_address=None)
        with_addr = _render_default_front(
            "IDC001", row, company_name="Acme Corp", logo_path=None,
            company_address="42 Wallaby Way, Sydney, Australia"
        )
        assert no_addr.size == with_addr.size == (500, 820)
        assert no_addr.tobytes() != with_addr.tobytes()

    def test_default_front_wraps_long_company_address(self, company_employee):
        from blueprints.employees import _render_default_front
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        long_address = "Suite 4500, One Very Long Corporate Plaza Tower, " * 3
        img = _render_default_front("IDC001", row, company_name="Acme Corp", logo_path=None, company_address=long_address)
        assert img.size == (500, 820)

    def test_default_back_shows_company_name_in_header(self, company_employee):
        """The back header should render the company name (like the front
        already does) instead of the generic 'ATTENDANCE MANAGEMENT SYSTEM'
        title when the employee's company has a name on file."""
        from blueprints.employees import _render_default_back
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        plain = _render_default_back("IDC001", row, company_name=None)
        branded = _render_default_back("IDC001", row, company_name="Acme Corp")
        assert plain.size == branded.size == (500, 820)
        assert plain.tobytes() != branded.tobytes()

    def test_default_front_shows_department(self, company_employee):
        from blueprints.employees import _render_default_front
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        no_dept = _render_default_front("IDC001", row, department=None)
        with_dept = _render_default_front("IDC001", row, department="Engineering")
        assert no_dept.size == with_dept.size == (500, 820)
        assert no_dept.tobytes() != with_dept.tobytes()

    def test_default_back_shows_shift_and_reporting_manager(self, company_employee):
        from blueprints.employees import _render_default_back
        row_no_shift = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        row_with_shift = row_no_shift[:6] + ("Morning Shift",) + row_no_shift[7:]
        plain = _render_default_back("IDC001", row_no_shift, manager_name=None)
        with_shift_and_manager = _render_default_back("IDC001", row_with_shift, manager_name="Priya Sharma")
        assert plain.size == with_shift_and_manager.size == (500, 820)
        assert plain.tobytes() != with_shift_and_manager.tobytes()

    def test_default_front_and_back_show_footer_contact_line(self, company_employee):
        """Company name/website/toll-free number should appear as a compact
        line just above the fixed footer bar on both sides, when on file."""
        from blueprints.employees import _render_default_front, _render_default_back
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")

        plain_front = _render_default_front("IDC001", row)
        with_contact_front = _render_default_front(
            "IDC001", row, company_name="Acme Corp",
            company_website="acmecorp.com", company_phone="1800-123-4567"
        )
        assert plain_front.size == with_contact_front.size == (500, 820)
        assert plain_front.tobytes() != with_contact_front.tobytes()

        plain_back = _render_default_back("IDC001", row)
        with_contact_back = _render_default_back(
            "IDC001", row, company_name="Acme Corp",
            company_website="acmecorp.com", company_phone="1800-123-4567"
        )
        assert plain_back.size == with_contact_back.size == (500, 820)
        assert plain_back.tobytes() != with_contact_back.tobytes()

    def test_default_back_shows_shift_timing_and_work_mode(self, company_employee):
        import datetime as _dt
        from blueprints.employees import _render_default_back
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        plain = _render_default_back("IDC001", row)
        with_timing = _render_default_back(
            "IDC001", row, shift_start=_dt.time(9, 0), shift_end=_dt.time(18, 0), work_mode="wfh"
        )
        assert plain.size == with_timing.size == (500, 820)
        assert plain.tobytes() != with_timing.tobytes()

    def test_default_back_shows_logo_and_emergency_contact(self, company_employee):
        from blueprints.employees import _render_default_back
        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        plain = _render_default_back("IDC001", row)
        with_emergency = _render_default_back(
            "IDC001", row, logo_path=None,
            emergency_name="Aisha Begum", emergency_phone="+91 90000 11223", emergency_relation="Sister"
        )
        assert plain.size == with_emergency.size == (500, 820)
        assert plain.tobytes() != with_emergency.tobytes()

        logo_rel = "company_logos/_test_back_logo.png"
        logo_abs = os.path.join(flask_app.root_path, "static", logo_rel)
        os.makedirs(os.path.dirname(logo_abs), exist_ok=True)
        Image.new("RGB", (100, 100), (10, 80, 200)).save(logo_abs, format="PNG")
        try:
            with_logo = _render_default_back("IDC001", row, logo_path=logo_rel)
            no_logo = _render_default_back("IDC001", row, logo_path=None)
            assert with_logo.tobytes() != no_logo.tobytes()
        finally:
            os.remove(logo_abs)

    def test_custom_template_dimensions_used(self, client, seed_admin, db_engine, company_employee, temp_company):
        _admin_session(client, seed_admin["username"])
        client.post(f"/companies/{temp_company}/id_card_template/upload", data={
            "front_image": (io.BytesIO(_png_bytes((300, 600))), "front.png"),
            "back_image": (io.BytesIO(_png_bytes((250, 500))), "back.png"),
        }, content_type="multipart/form-data")

        from blueprints.employees import _build_id_card_buf
        buf = _build_id_card_buf("IDC001")
        img = Image.open(buf)
        # front(300) + gap(40) + back(250) wide; tallest side (600) + label strip (24)
        assert img.size == (300 + 40 + 250, 600 + 24)

    def test_front_only_template_falls_back_to_default_back(self, client, seed_admin, db_engine,
                                                            company_employee, temp_company):
        _admin_session(client, seed_admin["username"])
        client.post(f"/companies/{temp_company}/id_card_template/upload", data={
            "front_image": (io.BytesIO(_png_bytes((320, 700))), "front.png"),
        }, content_type="multipart/form-data")

        from blueprints.employees import _build_id_card_buf
        buf = _build_id_card_buf("IDC001")
        img = Image.open(buf)
        # front(320) + gap(40) + default back(500) wide; tallest of (700, 820) + 24
        assert img.size == (320 + 40 + 500, 820 + 24)

    def test_custom_fields_render_without_error(self, client, seed_admin, db_engine, company_employee, temp_company):
        _admin_session(client, seed_admin["username"])
        client.post(f"/companies/{temp_company}/id_card_template/upload", data={
            "front_image": (io.BytesIO(_png_bytes((300, 500))), "front.png"),
            "back_image": (io.BytesIO(_png_bytes((300, 500))), "back.png"),
        }, content_type="multipart/form-data")
        positions = {
            "photo": {"side": "front", "x": 0.3, "y": 0.1, "w": 0.4, "h": 0.3},
            "name": {"side": "front", "x": 0.1, "y": 0.5, "w": 0.8, "h": 0.08, "font_size": 18},
            "employee_id": {"side": "front", "x": 0.1, "y": 0.6, "w": 0.8, "h": 0.06},
            "qr": {"side": "back", "x": 0.2, "y": 0.1, "w": 0.6, "h": 0.6},
            "blood_group": {"side": "back", "x": 0.1, "y": 0.75, "w": 0.8, "h": 0.06},
        }
        client.post(f"/companies/{temp_company}/id_card_template/save_positions",
                    data={"positions_json": json.dumps(positions)})

        from blueprints.employees import _build_id_card_buf
        buf = _build_id_card_buf("IDC001")
        assert buf is not None
        img = Image.open(buf)
        assert img.format == "PNG"

    def test_date_of_joining_and_company_address_end_to_end(self, client, seed_admin, db_engine,
                                                            company_employee, temp_company):
        _admin_session(client, seed_admin["username"])
        db_engine.cursor().execute("UPDATE companies SET address=%s WHERE id=%s",
                                   ("42 Wallaby Way, Sydney", temp_company))
        client.post(f"/companies/{temp_company}/id_card_template/upload", data={
            "front_image": (io.BytesIO(_png_bytes((300, 500))), "front.png"),
            "back_image": (io.BytesIO(_png_bytes((300, 500))), "back.png"),
        }, content_type="multipart/form-data")
        positions = {
            "date_of_joining": {"side": "front", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.1, "font_size": 14},
            "company_address": {"side": "back", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.1, "font_size": 12},
        }
        client.post(f"/companies/{temp_company}/id_card_template/save_positions",
                    data={"positions_json": json.dumps(positions)})

        from blueprints.employees import _build_id_card_buf
        buf = _build_id_card_buf("IDC001")
        assert buf is not None
        assert Image.open(buf).format == "PNG"

    def test_date_of_joining_and_address_actually_change_pixels(self, company_employee):
        """Direct unit check (not just 'renders without crashing') that a
        present vs absent value produces different pixel output."""
        import datetime as _dt
        from blueprints.employees import _render_custom_side

        rel_path = "id_card_templates/_test_doj_addr_front.png"
        abs_path = os.path.join(flask_app.root_path, "static", rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        Image.new("RGB", (300, 300), (240, 240, 240)).save(abs_path, format="PNG")
        try:
            row_with_date = ("IDC001", "Card Holder", "Employee", "card@test.local", None,
                             _dt.date(2022, 3, 15), None, "O+", "9999999999")
            row_without_date = row_with_date[:5] + (None,) + row_with_date[6:]
            doj_fields = {"date_of_joining": {"side": "front", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.2, "font_size": 14}}
            img_with_date = _render_custom_side(rel_path, doj_fields, "front", "IDC001", row_with_date, None)
            img_without_date = _render_custom_side(rel_path, doj_fields, "front", "IDC001", row_without_date, None)
            assert img_with_date.tobytes() != img_without_date.tobytes()

            addr_fields = {"company_address": {"side": "front", "x": 0.1, "y": 0.4, "w": 0.8, "h": 0.2, "font_size": 12}}
            img_with_addr = _render_custom_side(rel_path, addr_fields, "front", "IDC001", row_with_date, None,
                                                company_address="42 Wallaby Way, Sydney")
            img_without_addr = _render_custom_side(rel_path, addr_fields, "front", "IDC001", row_with_date, None,
                                                   company_address=None)
            assert img_with_addr.tobytes() != img_without_addr.tobytes()

            row_with_emergency = row_with_date + (None, None, "Aisha Begum", "+91 90000 11223", "Sister")
            row_without_emergency = row_with_date + (None, None, None, None, None)
            emergency_fields = {
                "emergency_contact_name": {"side": "front", "x": 0.1, "y": 0.6, "w": 0.8, "h": 0.1, "font_size": 12},
            }
            img_with_emg = _render_custom_side(rel_path, emergency_fields, "front", "IDC001", row_with_emergency, None)
            img_without_emg = _render_custom_side(rel_path, emergency_fields, "front", "IDC001", row_without_emergency, None)
            assert img_with_emg.tobytes() != img_without_emg.tobytes()

            row_with_prof = row_with_date[:6] + ("Morning Shift",) + row_with_date[7:] + ("Engineering", "Priya Sharma")
            row_without_prof = row_with_date[:6] + (None,) + row_with_date[7:] + (None, None)
            prof_fields = {
                "department": {"side": "front", "x": 0.1, "y": 0.7, "w": 0.8, "h": 0.1, "font_size": 12},
                "shift": {"side": "front", "x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1, "font_size": 12},
                "reporting_manager": {"side": "front", "x": 0.1, "y": 0.9, "w": 0.8, "h": 0.09, "font_size": 12},
            }
            img_with_prof = _render_custom_side(rel_path, prof_fields, "front", "IDC001", row_with_prof, None)
            img_without_prof = _render_custom_side(rel_path, prof_fields, "front", "IDC001", row_without_prof, None)
            assert img_with_prof.tobytes() != img_without_prof.tobytes()

            row_with_timing = row_with_date[:6] + (None,) + row_with_date[7:] + (
                None, None, None, None, None, _dt.time(9, 0), _dt.time(18, 0), "wfh")
            row_without_timing = row_with_date[:6] + (None,) + row_with_date[7:] + (
                None, None, None, None, None, None, None, None)
            timing_fields = {
                "shift_timing": {"side": "front", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.1, "font_size": 12},
                "work_mode": {"side": "front", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.1, "font_size": 12},
            }
            img_with_timing = _render_custom_side(rel_path, timing_fields, "front", "IDC001", row_with_timing, None)
            img_without_timing = _render_custom_side(rel_path, timing_fields, "front", "IDC001", row_without_timing, None)
            assert img_with_timing.tobytes() != img_without_timing.tobytes()

            phone_fields = {
                "company_phone": {"side": "front", "x": 0.1, "y": 0.3, "w": 0.8, "h": 0.1, "font_size": 12},
            }
            img_with_phone = _render_custom_side(rel_path, phone_fields, "front", "IDC001", row_with_date, None,
                                                 company_phone="1800-123-4567")
            img_without_phone = _render_custom_side(rel_path, phone_fields, "front", "IDC001", row_with_date, None,
                                                    company_phone=None)
            assert img_with_phone.tobytes() != img_without_phone.tobytes()
        finally:
            os.remove(abs_path)

    def test_logo_field_rectangular_and_round(self, company_employee):
        """Company logo field renders without distortion/crashing in both
        rectangular (aspect-preserving fit) and circular-mask modes, and a
        template with a logo placed looks different from one without."""
        from blueprints.employees import _render_custom_side

        template_rel = "id_card_templates/_test_logo_front.png"
        template_abs = os.path.join(flask_app.root_path, "static", template_rel)
        os.makedirs(os.path.dirname(template_abs), exist_ok=True)
        Image.new("RGB", (300, 300), (20, 30, 60)).save(template_abs, format="PNG")

        logo_rel = "company_logos/_test_wordmark_logo.png"
        logo_abs = os.path.join(flask_app.root_path, "static", logo_rel)
        os.makedirs(os.path.dirname(logo_abs), exist_ok=True)
        Image.new("RGB", (200, 60), (255, 255, 255)).save(logo_abs, format="PNG")

        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        try:
            rect_fields = {"logo": {"side": "front", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.15}}
            img_no_logo = _render_custom_side(template_rel, {}, "front", "IDC001", row, None)
            img_rect_logo = _render_custom_side(template_rel, rect_fields, "front", "IDC001", row, logo_rel)
            assert img_rect_logo.tobytes() != img_no_logo.tobytes()

            round_fields = {"logo": {"side": "front", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "round": True}}
            img_round_logo = _render_custom_side(template_rel, round_fields, "front", "IDC001", row, logo_rel)
            assert img_round_logo.tobytes() != img_no_logo.tobytes()
            assert img_round_logo.size == img_no_logo.size
        finally:
            os.remove(template_abs)
            os.remove(logo_abs)

    def test_text_color_defaults_to_readable_contrast(self, company_employee):
        """A text field with no explicit 'color' should auto-pick white text
        over a dark background and dark text over a light one, instead of
        always rendering the same fixed gray regardless of what's under it."""
        from blueprints.employees import _render_custom_side, _idc_contrast_color, _IDC_WHITE, _IDC_DGRAY

        assert _idc_contrast_color((20, 30, 60)) == _IDC_WHITE
        assert _idc_contrast_color((250, 250, 250)) == _IDC_DGRAY

        template_rel = "id_card_templates/_test_contrast_front.png"
        template_abs = os.path.join(flask_app.root_path, "static", template_rel)
        os.makedirs(os.path.dirname(template_abs), exist_ok=True)
        Image.new("RGB", (300, 300), (20, 30, 60)).save(template_abs, format="PNG")

        row = ("IDC001", "Card Holder", "Employee", "card@test.local", None, None, None, "O+", "9999999999")
        try:
            fields = {"name": {"side": "front", "x": 0.1, "y": 0.4, "w": 0.8, "h": 0.15, "font_size": 20, "bold": True}}
            img = _render_custom_side(template_rel, fields, "front", "IDC001", row, None)
            W, H = img.size
            bx, by = int(0.1 * W), int(0.4 * H)
            bw, bh = int(0.8 * W), int(0.15 * H)
            # Scan the whole box: over a dark-navy template, auto-contrast text
            # must render some near-white glyph pixels — a fixed dark-gray
            # default (the old behavior) would stay unreadably dim throughout.
            max_brightness = max(
                sum(img.getpixel((x, y)))
                for y in range(by, by + bh, 2)
                for x in range(bx, bx + bw, 2)
            )
            assert max_brightness > 600
        finally:
            os.remove(template_abs)


class TestMyIdCardMatchesAdminDownload:
    def test_identical_output(self, client, company_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = company_employee["employee_id"]
            sess["employee_name"] = "Card Holder"
        mine = client.get("/my_id_card")
        assert mine.status_code == 200

        # Compare against a direct call to the shared builder (what /admin_id_card also calls)
        from blueprints.employees import _build_id_card_buf
        direct_buf = _build_id_card_buf(company_employee["employee_id"])
        assert mine.data == direct_buf.getvalue()
