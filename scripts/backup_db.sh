#!/usr/bin/env bash
# ==============================================================================
# Automated PostgreSQL backup -- HRzest.com
#
#   pg_dump | gzip | encrypt  ->  backups/db_backup_<ts>.sql.gz.<gpg|age>
#   + SHA-256 sidecar, optional offsite copy, local retention.
#
# What changed from the old version, and why:
#   * Backups were plaintext .sql.gz on the SAME host as the app and its
#     ENCRYPTION_KEY. Anyone who could read the backups directory got every
#     table (including the columns the app encrypts, plus the key sitting in
#     .env next to them). Backups are now encrypted to a PUBLIC key/recipient:
#     the host can write backups but cannot read them back, so compromising
#     the host does not expose history.
#   * Backups never left the box ("copy offsite yourself"). Set BACKUP_REMOTE
#     and they do.
#   * Nothing proved a backup could be restored. scripts/restore_test.sh does,
#     on a machine that holds the private key -- run it on a schedule.
#
# Configuration (environment / .env via the systemd EnvironmentFile):
#   BACKUP_ENCRYPTION   gpg | age              (required unless BACKUP_ALLOW_PLAINTEXT=1)
#   BACKUP_GPG_RECIPIENT  key id/email in the host's gpg keyring (public half only)
#   BACKUP_AGE_RECIPIENT  age1... public recipient
#   BACKUP_REMOTE       s3://bucket/prefix  |  rclone:remote:path  (optional but advised)
#   BACKUP_RETAIN_DAYS  local retention, default 30
#   BACKUP_ALLOW_PLAINTEXT=1   explicit, loud opt-out (not for production)
#   BACKUP_HEARTBEAT_URL  pinged by the systemd unit only on success
# ==============================================================================
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
TIMESTAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-30}"
ENCRYPTION="${BACKUP_ENCRYPTION:-}"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

# ── encryption must be decided before a single byte of data is written ───────
case "${ENCRYPTION}" in
  gpg)
    : "${BACKUP_GPG_RECIPIENT:?BACKUP_ENCRYPTION=gpg needs BACKUP_GPG_RECIPIENT (public key id/email)}"
    command -v gpg >/dev/null || { echo "gpg not installed" >&2; exit 1; }
    gpg --list-keys "${BACKUP_GPG_RECIPIENT}" >/dev/null 2>&1 \
      || { echo "gpg public key for ${BACKUP_GPG_RECIPIENT} is not in this host's keyring" >&2; exit 1; }
    EXT="gpg"
    encrypt() { gpg --batch --yes --trust-model always --encrypt --recipient "${BACKUP_GPG_RECIPIENT}"; }
    ;;
  age)
    : "${BACKUP_AGE_RECIPIENT:?BACKUP_ENCRYPTION=age needs BACKUP_AGE_RECIPIENT (age1... public key)}"
    command -v age >/dev/null || { echo "age not installed" >&2; exit 1; }
    EXT="age"
    encrypt() { age -r "${BACKUP_AGE_RECIPIENT}"; }
    ;;
  "")
    if [ "${BACKUP_ALLOW_PLAINTEXT:-0}" = "1" ]; then
      echo "WARNING: BACKUP_ALLOW_PLAINTEXT=1 -- writing an UNENCRYPTED backup" >&2
      EXT="plain"
      encrypt() { cat; }
    else
      cat >&2 <<'EOF'
refusing to write a plaintext backup.
Set BACKUP_ENCRYPTION=gpg (+ BACKUP_GPG_RECIPIENT) or BACKUP_ENCRYPTION=age
(+ BACKUP_AGE_RECIPIENT) so backups are encrypted to a key this host cannot
use to read them back. For a throwaway dev box only: BACKUP_ALLOW_PLAINTEXT=1.
EOF
      exit 1
    fi
    ;;
  *) echo "unknown BACKUP_ENCRYPTION='${ENCRYPTION}' (use gpg or age)" >&2; exit 1 ;;
esac

if [ "${EXT}" = "plain" ]; then
  BACKUP_FILE="${BACKUP_DIR}/db_backup_${TIMESTAMP}.sql.gz"
else
  BACKUP_FILE="${BACKUP_DIR}/db_backup_${TIMESTAMP}.sql.gz.${EXT}"
fi
PARTIAL="${BACKUP_FILE}.partial"
trap 'rm -f "${PARTIAL}"' EXIT

# ── dump ─────────────────────────────────────────────────────────────────────
PG_HOST="${DB_HOST:-127.0.0.1}"
PG_PORT="${DB_PORT:-5432}"
PG_USER="${DB_OWNER_USER:-${DB_USER:-postgres}}"   # a dump needs read on everything, incl. audit_chain_head
PG_NAME="${DB_NAME:-postgres}"
# pg_dump would prompt for a password and hang under cron/systemd; PGPASSWORD
# is libpq's non-interactive source, taken from the same .env as everything else.
if [ -n "${DB_OWNER_USER:-}" ] && [ -n "${DB_OWNER_PASS:-}" ]; then
  export PGPASSWORD="${DB_OWNER_PASS}"
elif [ -n "${DB_PASS:-}" ]; then
  export PGPASSWORD="${DB_PASS}"
fi

echo "Starting PostgreSQL backup: ${PG_NAME} @ ${PG_HOST}:${PG_PORT} (encryption: ${EXT})"
# pipefail makes a pg_dump failure fail the whole pipeline instead of
# producing an empty-but-"successful" archive.
pg_dump -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" "${PG_NAME}" | gzip -9 | encrypt > "${PARTIAL}"

[ -s "${PARTIAL}" ] || { echo "backup is empty -- aborting" >&2; exit 1; }
mv "${PARTIAL}" "${BACKUP_FILE}"
( cd "${BACKUP_DIR}" && sha256sum "$(basename "${BACKUP_FILE}")" > "$(basename "${BACKUP_FILE}").sha256" )
echo "Backup written: ${BACKUP_FILE} ($(du -h "${BACKUP_FILE}" | cut -f1))"

# ── offsite ──────────────────────────────────────────────────────────────────
if [ -n "${BACKUP_REMOTE:-}" ]; then
  case "${BACKUP_REMOTE}" in
    s3://*)
      command -v aws >/dev/null || { echo "aws cli not installed (BACKUP_REMOTE=${BACKUP_REMOTE})" >&2; exit 1; }
      aws s3 cp --only-show-errors --sse AES256 "${BACKUP_FILE}" "${BACKUP_REMOTE%/}/$(basename "${BACKUP_FILE}")"
      aws s3 cp --only-show-errors "${BACKUP_FILE}.sha256" "${BACKUP_REMOTE%/}/$(basename "${BACKUP_FILE}").sha256"
      ;;
    rclone:*)
      command -v rclone >/dev/null || { echo "rclone not installed (BACKUP_REMOTE=${BACKUP_REMOTE})" >&2; exit 1; }
      rclone copyto "${BACKUP_FILE}" "${BACKUP_REMOTE#rclone:}/$(basename "${BACKUP_FILE}")"
      rclone copyto "${BACKUP_FILE}.sha256" "${BACKUP_REMOTE#rclone:}/$(basename "${BACKUP_FILE}").sha256"
      ;;
    *) echo "unsupported BACKUP_REMOTE='${BACKUP_REMOTE}' (use s3://... or rclone:remote:path)" >&2; exit 1 ;;
  esac
  echo "Offsite copy complete: ${BACKUP_REMOTE}"
else
  echo "WARNING: BACKUP_REMOTE is not set -- this backup exists ONLY on this host." >&2
fi

# ── local retention ──────────────────────────────────────────────────────────
find "${BACKUP_DIR}" -type f \( -name 'db_backup_*.sql.gz' -o -name 'db_backup_*.sql.gz.gpg' \
     -o -name 'db_backup_*.sql.gz.age' -o -name 'db_backup_*.sha256' \) -mtime +"${RETAIN_DAYS}" -delete

echo "Backup operation completed."
