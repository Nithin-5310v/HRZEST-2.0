#!/usr/bin/env bash
# Installs the firewall agent (docs/FIREWALL.md): the service that reports
# every firewall layer to the SOC console and enforces the console's ban list
# in the host firewall (and, with the AWS edge, in the WAF).
#
#   sudo bash scripts/firewall_agent/install_firewall_agent.sh --domain hrzest.com \
#        [--app-env /home/ubuntu/apps/HRZEST/.env] [--aws] [--dry-run]
#
#   --domain D     the site's name (TLS name and Host header for the local nginx)
#   --app-env F    the app's .env: the shared secret is read from its
#                  FIREWALL_AGENT_SECRET, or generated and added there when it
#                  has none (restart hrzest.service afterwards)
#   --aws          also install python3-boto3 for the AWS edge parts
#   --dry-run      print what would change, change nothing
#
# Run scripts/harden_host_firewall.sh first (it creates the nftables table the
# agent fills). Re-running upgrades the agent and keeps the existing settings.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DOMAIN=""
APP_ENV=""
AWS=0
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --app-env) APP_ENV="$2"; shift 2 ;;
    --aws) AWS=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
die() { echo "ERROR: $*" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
[ -n "$DOMAIN" ] || die "--domain is required"
[[ "$DOMAIN" =~ ^[a-z0-9.-]+$ ]] || die "not a domain name: $DOMAIN"
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || die "run as root (or with --dry-run)"
command -v python3 >/dev/null || die "python3 is required"

ENV_FILE=/etc/hrzest/firewall-agent.env

# ── the shared secret ────────────────────────────────────────────────────────
SECRET=""
if [ -f "$ENV_FILE" ]; then
  SECRET="$(sed -n 's/^HRZEST_FW_SECRET=//p' "$ENV_FILE" | tail -1)"
fi
if [ -z "$SECRET" ] && [ -n "$APP_ENV" ] && [ -f "$APP_ENV" ]; then
  SECRET="$(sed -n 's/^FIREWALL_AGENT_SECRET=//p' "$APP_ENV" | tail -1)"
fi
NEW_SECRET=0
if [ -z "$SECRET" ]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  NEW_SECRET=1
fi
[ "${#SECRET}" -ge 32 ] || die "the shared secret is shorter than 32 characters"

if [ "$AWS" = 1 ] && ! dpkg -s python3-boto3 >/dev/null 2>&1; then
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-boto3
fi

if [ "$DRY" = 1 ]; then
  echo "[dry-run] install $SRC/hrzest_fw_agent.py -> /opt/hrzest-fw/hrzest_fw_agent.py"
  echo "[dry-run] install the unit -> /etc/systemd/system/hrzest-fw-agent.service"
  [ -f "$ENV_FILE" ] && echo "[dry-run] keep $ENV_FILE" || echo "[dry-run] create $ENV_FILE (0600) for $DOMAIN"
  [ "$NEW_SECRET" = 1 ] && [ -n "$APP_ENV" ] && echo "[dry-run] add FIREWALL_AGENT_SECRET to $APP_ENV"
  exit 0
fi

install -d -m 0755 /opt/hrzest-fw
install -m 0644 "$SRC/hrzest_fw_agent.py" /opt/hrzest-fw/hrzest_fw_agent.py
install -m 0644 "$SRC/hrzest-fw-agent.service" /etc/systemd/system/hrzest-fw-agent.service
install -d -m 0750 /etc/hrzest

if [ ! -f "$ENV_FILE" ]; then
  umask 077
  sed -e "s#^HRZEST_FW_APP_HOST=.*#HRZEST_FW_APP_HOST=$DOMAIN#" \
      -e "s#^HRZEST_FW_SECRET=.*#HRZEST_FW_SECRET=$SECRET#" \
      "$SRC/firewall-agent.env.example" > "$ENV_FILE"
  chmod 0600 "$ENV_FILE"
  echo "created $ENV_FILE"
fi

if [ "$NEW_SECRET" = 1 ]; then
  if [ -n "$APP_ENV" ] && [ -f "$APP_ENV" ]; then
    cp -a "$APP_ENV" "$APP_ENV.bak-$(date +%Y%m%d-%H%M%S)"
    printf '\n# Firewall agent channel (docs/FIREWALL.md), added by install_firewall_agent.sh\nFIREWALL_AGENT_SECRET=%s\n' "$SECRET" >> "$APP_ENV"
    echo "added FIREWALL_AGENT_SECRET to $APP_ENV -- restart the app: sudo systemctl restart hrzest"
  else
    echo "A new shared secret was generated into $ENV_FILE. Put the same value in the app's .env as"
    echo "FIREWALL_AGENT_SECRET=... (sudo grep HRZEST_FW_SECRET $ENV_FILE) and restart the app."
  fi
fi

if [ -d /run/systemd/system ]; then
  systemctl daemon-reload
  systemctl enable hrzest-fw-agent.service >/dev/null
  systemctl restart hrzest-fw-agent.service
  echo "== check"
  # The agent's own settings, written above.
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
  python3 -I /opt/hrzest-fw/hrzest_fw_agent.py check || echo "WARNING: the check found a problem (see above); the service keeps retrying" >&2
  systemctl --no-pager --lines=5 status hrzest-fw-agent.service || true
fi
echo "done."
