#!/usr/bin/env bash
set -euo pipefail

echo "🚀 Starting HRzest.com..."

# ── 1. Pre-flight: warn about unset critical variables ───────────────────────
warn_missing() {
  local var="$1"
  if [ -z "${!var:-}" ]; then
    echo "⚠️  WARNING: $var is not set — see .env.example for instructions"
  fi
}
warn_missing SECRET_KEY
warn_missing ENCRYPTION_KEY
warn_missing APP_URL
warn_missing SMTP_HOST

# Fail hard if SECRET_KEY looks like the placeholder
if [[ "${SECRET_KEY:-}" == "your_secret_key_here" || -z "${SECRET_KEY:-}" ]]; then
  echo "❌ FATAL: SECRET_KEY is unset or still the placeholder value."
  echo "   Run: python -c \"import secrets; print(secrets.token_hex(32))\""
  echo "   Then set SECRET_KEY in your .env file."
  exit 1
fi

# ── 2. Wait for PostgreSQL ────────────────────────────────────────────────────
if [ -n "${DB_HOST:-}" ]; then
  DB_PORT="${DB_PORT:-5432}"
  echo "⏳ Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
  MAX_TRIES=30
  TRIES=0
  until nc -z "$DB_HOST" "$DB_PORT" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -ge "$MAX_TRIES" ]; then
      echo "❌ PostgreSQL not reachable after ${MAX_TRIES}s — aborting."
      exit 1
    fi
    sleep 1
  done
  echo "✅ PostgreSQL is reachable!"
fi

# ── 3. Run database migrations ────────────────────────────────────────────────
echo "📦 Running schema migrations..."
# (A `python migrate_plans.py` step used to run here. That file is not in this repository, so it
# failed on every boot and the failure was swallowed, which would also have hidden a REAL
# migration failure. Schema creation/upgrade runs inside the app at import time -- see
# wsgi.py (init_master_db / init_db / _run_migrations_for_all_tenants).)

# ── 4. Start Gunicorn ─────────────────────────────────────────────────────────
echo "🔥 Launching Gunicorn..."
exec gunicorn -c gunicorn.conf.py wsgi:application
