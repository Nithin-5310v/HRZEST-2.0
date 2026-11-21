#!/usr/bin/env bash
# fail2ban jails for the web tier (docs/FIREWALL.md, layer 3): ModSecurity
# refusals, sustained rate-limit refusals, failed API sign-ins. Run after
# install_nginx_waf.sh (it creates the logs these jails read).
#
#   sudo bash scripts/host-hardening/install_fail2ban_web.sh [--ignore CIDRS] [--dry-run]
#
#   --ignore CIDRS   comma-separated addresses never to ban (office egress IPs,
#                    uptime monitors); the same list as WAF_NEVER_BAN_CIDRS
#   --dry-run        print what would change, change nothing
#
# Each filter is checked against a sample log line before anything is
# installed, `fail2ban-client -t` must accept the result, and on failure the
# previous files are put back. The SSH and recidive jails
# (install_ssh_hardening.sh) are not touched.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/fail2ban"
IGNORE=""
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ignore) IGNORE="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
die() { echo "ERROR: $*" >&2; exit 1; }
IGNORE_LIST=""
if [ -n "$IGNORE" ]; then
  IFS=',' read -r -a _c <<< "$IGNORE"
  for c in "${_c[@]}"; do
    c="$(echo "$c" | tr -d ' ')"
    [[ "$c" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]] || die "--ignore: not an address or CIDR: $c"
    IGNORE_LIST="$IGNORE_LIST $c"
  done
fi
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || die "run as root (or with --dry-run)"

if ! command -v fail2ban-client >/dev/null 2>&1; then
  if [ "$DRY" = 1 ]; then echo "[dry-run] apt-get install -y fail2ban"
  else DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban; fi
fi

# ── check each filter against a line of the format it reads ──────────────────
check() {  # check <filter> <sample line> <expected address>
  local filter="$1" line="$2" ip="$3" out
  if [ "$DRY" = 1 ] && ! command -v fail2ban-regex >/dev/null 2>&1; then return; fi
  out=$(fail2ban-regex "$line" "$SRC/filter-$filter.conf" 2>&1 || true)
  echo "$out" | grep -qE "Lines: 1 lines, 0 ignored, 1 matched" \
    || { echo "$out" >&2; die "filter $filter does not match its sample line"; }
  echo "filter $filter: matches ($ip)"
}
check hrzest-modsec \
  '2026/11/21 06:21:24 [error] 57#57: *3 [client 203.0.113.5] ModSecurity: Access denied with code 403 (phase 2). Matched "Operator `Ge'"'"' with parameter `5'"'"' against variable `TX:BLOCKING_INBOUND_ANOMALY_SCORE'"'"' (Value: `30'"'"' )' \
  203.0.113.5
check hrzest-nginx-limit \
  '2026/11/21 06:21:25 [error] 57#57: *140 limiting requests, excess: 60.120 by zone "hzwaf_login", client: 203.0.113.6, server: hrzest.com, request: "POST /acme/admin_login HTTP/2.0", host: "hrzest.com"' \
  203.0.113.6
check hrzest-nginx-auth \
  '{"time":"2026-11-21T06:21:24+00:00","remote_addr":"203.0.113.7","method":"POST","uri":"/acme/api/login","status":401,"bytes":40,"rt":0.012,"host":"hrzest.com","ua":"okhttp/4.12","referer":"","request_id":"abc","upstream_status":"401"}' \
  203.0.113.7

# ── install (with backups) ───────────────────────────────────────────────────
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/fail2ban/hrzest-backups/$STAMP"
TOUCHED=()
put() {
  local src="$1" dest="$2"
  if [ "$DRY" = 1 ]; then echo "[dry-run] write $dest"; return; fi
  TOUCHED+=("$dest")
  if [ -e "$dest" ]; then mkdir -p "$BACKUP$(dirname "$dest")"; cp -a "$dest" "$BACKUP$dest"; fi
  install -D -m 0644 "$src" "$dest"
}
restore() {
  for f in "${TOUCHED[@]}"; do
    if [ -e "$BACKUP$f" ]; then cp -a "$BACKUP$f" "$f"; else rm -f "$f"; fi
  done
}
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
sed "s#__IGNOREIP__#${IGNORE_LIST# }#" "$SRC/jail-hrzest-web.local" > "$TMP/jail"
put "$SRC/filter-hrzest-modsec.conf"      /etc/fail2ban/filter.d/hrzest-modsec.conf
put "$SRC/filter-hrzest-nginx-limit.conf" /etc/fail2ban/filter.d/hrzest-nginx-limit.conf
put "$SRC/filter-hrzest-nginx-auth.conf"  /etc/fail2ban/filter.d/hrzest-nginx-auth.conf
put "$TMP/jail"                           /etc/fail2ban/jail.d/hrzest-web.local
[ "$DRY" = 1 ] && { echo "dry run: nothing changed."; exit 0; }

# The jails read these files; fail2ban refuses to start a jail whose log is missing.
for f in /var/log/nginx/hrzest-error.log /var/log/nginx/hrzest-access.json; do
  [ -e "$f" ] || { install -m 0640 -o www-data -g adm /dev/null "$f"; }
done

if ! fail2ban-client -t >"$TMP/t.log" 2>&1; then
  cat "$TMP/t.log" >&2
  restore
  die "fail2ban rejected the configuration; the previous files were put back"
fi
if [ -d /run/systemd/system ] && systemctl is-active --quiet fail2ban; then
  systemctl reload fail2ban
elif [ -d /run/systemd/system ]; then
  systemctl enable --now fail2ban
else
  fail2ban-client reload >/dev/null 2>&1 || fail2ban-client start >/dev/null
fi
sleep 2
for j in hrzest-modsec hrzest-nginx-limit hrzest-nginx-auth; do
  fail2ban-client status "$j" >/dev/null 2>&1 && echo "jail $j: running" || echo "WARNING: jail $j is not running (fail2ban-client status $j)" >&2
done
echo "done."
