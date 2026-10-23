"""Code-scanning alerts #1-#12 (Semgrep, HRZEST-2.0) and what they pointed at.

SEC-12 -- four statements formatted a column name into SQL with an
f-string (the settings toggles, the application-document viewer). Each
name was allowlist-checked first, so they weren't exploitable, but the
`nosemgrep` comments never closed the alerts: Semgrep still reports
suppressed findings in the SARIF that GitHub reads. Now every column gets
one fixed statement built from constants, and nothing from the request is
formatted into SQL.

SEC-13 -- float() was applied straight to form input. float() accepts
"nan", "inf" and "1e999" without raising; the payroll settings caught NaN
but stored infinity, and a "nan" late/half-day deduction percentage was
clamped to 100%. parse_finite_float() refuses both.
"""
import ast
import pathlib

import pytest

from tests.test_admin_views_routes import _admin_session
from utils.helpers import parse_finite_float

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── SEC-13: numbers from forms ───────────────────────────────────────────────
@pytest.mark.parametrize("raw, expected", [("12.5", 12.5), (12, 12.0), ("0", 0.0), (" 7 ", 7.0)])
def test_parse_finite_float_accepts_numbers(raw, expected):
    assert parse_finite_float(raw) == expected


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity", "1e999", "abc", "", None])
def test_parse_finite_float_rejects_everything_else(raw):
    with pytest.raises(ValueError):
        parse_finite_float(raw)


def _payroll_cap(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT pf_basic_cap FROM payroll_config LIMIT 1")
    row = cur.fetchone()
    cur.close()
    return float(row[0]) if row else None


def test_payroll_settings_refuse_an_infinite_value(client, seed_admin, db_engine):
    _admin_session(client, seed_admin["username"])
    client.get("/payroll_settings")                       # creates the config row
    before = _payroll_cap(db_engine)
    resp = client.post("/payroll_settings", data={"pf_employee_pct": "12", "pf_employer_pct": "12",
                                                  "professional_tax": "200", "tds_annual_pct": "0",
                                                  "pf_basic_cap": "inf"})
    assert resp.status_code == 302                         # "Invalid values.", not a 500
    assert _payroll_cap(db_engine) == before


def test_salary_rules_refuse_nan_instead_of_turning_it_into_100_percent(client, seed_admin, db_engine):
    _admin_session(client, seed_admin["username"])
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET late_deduction_pct=10")
    cur.close()
    client.post("/save_salary_rules", data={
        "late_deduction_pct": "nan", "half_day_deduction_pct": "50", "grace_minutes": "15",
        "holiday_pay": "paid", "leave_pay": "exclude",
        "shift_start": "09:00", "shift_half": "13:00", "shift_end": "18:00"})
    cur = db_engine.cursor()
    cur.execute("SELECT late_deduction_pct FROM company_settings LIMIT 1")
    assert float(cur.fetchone()[0]) == 10.0
    cur.close()


# ── SEC-12: column names in SQL ──────────────────────────────────────────────
class _Cursor:
    def __init__(self):
        self.executed = []

    def execute(self, *args):
        self.executed.append(args)


def test_settings_flag_update_refuses_an_unknown_column():
    from blueprints.admin_views import _update_company_setting_flag
    cur = _Cursor()
    with pytest.raises(ValueError):
        _update_company_setting_flag(cur, "qr_enabled=1, password", 1)
    assert cur.executed == []
    _update_company_setting_flag(cur, "qr_enabled", 1)
    assert cur.executed == [("UPDATE company_settings SET qr_enabled=%s", (1,))]


def test_every_toggle_column_has_a_fixed_statement():
    from blueprints.admin_views import _TOGGLE_COLUMN_MAP, _CS_FLAG_UPDATE_SQL
    assert set(_TOGGLE_COLUMN_MAP.values()) <= set(_CS_FLAG_UPDATE_SQL)


def test_application_document_queries_cover_exactly_the_allowed_kinds():
    from blueprints.platform_admin import _APPLICATION_DOC_KINDS, _APPLICATION_DOC_SQL
    assert set(_APPLICATION_DOC_SQL) == set(_APPLICATION_DOC_KINDS)
    for kind, sql in _APPLICATION_DOC_SQL.items():
        assert sql == f"SELECT doc_{kind} FROM tenant_applications WHERE id=%s"


# ── neither pattern comes back ───────────────────────────────────────────────
@pytest.mark.parametrize("path", ["app.py", "blueprints/admin_views.py", "blueprints/payroll.py",
                                  "blueprints/platform_admin.py"])
def test_no_request_value_is_cast_or_formatted_into_sql(path):
    """The shapes the alerts flagged: float()/bool() applied directly to
    request data, and an f-string passed to execute()."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        arg = ast.unparse(node.args[0])
        if name in ("float", "bool"):
            assert "request." not in arg, f"{path}:{node.lineno} {ast.unparse(node)}"
        if name == "execute" and isinstance(node.args[0], ast.JoinedStr):
            assert not any("request" in ast.unparse(v) for v in node.args[0].values
                           if isinstance(v, ast.FormattedValue)), f"{path}:{node.lineno}"
