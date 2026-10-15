"""BUG-37: assigning onboarding notifies the employee in the app.

onboarding_assign() inserted its "Onboarding Started" notification into an
employee_notifications table that doesn't exist, so it always failed (only
a warning was logged) and the employee never saw it.
"""
import pytest


@pytest.fixture
def template_id(db_engine):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO onboarding_templates (name) VALUES ('QA onboarding') RETURNING id")
    tid = cur.fetchone()[0]
    cur.close()
    return tid


def test_employee_gets_an_in_app_notification_when_onboarding_is_assigned(client, db_engine, seed_admin, seed_employee, template_id):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    client.post("/onboarding_assign", data={"employee_id": seed_employee["employee_id"], "template_id": template_id})
    cur = db_engine.cursor()
    cur.execute("SELECT title, message FROM notifications WHERE employee_id=%s AND title LIKE %s",
                (seed_employee["employee_id"], "%Onboarding%"))
    rows = cur.fetchall()
    cur.close()
    assert len(rows) == 1 and "QA onboarding" in rows[0][1]
