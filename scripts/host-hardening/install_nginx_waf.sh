#!/usr/bin/env bash
# Proxy firewall layer on the EC2 host (docs/FIREWALL.md, layer 3): nginx +
# ModSecurity v3 + OWASP CRS 4 (LTS), this app's exclusions, the production
# site file, rate limits and slow-client timeouts.
#
#   sudo bash scripts/host-hardening/install_nginx_waf.sh --domain hrzest.com [options]
#
#   --domain D             apex domain; its Let's Encrypt certificate must exist
#   --upstream HOST:PORT   gunicorn (default 127.0.0.1:8000, hrzest.service)
#   --engine On|DetectionOnly
#                          On (default) blocks. DetectionOnly logs what it would
#                          have blocked and blocks nothing: the fast way back
#                          if a legitimate request is refused.
#   --real-ip-from CIDRS   behind the AWS edge only: comma-separated ranges the
#                          load balancer connects from (the VPC CIDR). nginx
#                          then logs, limits and bans the real client instead
#                          of the load balancer. Never set it without the edge:
#                          anyone could then pick their own address.
#   --no-site              leave the site file alone; install ModSecurity, the
#                          snippets and the http-level file only, and print
#                          the lines to add to your own site file
#   --crs-tarball FILE     a local copy of the pinned CRS release (offline host)
#   --no-selftest          skip the two test requests at the end
#   --dry-run              print what would change, change nothing
#
# Safe to re-run: every file it replaces is backed up first, `nginx -t` must
# pass, and on any failure the previous files are put back before it exits.
# It does not touch the certificate or certbot.
set -euo pipefail

# OWASP CRS release, pinned. coreruleset-4.30.0-minimal.tar.gz, checked against
# the CRS release signing key (fingerprint 3600 6F0E 0BA1 6783 2158 8211 38EE
# ACA1 AB8A 6E72) when it was pinned.
#
# Why 4.30.0 and not the 4.25.x LTS line: 4.25.2 added `XML://@*` targets to
# 187 rules, which Ubuntu 24.04's libmodsecurity 3.0.12 cannot parse (nginx
# refuses to start). 4.30.0 is the newest release that loads on it. Before
# moving to a newer CRS, load it on the host first:  nginx -t  (this script
# does, and rolls back if it fails).
CRS_VERSION=4.30.0
CRS_SHA256=3d678a41fd5aade34760127fef5dd64fd7a77848913fc0f70dde0cf467c94427
CRS_URL="https://github.com/coreruleset/coreruleset/releases/download/v${CRS_VERSION}/coreruleset-${CRS_VERSION}-minimal.tar.gz"

SRC="$(cd "$(dirname "$0")" && pwd)"
DOMAIN=""
UPSTREAM="127.0.0.1:8000"
ENGINE="On"
REAL_IP_FROM=""
INSTALL_SITE=1
CRS_TARBALL=""
SELFTEST=1
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --upstream) UPSTREAM="$2"; shift 2 ;;
    --engine) ENGINE="$2"; shift 2 ;;
    --real-ip-from) REAL_IP_FROM="$2"; shift 2 ;;
    --no-site) INSTALL_SITE=0; shift ;;
    --crs-tarball) CRS_TARBALL="$2"; shift 2 ;;
    --no-selftest) SELFTEST=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "== $*"; }

[ -n "$DOMAIN" ] || die "--domain is required (e.g. --domain hrzest.com)"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || die "not a domain name: $DOMAIN"
[[ "$UPSTREAM" =~ ^[A-Za-z0-9.:-]+:[0-9]{1,5}$ ]] || die "--upstream must be HOST:PORT"
case "$ENGINE" in On|DetectionOnly) ;; *) die "--engine must be On or DetectionOnly" ;; esac
if [ -n "$REAL_IP_FROM" ]; then
  IFS=',' read -r -a _cidrs <<< "$REAL_IP_FROM"
  for c in "${_cidrs[@]}"; do
    [[ "$c" =~ ^[0-9a-fA-F:.]+/[0-9]{1,3}$ ]] || die "--real-ip-from: not a CIDR: $c"
  done
fi
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || die "run as root (or with --dry-run)"

CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
if [ "$INSTALL_SITE" = 1 ] && [ "$DRY" = 0 ]; then
  [ -f "$CERT_DIR/fullchain.pem" ] && [ -f "$CERT_DIR/privkey.pem" ] \
    || die "no certificate at $CERT_DIR (run certbot first, or use --no-site)"
fi

# ── 1. packages ───────────────────────────────────────────────────────────────
say "packages"
PKGS="nginx libnginx-mod-http-modsecurity curl ca-certificates"
if [ "$DRY" = 1 ]; then
  echo "[dry-run] apt-get install -y $PKGS"
else
  missing=""
  for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
  if [ -n "$missing" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $missing
  fi
fi

# ── backup / restore of everything this script replaces ──────────────────────
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/nginx/hrzest-backups/$STAMP"
TOUCHED=()
backup() {   # backup <path>: remember it, copy the current version if there is one
  TOUCHED+=("$1")
  if [ -e "$1" ] || [ -L "$1" ]; then
    mkdir -p "$BACKUP$(dirname "$1")"
    cp -a "$1" "$BACKUP$1"
  fi
}
restore() {
  echo "restoring the previous files from $BACKUP" >&2
  for f in "${TOUCHED[@]}"; do
    if [ -e "$BACKUP$f" ] || [ -L "$BACKUP$f" ]; then
      rm -rf "$f"; cp -a "$BACKUP$f" "$f"
    else
      rm -rf "$f"
    fi
  done
}
put() {      # put <source> <dest> [mode]: install a file, backing up what it replaces
  local src="$1" dest="$2" mode="${3:-0644}"
  if [ "$DRY" = 1 ]; then
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then echo "[dry-run] unchanged $dest"
    else echo "[dry-run] would write $dest"; fi
    return
  fi
  if [ -f "$dest" ] && cmp -s "$src" "$dest"; then return; fi
  backup "$dest"
  install -D -m "$mode" "$src" "$dest"
}

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
fail() {
  echo "ERROR: $*" >&2
  [ "$DRY" = 1 ] || restore
  exit 1
}

# ── 2. OWASP CRS, pinned and checksummed ─────────────────────────────────────
say "OWASP CRS $CRS_VERSION"
MODSEC=/etc/nginx/modsec
CRS_DIR="$MODSEC/coreruleset-$CRS_VERSION"
if [ -f "$CRS_DIR/rules/REQUEST-949-BLOCKING-EVALUATION.conf" ]; then
  echo "already installed at $CRS_DIR"
elif [ "$DRY" = 1 ]; then
  echo "[dry-run] would download $CRS_URL, check sha256 $CRS_SHA256 and unpack to $CRS_DIR"
else
  if [ -n "$CRS_TARBALL" ]; then
    cp "$CRS_TARBALL" "$TMP/crs.tar.gz"
  else
    curl -fsSL --proto '=https' --tlsv1.2 -o "$TMP/crs.tar.gz" "$CRS_URL" || die "could not download $CRS_URL"
  fi
  echo "$CRS_SHA256  $TMP/crs.tar.gz" | sha256sum -c --quiet - || die "CRS checksum mismatch: refusing to install it"
  mkdir -p "$MODSEC"
  tar -xzf "$TMP/crs.tar.gz" -C "$MODSEC"
  [ -f "$CRS_DIR/rules/REQUEST-949-BLOCKING-EVALUATION.conf" ] || die "unexpected CRS archive layout"
  chown -R root:root "$CRS_DIR"
fi
if [ "$DRY" = 0 ]; then
  backup "$MODSEC/crs"
  ln -sfn "coreruleset-$CRS_VERSION" "$MODSEC/crs"
fi

# ── 3. ModSecurity configuration ─────────────────────────────────────────────
say "ModSecurity configuration (engine: $ENGINE)"
sed "s/__ENGINE__/$ENGINE/" "$SRC/modsecurity/modsecurity.conf" > "$TMP/modsecurity.conf"
put "$TMP/modsecurity.conf"                     "$MODSEC/modsecurity.conf"
put "$SRC/modsecurity/main.conf"                "$MODSEC/main.conf"
put "$SRC/modsecurity/crs-setup.conf"           "$MODSEC/crs-setup.conf"
put "$SRC/modsecurity/hrzest-before-crs.conf"   "$MODSEC/hrzest-before-crs.conf"
put "$SRC/modsecurity/hrzest-after-crs.conf"    "$MODSEC/hrzest-after-crs.conf"
if [ "$DRY" = 0 ]; then
  for f in local-before-crs.conf local-after-crs.conf; do
    [ -e "$MODSEC/$f" ] || printf '# This server'"'"'s own ModSecurity exclusions (never overwritten by install_nginx_waf.sh).\n' > "$MODSEC/$f"
  done
  install -d -m 0750 -o www-data -g www-data /var/cache/modsecurity /var/cache/modsecurity/tmp /var/cache/modsecurity/data
  install -d -m 0750 -o www-data -g adm /var/log/nginx/modsec
fi

# ── 4. nginx pieces ──────────────────────────────────────────────────────────
say "nginx snippets and http-level settings"
put "$SRC/nginx/hrzest-waf-http.conf" /etc/nginx/conf.d/hrzest-waf-http.conf
put "$SRC/nginx/hrzest-tls.conf"      /etc/nginx/snippets/hrzest-tls.conf
put "$SRC/nginx/hrzest-proxy.conf"    /etc/nginx/snippets/hrzest-proxy.conf

{
  echo "# Written by install_nginx_waf.sh. Real client address behind the AWS edge."
  if [ -n "$REAL_IP_FROM" ]; then
    IFS=',' read -r -a _cidrs <<< "$REAL_IP_FROM"
    for c in "${_cidrs[@]}"; do echo "set_real_ip_from $c;"; done
    echo "real_ip_header X-Forwarded-For;"
    echo "real_ip_recursive on;"
  else
    echo "# Direct mode (no load balancer): nothing to trust, the TCP peer is the client."
  fi
} > "$TMP/hrzest-realip.conf"
put "$TMP/hrzest-realip.conf" /etc/nginx/snippets/hrzest-realip.conf

# OCSP stapling only when the certificate names a responder (Let's Encrypt
# certificates no longer do).
{
  echo "# Written by install_nginx_waf.sh from the certificate in $CERT_DIR."
  if [ -f "$CERT_DIR/fullchain.pem" ] && [ -n "$(openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -ocsp_uri 2>/dev/null)" ]; then
    echo "ssl_stapling on;"
    echo "ssl_stapling_verify on;"
    echo "ssl_trusted_certificate $CERT_DIR/chain.pem;"
    echo "resolver 127.0.0.53 valid=300s;"
    echo "resolver_timeout 5s;"
  else
    echo "# The certificate names no OCSP responder, so there is nothing to staple."
  fi
} > "$TMP/hrzest-ocsp.conf"
put "$TMP/hrzest-ocsp.conf" /etc/nginx/snippets/hrzest-ocsp.conf

# ── 5. the site ──────────────────────────────────────────────────────────────
if [ "$INSTALL_SITE" = 1 ]; then
  say "site file for $DOMAIN -> $UPSTREAM"
  sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__UPSTREAM__/$UPSTREAM/g" "$SRC/nginx/hrzest-site.conf" > "$TMP/hrzest-site.conf"
  put "$TMP/hrzest-site.conf" /etc/nginx/sites-available/hrzest
  if [ "$DRY" = 0 ]; then
    backup /etc/nginx/sites-enabled/hrzest
    ln -sfn /etc/nginx/sites-available/hrzest /etc/nginx/sites-enabled/hrzest
  fi
else
  cat <<EOF
== --no-site: add these to the 443 server block of your own site file, then
   run: nginx -t && systemctl reload nginx
       include /etc/nginx/snippets/hrzest-realip.conf;
       modsecurity on;
       modsecurity_rules_file /etc/nginx/modsec/main.conf;
       add_header Strict-Transport-Security \$hzwaf_hsts always;
   and "modsecurity off;" inside location /static/ and location = /healthz.
EOF
fi

# ── 6. log rotation ──────────────────────────────────────────────────────────
cat > "$TMP/logrotate" <<'EOF'
# Installed by scripts/host-hardening/install_nginx_waf.sh
/var/log/nginx/hrzest-access.json /var/log/nginx/hrzest-error.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    create 0640 www-data adm
    sharedscripts
    postrotate
        invoke-rc.d nginx rotate >/dev/null 2>&1 || true
    endscript
}
# ModSecurity keeps its audit log open itself, so it is copied and truncated
# in place; the firewall agent notices the shrink and starts from the top.
/var/log/nginx/modsec/audit.json {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
EOF
put "$TMP/logrotate" /etc/logrotate.d/hrzest-nginx-waf

[ "$DRY" = 1 ] && { echo "dry run: nothing changed."; exit 0; }

# ── 7. test, then reload ─────────────────────────────────────────────────────
say "nginx -t"
if ! nginx -t 2> "$TMP/nginx-t.log"; then
  cat "$TMP/nginx-t.log" >&2
  fail "nginx rejected the new configuration"
fi
if systemctl is-active --quiet nginx 2>/dev/null; then
  systemctl reload nginx || fail "nginx reload failed"
elif command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files nginx.service >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl start nginx || fail "nginx failed to start"
else
  nginx -s reload 2>/dev/null || nginx || fail "nginx failed to start"
fi

if [ "$SELFTEST" = 1 ] && [ "$INSTALL_SITE" = 1 ]; then
  say "self-test"
  sleep 1
  ok=$(curl -sk -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/healthz" || true)
  # A marker in the query string, so the check finds ModSecurity's own record
  # of THIS request: a 403 alone could also be the app's WAF answering.
  bad=$(curl -sk -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" \
        "https://$DOMAIN/?hzwaf_selftest=$STAMP&q=%3Cscript%3Ealert(1)%3C%2Fscript%3E%27%20UNION%20SELECT%201--" || true)
  sleep 1
  # The audit log, not the error log: nginx cuts long error lines short, and
  # the request line (where the marker is) comes last.
  seen=0
  grep -qF "hzwaf_selftest=$STAMP" /var/log/nginx/modsec/audit.json 2>/dev/null && seen=1
  echo "GET /healthz -> $ok (expect 200; anything else means the app is not answering on $UPSTREAM)"
  if [ "$ENGINE" = On ]; then
    echo "GET /?q=<script>... -> $bad (expect 403, refused by ModSecurity: $([ "$seen" = 1 ] && echo yes || echo NO))"
    [ "$bad" = 403 ] && [ "$seen" = 1 ] || echo "WARNING: ModSecurity did not refuse the test attack -- check /var/log/nginx/hrzest-error.log" >&2
  else
    echo "GET /?q=<script>... -> $bad (DetectionOnly: recorded by ModSecurity: $([ "$seen" = 1 ] && echo yes || echo NO), not blocked)"
  fi
fi
echo "done. Backups of replaced files: $BACKUP"
