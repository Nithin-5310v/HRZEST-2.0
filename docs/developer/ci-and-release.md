# CI and release

How a change gets from a branch to https://hrzest.com. Nobody deploys by hand.
Day-to-day Jenkins operation is in [ci/jenkins/README.md](../../ci/jenkins/README.md).

```text
feature branch ──PR──► develop ──Jenkins──► dev copy (localhost:5050 on the Jenkins PC)
                          │
                          └──PR (merge commit)──► main ──Jenkins: tests + checks──► you approve
                                                                                        │
                       live server pulls `production` every 2 min ◄── Jenkins moves `production`
```

## The rules

1. Work on a branch from `develop`; open a PR into **`develop`**.
2. Release with a PR from `develop` into **`main`**, merged with **Create a merge
   commit**.
3. **Never push to `main` directly.** Jenkins only releases a `main` commit that
   is the merge commit of a GitHub PR, with green GitHub Actions, whose exact code
   already ran on the dev copy. A direct push makes the `main` build end yellow
   (UNSTABLE) with no approval button, and aborts the build of the previous
   proper merge. Repair: [troubleshooting.md](troubleshooting.md#main-build-ends-yellow-unstable-with-not-a-merged-github-pr).
4. A human approves every production release in Jenkins.

## GitHub Actions (`.github/workflows/`)

All of these run on pushes and PRs to `develop` and `main` unless noted.
Python 3.14, PostgreSQL 16 service, `DB_NAME=att_test`.

| Workflow | What it checks | Notes |
| --- | --- | --- |
| `ci-cd.yml` "Total Automation CI/CD Pipeline" | flake8 → pip-audit → Bandit → pytest → Semgrep `p/default` (ERROR) → Snyk (if `SNYK_TOKEN`) | **One job, steps in order**: a failing step stops the rest, so a Bandit finding means pytest never runs |
| `deploy.yml` "CI" | `lint` (py_compile, file checks), `mobile-lockfile` (`npm ci --dry-run`), `security` (pip-audit, Trivy), `sast` (Bandit, Semgrep `owasp-top-ten`/`secrets`/`flask`/`javascript`), `secrets-scan` (TruffleHog) → `test` "Pytest" (with coverage ≥ 40%), `test-role-split` (least-privilege DB roles), `e2e-soc` (Playwright) → `podman` (image build, Trivy, smoke run) → `publish` (GHCR, signed, SBOM; `main` pushes only) | **Pytest needs lint, security, sast and secrets-scan**, so it's skipped when any of them fails. The `deploy` job is disabled (`if: false`); production is pull-based. |
| `security.yml` | py_compile + Bandit | |
| `mobile-tests.yml` | Jest (`--ci`) in `mobile/`; ESLint report-only | Only when `mobile/**` changes |
| `mobile-update.yml` | Publishes an over-the-air update to the **`preview`** channel | Pushes to `main` that touch `mobile/`, or manual; needs the `EXPO_TOKEN` secret |
| `dast.yml` | OWASP ZAP baseline scan of `vars.STAGING_URL` | Mondays 03:00 UTC; report-only |

**Dependabot** opens weekly PRs into `develop` for pip, npm (`mobile/`) and
GitHub Actions. It deliberately skips Expo SDK and React Native version moves:
those are done on purpose with `npx expo install expo@latest --fix`.

**When `develop` itself is red**, every PR into it fails too, for findings that
aren't in the PR. Fix `develop` first (a small PR), then update your branch.

## Jenkins (`Jenkinsfile`, `ci/jenkins/`)

Jenkins runs on the Windows PC at http://localhost:8090 (user `admin`; password
in `C:\Jenkins\secrets\admin-password.txt`). The multibranch job **HRZEST**
builds only `develop` and `main`, polls GitHub every 5 minutes, and has two
executors. A newer commit on a branch aborts that branch's running build.

| # | Stage | Branch | What happens |
| --- | --- | --- | --- |
| 1 | Python environment | both | Builds `%HRZEST_CI_HOME%\venvs\ci` from **Python 3.12** (`HRZEST_PYTHON`); pip runs only when requirements changed |
| 2 | Lint | both | `python -m flake8 .` |
| 3 | Tests | both | `ci\jenkins\run_tests.py -n 4`: full pytest suite on `att_test_jenkins_<branch>` databases, JUnit report. **The two branches' test runs take turns** (a PostgreSQL advisory lock; the second waits up to 90 minutes, then runs anyway): two suites at once exhausted the PC's memory. |
| 4 | Ran in development? | main | Is this commit's **file tree** listed in `C:\HRZEST-dev\verified-trees.txt`? If `develop` has the same tree, waits up to 40 minutes for its dev deploy. Never fails the build; only changes the approval message. |
| 5 | Merged on GitHub? | main | Is the commit the merge commit of a merged PR into `main`, with every GitHub Actions push run green (waits up to 45 minutes for running checks)? If not, the build ends **UNSTABLE** and stops. |
| 6 | Deploy to development | develop | `deploy_dev.ps1`: checks out the commit into `C:\HRZEST-dev\app`, updates the venv, starts `run_dev.py` on port 5050, waits up to 240 s for `/healthz`, and records the tree in `verified-trees.txt`. On failure it rolls the dev copy back to the previous commit. |
| 7 | Approve production | main | Waits up to 7 days for **Deploy to production**. Says whether the code ran on the dev copy. **HOTFIX** box (default off): deploy even though it never ran on the dev copy. Emergencies only; it doesn't skip the merged-PR or green-Actions checks. |
| 8 | Deploy to production | main | `deploy_prod.ps1`: re-checks everything, then `git push +<sha>:refs/heads/production`, and waits up to 25 minutes until `https://hrzest.com/healthz?commit=<sha>` answers `commit_match: true` |

The dev copy (`C:\HRZEST-dev`) is a real running instance with its own database
(`employee_attendance_dev`), over plain HTTP at http://localhost:5050.

## The live server

AWS EC2, Ubuntu. gunicorn (`gthread`, `GUNICORN_WORKERS` × 4 threads, 120 s
timeout, `gunicorn.conf.py`) under systemd unit `hrzest`, bound to
`127.0.0.1:8000`, behind nginx with TLS. PostgreSQL runs on the same host.

**Auto-deploy** (`scripts/ec2_autodeploy.sh`, timer `hrzest-autodeploy.timer`,
every 2 minutes) in production-branch mode
(`HRZEST_BRANCH=production` in `/etc/hrzest/autodeploy.env`):

1. Fetches `origin/production`. Nothing to do if it's already running.
2. Checks the commit is also on `origin/main`, isn't on the rejected list, and
   has green GitHub Actions.
3. Backs up the whole database (`pg_dumpall`, gzip) to
   `~/backups/pre-deploy/`, keeping the newest 10.
4. Checks out the commit, runs `pip install` if `requirements.txt` changed, and
   restarts `hrzest`. Migrations run at start-up.
5. Waits up to 120 s for `http://localhost:8000/healthz`.
6. **If it isn't healthy**: adds the commit to
   `~/.local/state/hrzest-autodeploy/rejected`, redeploys the previous commit,
   and exits with an error. A rejected commit isn't retried; fix it and release a
   new one.

The server doesn't need SSH from Jenkins: it pulls.

### Useful commands on the server

```bash
journalctl -u hrzest-autodeploy -n 50          # what the auto-deploy did
journalctl -u hrzest -n 100                    # the app itself
sudo systemctl start hrzest-autodeploy         # check for a new release now
curl -s "http://localhost:8000/healthz?commit=<full sha>"   # is this commit running?
```

### Rolling back

Point `production` at an earlier `main` commit; the server deploys it within
about 2 minutes (it must still be on `main` with green checks):

```bash
git push https://github.com/Nithin-5310v/HRZEST-2.0.git +<older main sha>:refs/heads/production
```

Database changes aren't rolled back by this. Restore from
`~/backups/pre-deploy/` if needed ([RESTORE.md](../RESTORE.md)).

### Hotfix

Merge the fix into `main` through a PR, build `main` in Jenkins, approve with
**HOTFIX** ticked, then merge `main` back into `develop` so the branches agree.

## Mobile releases

A server release doesn't update phones. JavaScript-only app changes reach phones
as over-the-air updates; native changes need a new build. See
[mobile.md](mobile.md#releasing).

## Keeping releases smooth

- Merge related PRs, then do one `develop` → `main` release. Each merge starts
  builds, and newer builds abort older ones.
- Keep the Jenkins PC awake and not short of memory during a release. The two
  branches' test runs take turns, so a `main` build can wait for a `develop`
  test run (about 15 minutes) before its own tests start; that's expected.
- Turn on GitHub branch protection for `main` ("Require a pull request before
  merging", 0 approvals, "Do not allow bypassing"). It isn't on today, and direct
  pushes have blocked several releases.
