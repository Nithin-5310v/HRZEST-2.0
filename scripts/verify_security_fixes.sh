#!/usr/bin/env bash
# Checks the fixes for the 2026-11 external security review against the live
# site and prints PASS / FAIL / SKIP per check (SKIP = a tool such as dig or
# nmap isn't installed). Exit code = number of FAILs (0 = everything passed).
#
#   bash scripts/verify_security_fixes.sh                 # https://hrzest.com
#   HOST=staging.example.com bash scripts/verify_security_fixes.sh
#
# Read-only: it only makes GET requests, DNS lookups and one nmap scan of
# port 22 (run nmap only from an address you're allowed to scan from; from
# any other address the port should show as filtered, which counts as PASS).
# SECURITY_REMEDIATION.md explains each check.
set -u

HOST="${HOST:-hrzest.com}"
BASE="https://$HOST"
WWW="https://www.$HOST"
FAILS=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
skip() { printf 'SKIP  %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
headers() { curl -sS -m 20 -D - -o /dev/null "$1" 2>/dev/null | tr -d '\r'; }
hval() { grep -i "^$1:" | head -1 | cut -d: -f2- | sed 's/^ *//'; }

if ! have curl; then echo "curl is required" >&2; exit 1; fi

echo "== OBS-01 / OBS-08: auth pages are not cached, no legacy XSS header =="
for path in login create_org admin_forgot_password; do
  h="$(headers "$BASE/$path")"
  cc="$(printf '%s\n' "$h" | hval cache-control)"
  case "$cc" in *no-store*) pass "/$path Cache-Control: $cc" ;; *) fail "/$path Cache-Control is '${cc:-missing}' (want no-store)" ;; esac
  [ "$(printf '%s\n' "$h" | hval pragma)" = "no-cache" ] && pass "/$path Pragma: no-cache" || fail "/$path Pragma missing"
  xss="$(printf '%s\n' "$h" | hval x-xss-protection)"
  case "$xss" in 1*) fail "/$path X-XSS-Protection: $xss (want 0 or absent)" ;; *) pass "/$path X-XSS-Protection: ${xss:-absent}" ;; esac
done

echo "== Ground rules: existing security headers unchanged, each sent once =="
h="$(headers "$BASE/login")"
check_hdr() {  # name, exact expected value ('' = just present)
  local n v count
  v="$(printf '%s\n' "$h" | hval "$1")"
  count="$(printf '%s\n' "$h" | grep -ci "^$1:")"
  if [ -z "$v" ]; then fail "$1 missing"
  elif [ -n "$2" ] && [ "$v" != "$2" ]; then fail "$1 is '$v' (want '$2')"
  elif [ "$count" -ne 1 ]; then fail "$1 sent $count times (want once)"
  else pass "$1: ${2:-present}"; fi
}
check_hdr strict-transport-security "max-age=63072000; includeSubDomains; preload"
check_hdr x-frame-options "DENY"
check_hdr x-content-type-options "nosniff"
check_hdr referrer-policy "strict-origin-when-cross-origin"
check_hdr content-security-policy ""
case "$(printf '%s\n' "$h" | hval content-security-policy)" in
  *"'nonce-"*) pass "CSP is nonce-based" ;; *) fail "CSP has no nonce" ;; esac
ck="$(printf '%s\n' "$h" | grep -i '^set-cookie: __Host-session=' | head -1)"
if [ -n "$ck" ]; then
  ok=1
  for flag in Secure HttpOnly "Path=/" "SameSite=Strict"; do
    printf '%s' "$ck" | grep -qi "; *$flag" || { ok=0; fail "__Host-session cookie lacks $flag"; }
  done
  printf '%s' "$ck" | grep -qi '; *Domain=' && { ok=0; fail "__Host-session cookie has a Domain"; }
  [ "$ok" -eq 1 ] && pass "__Host-session cookie flags (Secure; HttpOnly; Path=/; SameSite=Strict; no Domain)"
else
  skip "no __Host-session cookie on this response"
fi

echo "== OBS-06: security.txt =="
code="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "$BASE/.well-known/security.txt")"
[ "$code" = "200" ] && pass "/.well-known/security.txt -> 200" || fail "/.well-known/security.txt -> $code"
ct="$(headers "$BASE/.well-known/security.txt" | hval content-type)"
case "$ct" in "text/plain; charset=utf-8") pass "security.txt Content-Type: $ct" ;; *) fail "security.txt Content-Type '$ct'" ;; esac
body="$(curl -sS -m 20 "$BASE/.well-known/security.txt")"
for field in "Contact: mailto:security@hrzest.com" "Expires: " "Canonical: https://hrzest.com/.well-known/security.txt" "Policy: https://hrzest.com/security-policy"; do
  printf '%s' "$body" | grep -qF "$field" && pass "security.txt has '$field'" || fail "security.txt lacks '$field'"
done
r="$(curl -sS -m 20 -o /dev/null -w '%{http_code} %{redirect_url}' "$BASE/security.txt")"
case "$r" in "301 "*"/.well-known/security.txt") pass "/security.txt -> $r" ;; *) fail "/security.txt -> $r" ;; esac
code="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "$BASE/security-policy")"
[ "$code" = "200" ] && pass "/security-policy -> 200" || fail "/security-policy -> $code"

echo "== OBS-07: robots.txt and the operator console =="
if curl -sS -m 20 "$BASE/robots.txt" | grep -qiE 'super_admin|webhooks|csp-report'; then
  fail "robots.txt still names a private path"
else
  pass "robots.txt names no private path"
fi
code="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "$BASE/super_admin")"
[ "$code" = "404" ] && pass "/super_admin -> 404 without a super-admin session" || fail "/super_admin -> $code (want 404)"
case "$(headers "$BASE/super_admin/login" | hval x-robots-tag)" in
  *noindex*) pass "/super_admin/login has X-Robots-Tag: noindex" ;; *) fail "/super_admin/login lacks X-Robots-Tag noindex" ;; esac

echo "== OBS-09: www redirects to the canonical host =="
r="$(curl -sS -m 20 -o /dev/null -w '%{http_code} %{redirect_url}' "$WWW/test?x=1")"
[ "$r" = "301 $BASE/test?x=1" ] && pass "www -> $r" || fail "www -> '$r' (want '301 $BASE/test?x=1')"
r="$(curl -sS -m 20 -o /dev/null -w '%{http_code} %{redirect_url}' "http://www.$HOST/a?b=2")"
# One hop straight to the canonical HTTPS host (the nginx port-80 change).
[ "$r" = "301 $BASE/a?b=2" ] && pass "http://www -> $r" || fail "http://www -> '$r' (want '301 $BASE/a?b=2')"
if curl -sS -m 20 "$BASE/sitemap.xml" | grep -q "www\.$HOST"; then fail "sitemap.xml uses www"; else pass "sitemap.xml uses $HOST"; fi
canon="$(curl -sS -m 20 "$BASE/" | grep -oE '<link rel="canonical" href="[^"]+"' | head -1)"
case "$canon" in *"\"$BASE/\""*) pass "home canonical: $canon" ;; *) fail "home canonical: '${canon:-missing}'" ;; esac

echo "== OBS-02 / 03 / 04: DNS =="
if have dig; then
  d="$(dig +short "_dmarc.$HOST" TXT)"
  case "$d" in *"v=DMARC1"*) pass "DMARC: $d" ;; *) fail "no DMARC record at _dmarc.$HOST" ;; esac
  c="$(dig +short "$HOST" CAA)"
  case "$c" in *letsencrypt.org*) pass "CAA: $(echo "$c" | tr '\n' ' ')" ;; *) fail "no CAA record naming letsencrypt.org" ;; esac
  ds="$(dig +short "$HOST" DS)"
  [ -n "$ds" ] && pass "DS record published (DNSSEC)" || fail "no DS record (DNSSEC not enabled)"
  if dig +dnssec "$HOST" A @1.1.1.1 | grep -qE 'flags:.* ad[ ;]'; then pass "1.1.1.1 validates $HOST (AD flag)"
  else fail "1.1.1.1 does not validate $HOST (no AD flag)"; fi
else
  skip "dig not installed (apt install dnsutils): DMARC, CAA, DS checks skipped"
fi

echo "== OBS-05: SSH =="
if have nmap; then
  out="$(nmap -Pn -p 22 --script ssh2-enum-algos,ssh-auth-methods "$HOST" 2>/dev/null)"
  if printf '%s' "$out" | grep -qE '22/tcp +(filtered|closed)'; then
    pass "port 22 is filtered/closed from this address"
  elif printf '%s' "$out" | grep -q '22/tcp *open'; then
    printf '%s' "$out" | grep -qi 'hmac-sha1' && fail "SSH still offers hmac-sha1" || pass "SSH offers no hmac-sha1 MACs"
    if printf '%s' "$out" | grep -qi 'password\|keyboard-interactive'; then fail "SSH offers password logins"
    else pass "SSH offers publickey only"; fi
    echo "      (port 22 is open from this address: fine if it's on the allow-list, otherwise fix the security group)"
  else
    fail "nmap gave no result for port 22"
  fi
else
  skip "nmap not installed: SSH checks skipped"
fi

echo
[ "$FAILS" -eq 0 ] && echo "All checks passed." || echo "$FAILS check(s) failed."
exit "$FAILS"
