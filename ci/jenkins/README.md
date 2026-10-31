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
- **Production**: the EC2 host. Jenkins deploys a `main` commit there only
  after someone presses **Deploy to production** in Jenkins — and refuses
  code whose files never ran in the development copy, unless the approver
  ticks **HOTFIX**.

GitHub Actions still runs its checks (tests, security scans) on every PR
and on pushes to `develop` and `main`. Production won't switch to a commit
whose Actions runs failed.

## Everyday use

1. Branch from `develop`, open your PR **into `develop`**.
2. After merging, Jenkins picks up `develop` within 5 minutes (or press
   **Scan Multibranch Pipeline Now** on the HRZEST job), runs flake8 and the
   test suite, and deploys to http://localhost:5050.
3. Check the change there.
4. Open a PR from **`develop` into `main`** and merge it.
5. In Jenkins, open **HRZEST » main**, the newest build is waiting at
   **Approve production**: press **Deploy to production**. Jenkins waits for
   GitHub Actions on that commit, then the server backs up the database,
   switches to the commit, restarts and checks `/healthz`, rolling back by
   itself if the app doesn't come up.

A newer push to the same branch cancels a build that's still testing or
waiting for approval, so only the newest `main` commit can be approved.

**Hotfix**: if something must go to production without passing through
`develop`, merge it into `main` and tick **HOTFIX** when approving. Then
merge `main` back into `develop` so the branches don't drift.

## Files

| File | What it does |
|------|--------------|
| `Jenkinsfile` (repo root) | The pipeline: checks, deploy to development, approval, deploy to production. |
| `ci/jenkins/jenkins.yaml` | Jenkins' whole configuration (admin user, the HRZEST job, settings). Loaded at every start, so edit this file, not the Jenkins UI. |
| `ci/jenkins/install-jenkins.ps1` | One-time setup of Jenkins and the development copy on a Windows PC. |
| `ci/jenkins/setup_venv.ps1` | Builds the Python environments (handles `dlib` on Windows). |
| `ci/jenkins/run_tests.py` | The test step; each branch gets its own test databases. |
| `ci/jenkins/deploy_dev.ps1`, `start_dev_server.ps1` | Deploy to / (re)start the development copy, with rollback. |
| `ci/jenkins/deploy_prod.ps1` | Production deploy over SSH (plink), with the "ran in development" check. |
| `scripts/ec2_autodeploy.sh`, `install_ec2_autodeploy.sh` | The server side of a production deploy (backup, switch, health check, rollback). |

## Setting it up on a PC

Needs: Windows, Git, Python 3.12, PostgreSQL running locally, PuTTY's
`plink.exe`, and the HRZEST `.env` you use locally. No admin rights.

```powershell
powershell -ExecutionPolicy Bypass -File ci\jenkins\install-jenkins.ps1 `
  -EnvSource C:\path\to\HRZEST\.env `
  -Python C:\path\to\Python312\python.exe `
  -ProdHost ubuntu@<server-ip> `
  -ProdHostKey "SHA256:<server host-key fingerprint>" `
  -ProdSshKey C:\path\to\production-key.ppk
```

It installs Java 21 and Jenkins LTS into `C:\Jenkins`, writes
`C:\HRZEST-dev\.env` (your `.env` with the development database, port 5050
and plain HTTP), starts Jenkins and adds it to the Startup folder so it
comes back after a reboot (together with the development copy). Re-run it
with `-Upgrade` to update Java, Jenkins and the plugins.

The development copy uses the same email (SMTP) and payment keys as the
`.env` it was made from — use test keys there.

**On the server, once**, switch production to Jenkins deploys (turns the
old 2-minute auto-deploy timer off for good):

```bash
cd /home/ubuntu/apps/HRZEST && bash scripts/install_ec2_autodeploy.sh --jenkins
```

## When something goes wrong

- **Jenkins logs**: `C:\Jenkins\logs\jenkins.log`. Restart: end the
  `java.exe` running `jenkins.war` in Task Manager, then run
  `C:\Jenkins\start-jenkins.ps1`.
- **Development copy logs**: `C:\HRZEST-dev\logs\server.err.log`.
  Restart: `C:\HRZEST-dev\start_dev_server.ps1`.
- **Production deploy can't connect**: the server's SSH only accepts the
  office IP. If your internet IP changed, update the SSH rule in the AWS
  security group ("My IP").
- **Server side of a deploy**: its output is in the Jenkins build log; the
  app's own log is `journalctl -u hrzest` on the server. Database backups
  from before each deploy are in `~/backups/pre-deploy`.
