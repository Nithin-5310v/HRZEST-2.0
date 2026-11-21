"""Every SOC console view in a real browser, at desktop and phone width.

tests/ checks the console's API and the frontend's code statically; nothing there
renders a view. This opens each one with realistic data and fails on anything a
person would hit: a JavaScript error, a console error, a failed /socperson/api
call, a page wider than the phone screen, or a view that never finishes loading.

SOC_AUDIT_SHOTS=<dir> also saves a screenshot of every view (for a human review).
Sign-in here is the emailed code, then setting the account's first password; the
password step has its own browser test (test_soc_password.py).
"""
import os
import re

import pytest
from playwright.sync_api import expect

from utils import soc_auth

EMAIL = "e2e-soc-admin@example.invalid"
SHOTS = os.environ.get("SOC_AUDIT_SHOTS", "")

# (name, hash route) -- every registered view, plus the drill-downs people use most.
ROUTES = [
    ("overview", "#/overview"),
    ("alerts", "#/alerts"),
    ("events", "#/events"),
    ("events-filtered", "#/events?ip=185.220.101.1"),
    ("traffic", "#/traffic"),
    ("brief-kpi", "#/explain?kind=kpi&value=failed_logins"),
    ("brief-family", "#/explain?kind=family&value=waf"),
    ("lookup-empty", "#/entity"),
    ("lookup-ip", "#/entity?ip=185.220.101.1"),
    ("lookup-account", "#/entity?identifier=asha%40acme.example"),
    ("analysis", "#/analysis"),
    ("logs", "#/logs"),
    ("cases", "#/cases"),
    ("response", "#/response"),
    ("banreview", "#/banreview"),
    ("tenants", "#/tenants"),
    ("fraud", "#/fraud"),
    ("detections", "#/detections"),
    ("integrity", "#/integrity"),
    ("posture", "#/posture"),
    ("admin-users", "#/admin"),
    ("admin-audit", "#/admin?tab=audit"),
]
VIEWPORTS = {"desktop": {"width": 1366, "height": 900}, "phone": {"width": 390, "height": 844}}

SEED_TRAFFIC = [
    # (route pattern, method, status, hits, total ms, slowest ms, minutes ago)
    ("/login", "POST", 200, 40, 3200, 180, 30),
    ("/login", "POST", 401, 8, 400, 60, 29),
    ("/employees", "GET", 200, 25, 2500, 240, 20),
    ("/view_payslip/<emp_id>/<int:year>/<int:month>", "GET", 200, 5, 900, 310, 15),
    ("/api/attendance", "GET", 500, 2, 2600, 1900, 10),
]

SEED_EVENTS = [
    # (event_type, level, ip, identifier, path, tenant, minutes_ago, message)
    ("auth.failure", "WARNING", "45.33.32.156", "asha@acme.example", "/login", "e2e-co", 50, "Invalid password"),
    ("auth.failure", "WARNING", "45.33.32.156", "ravi@acme.example", "/login", "e2e-co", 48, "Invalid password"),
    ("auth.failure", "WARNING", "45.33.32.156", "meena@acme.example", "/login", "e2e-co", 47, "Invalid password"),
    ("auth.lockout", "WARNING", "45.33.32.156", "meena@acme.example", "/login", "e2e-co", 46, "Account locked"),
    ("detection.credential_stuffing", "ERROR", "45.33.32.156", "45.33.32.156", "/login", None, 45,
     "45.33.32.156 failed against 3 accounts in 10 minutes"),
    ("waf.sqli_blocked", "ERROR", "185.220.101.1", None, "/employees", "e2e-co", 30, "SQL injection blocked"),
    ("waf.xss_blocked", "ERROR", "185.220.101.1", None, "/search", "e2e-co", 29, "Cross-site scripting blocked"),
    ("secops.honeypot_triggered", "CRITICAL", "203.0.113.50", None, "/.env", None, 20, "Honeypot path requested"),
    ("access.denied", "WARNING", "198.51.100.23", "hr_bob", "/payroll", "e2e-co", 15, "Role cannot open payroll"),
    ("auth.admin_login_success", "INFO", "198.51.100.23", "hr_bob", "/login", "e2e-co", 14, "Admin signed in"),
    # a very long value, as attackers send: must wrap, never widen the page
    ("waf.path_traversal_blocked", "ERROR", "185.220.101.1", None,
     "/download?file=" + "..%2F" * 40 + "etc%2Fpasswd", "e2e-co", 10, "Path traversal blocked"),
]


@pytest.fixture
def soc(monkeypatch, db_conn, base_url):
    codes = []
    monkeypatch.setenv("SOC_LOGIN_EMAILS", EMAIL)
    monkeypatch.delenv("SOC_ALLOWED_CIDRS", raising=False)
    monkeypatch.setattr(soc_auth, "deliver_login_code", lambda email, code: codes.append(code) or True)
    cur = db_conn.cursor()

    def clean():
        cur.execute("DELETE FROM att_master.soc_users WHERE email=%s", (EMAIL,))
        cur.execute("DELETE FROM att_master.tenants WHERE subdomain='e2e-co'")
        cur.execute("DELETE FROM att_master.soc_cases WHERE created_by='e2e'")
        cur.execute("DELETE FROM att_master.soc_ban_reviews WHERE ip IN ('185.220.101.1', '45.33.32.156')")
        cur.execute("DELETE FROM public.banned_ips WHERE ip='45.33.32.156'")
        cur.execute("DELETE FROM att_master.http_minute_stats WHERE tenant='e2e-co'")
        cur.execute("DELETE FROM att_master.http_telemetry_flushes WHERE host='e2e-seed'")
    clean()
    cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
                "VALUES ('E2E Co', 'e2e-co', %s, 'active')", (os.environ["DB_NAME"],))
    for et, level, ip, ident, path, tenant, ago, msg in SEED_EVENTS:
        cur.execute("INSERT INTO public.security_events (event_type, level, message, identifier, ip, path, method, "
                    "tenant, created_at) VALUES (%s,%s,%s,%s,%s,%s,'GET',%s, NOW() - (%s * INTERVAL '1 minute'))",
                    (et, level, msg, ident, ip, path, tenant, ago))
    for ep, method, status, hits, total_ms, max_ms, ago in SEED_TRAFFIC:
        buckets = [0] * 10
        buckets[5 if max_ms <= 250 else 6 if max_ms <= 500 else 8] = hits
        cur.execute("INSERT INTO att_master.http_minute_stats (minute, endpoint, method, status, tenant, hits, total_ms, "
                    "max_ms, buckets) VALUES (date_trunc('minute', NOW() - (%s * INTERVAL '1 minute')), %s, %s, %s, "
                    "'e2e-co', %s, %s, %s, %s)", (ago, ep, method, status, hits, total_ms, max_ms, buckets))
    cur.execute("INSERT INTO att_master.http_telemetry_flushes (host, pid, flushed_at) VALUES ('e2e-seed', 1, NOW())")
    cur.execute("INSERT INTO att_master.soc_cases (title, severity, status, created_by, summary) "
                "VALUES ('Credential stuffing against Acme', 'high', 'investigating', 'e2e', 'Three accounts tried')")
    cur.execute("INSERT INTO att_master.soc_ban_reviews (ip, source, reason, hits) VALUES "
                "('185.220.101.1', 'waf', 'Repeated SQL injection payloads', 3)")
    cur.execute("INSERT INTO public.banned_ips (ip, reason, banned_by, expires_at) VALUES "
                "('45.33.32.156', 'Credential stuffing', 'e2e', NOW() + INTERVAL '1 day')")
    yield {"codes": codes, "url": base_url.replace("127.0.0.1", "localhost")}
    clean()
    cur.close()


def _sign_in(page, soc):
    page.goto(soc["url"] + "/socperson/login")
    page.fill('input[name="email"]', EMAIL)
    page.click("button:has-text('Send sign-in code')")
    page.fill('input[name="code"]', soc["codes"][-1])
    page.click("button:has-text('Continue')")
    page.fill('input[name="password"]', "e2e long passphrase 4 soc")     # each test starts with a new account
    page.fill('input[name="confirm"]', "e2e long passphrase 4 soc")
    page.click("button:has-text('Set password and sign in')")
    expect(page).to_have_url(soc["url"] + "/socperson")


def _watch(page, where):
    """Collect what a person would hit, labelled with the view it happened on."""
    problems = []

    def add(msg):
        problems.append(f"[{where['view']}] {msg}")
    page.on("pageerror", lambda exc: add(f"page error: {exc}"))
    page.on("console", lambda m: m.type == "error" and add(f"console error: {m.text}"))
    page.on("response", lambda r: "/socperson/" in r.url and r.status >= 400
            and add(f"HTTP {r.status} {r.request.method} {r.url.split('/socperson', 1)[1]}"))
    return problems, add


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_every_view_renders_cleanly(browser, soc, viewport):
    ctx = browser.new_context(viewport=VIEWPORTS[viewport])
    page = ctx.new_page()
    try:
        where = {"view": "sign-in"}
        problems, add = _watch(page, where)
        _sign_in(page, soc)
        for name, route in ROUTES:
            where["view"] = name
            page.goto(soc["url"] + "/socperson" + route)
            page.wait_for_load_state("networkidle")
            # Each view replaces the "loading" state with its content; wait for that.
            expect(page.locator("#view")).not_to_be_empty(timeout=10_000)
            page.wait_for_timeout(250)
            wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            if wide > 1:
                add(f"page is {wide}px wider than the screen")
            if SHOTS:
                os.makedirs(SHOTS, exist_ok=True)
                page.screenshot(path=os.path.join(SHOTS, f"{viewport}-{name}.png"), full_page=True)
        # The event drawer: open the first event from the list.
        where["view"] = "event-drawer"
        page.goto(soc["url"] + "/socperson#/events")
        page.wait_for_load_state("networkidle")
        page.locator("#view tbody tr").first.locator("td").first.click()     # the row, not its IP link
        expect(page.locator("#drawer")).to_be_visible()
        page.wait_for_load_state("networkidle")
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, f"{viewport}-event-drawer.png"), full_page=True)
        assert problems == [], "\n".join(problems)
    finally:
        ctx.close()


def test_long_attacker_values_wrap_instead_of_widening_the_page(browser, soc):
    ctx = browser.new_context(viewport=VIEWPORTS["phone"])
    page = ctx.new_page()
    try:
        _sign_in(page, soc)
        page.goto(soc["url"] + "/socperson#/events?ip=185.220.101.1")
        page.wait_for_load_state("networkidle")
        expect(page.locator("#view")).to_contain_text(re.compile("Path traversal|traversal", re.I))
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1
    finally:
        ctx.close()
