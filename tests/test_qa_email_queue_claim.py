"""SCALE-06: each queued email is sent once, even with several drainers.

Every gunicorn worker runs its own email-queue drainer. A drainer used to
SELECT ten pending rows without locking them and mark each one 'sending'
only just before sending it, so a second drainer starting mid-batch picked
up the same rows and the recipients got duplicates (payslips, OTP codes,
blasts). Rows left in 'sending' by a crashed worker were never retried.
"""
import datetime
import secrets

import pytest

import utils.email_utils as eu
from extensions import app as flask_app

SMTP = {"host": "smtp.invalid", "port": 587, "user": "u", "password": "p",
        "from_name": "QA", "from_email": "qa@test.local"}


@pytest.fixture
def queue(db_engine, monkeypatch):
    tag = "QA-SCALE06-" + secrets.token_hex(3)
    sent = []
    monkeypatch.setattr(eu, "get_email_config", lambda: SMTP)
    cur = db_engine.cursor()

    def add(n, **cols):
        for i in range(n):
            names = ["to_email", "subject", "html_body"] + list(cols)
            values = [f"r{i}@test.local", f"{tag} {i}", "<p>x</p>"] + list(cols.values())
            cur.execute(f"INSERT INTO email_queue ({', '.join(names)}) "  # nosec B608 -- fixed column names
                        f"VALUES ({', '.join(['%s'] * len(values))})", values)

    def mine(rows):
        return [r for r in rows if r.startswith(tag)]

    yield add, sent, mine, tag
    cur.execute("DELETE FROM email_queue WHERE subject LIKE %s", (tag + "%",))
    cur.close()


def _drain():
    with flask_app.app_context():
        eu._drain_queue_for_schema("public")


def test_overlapping_drainers_send_each_email_once(queue, monkeypatch):
    add, sent, mine, _tag = queue
    add(3)
    second_drainer_ran = []

    def fake_send(to_email, subject, html_body, cfg, **kwargs):
        sent.append(subject)
        if not second_drainer_ran:          # another worker wakes up mid-batch
            second_drainer_ran.append(1)
            _drain()

    monkeypatch.setattr(eu, "send_email_smtp", fake_send)
    _drain()
    assert sorted(mine(sent)) == sorted(set(mine(sent)))
    assert len(mine(sent)) == 3


def test_rows_stranded_in_sending_are_retried(queue, monkeypatch, db_engine):
    add, sent, mine, tag = queue
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    add(1, status="sending", attempts=1, claimed_at=now - datetime.timedelta(minutes=20))
    cur = db_engine.cursor()
    cur.execute("UPDATE email_queue SET subject=%s WHERE subject=%s", (tag + " stale", tag + " 0"))
    cur.close()
    add(1, status="sending", attempts=1, claimed_at=now - datetime.timedelta(minutes=1))
    monkeypatch.setattr(eu, "send_email_smtp", lambda to, subject, *a, **k: sent.append(subject))
    _drain()
    assert mine(sent) == [tag + " stale"]       # the in-flight one is left alone
