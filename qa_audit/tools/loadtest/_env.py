"""Process-local environment for the QA load test (QA_AUDIT.md §0.4).

Only os.environ of the CURRENT process is changed -- nothing in the repo's
config or defaults. Imported first by run_server.py / seed.py / cleanup.py.

  * DB_NAME=att_test        -- the local test database, never a real one
  * MANDATORY_*_MFA=false   -- owner-approved for this one process only
  * every external-service credential blanked, so the run can't send real
    email, push notifications, alert webhooks, AI or payment calls
Rate limiting is NOT disabled (there is no env switch for it): the
locustfile gives each simulated user its own X-Forwarded-For address,
which wsgi.py's ProxyFix(x_for=1) trusts, so per-IP limits apply per user
exactly as they would for real users behind nginx.
"""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

os.environ["DB_NAME"] = "att_test"
os.environ["APP_ENV"] = "development"
os.environ["REQUIRE_REDIS"] = "false"
os.environ.setdefault("DB_POOL_MAX_CONN", "40")  # >= server threads; see README in run_server.py
for flag in ("MANDATORY_LOGIN_MFA", "MANDATORY_ADMIN_MFA", "MANDATORY_PLATFORM_ADMIN_MFA", "REQUIRE_EMAIL_2FA"):
    os.environ[flag] = "false"

_EXTERNAL = (
    "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "EXPO_ACCESS_TOKEN", "SECURITY_ALERT_WEBHOOK_URL",
    "SECURITY_ALERT_SIGNING_SECRET", "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "REDIS_HOST", "REDIS_URL",
    "SOC_DASHBOARD_URL", "SOC_ADMIN_TOKEN", "GRAFANA_URL", "N8N_WEBHOOK_URL",
)
for name in _EXTERNAL:
    os.environ[name] = ""
for name in list(os.environ):
    if name.startswith("HEARTBEAT_URL_"):
        os.environ[name] = ""

LT_PREFIX = "LT_"
LT_ADMIN = "lt_admin"
LT_PASSWORD = os.environ.get("LT_PASSWORD") or "LoadTest-Only-Pw-2026"  # local att_test accounts only
