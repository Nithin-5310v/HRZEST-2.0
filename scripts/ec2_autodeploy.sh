#!/usr/bin/env bash
# ec2_autodeploy.sh -- pull-based production deploy for the EC2 host
# (Ubuntu + systemd: hrzest.service runs gunicorn from a git checkout of
# this repo, nginx in front, PostgreSQL on the same machine).
#
# hrzest-autodeploy.timer runs this every 2 minutes. When origin/main has
# moved and every GitHub Actions push run for that commit has passed, it
# backs up PostgreSQL, checks the commit out, installs requirements if they
# changed, restarts the app and waits for /healthz. If the app does not come
# back healthy it puts the previous commit back and restarts again.
#
# The host only pulls: GitHub cannot reach it (SSH stays locked to the
# admin's IP) and no deploy key or token is stored in GitHub.
#
#   sudo systemctl start hrzest-autodeploy       # check/deploy right now
#   journalctl -u hrzest-autodeploy -n 50        # what it did
#   sudo systemctl stop hrzest-autodeploy.timer  # pause auto-deploys
#   sudo systemctl start hrzest-autodeploy.timer # resume
#   HRZEST_DEPLOY_SHA=<sha> hrzest-autodeploy    # deploy one main commit by hand
#                                                # (stop the timer first to keep
#                                                # an older commit in place)
#
# Install or upgrade with scripts/install_ec2_autodeploy.sh. After each
# successful deploy this script re-runs that installer itself if the copies
# in the repo changed, so edits to it ship like any other code.
set -euo pipefail

APP_DIR="${HRZEST_APP_DIR:-/home/ubuntu/apps/HRZEST}"
SERVICE="${HRZEST_SERVICE:-hrzest}"
REPO="${HRZEST_REPO:-Nithin-5310v/HRZEST-2.0}"
BRANCH="${HRZEST_BRANCH:-main}"
# The app redirects 127.0.0.1 to localhost (app.py), so ask localhost directly.
HEALTH_URL="${HRZEST_HEALTH_URL:-http://localhost:8000/healthz}"
HEALTH_WAIT_SECONDS="${HRZEST_HEALTH_WAIT_SECONDS:-120}"
BACKUP_DIR="${HRZEST_BACKUP_DIR:-$HOME/backups/pre-deploy}"
KEEP_BACKUPS="${HRZEST_KEEP_BACKUPS:-10}"
STATE_DIR="${HRZEST_STATE_DIR:-$HOME/.local/state/hrzest-autodeploy}"

log() { echo "$*"; }   # stdout lands in the journal under the systemd unit

mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
flock -n 9 || { log "another deploy is already running; skipping"; exit 0; }

cd "$APP_DIR"
git fetch --quiet origin "$BRANCH"
current="$(git rev-parse HEAD)"
target="$(git rev-parse --verify --quiet "${HRZEST_DEPLOY_SHA:-origin/$BRANCH}^{commit}")" \
  || { log "unknown commit: ${HRZEST_DEPLOY_SHA:-origin/$BRANCH}"; exit 1; }

if [ "$target" = "$current" ]; then
  [ -n "${HRZEST_DEPLOY_SHA:-}" ] && log "already running ${target:0:7}"
  exit 0
fi

if ! git merge-base --is-ancestor "$target" "origin/$BRANCH"; then
  log "refusing to deploy ${target:0:7}: it is not on origin/$BRANCH"
  exit 1
fi

# A commit that already failed to deploy is not retried every 2 minutes;
# a manual HRZEST_DEPLOY_SHA run still can.
if [ -z "${HRZEST_DEPLOY_SHA:-}" ] && grep -qx "$target" "$STATE_DIR/rejected" 2>/dev/null; then
  exit 0
fi

# Log a status line only when it changes, not on every 2-minute tick.
note() {
  local line="${target:0:7} $1"
  if [ "$(cat "$STATE_DIR/last_status" 2>/dev/null)" != "$line" ]; then
    log "$line"
    echo "$line" > "$STATE_DIR/last_status"
  fi
}

# All GitHub Actions push runs for the commit, newest run per workflow
# (a re-run replaces a failed attempt). Prints: pending | success | failed: <names>
ci_state() {
  curl -fsS -m 20 -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/actions/runs?head_sha=$1&event=push&per_page=100" |
  python3 -c '
import json, sys
latest = {}
for run in json.load(sys.stdin)["workflow_runs"]:
    if run["workflow_id"] not in latest or run["id"] > latest[run["workflow_id"]]["id"]:
        latest[run["workflow_id"]] = run
runs = latest.values()
if not runs or any(r["status"] != "completed" for r in runs):
    print("pending")
else:
    bad = [r["name"] for r in runs if r["conclusion"] not in ("success", "skipped", "neutral")]
    print("failed: " + ", ".join(bad) if bad else "success")
'
}

if ! state="$(ci_state "$target")"; then
  note "could not read CI status from GitHub; will retry"
  exit 0
fi
case "$state" in
  success) ;;
  pending) note "waiting for CI to finish"; exit 0 ;;
  *)       note "not deploying, CI $state"; exit 0 ;;
esac

backup() {
  local file
  file="$BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ)-${current:0:7}.sql.gz"
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  (umask 077; cd / && sudo -n -u postgres pg_dumpall | gzip > "$file.part")
  mv "$file.part" "$file"
  # shellcheck disable=SC2012  # names are our own timestamps, no odd characters
  ls -1t "$BACKUP_DIR"/*.sql.gz | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f
  log "database backed up to $file ($(du -h "$file" | cut -f1))"
}

# Called from if-conditions, where set -e is off: every step returns on failure.
deploy_to() {  # $1 = commit to run, $2 = commit running now
  git -c advice.detachedHead=false checkout --quiet --detach "$1" || return 1
  if ! git diff --quiet "$2" "$1" -- requirements.txt; then
    log "requirements.txt changed -- installing"
    .venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt || return 1
  fi
  sudo -n systemctl restart "$SERVICE" || return 1
}

# Startup runs the database migrations, so allow some time.
healthy() {
  local waited=0
  while [ "$waited" -lt "$HEALTH_WAIT_SECONDS" ]; do
    sleep 3
    waited=$((waited + 3))
    [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$HEALTH_URL")" = 200 ] && return 0
  done
  return 1
}

log "deploying ${target:0:7} over ${current:0:7}: $(git log -1 --format=%s "$target")"
backup

if deploy_to "$target" "$current" && healthy; then
  log "deployed ${target:0:7}; $HEALTH_URL is healthy"
  rm -f "$STATE_DIR/last_status"
  # Commits from before this script existed don't carry it; keep what's installed.
  [ -f scripts/install_ec2_autodeploy.sh ] || exit 0
  for f in ec2_autodeploy.sh hrzest-autodeploy.service hrzest-autodeploy.timer; do
    installed="/etc/systemd/system/$f"
    [ "$f" = ec2_autodeploy.sh ] && installed=/usr/local/bin/hrzest-autodeploy
    if ! cmp -s "scripts/$f" "$installed"; then
      log "auto-deploy files changed in the repo -- reinstalling them"
      bash scripts/install_ec2_autodeploy.sh
      break
    fi
  done
  exit 0
fi

log "deploy of ${target:0:7} FAILED -- putting ${current:0:7} back"
echo "$target" >> "$STATE_DIR/rejected"
sudo -n journalctl -u "$SERVICE" -n 40 --no-pager || true
if deploy_to "$current" "$target" && healthy; then
  log "rolled back to ${current:0:7}; the app is healthy. Pre-deploy database backup is in $BACKUP_DIR"
else
  log "ROLLBACK IS NOT HEALTHY -- check 'journalctl -u $SERVICE'; pre-deploy database backup is in $BACKUP_DIR"
fi
exit 1
