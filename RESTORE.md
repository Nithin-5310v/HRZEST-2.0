# Database backup & restore

This is the disaster-recovery procedure for HRzest.com's PostgreSQL database.
If you're reading this because production is down, skip to
[**Restore procedure**](#restore-procedure).

## How backups work

- `scripts/backup_db.sh` runs `pg_dump | gzip | gpg/age` and writes a
  timestamped, **encrypted** copy to `backups/` (plus a `.sha256`). It is
  encrypted to a *public* key, so the host can write backups but cannot read
  them back — to restore you need the private key (`gpg --decrypt` /
  `age -d -i <identity>`). With no `BACKUP_ENCRYPTION` configured the job
  refuses to run. Set `BACKUP_REMOTE` (`s3://…` or `rclone:…`) so a copy
  leaves the host; without it the script warns that the backup exists only
  here. See `SECURITY_HARDENING.md` §5.
- **Prove restorability on a schedule:** `scripts/restore_test.sh` (run on a
  machine with the private key) decrypts, restores into a throw-away
  database, checks table counts and recomputes the audit hash chain.
- Scheduled daily at 02:15 server time via `scripts/hrzest-backup.service`
  + `scripts/hrzest-backup.timer` (systemd). Local copies older than 30
  days are pruned automatically.
- `BACKUP_HEARTBEAT_URL` (optional, `.env.example`) pings a dead-man's-switch
  monitor (Healthchecks.io, Cronitor, etc.) on every successful run — set
  this up so a silently-broken backup job is discovered by an alert, not by
  needing a backup that turns out not to exist.

## One-time setup (new deployment)

1. Install the scheduled job:
   ```bash
   sudo cp scripts/hrzest-backup.service scripts/hrzest-backup.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now hrzest-backup.timer
   systemctl list-timers hrzest-backup.timer   # confirm it's scheduled
   ```
2. Trigger one backup immediately rather than waiting for 02:15, to confirm
   the chain (dump → gzip → heartbeat) actually works on this host:
   ```bash
   sudo systemctl start hrzest-backup.service
   journalctl -u hrzest-backup.service -n 50 --no-pager
   ls -la backups/
   ```

## Restore procedure

**Read this whole section before running anything against production.**
Restoring into an already-populated database will fail on the first
`CREATE TABLE` (tables already exist) rather than silently merging or
overwriting — that's expected. Restore into a **new, empty database** and
cut the app over to it, don't restore on top of a live one.

1. **Get the backup file** from the app server's local copy (or your
   offsite copy, if you've set one up):
   ```bash
   ls -la /opt/employee-attendance/backups/
   ```

2. **Create a fresh, empty target database.** Never restore over the
   database the app is currently pointed at.
   ```bash
   psql -h <DB_HOST> -U <DB_USER> -d postgres \
     -c "CREATE DATABASE employee_attendance_restored;"
   ```

3. **Restore.**
   ```bash
   gunzip -c db_backup_2026-01-15_02-15-03.sql.gz \
     | psql -h <DB_HOST> -U <DB_USER> -d employee_attendance_restored
   ```
   Expect a stream of `SET`/`CREATE TABLE`/`ALTER TABLE`/`COPY N` lines
   ending without an error. A `COPY 0` for a table you expected data in is
   a red flag — that table was empty at backup time, not a restore failure,
   but worth double-checking against what you expected.

4. **Spot-check the data landed correctly** before cutting anything over —
   pick 2-3 tables you know should have rows and confirm counts/recent
   timestamps look right:
   ```bash
   psql -h <DB_HOST> -U <DB_USER> -d employee_attendance_restored \
     -c "SELECT COUNT(*) FROM employees;" \
     -c "SELECT MAX(date) FROM attendance;"
   ```

5. **Cut the app over.** Update `DB_NAME` (and `DB_HOST` if restoring to a
   different server/RDS instance) in `.env` to point at the restored
   database, then restart the app (`podman-compose restart app` /
   `docker compose restart app`, matching whichever compose file this
   deployment uses). Confirm `/healthz` returns `{"status":"ok"}` before
   considering the restore complete.

6. **Don't delete the old (broken) database immediately** — keep it until
   you've confirmed the restored one is serving real traffic correctly for
   at least a few hours, in case the restore itself needs redoing.

### Restoring a single table (not the whole database)

`pg_dump`'s output is a plain SQL script, so you can restore into a scratch
database (steps 1-3 above, using a throwaway database name) and then copy
just the table(s) you need back into production with `pg_dump -t
<table_name> ... | psql ...` from the scratch database, or a manual
`INSERT INTO ... SELECT ... FROM dblink(...)` — there's no single-table
extraction flag for a plain-SQL dump; the scratch-database round trip is
the straightforward way.

## Verified

This procedure (steps 2-4 above) was run end-to-end against a local scratch
database on 2026-09-11: a table with known rows was created, backed up via
`scripts/backup_db.sh` unmodified, restored into a fresh database via the
exact commands in step 3, and the restored rows were confirmed to match
byte-for-byte.
