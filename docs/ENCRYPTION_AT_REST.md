# Encrypting everything at rest and in transit

HRZEST protects data in two layers. You need both:

| Layer | Covers | Where it's set |
|---|---|---|
| **Storage encryption** (AWS KMS, AES-256) | *Every* byte on disk: all tables (names, attendance, salary, payroll…), indexes, WAL, logs, automated backups, snapshots, read replicas; the app server's uploads and photos | AWS: RDS instance + EBS volumes (this guide) |
| **Field/file encryption** (AES-256-GCM, app key) | Sensitive fields (Aadhaar, PAN, bank, DOB, address, email, phone…), uploaded documents, secrets | App code, `ENCRYPTION_KEY` (see `SECURITY_HARDENING.md`) |

Storage encryption protects against a copied disk, snapshot or backup. Field
encryption also protects the most sensitive values from anyone who can query
the database but doesn't have `ENCRYPTION_KEY` (a leaked DB login, a SQL
injection, an over-shared read replica).

Run the commands below with the AWS CLI, using credentials that can manage
RDS/EC2 in the account and region where HRZEST runs.

---

## 1. RDS: check whether storage is encrypted

```sh
aws rds describe-db-instances \
  --query "DBInstances[*].[DBInstanceIdentifier,StorageEncrypted,KmsKeyId]" --output table
```

`StorageEncrypted = True` → done, skip to step 3.
`False` → step 2. RDS **cannot** turn encryption on for an existing instance;
you restore an encrypted copy.

## 2. RDS: migrate to an encrypted instance (maintenance window)

Plan for downtime of roughly snapshot time + restore time (minutes for a small
database). Writes made after the snapshot are lost, so stop the app first.

```sh
DB=hrzest-db                       # your current instance identifier
KMS=alias/aws/rds                  # or your own customer-managed KMS key ARN

# a. stop writes: scale the app to zero / stop the containers
# b. snapshot the current (unencrypted) instance
aws rds create-db-snapshot --db-instance-identifier $DB --db-snapshot-identifier $DB-pre-encrypt
aws rds wait db-snapshot-available --db-snapshot-identifier $DB-pre-encrypt

# c. copy the snapshot WITH encryption
aws rds copy-db-snapshot --source-db-snapshot-identifier $DB-pre-encrypt \
  --target-db-snapshot-identifier $DB-encrypted --kms-key-id $KMS
aws rds wait db-snapshot-available --db-snapshot-identifier $DB-encrypted

# d. restore a new, encrypted instance (same class/subnet group/security groups as the old one)
aws rds restore-db-instance-from-db-snapshot --db-instance-identifier $DB-enc \
  --db-snapshot-identifier $DB-encrypted \
  --db-instance-class <same as old> --db-subnet-group-name <same> \
  --vpc-security-group-ids <same> --no-publicly-accessible
aws rds wait db-instance-available --db-instance-identifier $DB-enc
```

e. Point the app at the new endpoint (`DB_HOST` in `.env`), start it, and
check logins, an employee profile and a payslip.
f. Keep the old instance stopped (not deleted) for a few days, then delete it
**and** its unencrypted snapshots. Until then, the plaintext copies still exist.

Automated backups, manual snapshots and read replicas of the new instance are
encrypted automatically.

## 3. RDS: require TLS on the server side

In the instance's **DB parameter group** (a custom one; the default can't be
edited), set:

```
rds.force_ssl = 1
```

The server then rejects any unencrypted connection, from any client.

## 4. App: verify the database certificate

In `.env` on the app server:

```
DB_SSLMODE=verify-full
DB_SSLROOTCERT=/app/certs/rds-global-bundle.pem
```

The image ships AWS's RDS CA bundle at `certs/rds-global-bundle.pem`.
Outside `APP_ENV=development` the app **refuses to start** against a remote
database unless `DB_SSLMODE` is `require`, `verify-ca` or `verify-full`
(`extensions.py: check_db_tls`). Set this **before** deploying that release.

Bare metal (not containers): use the repo path instead, e.g.
`DB_SSLROOTCERT=/opt/hrzest/certs/rds-global-bundle.pem`.

## 5. App server: encrypt its EBS volume

The app host's disk holds `private_uploads/`, `dataset/` (face photos),
`logs/` and `.env` (with `ENCRYPTION_KEY`). Check it:

```sh
aws ec2 describe-volumes --filters Name=attachment.instance-id,Values=<instance-id> \
  --query "Volumes[*].[VolumeId,Encrypted,Size]" --output table
```

Make every *new* volume in the region encrypted:

```sh
aws ec2 enable-ebs-encryption-by-default
```

An existing unencrypted volume can't be switched in place: snapshot it, copy
the snapshot with `--encrypted`, create a volume from the copy, and swap it in
(for the root volume, stop the instance, detach, attach the new one as the
root device, start). Then delete the unencrypted snapshot and volume.

## 6. Keys

* Keep `ENCRYPTION_KEY` in AWS Secrets Manager (or SSM Parameter Store,
  SecureString), not only in `.env`, and back it up. **Losing it makes every
  encrypted field and file unrecoverable.** Database backups don't contain it.
* Backups made by `scripts/backup_db.sh` are encrypted to a public key
  (`BACKUP_ENCRYPTION`); keep the private key offline.
* Rotation: see `SECURITY_HARDENING.md` → Key rotation.

## Checklist

- [ ] RDS `StorageEncrypted = True`; old unencrypted instance and snapshots deleted
- [ ] `rds.force_ssl = 1` in the DB parameter group
- [ ] `DB_SSLMODE=verify-full`, `DB_SSLROOTCERT` set; app starts
- [ ] App server EBS volumes `Encrypted = True`; EBS encryption by default on
- [ ] `scripts/rotate_encryption_key.py` and `scripts/encrypt_private_uploads.py` run after deploy
- [ ] `scripts/encrypt_private_uploads.py --root dataset` run after deploy (face photos)
- [ ] `ENCRYPTION_KEY` stored in Secrets Manager and backed up
