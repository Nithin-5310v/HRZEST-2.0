# Jenkins: development first, then production

Code reaches production in two steps, and the second only takes code that
already ran in the first:

```text
feature branch ──PR──▶ develop ──Jenkins: lint + tests──▶ development copy (http://localhost:5050)
                                                              │  you check it
develop ──PR──▶ main ──Jenkins: lint + tests──▶ waits for your approval ──▶ production (EC2)
```

- **Jenkins** runs on the team's Windows PC at **http://localhost:8090** (only
  reachable from that PC). Log in as `admin`; the password is in
  `C:\Jenkins\secrets\admin-password.txt`.
- **Development copy**: the `develop` branch running on the same PC with its
  own database (`employee_attendance_dev`) at **http://localhost:5050**.
- **Production**: the EC2 host. A `main` commit goes there only after
  someone presses **Deploy to production** in Jenkins — and Jenkins refuses
  code whose files never ran in the development copy, unless the approver
  ticks **HOTFIX**.
- **How a release reaches the server**: approving moves the **`production`
  branch** on GitHub to that commit. The server checks the branch every 2
  minutes and deploys it by itself, so Jenkins needs no SSH access to it
  and a changing office IP doesn't matter. The server only takes commits
  that are on `main`.

GitHub Actions still runs its checks (tests, security scans) on every PR
and on pushes to `develop` and `main`. Production won't switch to a commit
whose Actions runs failed.

**Only completed GitHub merges reach production.** Before asking for
approval, a `main` build checks that its commit is the merge of a PR into
`main` (merged on GitHub, not a direct push) and that every GitHub Actions
run for it passed. If not, the build stops after the tests, marked
**unstable**, with no approval button; merge a PR into `main` to get a
releasable build. HOTFIX doesn't skip this. The deploy step checks it once
more after approval.

## Everyday use

1. Branch from `develop`, open your PR **into `develop`**.
2. After merging, Jenkins picks up `develop` within 5 minutes (or press
   **Scan Multibranch Pipeline Now** on the HRZEST job), runs flake8 and the
   test suite, and deploys to http://localhost:5050.
3. Check the change there.
4. Open a PR from **`develop` into `main`** and merge it.
5. In Jenkins, open **HRZEST » main**, the newest build is waiting at
   **Approve production**: press **Deploy to production**. Jenkins waits for
   GitHub Actions on that commit and moves the `production` branch; within
   a few minutes the server backs up the database, switches to the commit,
   restarts and checks `/healthz`, rolling back by itself if the app doesn't
   come up. The build finishes once the live site confirms the new commit.

A newer push to the same branch cancels a build that's still testing or
waiting for approval, so only the newest `main` commit can be approved.
When `develop` and `main` build the same code at the same time, the `main`
build waits for `develop` to finish deploying it before asking for approval.
Their **test runs take turns** (`run_tests.py` holds a PostgreSQL advisory
lock): the second build prints "another branch's test run is using this PC;
waiting" and starts its tests when the first finishes. Two suites at once ran
the PC out of memory and failed good builds.

**Going back to an earlier release**: point `production` at that `main`
commit — `git push https://github.com/Nithin-5310v/HRZEST-2.0.git +<commit>:refs/heads/production`
— and the server deploys it within a few minutes.

**Hotfix**: if something must go to production without passing through
`develop`, merge it into `main` and tick **HOTFIX** when approving. Then
merge `main` back into `develop` so the branches don't drift.

## Branch rules on GitHub

Two repository rulesets, kept as files in `ci/github/rulesets/`, hold the
branches to this process. Only the repository owner can apply them.

- **`develop-and-main.json`**:
  - `develop` and `main` change only through a merged PR: no direct pushes,
    no force-pushes, no deleting.
  - A PR can be merged only once these checks have passed: every job in
    `deploy.yml` that runs on each PR, plus "Code Quality & Security
    Scanning" and "Security & Code Quality Audit".
  - No approving review is required, so you can still merge your own PR.
  - It applies to admins too (there are no bypass actors).
- **`production.json`**: `production` can't be deleted. It still accepts
  force-pushes, because Jenkins moves it with one and a rollback is one.

To apply them, go to **Settings → Rules → Rulesets → New ruleset → Import a
ruleset** and pick each file. Or, signed in to `gh` as the owner:

```bash
gh api -X POST repos/Nithin-5310v/HRZEST-2.0/rulesets --input ci/github/rulesets/develop-and-main.json
gh api -X POST repos/Nithin-5310v/HRZEST-2.0/rulesets --input ci/github/rulesets/production.json
```

The older ruleset named `protect` is disabled and covers no branch; delete it.

`tests/test_branch_rulesets.py` fails if a required check no longer matches
a job that runs on every PR, because a renamed job would block every merge.
**When you rename a CI job**, update the file and the ruleset on GitHub
together. In an emergency, the owner can set a ruleset's enforcement to
*Disabled* and turn it back on afterwards.

## Files

| File | What it does |
|------|--------------|
| `Jenkinsfile` (repo root) | The pipeline: checks, deploy to development, approval, deploy to production. |
| `ci/jenkins/jenkins.yaml` | Jenkins' whole configuration (admin user, the HRZEST job, settings). Loaded at every start, so edit this file, not the Jenkins UI. |
| `ci/jenkins/install-jenkins.ps1` | One-time setup of Jenkins and the development copy on a Windows PC. |
| `ci/jenkins/setup_venv.ps1` | Builds the Python environments (handles `dlib` on Windows). |
| `ci/jenkins/run_tests.py` | The test step; each branch gets its own test databases. |
| `ci/jenkins/deploy_dev.ps1`, `start_dev_server.ps1` | Deploy to / (re)start the development copy, with rollback. |
| `ci/jenkins/deploy_prod.ps1` | Releasing to production: the "ran in development" check, moving the `production` branch, waiting until it's live. |
| `scripts/ec2_autodeploy.sh`, `install_ec2_autodeploy.sh` | The server side of a production deploy (backup, switch, health check, rollback). |
| `ci/github/rulesets/*.json` | The GitHub branch rules above, ready to import. |

## Setting it up on a PC

Needs: Windows, Git signed in to GitHub (Jenkins moves the `production`
branch with this Windows user's GitHub login), Python 3.12, PostgreSQL
running locally, and the HRZEST `.env` you use locally. No admin rights.

```powershell
powershell -ExecutionPolicy Bypass -File ci\jenkins\install-jenkins.ps1 `
  -EnvSource C:\path\to\HRZEST\.env `
  -Python C:\path\to\Python312\python.exe
```

It installs Java 21 and Jenkins LTS into `C:\Jenkins`, writes
`C:\HRZEST-dev\.env` (your `.env` with the development database, port 5050
and plain HTTP), starts Jenkins and adds it to the Startup folder so it
comes back after a reboot (together with the development copy). Re-run it
with `-Upgrade` to update Java, Jenkins and the plugins.

The development copy uses the same email (SMTP) and payment keys as the
`.env` it was made from — use test keys there.

**On the server, once**, after pointing `production` at the commit the
server already runs, switch it to deploy that branch:

```bash
cd /home/ubuntu/apps/HRZEST && bash scripts/install_ec2_autodeploy.sh --production-branch
```

## When something goes wrong

- **Jenkins logs**: `C:\Jenkins\logs\jenkins.log`. Restart: end the
  `java.exe` running `jenkins.war` in Task Manager, then run
  `C:\Jenkins\start-jenkins.ps1`.
- **Development copy logs**: `C:\HRZEST-dev\logs\server.err.log`.
  Restart: `C:\HRZEST-dev\start_dev_server.ps1`.
- **"Production did not switch"**: the server didn't deploy the commit in
  time — usually a failed deploy it rolled back. Its log:
  `journalctl -u hrzest-autodeploy` on the server; the app's own log is
  `journalctl -u hrzest`. Database backups from before each deploy are in
  `~/backups/pre-deploy`. A commit that failed once isn't retried by the
  timer; fix it and approve a new build.
- **Moving the branch fails**: this PC's GitHub login (Git Credential
  Manager) expired — run any `git push` here once to sign in again.
