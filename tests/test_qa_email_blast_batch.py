"""PERF-04: an email blast queues its recipients in one batched INSERT.

/api/admin/email-blast ran one INSERT INTO email_queue per recipient, so a
blast to a large company made one round trip per employee.
"""
import database


def test_blast_to_everyone_queues_all_recipients_in_one_insert(client, seed_admin, db_engine, monkeypatch):
    cur = db_engine.cursor()
    for i in range(25):
        cur.execute("INSERT INTO employees (employee_id, name, email, force_pin_change) VALUES (%s,%s,%s,0)",
                    (f"QAB{i:02d}", f"Blast {i}", f"blast{i}@test.local"))
    cur.execute("SELECT COUNT(*) FROM employees WHERE email IS NOT NULL AND email != ''")
    recipients = cur.fetchone()[0]
    cur.close()

    inserts = []
    real_cursor = database._PooledConnection.cursor

    class _Counting:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, params=None):
            if "INSERT INTO email_queue" in str(sql):
                inserts.append(sql)
            return self._c.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(database._PooledConnection, "cursor", lambda self, *a, **k: _Counting(real_cursor(self, *a, **k)))
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    resp = client.post("/api/admin/email-blast", json={"target_type": "all", "subject": "QA", "body": "Hello"})
    assert resp.get_json()["ok"] is True
    assert len(inserts) == 1
    cur = db_engine.cursor()
    cur.execute("SELECT COUNT(*) FROM email_queue WHERE subject='QA'")
    assert cur.fetchone()[0] == recipients
    cur.close()
