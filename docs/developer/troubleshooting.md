# Troubleshooting

Failures that have actually happened, how to recognise them, and what fixed
them. Start with the symptom you see.

## Tests

### Thousands of tests fail or error at once

**Looks like:** hundreds of `FAILED` and thousands of `ERROR` lines, mostly
`psycopg2.InterfaceError: connection already closed`, `TypeError: 'NoneType'
object is not subscriptable` at setup, `argon2 ... Memory allocation error`, or
`FATAL: the database system is in recovery mode`.

**Cause:** the PC ran out of memory and PostgreSQL crashed. When PostgreSQL is
unreachable, the app falls back to SQLite, which has none of the schema, so
every test fails at setup. It is not a code bug, especially if the same code
passed elsewhere.

**Fix:** check the database is back (`pg_isready -h localhost -p 5432` should say
*accepting connections*; recovery can take minutes). Close what you can, then
re-run with `-n 2`. Don't run a full suite while a Jenkins build is testing:
they share the same PostgreSQL. (Jenkins' own `develop` and `main` test runs
take turns since 26 Nov 2026, and each uses 2 workers since 27 Nov 2026, after
a single 4-worker run still crashed PostgreSQL.)

### "Another test run is already using database 'att_test...'"

Another pytest (yours, Jenkins', or another session's) is using that database.
Wait for it, or give your run its own: `HRZEST_TEST_DB=att_test_mine`.

### Mobile tests time out (30 s) on a busy PC

Re-run with `npx jest --runInBand`. If they pass one file at a time, it was load,
not the code.

### flake8 fails in CI but you only changed one file

CI lints the whole repo, tests included (`python -m flake8 .`). Continuation-line
indentation (E127/E128) in new test files is the usual culprit. Run the same
command locally before pushing.

### CI fails Bandit or Semgrep on a file your PR didn't touch

Check whether `develop` itself is red (GitHub → Actions → filter by branch
`develop`). If it is, the finding came in with an earlier merge and every PR
into `develop` will fail until it's fixed. Fix it on `develop` first (its own
small PR), then update your branch. Semgrep doesn't run on Windows; let CI be the
check for it.

## Jenkins and releases

See [ci-and-release.md](ci-and-release.md) for how the pipeline works.

### `main` build ends yellow (UNSTABLE) with "not a merged GitHub PR"

```
Not deploying <sha>: it isn't the merge commit of a completed GitHub PR into main (a direct push ...)
WARNING: <sha> is not a merged GitHub PR with passing checks: not offered for production.
```

**Cause:** someone pushed straight to `main`. Jenkins refuses to release a direct
push, and it also aborts the build of the last proper release.

**Fix:**
1. Open a PR from `main` into `develop` and merge it (brings the pushed commits
   into `develop`).
2. Open a PR from `develop` into `main` and merge it with a merge commit.
3. Jenkins builds the new merge commit; approve it.

**Prevent it:** turn on GitHub branch protection for `main` ("Require a pull
request before merging", 0 approvals, "Do not allow bypassing").

### `main` build ends yellow with "has NOT run in the development copy"

The release rules need the exact same code to have run on the development copy
first. Usually `develop` hasn't finished deploying yet, or its build failed.
Get a green `develop` build (it deploys to the dev copy), then build `main`
again. If the approval prompt is already open, wait for `develop` to deploy,
then click **Deploy to production** with **HOTFIX unticked**: the deploy step
checks the dev copy again at that moment. Tick HOTFIX only for a real emergency.

### Builds sit at "Waiting for next available executor"

Jenkins has two executors. A build that failed while fetching from GitHub can
stay marked as running and hold one. Abort it from **Build Executor Status** in
Jenkins (red ✕); if that doesn't free it, restart Jenkins
(`C:\Jenkins\start-jenkins.ps1`).

### "Build Now" didn't start anything

The **Build Now** button on the top-level HRZEST page only rescans GitHub. Open
the branch first: **HRZEST → develop** (or **main**) → **Build Now**.

### A build took many hours

The PC went to sleep mid-build and the tests resumed when it woke. One test that
measures time by the clock may then fail on a timeout. Set the PC not to sleep
while plugged in, and keep it awake during releases.

### `develop` build fails deploying to the dev copy

```
Deploy of <sha> to the development copy failed: http://localhost:5050/healthz did not answer 200 within 240 s.
```

Look at `C:\HRZEST-dev\logs\server.err.log`. If it holds only `^C` and even the
rollback to the previous commit failed, the PC was too short of memory to start
the app in time, and the code is fine. Start the dev copy by hand
(`powershell -File C:\HRZEST-dev\start_dev_server.ps1 -DevHome C:\HRZEST-dev`;
`/healthz` should answer within about 20 s), free some memory, and run the
`develop` build again.

### Approved, but the live site still runs the old version

Check what's live: `https://hrzest.com/healthz?commit=<full sha>` answers
`commit_match: true` once the server runs it. The server checks the `production`
branch every 2 minutes. If it stays `false` for more than ~10 minutes, look at
the auto-deploy log on the server:

```bash
sudo journalctl -u hrzest-autodeploy --since "30 min ago" | tail -50
```

A hand-edited file on the server makes `git checkout` abort ("Your local changes
... would be overwritten"), so every deploy rolls back. Restore the file
(`git checkout -- <file>`), and remove the commit from the rejected list
(`~/.local/state/hrzest-autodeploy/rejected`) so it's tried again.

## Live site and users

### Someone gets `{"ok": false, "msg": "Access denied."}` on every page

Their IP address is banned by the firewall. Repeated firewall blocks (5 in 10
minutes) give a 15-minute ban; the honeypot gives 7 days. On the server:

```sql
SELECT ip, reason, banned_by, banned_at, expires_at FROM public.banned_ips ORDER BY banned_at DESC LIMIT 10;
DELETE FROM public.banned_ips WHERE ip = '<ip>';   -- takes effect at once
```

### A request fails with "Request blocked by security policy"

The firewall (`utils/waf.py`) matched something in the request, often by
accident: `0x` followed by four hex characters, `true or false`, a pasted
SQL-like snippet. If the value was generated by the server (a code or link
token), it should come from `waf_safe_token()`; see
[conventions.md](conventions.md#security-habits). If it's user text, the user
needs to rephrase it, or the endpoint needs a reviewed exemption.

### The HR Assistant says "Couldn't reach the server"

Since 21 Nov 2026 it does this only for real network failures. Other cases have
their own message: session ended, server busy or restarting, too many requests,
security filter. Older failures were usually a sign-in problem; see the report
"HR Assistant: When and Why It Fails". On the server, chat requests appear in
`/var/log/nginx/access.log` as `/api/employee/chat`; a `502`/`504` there means
the server was restarting or too slow.

## Mobile app

### EAS build log shows "expo doctor ... Patch version mismatches"

Expo SDK packages are a patch or two behind. It doesn't fail the build. Fix it
with `npx expo install --fix` in `mobile/`, check `npx expo-doctor` shows all
checks passing, run the Jest tests, and open a PR.

### The APK is suddenly large again

The release APK is about 35 MB. If it's back near 130 MB, check that
`expo-build-properties` in `mobile/app.json` still limits `buildArchs` to
`arm64-v8a` and `armeabi-v7a`, and that `useLegacyPackaging` is still on. See
[mobile.md](mobile.md#build-size).
