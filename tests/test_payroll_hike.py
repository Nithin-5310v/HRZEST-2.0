"""
apply_hike / award_performance_bonus tests — these two routes were just
rewritten to batch-fetch (SELECT ... WHERE employee_id = ANY(%s)) instead
of running 2-3 queries per selected employee (a real N+1 found in a
query-usage audit). These tests exist to prove the batched version
preserves the exact original semantics — rating=0 skip, hike_pct<=0 skip,
missing/zero salary_config skip, and the quarter/year idempotency check —
not just that it doesn't crash. hike_config is seeded with 5 real bands
by init_db(), so no fixture needed for that part.

Run with:
    python -m pytest tests/test_payroll_hike.py -v
"""

from utils.helpers import decrypt_pii as _unpay  # pay columns hold ciphertext; this reads them
from utils.helpers import encrypt_amount as _pay  # pay columns only accept ciphertext


def _admin_session(client, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"],
        "password": seed_admin["password"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        logged_in = sess.get("admin_logged_in")
    if not logged_in:
        # status_code==200 alone doesn't distinguish success (redirected-
        # then-followed to /admin) from failure (the login page re-rendered
        # with an error, also 200) -- check the actual session state, and
        # retry once in case of a one-off transient hiccup rather than
        # letting a wrong-session test fail confusingly several steps later.
        resp = client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        }, follow_redirects=True)
        with client.session_transaction() as sess:
            logged_in = sess.get("admin_logged_in")
    assert logged_in, f"Admin login failed for {seed_admin['username']!r} -- check for stale admin_users.role state from another test."
    return resp


def _set_review(db_engine, emp_id, quarter, year, rating):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO performance_reviews (employee_id, quarter, year, overall_rating, status) "
        "VALUES (%s,%s,%s,%s,'Finalized') "
        "ON CONFLICT (employee_id, quarter, year) DO UPDATE SET overall_rating=EXCLUDED.overall_rating",
        (emp_id, quarter, year, rating),
    )
    cur.close()


def _set_salary(db_engine, emp_id, monthly_ctc, last_hike_quarter=None, last_hike_year=None):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO salary_config (employee_id, monthly_ctc, last_hike_quarter, last_hike_year) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (employee_id) DO UPDATE SET "
        "monthly_ctc=EXCLUDED.monthly_ctc, last_hike_quarter=EXCLUDED.last_hike_quarter, "
        "last_hike_year=EXCLUDED.last_hike_year",
        (emp_id, _pay(monthly_ctc), last_hike_quarter, last_hike_year),
    )
    cur.close()


def _cleanup(db_engine, emp_ids, quarter, year):
    cur = db_engine.cursor()
    for eid in emp_ids:
        cur.execute("DELETE FROM performance_reviews WHERE employee_id=%s AND quarter=%s AND year=%s", (eid, quarter, year))
        cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (eid,))
        cur.execute("DELETE FROM employee_incentives WHERE employee_id=%s", (eid,))
    cur.execute("DELETE FROM incentive_goals WHERE title='Performance Bonus'")
    cur.close()


class TestApplyHike:
    def test_no_employees_selected_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/apply_hike", data={"quarter": "1", "year": "2026"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"no employees selected" in resp.data.lower()

    def test_high_rating_gets_hike_applied(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_review(db_engine, emp_id, 1, 2026, 4.8)  # "Exceptional" band -> 20% hike
        _set_salary(db_engine, emp_id, 50000)
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/apply_hike", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"hike applied to 1 employee" in resp.data.lower()

            cur = db_engine.cursor()
            cur.execute("SELECT monthly_ctc, last_hike_quarter, last_hike_year FROM salary_config WHERE employee_id=%s", (emp_id,))
            ctc, lq, ly = cur.fetchone()
            assert float(_unpay(ctc)) == 60000.0  # 50000 * 1.20
            assert lq == 1 and ly == 2026
            cur.close()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)

    def test_zero_rating_skipped(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_salary(db_engine, emp_id, 50000)  # no review row at all -> rating defaults to 0
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/apply_hike", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert b"hike applied to 0 employee" in resp.data.lower()
            cur = db_engine.cursor()
            cur.execute("SELECT monthly_ctc FROM salary_config WHERE employee_id=%s", (emp_id,))
            assert float(_unpay(cur.fetchone()[0])) == 50000.0
            cur.close()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)

    def test_below_expectations_band_has_zero_hike_pct_skipped(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_review(db_engine, emp_id, 1, 2026, 1.5)  # "Below Expectations" -> hike_pct 0.00
        _set_salary(db_engine, emp_id, 50000)
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/apply_hike", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert b"hike applied to 0 employee" in resp.data.lower()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)

    def test_missing_salary_config_skipped(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_review(db_engine, emp_id, 1, 2026, 4.8)
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/apply_hike", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert b"hike applied to 0 employee" in resp.data.lower()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)

    def test_already_hiked_this_quarter_is_idempotent(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_review(db_engine, emp_id, 1, 2026, 4.8)
        _set_salary(db_engine, emp_id, 50000, last_hike_quarter=1, last_hike_year=2026)
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/apply_hike", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert b"hike applied to 0 employee" in resp.data.lower()
            cur = db_engine.cursor()
            cur.execute("SELECT monthly_ctc FROM salary_config WHERE employee_id=%s", (emp_id,))
            assert float(_unpay(cur.fetchone()[0])) == 50000.0  # unchanged
            cur.close()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)


class TestAwardPerformanceBonus:
    def test_no_employees_selected_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/award_performance_bonus", data={"quarter": "1", "year": "2026"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"no employees selected" in resp.data.lower()

    def test_high_rating_awards_bonus_with_correct_amount(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_review(db_engine, emp_id, 1, 2026, 4.8)  # "Exceptional" -> incentive_pct 15%
        _set_salary(db_engine, emp_id, 50000)
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/award_performance_bonus", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"bonus awarded to 1 employee" in resp.data.lower()

            cur = db_engine.cursor()
            cur.execute(
                "SELECT amount FROM employee_incentives ei JOIN incentive_goals ig ON ig.id=ei.goal_id "
                "WHERE ei.employee_id=%s AND ig.title='Performance Bonus'", (emp_id,)
            )
            amount = cur.fetchone()[0]
            assert float(_unpay(amount)) == 7500.0  # 50000 * 15%
            cur.close()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)

    def test_zero_rating_skipped(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_salary(db_engine, emp_id, 50000)
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/award_performance_bonus", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert b"bonus awarded to 0 employee" in resp.data.lower()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)

    def test_duplicate_award_is_idempotent(self, client, seed_admin, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        _set_review(db_engine, emp_id, 1, 2026, 4.8)
        _set_salary(db_engine, emp_id, 50000)
        try:
            _admin_session(client, seed_admin)
            r1 = client.post("/award_performance_bonus", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert b"bonus awarded to 1 employee" in r1.data.lower()

            r2 = client.post("/award_performance_bonus", data={
                "quarter": "1", "year": "2026", "emp_ids": [emp_id],
            }, follow_redirects=True)
            assert b"bonus awarded to 0 employee" in r2.data.lower()

            cur = db_engine.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM employee_incentives ei JOIN incentive_goals ig ON ig.id=ei.goal_id "
                "WHERE ei.employee_id=%s AND ig.title='Performance Bonus'", (emp_id,)
            )
            assert cur.fetchone()[0] == 1  # not double-awarded
            cur.close()
        finally:
            _cleanup(db_engine, [emp_id], 1, 2026)
