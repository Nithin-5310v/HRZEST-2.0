"""Unit tests for utils/helpers.py functions that don't go through a
blueprint route (PII field round-trips through routes are already covered
by tests/test_pii_encryption.py). att_test is a shared, persistent DB —
every test that writes a row cleans it up in a finally block (see
tests/test_admin_search.py for the incident this pattern guards against).
"""
import io
import hashlib
from PIL import Image
from werkzeug.datastructures import FileStorage
from extensions import app as flask_app
import utils.helpers as helpers


def _jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _fs(filename, content, content_type=None):
    return FileStorage(stream=io.BytesIO(content), filename=filename, content_type=content_type)


def _raise(*_a, **_k):
    raise RuntimeError("db down")


class TestValidateEmpId:
    def test_valid_ids_accepted(self):
        assert helpers.validate_emp_id("EMP001")
        assert helpers.validate_emp_id("a_b-2")

    def test_empty_and_invalid_rejected(self):
        assert not helpers.validate_emp_id("")
        assert not helpers.validate_emp_id(None)
        assert not helpers.validate_emp_id("has space")
        assert not helpers.validate_emp_id("has/slash")


class TestCleanEmailDomain:
    def test_normalizes_to_bare_domain(self):
        for raw in ("acme.com", " Acme.COM ", "@acme.com", "www.acme.com", "https://acme.com/",
                    "admin@acme.com", "Admin@Acme.com", "acme.com."):
            assert helpers.clean_email_domain(raw) == "acme.com", raw

    def test_cleaned_email_passes_validation(self):
        assert helpers.validate_email_domain_format(helpers.clean_email_domain("nithin@magzest.in")) is None

    def test_invalid_domain_error_names_the_value(self):
        assert helpers.validate_email_domain_format("") == "Company email domain is required."
        assert "'acme'" in helpers.validate_email_domain_format(helpers.clean_email_domain("acme"))


class TestSafeRedirect:
    def test_relative_path_allowed(self):
        assert helpers._safe_redirect("/dashboard") == "/dashboard"

    def test_protocol_relative_rejected(self):
        assert helpers._safe_redirect("//evil.com") == "/admin"

    def test_absolute_url_rejected(self):
        assert helpers._safe_redirect("https://evil.com") == "/admin"

    def test_empty_falls_back(self):
        assert helpers._safe_redirect("", "/x") == "/x"


class TestSafeReferrerRedirect:
    def test_empty_referrer_falls_back(self):
        with flask_app.test_request_context("/"):
            assert helpers._safe_referrer_redirect("", "/fallback") == "/fallback"

    def test_relative_referrer_passthrough(self):
        with flask_app.test_request_context("/"):
            assert helpers._safe_referrer_redirect("/some/path", "/fallback") == "/some/path"

    def test_same_host_absolute_referrer_reduced_to_path(self):
        with flask_app.test_request_context("/", base_url="http://localhost:5000"):
            got = helpers._safe_referrer_redirect("http://localhost:5000/employees?x=1", "/fallback")
            assert got == "/employees?x=1"

    def test_foreign_host_referrer_rejected(self):
        with flask_app.test_request_context("/", base_url="http://localhost:5000"):
            got = helpers._safe_referrer_redirect("http://evil.com/steal", "/fallback")
            assert got == "/fallback"


class TestDecryptPiiDate:
    def test_roundtrip(self):
        enc = helpers.encrypt_pii("2000-01-01")
        assert helpers.decrypt_pii_date(enc).isoformat() == "2000-01-01"

    def test_none_and_empty_return_none(self):
        assert helpers.decrypt_pii_date(None) is None
        assert helpers.decrypt_pii_date("") is None

    def test_garbage_value_returns_none(self):
        assert helpers.decrypt_pii_date("not-a-date") is None


class TestHashToken:
    def test_deterministic_sha256(self):
        # _hash_token lives in utils/auth.py, not utils/helpers.py.
        from utils.auth import _hash_token
        assert _hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


class TestDbContextManager:
    def test_yields_working_cursor_and_closes(self):
        with helpers._db() as (cur, conn):
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


class TestAudit:
    def test_writes_row_when_in_request_context(self, db_engine):
        with flask_app.test_request_context("/"):
            from flask import session
            session["admin_logged_in"] = True
            session["admin_username"] = "helper_test_admin"
            helpers._audit("helper_test_action", table="employees", record_id="X1", detail="unit test")
        cur = db_engine.cursor()
        cur.execute("SELECT actor, actor_type FROM audit_logs WHERE action='helper_test_action'")
        row = cur.fetchone()
        # audit_logs is append-only (BEFORE DELETE trigger — see app.py's
        # _reject_audit_mutation), same bypass tests/test_security_events_log.py uses.
        cur.execute("SET audit.bypass = 'on'")
        cur.execute("DELETE FROM audit_logs WHERE action='helper_test_action'")
        cur.execute("SET audit.bypass = 'off'")
        cur.close()
        assert row == ("helper_test_admin", "admin")

    def test_silently_noops_outside_request_context(self):
        # session.get() raises RuntimeError with no active request context;
        # _audit must swallow it rather than propagate.
        helpers._audit("should_not_raise_helper_test")


class TestCreateNotification:
    def test_inserts_row(self, db_engine):
        helpers._create_notification("admin", "Helper test title", "Helper test message")
        cur = db_engine.cursor()
        cur.execute("SELECT title FROM notifications WHERE title='Helper test title'")
        row = cur.fetchone()
        cur.execute("DELETE FROM notifications WHERE title='Helper test title'")
        cur.close()
        assert row is not None

    def test_db_error_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        helpers._create_notification("admin", "x", "y")


class TestScanForMalware:
    def test_disabled_returns_clean(self, monkeypatch):
        monkeypatch.setattr(helpers, "_MALWARE_SCAN_ENABLED", False)
        clean, err = helpers._scan_for_malware(_fs("x.pdf", b"%PDF-1.4"))
        assert clean is True and err is None

    def test_unavailable_in_dev_fails_open(self, monkeypatch):
        monkeypatch.setattr(helpers, "_MALWARE_SCAN_ENABLED", True)
        monkeypatch.setattr(helpers, "_clamav_available", False)
        monkeypatch.setenv("APP_ENV", "development")
        clean, err = helpers._scan_for_malware(_fs("x.pdf", b"%PDF-1.4"))
        assert clean is True

    def test_unavailable_in_production_fails_closed(self, monkeypatch):
        monkeypatch.setattr(helpers, "_MALWARE_SCAN_ENABLED", True)
        monkeypatch.setattr(helpers, "_clamav_available", False)
        monkeypatch.setenv("APP_ENV", "production")
        clean, err = helpers._scan_for_malware(_fs("x.pdf", b"%PDF-1.4"))
        assert clean is False
        assert err

    def test_clean_scan_result(self, monkeypatch):
        monkeypatch.setattr(helpers, "_MALWARE_SCAN_ENABLED", True)
        monkeypatch.setattr(helpers, "_clamav_available", True)

        class _FakeSocket:
            def __init__(self, **kw):
                pass

            def instream(self, stream):
                return {"stream": ("OK", None)}

        monkeypatch.setattr(helpers, "_clamd_lib", type("m", (), {"ClamdNetworkSocket": _FakeSocket}))
        clean, err = helpers._scan_for_malware(_fs("x.pdf", b"%PDF-1.4"))
        assert clean is True

    def test_malware_detected(self, monkeypatch):
        monkeypatch.setattr(helpers, "_MALWARE_SCAN_ENABLED", True)
        monkeypatch.setattr(helpers, "_clamav_available", True)

        class _FakeSocket:
            def __init__(self, **kw):
                pass

            def instream(self, stream):
                return {"stream": ("FOUND", "Eicar-Test-Signature")}

        monkeypatch.setattr(helpers, "_clamd_lib", type("m", (), {"ClamdNetworkSocket": _FakeSocket}))
        clean, err = helpers._scan_for_malware(_fs("x.pdf", b"%PDF-1.4"))
        assert clean is False

    def test_scan_exception_fails_closed_in_prod(self, monkeypatch):
        monkeypatch.setattr(helpers, "_MALWARE_SCAN_ENABLED", True)
        monkeypatch.setattr(helpers, "_clamav_available", True)

        class _FakeSocket:
            def __init__(self, **kw):
                raise ConnectionError("clamav down")

        monkeypatch.setattr(helpers, "_clamd_lib", type("m", (), {"ClamdNetworkSocket": _FakeSocket}))
        monkeypatch.setenv("APP_ENV", "production")
        clean, err = helpers._scan_for_malware(_fs("x.pdf", b"%PDF-1.4"))
        assert clean is False


class TestValidateUpload:
    def _ok_scan(self, monkeypatch):
        monkeypatch.setattr(helpers, "_scan_for_malware", lambda f: (True, None))

    def test_no_file_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(None)
        assert not ok

    def test_disallowed_extension_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(_fs("malware.exe", b"MZ"), allowed_exts={"pdf"})
        assert not ok
        assert "not allowed" in msg

    def test_content_type_extension_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("doc.pdf", b"%PDF-1.4", content_type="image/png"), allowed_exts={"pdf"})
        assert not ok

    def test_pdf_magic_bytes_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("doc.pdf", b"NOTPDF12", content_type="application/pdf"), allowed_exts={"pdf"})
        assert not ok
        assert "Invalid PDF" in msg

    def test_png_magic_bytes_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("img.png", b"NOTPNG12", content_type="image/png"), allowed_exts={"png"})
        assert not ok

    def test_jpg_magic_bytes_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("img.jpg", b"NOTJPG12", content_type="image/jpeg"), allowed_exts={"jpg"})
        assert not ok

    def test_valid_pdf_accepted(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("doc.pdf", b"%PDF-1.4 rest of file", content_type="application/pdf"), allowed_exts={"pdf"})
        assert ok

    def test_oversized_file_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        big = b"%PDF-1.4" + b"0" * (11 * 1024 * 1024)
        ok, msg = helpers._validate_upload(
            _fs("doc.pdf", big, content_type="application/pdf"), allowed_exts={"pdf"})
        assert not ok
        assert "too large" in msg.lower()

    # Finding #11 (Medium): doc/docx/xls/xlsx previously had no magic-byte
    # check at all -- only filename extension + client-supplied
    # Content-Type, both attacker-controlled. Mirrors the existing pdf/
    # png/jpg pattern above.
    def test_docx_magic_bytes_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("resume.docx", b"NOT A REAL DOCX FILE AT ALL",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            allowed_exts={"docx"})
        assert not ok
        assert "Invalid DOCX" in msg

    def test_xlsx_magic_bytes_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("data.xlsx", b"<script>alert(1)</script>",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            allowed_exts={"xlsx"})
        assert not ok
        assert "Invalid XLSX" in msg

    def test_doc_magic_bytes_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("resume.doc", b"NOT A REAL DOC FILE AT ALL", content_type="application/msword"),
            allowed_exts={"doc"})
        assert not ok
        assert "Invalid DOC" in msg

    def test_xls_magic_bytes_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("data.xls", b"NOT A REAL XLS FILE AT ALL", content_type="application/vnd.ms-excel"),
            allowed_exts={"xls"})
        assert not ok
        assert "Invalid XLS" in msg

    def test_valid_docx_accepted(self, monkeypatch):
        self._ok_scan(monkeypatch)
        # A real docx/xlsx is a ZIP (OOXML) -- PK\x03\x04 is the real
        # signature every such file starts with, regardless of what's
        # actually zipped inside it.
        ok, msg = helpers._validate_upload(
            _fs("resume.docx", b"PK\x03\x04" + b"rest of a real zip container",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            allowed_exts={"docx"})
        assert ok

    def test_valid_doc_accepted(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_upload(
            _fs("resume.doc", b"\xd0\xcf\x11\xe0" + b"rest of a real OLE2 container",
                content_type="application/msword"),
            allowed_exts={"doc"})
        assert ok


class TestValidateImageFile:
    def _ok_scan(self, monkeypatch):
        monkeypatch.setattr(helpers, "_scan_for_malware", lambda f: (True, None))

    def test_no_file_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_image_file(None)
        assert not ok

    def test_disallowed_extension_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_image_file(_fs("x.gif", b"GIF89a"))
        assert not ok

    def test_disallowed_content_type_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_image_file(_fs("x.jpg", _jpeg_bytes(), content_type="application/pdf"))
        assert not ok

    def test_magic_byte_mismatch_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_image_file(_fs("x.png", b"NOTPNG12", content_type="image/png"))
        assert not ok

    def test_valid_jpeg_accepted(self, monkeypatch):
        self._ok_scan(monkeypatch)
        ok, msg = helpers._validate_image_file(_fs("x.jpg", _jpeg_bytes(), content_type="image/jpeg"))
        assert ok

    def test_oversized_photo_rejected(self, monkeypatch):
        self._ok_scan(monkeypatch)
        big = _jpeg_bytes() + b"0" * (6 * 1024 * 1024)
        ok, msg = helpers._validate_image_file(_fs("x.jpg", big, content_type="image/jpeg"))
        assert not ok


class TestCompanySettingsCache:
    def test_returns_dict_with_expected_keys(self):
        helpers.invalidate_settings_cache()
        result = helpers.get_company_settings()
        assert "company_name" in result and "session_timeout" in result
        helpers.invalidate_settings_cache()

    def test_cache_hit_avoids_requery(self, monkeypatch):
        helpers.invalidate_settings_cache()
        first = helpers.get_company_settings()
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        second = helpers.get_company_settings()
        assert second == first
        helpers.invalidate_settings_cache()

    def test_db_error_falls_back_to_defaults(self, monkeypatch):
        helpers.invalidate_settings_cache()
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        result = helpers.get_company_settings()
        assert result["company_name"] == "My Company"
        helpers.invalidate_settings_cache()


class TestCompaniesListCache:
    def test_lists_and_invalidates(self, db_engine):
        helpers.invalidate_companies_cache()
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name, code) VALUES ('Helper Test Co', 'HTC') RETURNING id")
        cid = cur.fetchone()[0]
        try:
            rows = helpers.get_companies_list()
            assert any(r[0] == cid for r in rows)
            cur.execute("UPDATE companies SET name='Helper Renamed Co' WHERE id=%s", (cid,))
            cached_rows = helpers.get_companies_list()
            assert not any(r[1] == "Helper Renamed Co" for r in cached_rows)
            helpers.invalidate_companies_cache()
            fresh_rows = helpers.get_companies_list()
            assert any(r[1] == "Helper Renamed Co" for r in fresh_rows)
        finally:
            cur.execute("DELETE FROM company_feature_settings WHERE company_id=%s", (cid,))
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()
            helpers.invalidate_companies_cache()

    def test_db_error_returns_empty_list(self, monkeypatch):
        helpers.invalidate_companies_cache()
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        assert helpers.get_companies_list() == []
        helpers.invalidate_companies_cache()


class TestOverdueOnboardingCount:
    def test_returns_int(self):
        helpers._onboarding_cache.clear()
        count = helpers.get_overdue_onboarding_count()
        assert isinstance(count, int)

    def test_db_error_returns_zero(self, monkeypatch):
        helpers._onboarding_cache.clear()
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        assert helpers.get_overdue_onboarding_count() == 0
        helpers._onboarding_cache.clear()


class TestAuthConfig:
    def test_returns_dict(self):
        helpers._auth_cache.clear()
        result = helpers.get_auth_config()
        assert "face_enabled" in result

    def test_db_error_falls_back_to_defaults(self, monkeypatch):
        helpers._auth_cache.clear()
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        result = helpers.get_auth_config()
        assert result == helpers._AUTH_CONFIG_DEFAULTS
        helpers._auth_cache.clear()


class TestReadGlobalFeatures:
    def test_returns_dict_with_expected_keys(self):
        result = helpers._read_global_features()
        assert "shift_start" in result and "geo_radius" in result

    def test_db_error_returns_hardcoded_defaults(self, monkeypatch):
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        result = helpers._read_global_features()
        assert result["shift_start"] == "09:00:00"


class TestGetCoFeatures:
    def test_no_company_id_uses_global(self):
        result = helpers.get_co_features(None)
        assert "geo_radius" in result

    def test_with_company_id_reads_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Helper Feat Co') RETURNING id")
        cid = cur.fetchone()[0]
        cur.execute("INSERT INTO company_feature_settings (company_id, geo_radius) VALUES (%s, 500)", (cid,))
        try:
            result = helpers.get_co_features(cid)
            assert result["geo_radius"] == 500
        finally:
            cur.execute("DELETE FROM company_feature_settings WHERE company_id=%s", (cid,))
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()

    def test_with_company_id_missing_row_falls_back_to_global(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Helper NoFeat Co') RETURNING id")
        cid = cur.fetchone()[0]
        try:
            result = helpers.get_co_features(cid)
            assert result["geo_radius"] == helpers._read_global_features()["geo_radius"]
        finally:
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()

    def test_db_error_falls_back_to_global(self, monkeypatch, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Helper Err Co') RETURNING id")
        cid = cur.fetchone()[0]
        try:
            monkeypatch.setattr(helpers, "get_db_connection", _raise)
            result = helpers.get_co_features(cid)
            assert "geo_radius" in result
        finally:
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()


class TestUpsertCoFeature:
    def test_rejects_invalid_column(self, db_engine):
        helpers._upsert_co_feature(999999, "evil; DROP TABLE companies; --", "x")
        cur = db_engine.cursor()
        cur.execute("SELECT to_regclass('public.companies')")
        assert cur.fetchone()[0] == "companies"
        cur.close()

    def test_noop_without_company_id(self):
        helpers._upsert_co_feature(None, "geo_radius", 100)

    def test_valid_field_updates_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Helper Upsert Co') RETURNING id")
        cid = cur.fetchone()[0]
        try:
            helpers._upsert_co_feature(cid, "geo_radius", 777)
            cur.execute("SELECT geo_radius FROM company_feature_settings WHERE company_id=%s", (cid,))
            assert cur.fetchone()[0] == 777
        finally:
            cur.execute("DELETE FROM company_feature_settings WHERE company_id=%s", (cid,))
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()

    def test_db_error_swallowed(self, monkeypatch):
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        helpers._upsert_co_feature(1, "geo_radius", 1)


class TestUpsertCoFeatures:
    def test_rejects_call_containing_any_bad_column(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Helper Upserts Co') RETURNING id")
        cid = cur.fetchone()[0]
        try:
            helpers._upsert_co_features(cid, {"geo_radius": 1, "evil; DROP TABLE companies;--": 1})
            cur.execute("SELECT 1 FROM company_feature_settings WHERE company_id=%s", (cid,))
            assert cur.fetchone() is None
        finally:
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()

    def test_noop_without_company_id_or_fields(self):
        helpers._upsert_co_features(1, {})
        helpers._upsert_co_features(None, {"geo_radius": 1})

    def test_valid_fields_update_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Helper Upserts2 Co') RETURNING id")
        cid = cur.fetchone()[0]
        try:
            helpers._upsert_co_features(cid, {"geo_radius": 888, "pin_enabled": 0})
            cur.execute("SELECT geo_radius, pin_enabled FROM company_feature_settings WHERE company_id=%s", (cid,))
            assert cur.fetchone() == (888, 0)
        finally:
            cur.execute("DELETE FROM company_feature_settings WHERE company_id=%s", (cid,))
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()

    def test_db_error_swallowed(self, monkeypatch):
        monkeypatch.setattr(helpers, "get_db_connection", _raise)
        helpers._upsert_co_features(1, {"geo_radius": 1})


class TestCoScopeHelpers:
    def test_subquery_without_active_cid(self):
        assert helpers.co_scope_subquery(None) == ("", ())

    def test_subquery_with_active_cid_no_alias(self):
        frag, params = helpers.co_scope_subquery(5)
        assert "employee_id IN" in frag and params == (5,)

    def test_subquery_with_alias(self):
        frag, params = helpers.co_scope_subquery(5, alias="a")
        assert "a.employee_id IN" in frag

    def test_column_without_active_cid(self):
        assert helpers.co_scope_column(None) == ("", ())

    def test_column_with_active_cid(self):
        frag, params = helpers.co_scope_column(5)
        assert frag == "AND company_id=%s" and params == (5,)

    def test_column_with_alias(self):
        frag, params = helpers.co_scope_column(5, alias="e")
        assert frag == "AND e.company_id=%s"


class TestErrorPage:
    def test_anonymous_links_home(self):
        with flask_app.test_request_context("/"):
            html, code = helpers._error_page(404, "?", "Not Found", "sub", "hint")
            assert code == 404
            assert "Go to Home" in html

    def test_admin_session_links_dashboard(self):
        with flask_app.test_request_context("/"):
            from flask import session
            session["admin_logged_in"] = True
            html, code = helpers._error_page(403, "x", "Forbidden", "sub", "hint")
            assert "Go to Admin Dashboard" in html

    def test_employee_session_links_portal(self):
        with flask_app.test_request_context("/"):
            from flask import session
            session["employee_id"] = "TST001"
            html, code = helpers._error_page(500, "x", "Error", "sub", "hint")
            assert "Go to My Portal" in html
