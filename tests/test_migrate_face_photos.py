# -*- coding: utf-8 -*-
"""scripts/migrate_face_photos_per_tenant.py's planner: an employee ID used
by more than one company can't be attributed, so its shared flat photo is
never copied into any company's folder."""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "migrate_face_photos_per_tenant",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "migrate_face_photos_per_tenant.py"))
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def _legacy(root):
    return lambda emp_id: os.path.join(root, emp_id + ".jpg")


def test_unique_id_is_copied(tmp_path):
    root = str(tmp_path)
    rows = {"att_a": [("EMP001", os.path.join(root, "EMP001.jpg"))], "att_b": [("EMP900", None)]}
    out = mig.plan(rows, _legacy(root))
    assert out["copy"] == [("att_a", "EMP001", os.path.join(root, "EMP001.jpg"))]
    assert out["ambiguous"] == []


def test_id_shared_by_two_companies_is_ambiguous_for_both(tmp_path):
    root = str(tmp_path)
    flat = os.path.join(root, "EMP001.jpg")
    rows = {"att_a": [("EMP001", flat)], "att_b": [("emp001", None)], "att_c": [("EMP001", flat)]}
    out = mig.plan(rows, _legacy(root))
    assert out["copy"] == []
    assert {(t, e) for t, e, _ in out["ambiguous"]} == {("att_a", "EMP001"), ("att_c", "EMP001")}


def test_rows_already_in_company_folder_untouched(tmp_path):
    root = str(tmp_path)
    rows = {"att_a": [("EMP001", os.path.join(root, "tenants", "att_a", "EMP001.jpg"))]}
    assert mig.plan(rows, _legacy(root)) == {"copy": [], "ambiguous": []}
