#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go-live check for the SOC console: run it on a server before approving a
release, and after changing its .env.

    python scripts/soc_preflight.py          # reads ./.env like the app does
    python scripts/soc_preflight.py --db     # also looks for email settings saved in the app

Each setting the console depends on is reported as PASS / WARN / FAIL with the
fix. It reuses the app's own rules (utils/soc_auth.py, utils/soc_scope.py), so it
cannot disagree with how the console behaves. Exit code 1 when anything FAILs.
It never prints a secret or an address: only whether a setting is present and
what kind of value it holds.
"""
import argparse
import os
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


def _env(name):
    return os.environ.get(name, "").strip()


def _https(url):
    return urlparse(url).scheme == "https" and bool(urlparse(url).hostname)


# The same rules as utils/soc_digest.enabled() / digest_hour()
# (tests/test_soc_preflight.py holds them equal). Read here directly because
# importing that module runs the app's start-up checks, which raise on a
# misconfigured server -- and saying so is this script's job, not crashing.
_OFF = ("0", "off", "false", "no")


def _digest():
    on = os.environ.get("SOC_DIGEST", "on").strip().lower() not in _OFF
    try:
        hour = max(0, min(23, int(os.environ.get("SOC_DIGEST_HOUR", "8"))))
    except ValueError:
        hour = 8
    return on, hour


def checks(with_db=False):
    """-> [(status, title, detail, fix)] for the current environment."""
    from utils import soc_auth, soc_scope
    out = []

    def add(status, title, detail, fix=""):
        out.append((status, title, detail, fix))

    try:
        import extensions  # noqa: F401 -- runs the app's own start-up checks (SECRET_KEY, ENCRYPTION_KEY, ...)
    except Exception as exc:
        add(FAIL, "App starts", f"The app refuses to start with these settings: {str(exc)[:200]}",
            "Fix that first: nothing else matters while the app is down.")
    else:
        add(PASS, "App starts", "The app's own start-up checks pass.")

    app_env = _env("APP_ENV") or "production"
    add(PASS if app_env == "production" else FAIL, "Production mode", f"APP_ENV is '{app_env}'.",
        "" if app_env == "production" else
        "Set APP_ENV=production: in development the console is open to every network and sign-in codes are logged.")

    emails = soc_auth.login_emails()
    roles = sorted({r for r in emails.values()})
    add(PASS if emails else FAIL, "Who can sign in",
        f"{len(emails)} valid address(es) in SOC_LOGIN_EMAILS (roles: {', '.join(roles) or 'none'})."
        if emails else "SOC_LOGIN_EMAILS lists no valid address: nobody can sign in.",
        "" if emails else "SOC_LOGIN_EMAILS=you@example.com (optionally :analyst / :viewer per address).")

    state = soc_auth.allow_list_state()
    add(*{
        "restricted": (PASS, "Network allow-list", "SOC_ALLOWED_CIDRS restricts who can reach /socperson.", ""),
        "everywhere": (WARN, "Network allow-list", "SOC_ALLOWED_CIDRS opens /socperson to every address.",
                       "List your office / VPN ranges instead of 0.0.0.0/0,::/0 unless that is deliberate."),
        "invalid": (FAIL, "Network allow-list", "SOC_ALLOWED_CIDRS cannot be parsed: /socperson refuses everyone.",
                    "Use comma-separated CIDRs, e.g. SOC_ALLOWED_CIDRS=203.0.113.0/24,198.51.100.7/32"),
        "unset_closed": (FAIL, "Network allow-list", "SOC_ALLOWED_CIDRS is not set: /socperson answers 404 to everyone.",
                         "Set SOC_ALLOWED_CIDRS to your office / VPN ranges."),
        "unset_open": (WARN, "Network allow-list", "SOC_ALLOWED_CIDRS is not set (open only because APP_ENV=development).",
                       "Set SOC_ALLOWED_CIDRS before going live."),
    }[state])

    if _env("SOC_PASSKEY"):
        add(WARN, "Old passkey setting", "SOC_PASSKEY is still set; nothing reads it any more "
            "(sign-in is the emailed code, then the person's password).", "Remove SOC_PASSKEY from .env.")

    smtp_env = all(_env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))
    smtp_db = None
    if with_db and not smtp_env and out[0][0] == PASS:     # only when the app can start
        try:
            from extensions import app
            from utils.email_utils import get_email_config
            with app.app_context():
                smtp_db = bool(get_email_config())
        except Exception as exc:
            smtp_db = None
            add(WARN, "Email (database)", f"Could not read the email settings saved in the app ({type(exc).__name__}).")
    if smtp_env or smtp_db:
        add(PASS, "Email for sign-in codes", "SMTP is configured" + (" (.env)." if smtp_env else " (app settings)."))
    else:
        add(FAIL if smtp_db is False else WARN, "Email for sign-in codes",
            "No SMTP in .env" + (" or in the app's email settings" if smtp_db is False else "") +
            ": sign-in codes and the daily summary cannot be sent, so nobody can sign in.",
            "Set SMTP_HOST, SMTP_USER and SMTP_PASS" + ("" if with_db else
                                                        " (or run with --db if email is set in the app's settings)") + ".")

    app_url = _env("APP_URL")
    add(PASS if _https(app_url) else WARN, "Link in the daily summary",
        "APP_URL is an https:// address." if _https(app_url) else "APP_URL is not an https:// address: the summary has no console link.",
        "" if _https(app_url) else "Set APP_URL=https://<your domain>.")

    digest_on, digest_hour = _digest()
    add(INFO, "Daily summary email",
        f"On, sent at {digest_hour:02d}:00 Asia/Kolkata." if digest_on else "Off (SOC_DIGEST=off).")

    hook, mail = _env("SECURITY_ALERT_WEBHOOK_URL"), _env("SECURITY_ALERT_EMAIL")
    if hook and not _https(hook):
        add(WARN, "Real-time alerts", "SECURITY_ALERT_WEBHOOK_URL is not an https:// address.",
            "Use the https:// incoming-webhook URL from Slack, Discord or Teams.")
    elif hook or mail:
        add(PASS, "Real-time alerts", "Critical events and detections are sent by " +
            " and ".join(x for x in ("webhook" if hook else "", "email" if mail else "") if x) + ".")
    else:
        add(WARN, "Real-time alerts", "No alert channel: critical events wait for the next daily summary.",
            "Set SECURITY_ALERT_WEBHOOK_URL (Slack / Discord / Teams) and/or SECURITY_ALERT_EMAIL.")

    add(PASS if _env("TRUSTED_PROXY_HOPS") else WARN, "Real visitor addresses",
        "TRUSTED_PROXY_HOPS is set." if _env("TRUSTED_PROXY_HOPS") else
        "TRUSTED_PROXY_HOPS is not set: behind a proxy every visitor looks like the proxy, and the console "
        "leaves the proxy's own address out as the server's traffic.",
        "" if _env("TRUSTED_PROXY_HOPS") else "Set it to the number of proxies in front of the app (1 behind nginx).")

    dash = _env("SOC_DASHBOARD_URL")
    if dash and not _https(dash):
        add(FAIL, "External SOC dashboard", "SOC_DASHBOARD_URL is not https://: /super_admin/soc refuses to embed it.",
            "Serve the soc-dashboard over HTTPS and use that address.")
    elif dash:
        add(PASS, "External SOC dashboard", "SOC_DASHBOARD_URL is an https:// address.")
    if _env("SOC_ADMIN_TOKEN"):
        add(WARN, "Old dashboard token", "SOC_ADMIN_TOKEN is still set; nothing reads it any more.",
            "Remove it, and change the token on the soc-dashboard: it used to be visible in the page.")

    n = (len(soc_scope.test_networks()), len(soc_scope.test_accounts()), len(soc_scope.test_companies()))
    add(INFO, "Your testing left out", f"{n[0]} address range(s), {n[1]} account(s), {n[2]} company(ies) listed "
        "in SOC_TEST_CIDRS / SOC_TEST_ACCOUNTS / SOC_TEST_COMPANIES.")
    return out


def render(results):
    lines = [f"SOC console go-live check (APP_ENV={_env('APP_ENV') or 'production'})", ""]
    for status, title, detail, fix in results:
        lines.append(f"  {status}  {title}: {detail}")
        if fix and status in (FAIL, WARN):
            lines.append(f"        -> {fix}")
    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    lines += ["", (f"Result: {fails} problem(s) to fix before going live" if fails else "Result: ready to go live")
              + (f", {warns} warning(s)." if warns else ".")]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", action="store_true", help="also look for email settings saved in the app (needs the database)")
    ap.add_argument("--env-file", default=os.path.join(ROOT, ".env"), help="the .env to read (default: ./.env)")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        # The same files, in the same order, as app.py: ./.env, then hashi/.env.
        load_dotenv(args.env_file)
        load_dotenv(os.path.join(ROOT, "hashi", ".env"))
    except Exception:       # nosec B110 -- without python-dotenv, the process environment is checked as is
        pass
    results = checks(with_db=args.db)
    print(render(results))
    return 1 if any(r[0] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
