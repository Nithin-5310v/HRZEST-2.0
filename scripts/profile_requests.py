"""Count true database round-trips per request: queries + SET search_path + borrows.

Reproduces the table in MODERNIZATION.md. Run from the repo root against a
DISPOSABLE database (it inserts a throw-away admin and employee):

    DB_HOST=127.0.0.1 DB_NAME=att_test DB_USER=postgres DB_PASS=...     SECRET_KEY=x APP_ENV=development python scripts/profile_requests.py
    # compare the opt-in switch:
    DB_SKIP_REDUNDANT_SET_SEARCH_PATH=true ... python scripts/profile_requests.py

"Round-trip" = every query sent through a pooled cursor plus every SET
search_path actually sent to the server. Wall-clock milliseconds on a local
database say little (no network latency); multiply round-trips by your real
RTT to estimate what a page costs against RDS.
"""
import os
import sys

if "test" not in os.environ.get("DB_NAME", "") and os.environ.get("PROFILE_ALLOW_ANY_DB") != "1":
    sys.exit("refusing to run: DB_NAME does not look like a test database "
             "(set PROFILE_ALLOW_ANY_DB=1 to override -- it writes rows).")
import time

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "profile-secret")
os.environ.setdefault("ENCRYPTION_KEY", "_jboJL8OrI9muPNyf0xCNrakSo_Iz5EbJSQ1KpDcAgY=")
sys.path.insert(0, os.getcwd())

from extensions import app as flask_app  # noqa: E402
import wsgi  # noqa: E402,F401  (registers blueprints exactly like production)
import database  # noqa: E402
import app as _app  # noqa: E402

flask_app.config["TESTING"] = True
_app.limiter.enabled = False
for flag in ("MANDATORY_ADMIN_MFA", "MANDATORY_LOGIN_MFA", "MANDATORY_PLATFORM_ADMIN_MFA", "REQUIRE_EMAIL_2FA"):
    flask_app.config[flag] = False

STATS = {"queries": 0, "sets": 0, "borrows": 0}


class CountingCursor:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, *a, **k):
        STATS["queries"] += 1
        return self._cur.execute(sql, *a, **k)

    def executemany(self, sql, *a, **k):
        STATS["queries"] += 1
        return self._cur.executemany(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._cur, name)


_orig_cursor = database._PooledConnection.cursor
database._PooledConnection.cursor = lambda self, *a, **k: CountingCursor(_orig_cursor(self, *a, **k))

_orig_set = database._set_search_path


def _counted_set(conn, schema):
    before = getattr(conn, "_hrz_sp_reuse", None)
    result = _orig_set(conn, schema)
    after = getattr(conn, "_hrz_sp_reuse", None)
    skipped = database._SKIP_REDUNDANT_SP and before is not None and after == before + 1
    if not skipped:                 # only count statements actually sent to the server
        STATS["sets"] += 1
    return result


database._set_search_path = _counted_set
_orig_borrow = database._borrow


def _counted_borrow():
    STATS["borrows"] += 1
    return _orig_borrow()


database._borrow = _counted_borrow


def measure(label, fn, repeat=5):
    fn()  # warm up (template compile, caches)
    rows = []
    for _ in range(repeat):
        STATS.update(queries=0, sets=0, borrows=0)
        t = time.perf_counter()
        r = fn()
        wall = (time.perf_counter() - t) * 1000
        rows.append((wall, STATS["queries"], STATS["sets"], STATS["borrows"], r.status_code))
    rows.sort(key=lambda x: x[0])
    wall, q, s, b, status = rows[len(rows) // 2]
    print(f"{label:<44} HTTP {status} | {wall:5.0f} ms | {q:>2} queries + {s:>2} SET = {q + s:>2} round-trips | {b:>2} borrows")


with flask_app.app_context():
    from utils.auth import generate_password_hash
    db = database.get_db_connection()
    cur = db._conn.cursor()
    cur.execute("DELETE FROM login_attempts WHERE identifier IN ('prof_admin','PROF001')")
    cur.execute("INSERT INTO admin_users (username,password,email) VALUES ('prof_admin',%s,'p@x.local') "
                "ON CONFLICT (username) DO UPDATE SET password=EXCLUDED.password",
                (generate_password_hash("Prof@12345"),))
    cur.execute("INSERT INTO employees (employee_id,name,email,password,force_pin_change) VALUES "
                "('PROF001','Prof Emp','pe@x.local',%s,0) ON CONFLICT (employee_id) DO UPDATE SET password=EXCLUDED.password",
                (generate_password_hash("EmpPass@1"),))
    cur.close()
    db.close()

print("ROUND-TRIPS PER REQUEST (median of 5, warm)")
c = flask_app.test_client()
measure("GET /healthz", lambda: c.get("/healthz"))
measure("GET /login (anonymous)", lambda: c.get("/login"))
c.post("/login", data={"identifier": "prof_admin", "password": "Prof@12345"})
for path in ("/admin", "/employees", "/today_present", "/monthly_report", "/analytics", "/salary_report",
             "/leave_holidays", "/api/admin/attendance/flagged"):
    measure(f"GET {path} (admin session)", lambda p=path: c.get(p))

c2 = flask_app.test_client()
tok = c2.post("/api/employee/login", json={"employee_id": "PROF001", "password": "EmpPass@1"}).get_json().get("token")
hdr = {"Authorization": f"Bearer {tok}"}
measure("GET /api/employee/portal (Bearer)", lambda: c2.get("/api/employee/portal", headers=hdr))
measure("POST /api/employee/checkin (Bearer)",
        lambda: c2.post("/api/employee/checkin", json={"lat": 12.97, "lon": 77.59}, headers=hdr))
