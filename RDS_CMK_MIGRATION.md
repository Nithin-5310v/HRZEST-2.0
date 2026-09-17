# RDS Customer-Managed KMS Key Migration

Moves the production Postgres RDS instance from AWS-managed encryption to a
customer-managed KMS key (CMK). **This is a data-plane change against a live
production database — do not run it outside a planned maintenance window,
and do not run it at all until this doc and `terraform/rds_kms.tf` have been
reviewed and explicitly approved.**

## Why this can't be done in place

`aws_db_instance.storage_encrypted`/`kms_key_id` are only evaluated when RDS
*creates* the underlying storage — AWS has no API to re-key a live instance.
The only two supported paths are:

1. **Snapshot → copy-with-new-key → restore into a new instance.** What
   this doc walks through. No new EC2/compute needed; the new instance is a
   sibling of the old one until cutover.
2. **Blue/green deployment** (`aws rds create-blue-green-deployment`) — RDS
   manages a full green replica environment for you and handles the
   switchover, but blue/green deployments do **not** support changing the
   KMS key between blue and green (AWS restriction: green must use the same
   encryption key as blue, or no encryption change at all). **Not usable
   for this specific migration** — noted here only so it isn't proposed as
   a shortcut later.
3. **Read-replica-based cutover** — also does not work for this: an
   encrypted read replica must use the same region-and-account default
   behavior as its source *unless* it's a cross-region replica, and even
   then a replica's own storage key is fixed at replica-creation time, not
   swappable afterward either. A cross-region encrof-replica-then-promote
   approach is technically possible but adds a second full data transfer
   and a region hop for no benefit over the snapshot-copy path below, which
   stays in-region and is simpler to reason about and roll back.

So: **snapshot/copy/restore is the only viable path**, and it's what
`terraform/rds_kms.tf`'s gated `aws_db_instance.this_cmk` resource is built
for.

## What reduces downtime here

The actual cutover-affecting downtime is **not** the snapshot/copy/restore
work itself — the old instance keeps serving traffic throughout all of
that, since it's building a completely separate new instance alongside it.
The downtime is only the final swap: pointing the app at the new instance
and making sure no write lands on the old one after that point. Two ways to
shrink that window:

- **Read-only freeze + final incremental snapshot** (what this doc uses):
  put the app into read-only/maintenance mode, take one last snapshot
  covering the final writes, copy+restore *that* snapshot too (a second,
  much smaller copy since most data is already in the first restored
  instance... actually not true for RDS snapshots, which aren't
  incremental at the copy/restore level — see the honest caveat below),
  then cut over. Simpler to implement correctly; downtime scales with
  final-snapshot restore time.
- **DMS (Database Migration Service) ongoing replication**: stand up the
  new CMK-encrypted instance early (from an initial snapshot), then use
  AWS DMS in CDC (change-data-capture) mode to keep it continuously synced
  with the old instance's ongoing writes, and only stop the source +
  cut over once DMS reports near-zero replication lag. This shrinks the
  actual downtime window to seconds/low-minutes instead of the full
  restore time, at the cost of setting up and monitoring a DMS
  replication task. **Recommended if the downtime budget below is too
  large for this deployment** — flagged as the fallback rather than the
  default because it's meaningfully more moving parts for a first pass at
  this migration.

**Honest caveat on snapshot restore time**: an RDS snapshot restore's
duration scales with database size, not with how much changed since the
last snapshot — there's no "incremental restore." For a database in the
low tens of GB (this deployment's `db_allocated_storage` default is 20GB,
likely still near that), expect the restore step itself to take
**5–20 minutes**; validation and DNS/config cutover add more on top. Get an
actual number by doing a dry run (steps 1–4 below) against a throwaway
target *before* the real maintenance window, so the downtime estimate you
give stakeholders is measured, not guessed.

## Estimated downtime window

| Approach | App-visible downtime | Setup complexity |
|---|---|---|
| Read-only freeze + final snapshot (this doc) | ~15–40 min (restore time + validation + DNS/env cutover) | Low |
| DMS CDC replication | ~1–5 min (just the final cutover) | Medium — new DMS task to build/monitor |

Pick DMS if 15–40 minutes of read-only mode is unacceptable; otherwise the
simpler path below is enough for a single small-to-mid-size Postgres
instance.

## Prerequisites

- [ ] `terraform/rds_kms.tf` reviewed and applied *for the key only*
      (`enable_rds_cmk_cutover` stays `false` for this step) — this creates
      `aws_kms_key.rds_cmk` with no effect on the live database:
      ```bash
      cd terraform
      terraform apply -target=aws_kms_key.rds_cmk -target=aws_kms_alias.rds_cmk -target=data.aws_iam_policy_document.rds_cmk_policy
      terraform output rds_cmk_arn
      ```
- [ ] A maintenance window communicated to whoever needs to know (this is
      a multi-tenant HRMS — every tenant is affected simultaneously, there
      is no per-tenant maintenance mode).
- [ ] A verified, recent application-level backup exists independent of
      this RDS snapshot chain (this deployment's own S3 backup job — see
      `terraform/s3_storage.tf`/`aws_s3_bucket.backups`) as a rollback of
      last resort.
- [ ] AWS CLI access with `rds:CreateDBSnapshot`, `rds:CopyDBSnapshot`,
      `rds:RestoreDBInstanceFromDBSnapshot`, `rds:DescribeDBInstances`,
      `kms:CreateGrant` on the new CMK.

## Migration steps

### 1. Put the app into read-only / maintenance mode

Stop writes so the snapshot in step 2 is the true final state. This
codebase has no built-in maintenance-mode flag today — the simplest
reliable option is to scale the app container(s) to zero / stop the
service, accepting a short full outage rather than a subtler read-only
enforcement. Note the exact stop time.

### 2. Snapshot the current instance, then copy it with the new key

```bash
aws rds create-db-snapshot \
  --db-instance-identifier employee-attendance-db \
  --db-snapshot-identifier employee-attendance-db-precmk-$(date +%Y%m%d%H%M)

aws rds wait db-snapshot-completed \
  --db-snapshot-identifier employee-attendance-db-precmk-$(date +%Y%m%d%H%M)

# This is the step that actually re-encrypts -- copying a snapshot with a
# different --kms-key-id than the source produces a new snapshot encrypted
# under that key, without touching the source instance or its snapshot.
aws rds copy-db-snapshot \
  --source-db-snapshot-identifier employee-attendance-db-precmk-<timestamp> \
  --target-db-snapshot-identifier employee-attendance-db-cmk-<timestamp> \
  --kms-key-id "$(terraform -chdir=terraform output -raw rds_cmk_arn)"

aws rds wait db-snapshot-completed \
  --db-snapshot-identifier employee-attendance-db-cmk-<timestamp>
```

### 3. Restore the re-encrypted snapshot into the new instance via Terraform

```bash
cd terraform
terraform apply \
  -var="enable_rds_cmk_cutover=true" \
  -var="rds_cmk_cutover_snapshot_id=employee-attendance-db-cmk-<timestamp>"

terraform output rds_cmk_cutover_endpoint
```

This creates `aws_db_instance.this_cmk` — a full sibling instance, same
subnet group and security group as the original, encrypted with the new
CMK. The old instance (`aws_db_instance.this`) is untouched and still
exists.

### 4. Validate the new instance before cutting anything over

- [ ] Connect directly (`psql -h <rds_cmk_cutover_endpoint> ...`) and spot
      check row counts against the old instance for a few key tables
      (`employees`, `security_events`, `att_master.tenants`).
- [ ] Confirm `pg_stat_activity` shows no unexpected replication/connection
      errors.
- [ ] Run this repo's own test suite's DB-dependent tests against the new
      instance's connection string in a scratch environment if time
      allows — not required, but cheap insurance.

### 5. Cut over connection strings / secrets

- [ ] Update `DB_HOST` (and `DB_SSLMODE`/`DB_SSLROOTCERT` if applicable —
      see `extensions.py`'s `check_production_safety()` for why
      `DB_SSLMODE=require` matters here too) in whatever holds production
      env vars for this deployment: `terraform/security_hardening.tf`'s
      `aws_secretsmanager_secret.app_secrets` if Secrets Manager is in use,
      or the EC2 instance's `.env` directly otherwise.
- [ ] Restart the app so it picks up the new `DB_HOST`.
- [ ] Smoke-test login, one read query, one write query (e.g. clock-in) end
      to end against the new instance before ending the maintenance window.

### 6. End maintenance mode

Resume normal traffic once step 5's smoke test passes.

### 7. Decommission the old instance — only after a soak period

Do **not** delete `aws_db_instance.this` (the old, AWS-managed-key
instance) immediately. Keep it (stopped, if cost is a concern — RDS
supports stopping an instance for up to 7 days at a time, auto-restarting
after) as a rollback target for **at least 48–72 hours** of real
production traffic against the new instance before decommissioning:

```bash
cd terraform
# Remove the old aws_db_instance.this block (main.tf) and its
# now-superseded outputs (outputs.tf's rds_endpoint/rds_port, which should
# be repointed at aws_db_instance.this_cmk first), then:
terraform plan   # confirm ONLY the old instance is being destroyed
terraform apply
```

Also at this point: rename/re-tag `aws_db_instance.this_cmk` back to the
canonical `this` resource name in Terraform (via `terraform state mv`, not
a destroy/recreate) if you want the "temporary cutover name" to stop
showing up in `terraform plan` output going forward — cosmetic, not
required for correctness.

## Rollback plan

Before step 6 (ending maintenance mode): just don't — repoint nothing, the
old instance was never touched, restart the app against the original
`DB_HOST` and you're back to the pre-migration state exactly.

After step 6 but within the soak period (step 7 not yet run): revert the
`DB_HOST` secret/env var back to the old instance's endpoint and restart
the app. Any writes that landed on the new instance during the time it was
live are lost unless manually replayed — this is why the soak period
before decommissioning exists, and why step 1's full-stop (rather than a
partial freeze) minimizes the window where this divergence is even
possible.

## Docs/runbooks touched by this change

- `terraform/outputs.tf` — add `rds_cmk_arn`/`rds_cmk_alias` awareness
  (already output directly from `terraform/rds_kms.tf`; no changes needed
  to `outputs.tf` itself until step 7's `rds_endpoint`/`rds_port` repoint).
- No other doc in this repo referenced an RDS KMS key ARN before this
  change (`AWS_DEPLOYMENT.md`/`AWS_DEPLOYMENT_GUIDE.md` were checked —
  neither mentions RDS encryption specifics, since it was previously just
  the AWS-managed default). Once the cutover in step 5 actually happens,
  add the new `DB_HOST` and a pointer to this doc's "Rollback plan" section
  to whichever of those two is the deployment runbook actually in use.
