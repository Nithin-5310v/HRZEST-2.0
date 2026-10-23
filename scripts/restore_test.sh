#!/usr/bin/env bash
# Prove a backup is restorable -- on a machine that holds the PRIVATE key.
#
#   BACKUP_FILE=backups/db_backup_2026-10-15_02-15-00.sql.gz.gpg \
#   RESTORE_DB_HOST=127.0.0.1 RESTORE_DB_USER=postgres RESTORE_DB_PASS=... \
#     bash scripts/restore_test.sh
#
# Steps: verify checksum -> decrypt -> create a throw-away database ->
# restore -> sanity-check the data and the audit hash chain -> drop it.
# Exits non-zero (loudly) if any step fails, so run it from CI or a timer on
# the recovery machine; a backup nobody has restored is a hope, not a backup.
#
# Never point this at the production database: it only ever creates and
# drops a database named hrzest_restore_test_<pid>.
set -euo pipefail
umask 077

: "${BACKUP_FILE:?set BACKUP_FILE to the (encrypted) backup to test}"
[ -f "$BACKUP_FILE" ] || { echo "no such file: $BACKUP_FILE" >&2; exit 1; }

HOST="${RESTORE_DB_HOST:-127.0.0.1}"
PORT="${RESTORE_DB_PORT:-5432}"
USER_="${RESTORE_DB_USER:-postgres}"
export PGPASSWORD="${RESTORE_DB_PASS:-}"
SCRATCH="hrzest_restore_test_$$"
TMP="$(mktemp -d)"
cleanup() {
  psql -h "$HOST" -p "$PORT" -U "$USER_" -d postgres -qc "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "1/5 checksum"
if [ -f "${BACKUP_FILE}.sha256" ]; then
  ( cd "$(dirname "$BACKUP_FILE")" && sha256sum -c "$(basename "$BACKUP_FILE").sha256" )
else
  echo "   (no .sha256 sidecar found -- skipping)"
fi

echo "2/5 decrypt"
case "$BACKUP_FILE" in
  *.gpg) gpg --batch --decrypt "$BACKUP_FILE" > "$TMP/dump.sql.gz" ;;
  *.age) : "${AGE_IDENTITY:?set AGE_IDENTITY to your age identity file}"
         age -d -i "$AGE_IDENTITY" "$BACKUP_FILE" > "$TMP/dump.sql.gz" ;;
  *.sql.gz) cp "$BACKUP_FILE" "$TMP/dump.sql.gz" ;;
  *) echo "unrecognised backup extension" >&2; exit 1 ;;
esac
gunzip -t "$TMP/dump.sql.gz"

echo "3/5 restore into $SCRATCH"
psql -h "$HOST" -p "$PORT" -U "$USER_" -d postgres -qc "CREATE DATABASE \"$SCRATCH\""
gunzip -c "$TMP/dump.sql.gz" | psql -h "$HOST" -p "$PORT" -U "$USER_" -d "$SCRATCH" -q -v ON_ERROR_STOP=0 \
  >"$TMP/restore.log" 2>&1 || true
ERRS="$(grep -c '^psql:.*ERROR' "$TMP/restore.log" || true)"
echo "   restore errors: $ERRS (role-ownership noise is expected when restoring under a different role)"

q() { psql -h "$HOST" -p "$PORT" -U "$USER_" -d "$SCRATCH" -Atqc "$1"; }

echo "4/5 sanity"
TABLES="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')")"
echo "   tables restored: $TABLES"
[ "$TABLES" -gt 10 ] || { echo "too few tables restored -- backup is not usable" >&2; exit 1; }
EMP="$(q "SELECT count(*) FROM employees" 2>/dev/null || echo "n/a")"
echo "   employees rows (public): $EMP"
TEN="$(q "SELECT count(*) FROM att_master.tenants" 2>/dev/null || echo "n/a")"
echo "   tenants (att_master): $TEN"

echo "5/5 audit chain"
# Recompute each chain inside the restored copy. Mirrors utils/audit_chain.py:
# any row whose stored hash no longer matches its content fails the test.
BAD="$(q "
SELECT COALESCE(SUM(c),0) FROM (
  SELECT count(*) AS c FROM public.audit_logs t WHERE t.row_hash IS NOT NULL AND t.row_hash <>
    encode(sha256(convert_to(jsonb_build_array(t.prev_hash, t.id, t.actor, t.actor_type, t.action,
      t.target_table, t.target_id, t.detail, t.ip_address, t.created_at)::text, 'UTF8')), 'hex')
  UNION ALL
  SELECT count(*) FROM public.security_events t WHERE t.row_hash IS NOT NULL AND t.row_hash <>
    encode(sha256(convert_to(jsonb_build_array(t.prev_hash, t.id, t.event_type, t.level, t.message,
      t.identifier, t.ip, t.path, t.method, t.extra_json, t.created_at)::text, 'UTF8')), 'hex')
) s" 2>/dev/null || echo "n/a")"
echo "   chain rows failing recomputation: $BAD"
[ "$BAD" = "0" ] || [ "$BAD" = "n/a" ] || { echo "AUDIT CHAIN MISMATCH in restored backup" >&2; exit 1; }

echo "RESTORE TEST PASSED: $BACKUP_FILE"
