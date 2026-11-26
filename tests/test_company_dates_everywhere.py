"""Calendar dates come from the company's (or the platform's) clock everywhere.

The server and the database run on UTC, so date.today() and SQL CURRENT_DATE
are yesterday's date in India until 05:30. QA BUG-17..27 moved the main
blueprints to company_today() / platform_today(), and
tests/test_qa_no_server_clock.py guards those files; trial billing, the
payslip footer, "on leave today", platform traffic counts and others still
used the server's date. This guards the whole code base, and checks a few of
the fixed paths end to end.
"""
import datetime
import io
import pathlib
import re
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "tests_e2e", "vendor", "mobile", "qa_audit", "node_modules", ".git", "venv", ".venv",
             ".venv_test", "observability", "infra", "static", "templates"}

# date.today() / SQL CURRENT_DATE give the server's (UTC) date; utcnow() is
# deprecated (use datetime.now(datetime.timezone.utc)).
SERVER_DATE = re.compile(r"\bdate\.today\(\)|\butcnow\(|\bCURRENT_DATE\b")

# Reviewed exceptions: (file, text of the line) -> why it may stay.
ALLOWED = {
    ("utils/helpers/settings.py", "datetime.datetime.now()/datetime.date.today() anywhere \"today\"/\"now\" is used"):
        "company_now()'s docstring telling callers not to use them",
}


def _python_files():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in SKIP_DIRS or any(p.startswith(("venv", ".venv")) for p in rel.parts):
            continue
        yield rel.as_posix(), path


def _code_lines(path):
    """Source lines with comments removed (strings, i.e. SQL, are kept)."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            lines[row - 1] = lines[row - 1][:col]
    return lines


def test_no_server_dates_anywhere():
    hits = []
    for rel, path in _python_files():
        for n, line in enumerate(_code_lines(path), 1):
            if SERVER_DATE.search(line) and (rel, line.strip()) not in ALLOWED:
                hits.append(f"{rel}:{n}: {line.strip()}")
    assert hits == [], ("Use company_today()/company_now() (a company's data) or platform_today() "
                        "(platform billing, platform-wide counts) instead of the server's date:\n" + "\n".join(hits))


def test_the_guard_sees_sql_and_ignores_comments(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('# CURRENT_DATE in a comment is fine\nq = "SELECT 1 WHERE d = CURRENT_DATE"  # x\n',
                      encoding="utf-8")
    lines = _code_lines(sample)
    assert not SERVER_DATE.search(lines[0])
    assert SERVER_DATE.search(lines[1])


# ── end to end ──────────────────────────────────────────────────────────────

FUTURE = datetime.date(2031, 5, 20)       # never the database's CURRENT_DATE


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


@pytest.fixture
def leave_on_future_day(db_engine, seed_employee):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
                "VALUES ('TST001', %s, 'test', 'Approved') RETURNING id", (FUTURE,))
    lid = cur.fetchone()[0]
    yield
    cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
    cur.close()


@pytest.mark.parametrize("today, expected", [(FUTURE, "On Leave"), (FUTURE - datetime.timedelta(days=1), "Active")])
def test_on_leave_today_follows_the_company_date(client, seed_admin, leave_on_future_day, monkeypatch, today, expected):
    import blueprints.employees.directory as directory
    monkeypatch.setattr(directory, "company_today", lambda: today)
    _admin_session(client, seed_admin["username"])
    page = client.get("/employee_detail/TST001").get_data(as_text=True)
    badge = re.search(r'class="pbadge [^"]*">\s*([A-Za-z ]+?)\s*<', page)
    assert badge and badge.group(1) == expected


def test_payslip_generated_on_is_the_company_date(monkeypatch):
    import utils.helpers
    from utils.salary_utils import build_salary_slip_html
    monkeypatch.setattr(utils.helpers, "company_today", lambda: FUTURE)
    args = dict(emp_name="A", emp_id="E1", emp_email="a@test.local", month_name="May 2031", year=2031, month=5,
                salary_data={"monthly_ctc": 50000, "basic_pct": 50, "full_days": 22, "late_days": 0,
                             "half_days": 0, "absent": 0})
    assert "Generated on 20 May 2031" in build_salary_slip_html(**args)
    assert "Generated on 01 June 2031" in build_salary_slip_html(**args, generated_on=datetime.date(2031, 6, 1))


def test_page_views_count_on_the_platform_date(client, monkeypatch):
    import utils.analytics as analytics
    monkeypatch.setattr(analytics, "platform_today", lambda: FUTURE)
    before = analytics.get_traffic_stats()
    analytics.track_page_view("/test-company-dates")
    analytics.track_page_view("/test-company-dates")
    after = analytics.get_traffic_stats()
    assert after["today_total"] - before["today_total"] == 2


def test_legacy_qr_window_uses_the_platform_date(monkeypatch):
    import qr_generator
    import utils.helpers
    monkeypatch.setenv("QR_LEGACY_ACCEPT_UNTIL", "2031-05-20")
    monkeypatch.setattr(utils.helpers, "platform_today", lambda: FUTURE)
    assert qr_generator._legacy_qr_accepted() is True
    monkeypatch.setattr(utils.helpers, "platform_today", lambda: FUTURE + datetime.timedelta(days=1))
    assert qr_generator._legacy_qr_accepted() is False
