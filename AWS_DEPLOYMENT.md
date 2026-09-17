# AWS Production Deployment Runbook

Target: existing Linux EC2 instance + RDS PostgreSQL + nginx/certbot for a
trusted HTTPS cert + CloudWatch monitoring + automated backups.

Read this top to bottom once before running anything — several steps are
order-dependent.

## 0. Prerequisites

- AWS CLI configured locally (`aws configure`) with permissions to create
  RDS, IAM, CloudWatch, S3, DLM, SNS resources.
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.
- SSH access to the existing EC2 instance confirmed working **with a key**
  (deploy.sh disables SSH password auth — verify key-based login works
  *before* running it, or you can lock yourself out).
- The instance's VPC ID, at least two subnet IDs (different AZs), its
  security group ID, and its instance ID. Find these in the EC2 console
  (Instance summary tab) or: `aws ec2 describe-instances --instance-ids <id>`.
- Domain + DNS already pointed at the instance's public IP (per earlier
  setup — confirm with `dig yourdomain.com`).

## 1. Provision AWS resources with Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: vpc_id, subnet_ids, ec2_security_group_id,
# ec2_instance_id, alert_email, trusted_admin_cidrs. Do NOT put db_password
# in this file.
export TF_VAR_db_password="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)"
echo "Save this RDS password somewhere safe — you'll need it for .env: $TF_VAR_db_password"

terraform init
terraform plan
terraform apply
```

This creates: RDS PostgreSQL (private, 7-day automated backups), a security
group that only allows the EC2 instance to reach RDS on 5432, a firewall
security group for the app server itself (80/443 open to everyone; SSH/RDP/
direct-DB/alternate-HTTP ports filtered to `trusted_admin_cidrs` only; FTP/
Telnet/SMTP never opened at all), an IAM role + instance profile
(CloudWatch + S3 backup access), an SNS alert topic, CloudWatch alarms (CPU,
status check, disk, RDS storage), an S3 bucket for backups, and a daily
EBS-snapshot policy.

**Attach the new firewall security group to the instance** — it's created by
Terraform but not attached automatically, since the instance itself was
provisioned outside Terraform:

```bash
terraform output -raw app_firewall_sg_id
# EC2 console -> instance -> Actions -> Security -> Change security groups
# -> add the SG ID above (alongside or in place of the instance's current SG)
```

**Check your email** and confirm the SNS subscription, or alarms will fire
silently into the void.

```bash
terraform output
```

Note the `rds_endpoint`, `ec2_iam_instance_profile_name`, and
`dlm_required_instance_tag` values — you need all three next.

## 2. Wire the EC2 instance to the new AWS resources (console steps)

Terraform doesn't manage the EC2 instance itself, so two things need to be
done manually in the EC2 console:

1. **Attach the IAM role**: select the instance → Actions → Security →
   Modify IAM role → choose the `ec2_iam_instance_profile_name` output.
2. **Tag the instance** for backups: Actions → Manage tags → add the
   key/value shown in `dlm_required_instance_tag` (e.g.
   `DlmBackup = employee-attendance-app-server`).

## 3. Bootstrap / harden the server

SSH into the instance, then:

```bash
sudo curl -sSL https://raw.githubusercontent.com/Nithin-magzest/emplyeee-attendance/master/deploy.sh -o deploy.sh
sudo bash deploy.sh
```

First run installs Podman, applies firewall/fail2ban/auto-update hardening,
creates an unprivileged `attendance` user that runs the whole stack as
**rootless Podman** (no root-owned container daemon at all), clones the repo
to `/opt/employee-attendance`, and writes a `.env` template with
`REPLACE_WITH_*` placeholders — then **stops** so you can fill them in.

> Use Ubuntu 24.04 LTS for the instance if you can — it ships Podman 4.9.
> Ubuntu 22.04's stock Podman (3.4.4) is old enough that rootless behavior
> may not match what deploy.sh assumes.

```bash
sudo nano /opt/employee-attendance/.env
```

Set:
- `DB_HOST` = the `rds_endpoint` from step 1
- `DB_PASS` = the password you generated in step 1
- `ALLOWED_ORIGINS` = `https://yourdomain.com`
- Review `ADMIN_PASSWORD`, SMTP settings, `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` per `.env.example`

Re-run the bootstrap to pick up the filled-in `.env` and start the stack:

```bash
sudo bash deploy.sh
```

This builds and starts `app` against RDS (the local `db`
container from `compose.yaml` is intentionally skipped via
`--no-deps`). `nginx` and `certbot` aren't started yet — `nginx.conf` only
exists as a template (`nginx/nginx.conf.template`, since the real one is
gitignored and rendered per-domain) until the next step runs. This step
also schedules a daily cron job to reload nginx so renewed certs take
effect once it's up.

## 4. Get a real (trusted) HTTPS certificate

```bash
cd /opt/employee-attendance
sudo ./init-letsencrypt.sh yourdomain.com you@example.com
```

This renders `nginx/nginx.conf` from the template with your domain,
bootstraps a temporary cert so nginx can start, requests the real Let's
Encrypt cert via the HTTP-01 webroot challenge, reloads nginx with it, and
renewal service, which keeps running afterward and renews automatically.

Verify: open `https://yourdomain.com/admin` in a browser — the padlock
should now show as trusted (no "Not secure"), resolving the original issue.

## 5. CI/CD — enable auto-deploy on push

In the GitHub repo: Settings → Secrets and variables → Actions, add:
- `SSH_HOST` — the EC2 instance's public IP or DNS name
- `SSH_USER` — must be `attendance`, the user deploy.sh creates. It runs
  rootless Podman directly (no daemon, no sudo needed or granted) and owns
  `/opt/employee-attendance`, so CI can `git pull` + `podman-compose` as
  itself. Add the CI key to `/home/attendance/.ssh/authorized_keys` on the
  instance — deploy.sh doesn't provision that for you.
- `SSH_KEY` — the private key matching that public key

Once set, every push to `master` that passes the lint + Podman build jobs
will SSH in, `git pull`, rebuild, and restart the `app` container
automatically (see `.github/workflows/deploy.yml`).

## 6. Verify everything end-to-end

```bash
# Security headers + trusted cert
curl -I https://yourdomain.com/admin

# RDS is NOT reachable from outside the EC2 security group
nc -zv -w3 <rds_endpoint> 5432   # run from your own machine — should fail/timeout

# Health check
curl -I https://yourdomain.com/healthz

# CloudWatch alarm wiring (forces ALARM state to confirm the SNS email arrives)
aws cloudwatch set-alarm-state --alarm-name employee-attendance-cpu-high \
  --state-value ALARM --state-reason "manual test"
```

Then push a trivial commit to `master` and confirm the `deploy` job in
GitHub Actions runs green and the live site reflects the change.

## Ongoing operations

- **Logs**: SSH in as `attendance` (or `sudo -iu attendance`), then
  `podman-compose -f compose.yaml -f compose.prod.yaml logs -f app`
- **Manual deploy** (without waiting for CI): re-run step 3's last command on the server.
- **DB backups**: automatic via RDS (7-day retention, tunable via `db_backup_retention_days` in terraform.tfvars). Manual snapshot: `aws rds create-db-snapshot ...`.
- **File backups** (employee photos/documents on the EC2 volume): automatic daily EBS snapshot via DLM (7-day retention).
- **Rotating the RDS password**: update it via `terraform apply` (changing `db_password`), then update `.env` on the server and restart the `app` container.
- **RDS encryption key**: today `storage_encrypted = true` with the AWS-managed key. `terraform/rds_kms.tf` provisions a customer-managed key (CMK) ready to use, but switching the live instance to it requires a snapshot-restore-into-new-instance migration, not an in-place change — see `RDS_CMK_MIGRATION.md` before doing this; do not set `enable_rds_cmk_cutover=true` outside that documented, deliberate maintenance window.
- **REQUIRE_REDIS**: production boots refuse to start without `REDIS_HOST` set by default (the rate limiter and WAF auto-ban counter both need a shared backend to enforce correctly across multiple gunicorn workers) — see `.env.example`'s `REQUIRE_REDIS` block if this deployment intentionally runs a single worker with no Redis.
