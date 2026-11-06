# End-to-end tests (Playwright)

Real browser, real running server -- different from `tests/`, which drives
Flask's in-process test client and never actually renders a page or runs
client-side JS. These exist to catch the class of bug that only shows up
when the two sides (server-rendered HTML/redirects and the JS that reads
it) actually have to agree: a renamed form field, a tab hidden behind JS
that a template-only check wouldn't notice, a redirect target that changed
on one side but not the other.

## Setup (one-time)

```bash
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
createdb -h localhost -U postgres att_test_e2e
```

`att_test_e2e` is a **dedicated** database, separate from `att_test` (the
main `tests/` suite's database). These tests write rows directly via SQL
outside of `tests/conftest.py`'s per-test snapshot/restore fixture -- this
is a separate pytest process with no access to that fixture graph -- so
sharing `att_test` would risk leaking e2e-only rows into the main suite's
assumptions about that database's contents, and colliding with anything
else (another session, CI) actively using `att_test` at the same time.

## Running

```bash
pytest tests_e2e/ --browser chromium
```

Not part of `pytest tests/` or `pytest` with no args -- `pytest.ini`'s
`testpaths = tests` already excludes this directory, and it isn't wired
into CI (see below for why).

`tests_e2e/conftest.py` starts the real app (the same `wsgi.py` entry
point production/gunicorn use -- full migration bootstrap against
`att_test_e2e`, every blueprint registered in its real order) on a
background thread once per session, with MFA disabled (same reason
`tests/conftest.py` disables it: exercising the emailed-OTP/TOTP second
factor would mean this suite also has to simulate reading a code out of a
real mailbox). It tears the server down and closes the DB connection at
the end of the session; each test that needs its own admin/employee row
seeds and cleans up that row itself.

## What's covered, and what deliberately isn't

Five flows, chosen to be both genuinely critical and achievable without a
real detectable face photo (Add Employee's web form and `/api/employees`
both reject a photo with no detected face via
`face_recognition.face_encodings()` -- see `blueprints/employees/` --
and this environment has no legitimate way to supply one without either a
real photograph or monkeypatching the live server process, which an
external browser-driven test can't do):

1. Admin login reaches the dashboard.
2. Employee login reaches the portal.
3. Employee submits a leave request -> admin approves it (checked against
   the database, not just "the page didn't error").
4. Employee raises a support ticket -> admin resolves it.
5. Admin changes their own password -> the old password stops working and
   the new one logs in (checked from fresh, cookie-less browser contexts,
   not by trusting this app's own logout redirect/cookie timing).

Not covered here: face-recognition check-in/photo upload (see above),
payment flows (Razorpay has no sandbox reachable from a CI-shaped
environment without real API keys), and anything requiring a second
factor (MFA is disabled for this whole suite -- see "Running" above).

## Why this isn't in CI (yet)

Wiring this into `.github/workflows/*.yml` would mean: a Postgres service
(already true for the main suite), `playwright install chromium
--with-deps` (a real, non-trivial download+install on every run unless
cached), and a meaningfully slower job (~1-2 minutes for 5 flows vs.
seconds for the equivalent test-client-based coverage). Left as a manual
verification step for now rather than a blocking gate on every push --
revisit if these flows start catching real regressions the main suite
misses, which is the actual signal that the CI cost is worth paying.
