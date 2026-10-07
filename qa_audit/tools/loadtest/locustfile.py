"""QA load test: login, admin dashboard, attendance check-in, payslip download.

    locust -f qa_audit/tools/loadtest/locustfile.py --host http://127.0.0.1:5050 \
           --headless -u 50 -r 10 -t 3m --csv qa_audit/loadtest/before_50

Each simulated user gets its own X-Forwarded-For address (trusted by
wsgi.py's ProxyFix), so the app's per-IP rate limits apply per user, as they
would behind nginx. The session cookie is carried manually because the app
always marks it Secure and this test runs over plain HTTP locally.
"""
import datetime
import itertools
import os
import random
import re

from locust import HttpUser, between, task

PASSWORD = os.environ.get("LT_PASSWORD") or "LoadTest-Only-Pw-2026"
N_EMPLOYEES = int(os.environ.get("LT_EMPLOYEES", "200"))
_ip = itertools.count(1)
_emp = itertools.count(1)
_COOKIE_RE = re.compile(r"(?:^|,\s*)session=([^;]*)")
TODAY = datetime.date.today()
PAYSLIP_YEAR, PAYSLIP_MONTH = (TODAY.year, TODAY.month - 1) if TODAY.month > 1 else (TODAY.year - 1, 12)


class _Base(HttpUser):
    abstract = True
    wait_time = between(1, 3)

    def on_start(self):
        n = next(_ip)
        self.client.headers["X-Forwarded-For"] = f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"
        self.session_cookie = None

    def _remember_cookie(self, resp):
        raw = resp.headers.get("Set-Cookie", "")
        m = _COOKIE_RE.search(raw)
        if m:
            self.session_cookie = m.group(1)
            self.client.headers["Cookie"] = f"session={self.session_cookie}"

    def web_login(self, identifier, name):
        self.client.headers.pop("Cookie", None)
        self.client.cookies.clear()
        with self.client.post("/login", data={"identifier": identifier, "password": PASSWORD},
                              allow_redirects=False, name=name, catch_response=True) as r:
            if r.status_code != 302:
                r.failure(f"login status {r.status_code}")
                return False
            self._remember_cookie(r)
            r.success()
            return True

    def get(self, url, name):
        with self.client.get(url, allow_redirects=False, name=name, catch_response=True) as r:
            self._remember_cookie(r)
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")


class AdminUser(_Base):
    weight = 1

    def on_start(self):
        super().on_start()
        self.web_login("lt_admin", "login (web, admin)")

    @task(4)
    def dashboard(self):
        self.get("/admin", "dashboard /admin")

    @task(2)
    def payslip(self):
        emp = f"LT_{random.randint(1, N_EMPLOYEES):04d}"
        self.get(f"/download_payslip/{emp}/{PAYSLIP_YEAR}/{PAYSLIP_MONTH}", "payslip download")

    @task(1)
    def relogin(self):
        self.web_login("lt_admin", "login (web, admin)")


class EmployeeUser(_Base):
    weight = 4

    def on_start(self):
        super().on_start()
        self.emp_id = f"LT_{((next(_emp) - 1) % N_EMPLOYEES) + 1:04d}"
        self.token = None
        with self.client.post("/api/employee/login", json={"employee_id": self.emp_id, "password": PASSWORD},
                              name="login (api, employee)", catch_response=True) as r:
            if r.status_code == 200 and (r.json() or {}).get("token"):
                self.token = r.json()["token"]
                r.success()
            else:
                r.failure(f"api login status {r.status_code}")
        self.web_login(self.emp_id, "login (web, employee)")

    @task(3)
    def checkin(self):
        if not self.token:
            return
        with self.client.post("/api/employee/checkin", json={"lat": 12.9716, "lon": 77.5946},
                              headers={"Authorization": f"Bearer {self.token}"},
                              name="check-in /api/employee/checkin", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")

    @task(2)
    def own_payslip(self):
        self.get(f"/download_payslip/{self.emp_id}/{PAYSLIP_YEAR}/{PAYSLIP_MONTH}", "payslip download")
