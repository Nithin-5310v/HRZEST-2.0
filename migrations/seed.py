import os
import re

from extensions import app_log
from utils.auth import _mask_identifier, generate_password_hash, validate_new_password

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _create_env_admin(cursor, db):
    """Create the default company's first admin from ADMIN_USERNAME,
    ADMIN_PASSWORD and ADMIN_EMAIL. Returns True if it was created; logs
    why not otherwise. Caller has checked that no admin exists yet."""
    username = os.environ.get("ADMIN_USERNAME", "admin").strip()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    # role='admin' accounts authenticate via emailed one-time code only (see
    # blueprints/auth.py's admin_login()) -- without an email on file here,
    # a freshly seeded admin could never complete that first login.
    email = os.environ.get("ADMIN_EMAIL", "").strip() or None
    if not password:
        app_log.warning("No admin account was created: ADMIN_PASSWORD isn't set in .env. "
                        "Set it and restart with MIGRATIONS_FORCE=1.")
        return False
    if email and not _EMAIL_RE.fullmatch(email):
        app_log.warning("ADMIN_EMAIL in .env isn't a single email address, so it was ignored.")
        email = None
    # Same rules as every other password (length, not the account's own
    # name or email, not in a known breach): .env.example's placeholder,
    # or "admin123", must not become the admin's real password.
    ok, err = validate_new_password(password, (username, email))
    if not ok:
        app_log.error("No admin account was created: ADMIN_PASSWORD in .env doesn't meet the password "
                      "rules. %s Set a stronger one and restart with MIGRATIONS_FORCE=1.", err)
        return False
    cursor.execute(
        "INSERT INTO admin_users (username, password, email) VALUES (%s, %s, %s)",
        (username, generate_password_hash(password), email)
    )
    db.commit()
    if not email:
        app_log.warning(
            "Admin created: username=%s but ADMIN_EMAIL isn't set -- this account can't "
            "log in until an email is added (Settings, or 'UPDATE admin_users SET email=...').",
            username,
        )
    else:
        app_log.info("Admin created: username=%s email=%s", username, _mask_identifier(email))
    # The account exists now; the plain-text copy in .env (and in every
    # backup of it) only helps someone who reads the file.
    app_log.warning("Remove ADMIN_PASSWORD from .env now that the admin account exists.")
    return True


def _seed_defaults_and_admin(cursor, db, seed_admin=True):
    """One-time seeding for a fresh install: the company_settings row,
     the env-configured admin account, and marking existing installs
     that already have an admin as setup-complete.

     seed_admin=False skips the env-var admin entirely -- used when this
     runs against a brand-new SaaS tenant schema (init_tenant_db(), called
     from provision_tenant() during /create_org self-signup). That path
     inserts its own admin right after using the credentials the customer
     actually typed on the signup form; seeding one from ADMIN_USERNAME/
     ADMIN_PASSWORD first meant every new tenant silently got a second,
     undocumented admin login shared across every tenant on the box,
     using whichever operator credentials happen to be in this server's
     .env. The blank company_settings row this also creates is still
     needed either way -- provision_tenant()'s own UPDATE assumes that
     row (id=1) already exists."""
    cursor.execute("SELECT COUNT(*) FROM company_settings")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO company_settings (setup_done) VALUES (0)")
        db.commit()

    if not seed_admin:
        return

    # Seed admin from env -- only if no admin exists yet
    cursor.execute("SELECT COUNT(*) FROM admin_users")
    admin_count = cursor.fetchone()[0]
    if admin_count == 0 and _create_env_admin(cursor, db):
        admin_count = 1

    # Auto-mark setup done for existing installs that already have an admin
    if admin_count > 0:
        cursor.execute("UPDATE company_settings SET setup_done=1 WHERE setup_done=0")
        db.commit()
