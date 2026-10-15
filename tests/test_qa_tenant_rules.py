"""BUG-01: shift times and payroll rules belong to each company.

They used to be process-wide globals in utils/config.py, loaded from the
default schema at startup and overwritten by whichever company last saved
its settings -- so one company's shift and deduction rates silently decided
another company's late/half-day marks and salary deductions.
"""
import datetime
import secrets

import pytest
import pytz
from flask import g

from extensions import app as flask_app
from tests.test_auth_hardening import _make_tenant
from utils.auth import generate_password_hash

EMP_PW = "Emp-Pass-" + secrets.token_hex(3)


@pytest.fixture
def tenants(db_engine, client):
    # `client` (conftest) sets TESTING=True, which is what disables CSRF for
    # the extra test clients created below -- don't rely on another test
    # having set it first.
    sfx = secrets.token_hex(3)
    a = _make_tenant(db_engine, f"rulea{sfx}")
    b = _make_tenant(db_engine, f"ruleb{sfx}")
    cur = db_engine.cursor()
    cur.execute(f'INSERT INTO "{b[1]}".employees (employee_id, name, email, password, force_pin_change) '  # nosec B608
                "VALUES (%s,%s,%s,%s,0)", ("RB001", "Rule B", "rb001@test.local", generate_password_hash(EMP_PW)))
    # RB001 checks in through the location-only app check-in (QA NV-18).
    cur.execute(f'UPDATE "{b[1]}".company_settings SET app_gps_checkin=1')  # nosec B608
    cur.close()
    yield a, b
    from utils.helpers import invalidate_settings_cache
    invalidate_settings_cache()


def _ist(hour, minute):
    tz = pytz.timezone("Asia/Kolkata")
    return tz.localize(datetime.datetime.combine(datetime.date.today(), datetime.time(hour, minute)))


def test_one_company_saving_rules_does_not_change_anothers_checkins(tenants, monkeypatch):
    (slug_a, _schema_a), (slug_b, _schema_b) = tenants
    admin_a = flask_app.test_client()
    admin_a.post(f"/{slug_a}/login", data={"identifier": "iso_admin", "password": "IsoAdmin@123"})
    resp = admin_a.post(f"/{slug_a}/save_salary_rules", data={
        "late_deduction_pct": "10", "half_day_deduction_pct": "50", "grace_minutes": "0",
        "holiday_pay": "paid", "leave_pay": "exclude",
        "shift_start": "06:00", "shift_half": "07:00", "shift_end": "15:00",
    })
    assert resp.status_code in (200, 302)

    # Company B kept the defaults (09:00 / 13:00 / 18:00, 15 min grace): 08:00 is on time.
    import blueprints.employee_portal as ep
    monkeypatch.setattr(ep, "company_now", lambda: _ist(8, 0))
    emp_b = flask_app.test_client()
    tok = emp_b.post(f"/{slug_b}/api/employee/login",
                     json={"employee_id": "RB001", "password": EMP_PW}).get_json()["token"]
    resp = emp_b.post(f"/{slug_b}/api/employee/checkin", json={"lat": 12.9716, "lon": 77.5946},
                      headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "Full Day Login"


def test_payroll_deduction_rates_are_per_company(tenants, db_engine):
    from utils.helpers import invalidate_settings_cache
    from utils.salary_utils import compute_salary_entry
    (_slug_a, schema_a), (_slug_b, schema_b) = tenants
    cur = db_engine.cursor()
    cur.execute(f'UPDATE "{schema_a}".company_settings SET late_deduction_pct=50')  # nosec B608
    cur.execute(f'UPDATE "{schema_b}".company_settings SET late_deduction_pct=10')  # nosec B608
    cur.close()
    invalidate_settings_cache()
    day = datetime.date(2026, 9, 1)
    row = (day, "E1", datetime.time(9, 30), datetime.time(18, 0), "Late Login", "Completed", "Late - Full Day")
    late_earn = {}
    for schema in (schema_a, schema_b):
        with flask_app.test_request_context():
            g.tenant_db = schema
            late_earn[schema] = compute_salary_entry("E1", "E", 1000, {"E1": {day: row}}, [day])["late_earn"]
    assert late_earn[schema_a] == 500.0
    assert late_earn[schema_b] == 900.0


def test_rules_accessors_follow_the_current_company(tenants, db_engine):
    import utils.config as cfg
    from utils.helpers import invalidate_settings_cache
    (_slug_a, schema_a), (_slug_b, schema_b) = tenants
    cur = db_engine.cursor()
    cur.execute(f"UPDATE \"{schema_a}\".company_settings SET shift_start='07:30', grace_minutes=5, "  # nosec B608
                "holiday_pay='unpaid', half_day_deduction_pct=40")
    cur.close()
    invalidate_settings_cache()
    with flask_app.test_request_context():
        g.tenant_db = schema_a
        a_shift, a_pay = cfg.shift_rules(), cfg.salary_rules()
    with flask_app.test_request_context():
        g.tenant_db = schema_b
        b_shift, b_pay = cfg.shift_rules(), cfg.salary_rules()
    assert (a_shift.start, a_shift.grace_minutes) == (datetime.time(7, 30), 5)
    assert (a_pay.holiday_pay, a_pay.half_day_rate) == ("unpaid", 0.40)
    assert (b_shift.start, b_shift.grace_minutes) == (datetime.time(9, 0), 15)
    assert (b_pay.holiday_pay, b_pay.half_day_rate) == ("paid", 0.50)
