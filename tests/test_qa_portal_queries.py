"""PERF-03: the employee portal doesn't compute payslip summaries nobody sees.

employee_portal() built a "last 3 months payslip" summary -- 9 queries on
every portal load (attendance, incentives and overtime for each month) --
and passed it to the template, which never displays it.
"""
import datetime

import database


class _CountingCursor:
    def __init__(self, cur, log):
        self._cur, self._log = cur, log

    def execute(self, sql, params=None):
        self._log.append((sql, params))
        return self._cur.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)


def _portal_queries(client, seed_employee, monkeypatch):
    log = []
    real_cursor = database._PooledConnection.cursor
    monkeypatch.setattr(database._PooledConnection, "cursor",
                        lambda self, *a, **k: _CountingCursor(real_cursor(self, *a, **k), log))
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    resp = client.get("/employee_portal")
    assert resp.status_code == 200
    return log


def test_portal_runs_no_queries_for_the_hidden_payslip_summary(client, seed_employee, monkeypatch):
    first_of_month = datetime.date.today().replace(day=1)
    log = _portal_queries(client, seed_employee, monkeypatch)
    previous_month_attendance = [
        sql for sql, params in log
        if "FROM attendance" in sql and params
        and any(isinstance(p, datetime.date) and p < first_of_month for p in params)
    ]
    per_month_extras = [sql for sql, params in log
                        if ("employee_incentives" in sql or "overtime_records" in sql)
                        and params and len(params) == 3
                        and (params[1], params[2]) != (first_of_month.month, first_of_month.year)]
    assert previous_month_attendance == [] and per_month_extras == []
