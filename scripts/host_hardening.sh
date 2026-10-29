#!/usr/bin/env bash
# Host-level controls that compose.yaml, nginx and the honeypot rely on but
# which previously existed only as comments (the terraform/ directory and the
# "deploy.sh ufw block" they cited are not in this repository).
#
#   sudo bash scripts/host_hardening.sh [--user attendance] [--dry-run]
#
# Idempotent: re-running converges instead of duplicating rules. Run as root
# on the Ubuntu/Debian VPS that hosts the stack. It does NOT open the SSH
# port to anything new, and it asks ufw to enable only after SSH is allowed,
# so it cannot lock you out of an SSH session that is already open.
#
# What it does
#   1. ufw: default-deny inbound; allow SSH/HTTP/HTTPS and the six honeypot
#      ports; NAT-redirect 80->8080, 443->8443 (rootless nginx) and
#      21->8021, 23->8023, 25->8025 (rootless honeypot) in /etc/ufw/before.rules.
#   2. podman, for the rootless service user: enable lingering, the
#      auto-update timer (digest-pinned images roll forward / back on failed
#      healthcheck) and podman-restart.service (restart:always across reboots).
#   3. Reports whether rootless networking preserves real client IPs
#      (pasta does; slirp4netns does not -- see utils/client_ip.py).
set -euo pipefail

SERVICE_USER="attendance"
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --user) SERVICE_USER="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

run() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || { echo "run as root (or use --dry-run)" >&2; exit 1; }

BEGIN="# BEGIN hrzest port redirects"
END="# END hrzest port redirects"
RULES_FILE=/etc/ufw/before.rules

# ── 1. firewall ──────────────────────────────────────────────────────────────
echo "== ufw =="
command -v ufw >/dev/null || run apt-get install -y ufw

run ufw default deny incoming
run ufw default allow outgoing
run ufw allow 22/tcp comment 'ssh'
run ufw allow 80/tcp comment 'http (acme + redirect)'
run ufw allow 443/tcp comment 'https'
# Honeypot decoys. The listener binds the high port; the NAT block below maps
# the public low port to it. 1433/3306/3389 are bound directly.
for p in 21 23 25 1433 3306 3389 8021 8023 8025 8080 8443; do
  run ufw allow "${p}/tcp" comment 'hrzest decoy/redirect target'
done

NAT_BLOCK="$BEGIN
*nat
:PREROUTING ACCEPT [0:0]
-A PREROUTING -p tcp --dport 80  -j REDIRECT --to-port 8080
-A PREROUTING -p tcp --dport 443 -j REDIRECT --to-port 8443
-A PREROUTING -p tcp --dport 21  -j REDIRECT --to-port 8021
-A PREROUTING -p tcp --dport 23  -j REDIRECT --to-port 8023
-A PREROUTING -p tcp --dport 25  -j REDIRECT --to-port 8025
COMMIT
$END"

if [ -f "$RULES_FILE" ] && grep -qF "$BEGIN" "$RULES_FILE"; then
  echo "NAT redirect block already present in $RULES_FILE"
else
  echo "adding NAT redirect block to $RULES_FILE"
  if [ "$DRY" = 1 ]; then
    printf '%s\n' "$NAT_BLOCK"
  else
    cp -a "$RULES_FILE" "${RULES_FILE}.hrzest.bak.$(date +%s)"
    # The *nat table must precede the *filter section of before.rules.
    { printf '%s\n\n' "$NAT_BLOCK"; cat "$RULES_FILE"; } > "${RULES_FILE}.new"
    mv "${RULES_FILE}.new" "$RULES_FILE"
  fi
fi
run ufw --force enable
run ufw reload

# ── 2. rootless podman for the service user ──────────────────────────────────
echo "== podman ($SERVICE_USER) =="
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "user '$SERVICE_USER' does not exist -- create it first (deploy.sh does)" >&2
  exit 1
fi
UID_NUM="$(id -u "$SERVICE_USER")"
run loginctl enable-linger "$SERVICE_USER"
as_user() {
  run runuser -u "$SERVICE_USER" -- env XDG_RUNTIME_DIR="/run/user/${UID_NUM}" "$@"
}
as_user systemctl --user enable --now podman-auto-update.timer
as_user systemctl --user enable podman-restart.service

# ── 3. do clients keep their real IP? ───────────────────────────────────────
echo "== rootless network mode =="
if [ "$DRY" = 0 ]; then
  MODE="$(runuser -u "$SERVICE_USER" -- env XDG_RUNTIME_DIR="/run/user/${UID_NUM}" \
            podman info --format '{{.Host.RootlessNetworkCmd}}' 2>/dev/null || echo unknown)"
  echo "rootless network command: $MODE"
  if [ "$MODE" != "pasta" ]; then
    cat >&2 <<'EOF'
WARNING: this host is not using pasta for rootless networking. With
slirp4netns every inbound connection reaches the containers from the same
internal gateway address, so the application cannot tell visitors apart:
per-IP rate limits, the WAF auto-ban and the honeypot ban are disabled for
such addresses (utils/client_ip.py logs a CRITICAL line when it sees this).
Fix: podman >= 5.0 with pasta (containers.conf: [network] default_rootless_network_cmd="pasta"),
or run nginx with host networking, or put a trusted edge in front that
forwards the real address. See docs/SECURITY_HARDENING.md, "Real client IP".
EOF
  fi
fi
echo "done."
