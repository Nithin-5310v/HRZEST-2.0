"""The owner admin's own employee profile.

HR and manager logins already are employees: their admin username is their
employee_id, so My Workspace knows which record is theirs. The company's
owner admin logs in with a username or email that isn't an employee, so it
had no way to record its own attendance. "Create my employee profile" on the
Organization page makes an ordinary employees row for them and links it to
the admin login (admin_users.linked_employee_id). From then on My Workspace
and the app's "Me" tab open that record, with the same login and 2-step
check: no employee password is ever shown or sent for it.

The linked employee is not a paid seat (BILLABLE_EMPLOYEES_SQL in
utils/plan_limits.py) but is otherwise a normal employee: attendance,
leave, payroll and reports treat it like anyone else.
"""

OWNER_ROLE = "admin"


def linked_employee_id(cursor, username):
    """The active employee_id linked to admin login `username`, or None
    (no profile, or the employee row is gone or deactivated)."""
    if not username:
        return None
    cursor.execute(
        "SELECT a.linked_employee_id FROM admin_users a "
        "JOIN employees e ON e.employee_id = a.linked_employee_id "
        "WHERE a.username=%s AND COALESCE(e.is_active, 1) = 1",
        (username,),
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def is_linked_employee(cursor, employee_id):
    """True when `employee_id` is some admin login's own profile."""
    if not employee_id:
        return False
    cursor.execute("SELECT 1 FROM admin_users WHERE linked_employee_id=%s", (employee_id,))
    return cursor.fetchone() is not None


def session_linked_employee_id():
    """linked_employee_id() for the current owner-admin web session, looked
    up once per request. None for any other session."""
    from flask import g, session
    if not session.get("admin_logged_in") or session.get("admin_role") != OWNER_ROLE:
        return None
    if not hasattr(g, "_own_linked_employee_id"):
        value = None
        try:
            from database import get_db_connection
            db = get_db_connection()
            cursor = db.cursor(buffered=True)
            try:
                value = linked_employee_id(cursor, session.get("admin_username"))
            finally:
                cursor.close()
                db.close()
        except Exception:
            value = None
        g._own_linked_employee_id = value
    return g._own_linked_employee_id
