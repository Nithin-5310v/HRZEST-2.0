# -*- coding: utf-8 -*-
"""QR attendance codes are signed per company: every company shares one
SECRET_KEY and employee IDs repeat across companies, so a signature over
the employee ID alone made company A's EMP001 badge valid at company B's
kiosk. See qr_generator.py's _qr_signature()."""
import datetime

import pytest
from flask import g

import qr_generator
from qr_generator import _legacy_qr_signature, _qr_signature, verify_qr_value


def _verify_as(app, tenant, value):
    with app.test_request_context("/"):
        g.tenant_db = tenant
        return verify_qr_value(value)


@pytest.fixture
def flask_app():
    from extensions import app
    return app


class TestTenantBoundSignature:
    def test_signature_differs_per_tenant(self):
        assert _qr_signature("EMP001", "att_company_a") != _qr_signature("EMP001", "att_company_b")

    def test_badge_verifies_in_its_own_tenant(self, flask_app):
        value = f"EMP001.{_qr_signature('EMP001', 'att_company_a')}"
        assert _verify_as(flask_app, "att_company_a", value) == ("EMP001", True)

    def test_badge_from_another_tenant_is_rejected(self, flask_app, monkeypatch):
        monkeypatch.setenv("QR_LEGACY_ACCEPT_UNTIL", "2000-01-01")
        value = f"EMP001.{_qr_signature('EMP001', 'att_company_a')}"
        assert _verify_as(flask_app, "att_company_b", value) == (None, False)

    def test_default_tenant_normalises_to_public(self, flask_app, monkeypatch):
        """No request, or the single-tenant DB_NAME default, both sign as
        "public" -- what a migration cursor's search_path reports."""
        monkeypatch.setenv("DB_NAME", "employee_attendance")
        assert _qr_signature("EMP001") == _qr_signature("EMP001", "public")
        value = f"EMP001.{_qr_signature('EMP001', 'public')}"
        assert _verify_as(flask_app, "employee_attendance", value) == ("EMP001", True)

    def test_signature_is_case_insensitive(self, flask_app):
        value = f"EMP001.{_qr_signature('EMP001', 'att_company_a').upper()}"
        assert _verify_as(flask_app, "att_company_a", value) == ("EMP001", True)


class TestLegacyGraceWindow:
    def test_legacy_badge_accepted_inside_window(self, flask_app, monkeypatch):
        monkeypatch.setenv("QR_LEGACY_ACCEPT_UNTIL", "2999-12-31")
        value = f"EMP001.{_legacy_qr_signature('EMP001')}"
        assert _verify_as(flask_app, "att_company_a", value) == ("EMP001", True)

    def test_legacy_badge_refused_after_window(self, flask_app, monkeypatch):
        monkeypatch.setenv("QR_LEGACY_ACCEPT_UNTIL", "2000-01-01")
        value = f"EMP001.{_legacy_qr_signature('EMP001')}"
        assert _verify_as(flask_app, "att_company_a", value) == (None, False)

    def test_malformed_window_refuses_legacy(self, monkeypatch):
        monkeypatch.setenv("QR_LEGACY_ACCEPT_UNTIL", "next month")
        assert qr_generator._legacy_qr_accepted() is False

    def test_default_window_ends_on_documented_date(self, monkeypatch):
        monkeypatch.delenv("QR_LEGACY_ACCEPT_UNTIL", raising=False)
        until = datetime.date.fromisoformat(qr_generator._LEGACY_QR_DEFAULT_UNTIL)
        assert qr_generator._legacy_qr_accepted(until) is True
        assert qr_generator._legacy_qr_accepted(until + datetime.timedelta(days=1)) is False


class TestPerTenantStorage:
    def test_image_path_includes_tenant(self, monkeypatch):
        saved = {}

        def fake_save_private(root, fs, rel_path):
            saved["rel"] = rel_path
            return "ref", None

        monkeypatch.setattr("utils.storage.save_private", fake_save_private)
        qr_generator.generate_qr("EMP001", "att_company_a")
        assert saved["rel"] == "qrcodes/att_company_a/EMP001.png"


class TestMigrationTenant:
    def test_cursor_tenant_reads_first_search_path_entry(self, db_engine):
        from migrations.column_migrations import _cursor_tenant
        cur = db_engine.cursor()
        try:
            cur.execute('SET search_path TO "att_company_a", public')
            assert _cursor_tenant(cur) == "att_company_a"
            cur.execute("SET search_path TO public")
            assert _cursor_tenant(cur) == "public"
        finally:
            cur.execute("SET search_path TO public")
            cur.close()
