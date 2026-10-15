"""SCALE-07: each queued KYC verification job is processed by one worker.

Every gunicorn worker runs a document-verification worker. A worker used
to SELECT up to five 'pending' applications without locking them and mark
them 'processing' in later statements, so two workers could take the same
application: duplicate AI/OCR calls (cost) and duplicate applicant emails.
Jobs left in 'processing' by a crashed worker were never retried.
"""
import datetime
import secrets
import threading

import pytest

from extensions import app as flask_app
from utils.auth import generate_password_hash


@pytest.fixture
def pending_jobs(db_engine):
    tag = "qa-scale07-" + secrets.token_hex(3)
    cur = db_engine.cursor()
    # Leave only this test's jobs in the queue.
    cur.execute("UPDATE att_master.tenant_applications SET verification_job_status='done' "
                "WHERE verification_job_status IN ('pending', 'processing')")

    def add(n, status="pending", claimed_minutes_ago=None):
        ids = []
        for i in range(n):
            claimed = (None if claimed_minutes_ago is None else
                       datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                       - datetime.timedelta(minutes=claimed_minutes_ago))
            cur.execute(
                "INSERT INTO att_master.tenant_applications (company_name, subdomain, admin_username, admin_email, "
                "admin_password_hash, access_token_hash, status, documents_submitted_at, verification_job_status, "
                "verification_claimed_at) VALUES (%s,%s,%s,%s,%s,'tokhash','provisioned',NOW(),%s,%s) RETURNING id",
                (f"{tag} {status} {i}", f"{tag}{status}{i}", f"{tag}{i}", f"{tag}{i}@test.local",
                 generate_password_hash("x"), status, claimed))
            ids.append(cur.fetchone()[0])
        return ids

    yield add
    cur.execute("DELETE FROM att_master.tenant_applications WHERE company_name LIKE %s", (tag + "%",))
    cur.close()


def _claim():
    from utils.document_verification import _claim_verification_jobs
    with flask_app.app_context():
        return _claim_verification_jobs()


def test_concurrent_workers_never_claim_the_same_job(pending_jobs):
    job_ids = set(pending_jobs(12))
    start = threading.Barrier(6)
    claimed = []

    def worker():
        start.wait()
        for _ in range(3):
            claimed.extend(_claim())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(claimed) == sorted(set(claimed))      # nothing claimed twice
    assert set(claimed) == job_ids                       # and nothing left behind


def test_a_job_stranded_in_processing_is_retried(pending_jobs):
    stale = pending_jobs(1, status="processing", claimed_minutes_ago=45)
    pending_jobs(1, status="processing", claimed_minutes_ago=2)      # still being worked on
    assert _claim() == stale
