#!/usr/bin/env bash
# Nightly encrypted database backup for the EC2 host (gunicorn under systemd,
# app in /home/ubuntu/apps/HRZEST, PostgreSQL 16 on the same host).
#
# Backups are encrypted to a GPG *public* key, so this server can write them
# but can't read them back. The private key stays off the server (see
# docs/RESTORE.md, "EC2 host: nightly backups"). Copy only the PUBLIC key here,
# then run, as ubuntu (it asks for sudo itself):
#
#     bash scripts/install_ec2_backup.sh ~/hrzest-backup-public.asc
#
# It imports the key into ubuntu's keyring, writes /etc/hrzest/backup.env,
# installs hrzest-backup.service/.timer (03:00 India time every night), takes
# one backup straight away and shows the result. Safe to run again, e.g. with
# a new key.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
pub="${1:?usage: bash scripts/install_ec2_backup.sh <public-key.asc>}"
backup_dir="/home/ubuntu/backups/nightly"

[ "$(id -un)" = "ubuntu" ] || { echo "Run this as ubuntu, without sudo (it uses sudo where needed)." >&2; exit 1; }
[ -r "$pub" ] || { echo "Can't read $pub" >&2; exit 1; }
if grep -q "PRIVATE KEY" "$pub"; then
  echo "That file is a PRIVATE key. Only the public key belongs on the server; delete it from here." >&2
  exit 1
fi

fpr="$(gpg --with-colons --import-options show-only --import "$pub" 2>/dev/null | awk -F: '/^fpr/ {print $10; exit}')"
[ -n "$fpr" ] || { echo "No public key found in $pub" >&2; exit 1; }
gpg --batch --quiet --import "$pub"
echo "Backup key: $fpr"

sudo install -d -m 755 /etc/hrzest
printf '%s\n' \
  "# Read by hrzest-backup.service after the app's .env (these win)." \
  "BACKUP_ENCRYPTION=gpg" \
  "BACKUP_GPG_RECIPIENT=$fpr" \
  "BACKUP_DIR=$backup_dir" \
  "BACKUP_RETAIN_DAYS=30" \
  "# Optional: a Healthchecks.io (or similar) URL pinged after each good backup." \
  "#BACKUP_HEARTBEAT_URL=" \
  "# Optional: an off-server copy, e.g. s3://bucket/hrzest-backups (needs the aws cli)." \
  "#BACKUP_REMOTE=" \
  | sudo tee /etc/hrzest/backup.env >/dev/null
sudo chmod 644 /etc/hrzest/backup.env

sudo install -m 644 "$here/hrzest-backup-ec2.service" /etc/systemd/system/hrzest-backup.service
sudo install -m 644 "$here/hrzest-backup-ec2.timer" /etc/systemd/system/hrzest-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now hrzest-backup.timer >/dev/null

echo "Taking the first backup now..."
if sudo systemctl start hrzest-backup.service; then
  echo "OK: first backup written."
else
  echo "FAILED: the first backup didn't work. The log below says why." >&2
fi
sudo journalctl -u hrzest-backup.service -n 12 --no-pager -o cat
ls -lh "$backup_dir" 2>/dev/null || true
systemctl list-timers hrzest-backup.timer --no-pager
df -h / | tail -1
