# -*- coding: utf-8 -*-
"""utils/tenant_files.py: face photos live per company, so two companies'
EMP001 never share (or can read) one reference photo."""
import os

import pytest
from flask import g

from extensions import app as flask_app
import utils.tenant_files as tf


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    monkeypatch.setitem(flask_app.config, "UPLOAD_FOLDER", str(tmp_path))
    with flask_app.app_context():
        yield tmp_path


def _as_tenant(tenant):
    ctx = flask_app.test_request_context("/")
    ctx.push()
    g.tenant_db = tenant
    return ctx


class TestPaths:
    def test_photo_path_is_per_company(self, upload_root):
        a = tf.face_photo_path("EMP001", "att_company_a")
        b = tf.face_photo_path("EMP001", "att_company_b")
        assert a != b
        assert a == os.path.join(str(upload_root), "tenants", "att_company_a", "EMP001.jpg")

    def test_current_request_company_is_used(self, upload_root):
        ctx = _as_tenant("att_company_a")
        try:
            assert tf.face_photo_path("EMP001") == tf.face_photo_path("EMP001", "att_company_a")
        finally:
            ctx.pop()

    @pytest.mark.parametrize("bad", ["../EMP001", "a/b", "a\\b", "..", ""])
    def test_unsafe_ids_rejected(self, upload_root, bad):
        with pytest.raises(ValueError):
            tf.face_photo_path(bad, "public")


class TestResolve:
    def _no_db_row(self, monkeypatch, stored=None):
        class _Cur:
            def execute(self, *a): pass
            def fetchone(self): return (stored,) if stored else None
            def close(self): pass

        class _Db:
            def cursor(self): return _Cur()
            def close(self): pass

        monkeypatch.setattr("database.get_db_connection", lambda: _Db())

    def test_other_company_photo_is_not_visible(self, upload_root, monkeypatch):
        self._no_db_row(monkeypatch)
        ctx = _as_tenant("att_company_b")
        a_photo = tf.face_photo_path("EMP001", "att_company_a")
        open(a_photo, "wb").write(b"a")
        try:
            assert tf.resolve_face_photo("EMP001") is None
        finally:
            ctx.pop()

    def test_own_company_photo_resolves(self, upload_root, monkeypatch):
        self._no_db_row(monkeypatch)
        ctx = _as_tenant("att_company_a")
        a_photo = tf.face_photo_path("EMP001", "att_company_a")
        open(a_photo, "wb").write(b"a")
        try:
            assert tf.resolve_face_photo("EMP001") == a_photo
        finally:
            ctx.pop()

    def test_stored_pre_migration_path_still_resolves(self, upload_root, monkeypatch):
        legacy = upload_root / "EMP001.jpg"
        legacy.write_bytes(b"old")
        self._no_db_row(monkeypatch, stored=str(legacy))
        ctx = _as_tenant("att_company_a")
        try:
            assert tf.resolve_face_photo("EMP001") == str(legacy)
        finally:
            ctx.pop()

    def test_stored_path_outside_upload_root_ignored(self, upload_root, tmp_path_factory, monkeypatch):
        outside = tmp_path_factory.mktemp("elsewhere") / "EMP001.jpg"
        outside.write_bytes(b"x")
        self._no_db_row(monkeypatch, stored=str(outside))
        ctx = _as_tenant("att_company_a")
        try:
            assert tf.resolve_face_photo("EMP001") is None
        finally:
            ctx.pop()
