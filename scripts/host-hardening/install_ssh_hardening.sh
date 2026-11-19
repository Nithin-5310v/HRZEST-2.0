#!/usr/bin/env bash
# Security review OBS-05 (2026-11): SSH hardening, fail2ban and automatic
# security updates for the EC2 host. Run ON THE SERVER from the app checkout:
#
#     cd /home/ubuntu/apps/HRZEST && sudo bash scripts/host-hardening/install_ssh_hardening.sh
#
# BEFORE YOU RUN IT: keep your current SSH session open the whole time, and
# when it finishes, open a SECOND terminal and log in again. Only close the
# first session once the second one works. If the second login fails, use
# the still-open first session to run:
#     sudo rm /etc/ssh/sshd_config.d/00-hrzest-ssh-hardening.conf && sudo systemctl reload ssh
#
# Safe to run more than once.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DROPIN_SRC="$HERE/00-hrzest-ssh-hardening.conf"
DROPIN_DST="/etc/ssh/sshd_config.d/00-hrzest-ssh-hardening.conf"
JAIL_SRC="$HERE/fail2ban-hrzest-sshd.local"
JAIL_DST="/etc/fail2ban/jail.d/hrzest-sshd.local"
DEPLOY_USER="ubuntu"

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

# 1. Lock-out guard: the only allowed user must have a key to log in with.
KEYS="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)/.ssh/authorized_keys"
if ! grep -qE '^(ssh-|ecdsa-|sk-)' "$KEYS" 2>/dev/null; then
  echo "ABORT: $KEYS has no public key. Installing this would lock everyone out." >&2
  exit 1
fi

# 2. Back up the current SSH config.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/ssh-config-backup-$STAMP.tgz"
tar -czf "$BACKUP" /etc/ssh/sshd_config /etc/ssh/sshd_config.d
echo "Backed up the SSH config to $BACKUP"

# 3. Install the drop-in and validate it; undo it if sshd rejects it.
install -m 0644 -o root -g root "$DROPIN_SRC" "$DROPIN_DST"
if ! sshd -t; then
  rm -f "$DROPIN_DST"
  echo "ABORT: sshd -t rejected the new config; it was removed, nothing reloaded." >&2
  exit 1
fi

echo "Effective settings (these must match the drop-in):"
sshd -T | grep -Ei '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin|pubkeyauthentication|maxauthtries|logingracetime|x11forwarding|allowtcpforwarding|allowusers|kexalgorithms|ciphers|macs|hostkeyalgorithms) '
if sshd -T | grep -Ei '^macs ' | grep -qi 'sha1'; then
  rm -f "$DROPIN_DST"
  echo "ABORT: SHA-1 MACs are still enabled (another file wins); drop-in removed." >&2
  exit 1
fi
if sshd -T | grep -Eiq '^passwordauthentication yes'; then
  rm -f "$DROPIN_DST"
  echo "ABORT: password logins are still on (another file wins); drop-in removed." >&2
  exit 1
fi

systemctl reload ssh
echo "sshd reloaded. Your current session stays open."

# 4. fail2ban (sshd jail) and unattended security upgrades.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq fail2ban unattended-upgrades >/dev/null
install -m 0644 -o root -g root "$JAIL_SRC" "$JAIL_DST"
fail2ban-client -t >/dev/null
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT
systemctl enable --now unattended-upgrades >/dev/null
echo "fail2ban: $(fail2ban-client status sshd | grep -E 'Currently banned' | xargs)"
echo "unattended-upgrades: $(systemctl is-active unattended-upgrades)"

cat <<'NEXT'

Done. NOW, from a SECOND terminal, log in again:
    ssh -i <your key> ubuntu@18.61.48.198      (or a new PuTTY window)
PuTTY may say the server's host key isn't cached. Compare the fingerprint it
shows with:  sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
Only close this session after the new login works.
NEXT
