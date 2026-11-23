#!/usr/bin/env bash
# update_geoip.sh -- install or refresh the GeoIP databases the SOC console
# reads to show where an IP address is (utils/ip_geo.py).
#
# Downloads DB-IP's free monthly "Lite" databases (CC BY 4.0, no account or
# key; the console shows the attribution the licence asks for):
#   IP to City Lite  ->  $GEOIP_DIR/city.mmdb   (city, region, country)
#   IP to ASN Lite   ->  $GEOIP_DIR/asn.mmdb    (the network: AS number, owner)
# GEOIP_DIR defaults to instance/geoip in the app checkout, where the app
# looks by default. Only the download goes out: the app itself never sends an
# address anywhere.
#
# Each file is downloaded under a temporary name, checked by opening it with
# the app's own maxminddb library and looking up a well-known address, and
# only then moved over the old one. A failed or partial download never
# replaces a working file. The app picks the new file up within a minute.
# A file less than 20 days old is left alone unless --force is given.
#
# hrzest-geoip.timer runs this monthly (and once soon after it's installed).
# By hand:
#   sudo systemctl start hrzest-geoip      # as the service does
#   bash scripts/update_geoip.sh --force   # from the app checkout
#
# To use MaxMind GeoLite2 instead (free account), put GeoLite2-City.mmdb and
# GeoLite2-ASN.mmdb at those two paths yourself and stop this timer.
set -euo pipefail

APP_DIR="${HRZEST_APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
GEOIP_DIR="${GEOIP_DIR:-$APP_DIR/instance/geoip}"
PYTHON="${HRZEST_PYTHON:-$APP_DIR/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
BASE_URL="${GEOIP_DBIP_URL:-https://download.db-ip.com/free}"
FRESH_DAYS=20
force=""
[ "${1:-}" = "--force" ] && force=1

log() { echo "$*"; }   # stdout lands in the journal under the systemd unit

mkdir -p "$GEOIP_DIR"
tmp="$(mktemp -d "$GEOIP_DIR/.download.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

# This month's file, or last month's while this month's isn't published yet.
months=("$(date -u +%Y-%m)" "$(date -u -d "$(date -u +%Y-%m-15) -1 month" +%Y-%m)")

# $1 = file to check, $2 = city | asn. Exits non-zero unless it is a readable
# database that knows a well-known public address (Google's 8.8.8.8).
verify() {
  "$PYTHON" - "$1" "$2" <<'PY'
import sys
import maxminddb
path, kind = sys.argv[1], sys.argv[2]
reader = maxminddb.open_database(path)
try:
    rec = reader.get("8.8.8.8") or {}
    meta = reader.metadata()
finally:
    reader.close()
ok = (rec.get("country") or {}).get("iso_code") if kind == "city" else rec.get("autonomous_system_number")
if not ok:
    sys.exit(f"{path} has no {kind} data for 8.8.8.8 -- not the expected database")
print(f"{meta.database_type}, {meta.node_count} nodes")
PY
}

failed=0
for kind in city asn; do
  dest="$GEOIP_DIR/$kind.mmdb"
  if [ -z "$force" ] && [ -f "$dest" ] && [ -n "$(find "$dest" -mtime -"$FRESH_DAYS")" ]; then
    log "$kind: $dest is less than $FRESH_DAYS days old -- kept (use --force to refresh)"
    continue
  fi
  got=""
  for month in "${months[@]}"; do
    url="$BASE_URL/dbip-$kind-lite-$month.mmdb.gz"
    if curl -fsSL --retry 3 --retry-delay 10 --connect-timeout 20 --max-time 900 -o "$tmp/$kind.mmdb.gz" "$url"; then
      got="$month"
      break
    fi
    log "$kind: $url not available"
  done
  if [ -z "$got" ]; then
    log "$kind: download FAILED -- the current file (if any) is unchanged"
    failed=1
    continue
  fi
  if ! gzip -dc "$tmp/$kind.mmdb.gz" > "$tmp/$kind.mmdb" || ! info="$(verify "$tmp/$kind.mmdb" "$kind")"; then
    log "$kind: the $got file did not check out -- the current file (if any) is unchanged"
    failed=1
    continue
  fi
  chmod 644 "$tmp/$kind.mmdb"
  mv -f "$tmp/$kind.mmdb" "$dest"
  log "$kind: installed DB-IP $got ($info, $(du -h "$dest" | cut -f1)) as $dest"
done
exit "$failed"
