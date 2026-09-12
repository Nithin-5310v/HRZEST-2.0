import os

from extensions import app_log
from utils.auth import generate_password_hash


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
    _admin_user = os.environ.get("ADMIN_USERNAME", "admin").strip()
    _admin_pass = os.environ.get("ADMIN_PASSWORD", "").strip()
    # role='admin' accounts authenticate via emailed one-time code only (see
    # blueprints/auth.py's admin_login()) -- without an email on file here,
    # a freshly seeded admin could never complete that first login.
    _admin_email = os.environ.get("ADMIN_EMAIL", "").strip() or None
    cursor.execute("SELECT COUNT(*) FROM admin_users")
    admin_count = cursor.fetchone()[0]
    if admin_count == 0 and _admin_pass:
        cursor.execute(
            "INSERT INTO admin_users (username, password, email) VALUES (%s, %s, %s)",
            (_admin_user, generate_password_hash(_admin_pass), _admin_email)
        )
        db.commit()
        if not _admin_email:
            app_log.warning(
                "Admin created: username=%s but ADMIN_EMAIL isn't set -- this account can't "
                "log in until an email is added (Settings, or 'UPDATE admin_users SET email=...').",
                _admin_user,
            )
        else:
            app_log.info("Admin created: username=%s email=%s", _admin_user, _admin_email)
        admin_count = 1
    elif admin_count == 0 and not _admin_pass:
        app_log.warning("ADMIN_PASSWORD not set in .env -- complete setup via /setup")

    # Auto-mark setup done for existing installs that already have an admin
    if admin_count > 0:
        cursor.execute("UPDATE company_settings SET setup_done=1 WHERE setup_done=0")
        db.commit()


# assign_leave_balances_for_employee moved to utils/leave_utils.py
