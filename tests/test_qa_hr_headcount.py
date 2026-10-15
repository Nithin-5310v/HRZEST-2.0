"""PERF-05: the HR reports' headcount trend takes one query, not one per month."""
import datetime

import database


def test_headcount_trend_uses_one_query_and_counts_correctly(client, seed_hr_admin, seed_assigned_employee, db_engine, monkeypatch):
    from utils.auth import HR_ROLE
    cur = db_engine.cursor()
    joined = datetime.date.today().replace(day=1) - datetime.timedelta(days=70)     # ~3 months ago
    cur.execute("UPDATE employees SET date_of_joining=%s WHERE employee_id=%s", (joined, seed_assigned_employee["employee_id"]))
    cur.close()
    queries = []
    real_cursor = database._PooledConnection.cursor

    class _Counting:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, params=None):
            if "date_of_joining" in str(sql):
                queries.append(sql)
            return self._c.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(database._PooledConnection, "cursor", lambda self, *a, **k: _Counting(real_cursor(self, *a, **k)))
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    trend = client.get("/api/hr_dashboard/reports").get_json()["headcount_trend"]
    assert len(queries) == 1
    counts = [m["count"] for m in trend]
    assert len(counts) == 6 and counts[-1] == 1 and counts[0] == 0          # joined ~3 months ago
