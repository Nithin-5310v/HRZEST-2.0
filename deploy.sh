#!/usr/bin/env bash
# deploy.sh -- build-on-host deploy for HRzest.com (rootless Podman).
#
# Normal production deploys do NOT use this: CI builds, scans and signs the
# image and the host only verifies and runs it by digest
# (scripts/deploy_verified_image.sh, called from .github/workflows/deploy.yml).
# Use this script for a first install, or when CI is unavailable.
#
#   bash deploy.sh
#
# Requires: podman, podman-compose, git, curl. First time on a new host also
# run (as root):  bash scripts/host_hardening.sh --user "$(id -un)"
# which installs the firewall/NAT redirects and enables podman auto-update.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
COMPOSE="podman-compose -f compose.yaml -f compose.prod.yaml"

echo -e "${GREEN}HRzest.com -- deployment${NC}"
echo "=================================================="

# 1. .env present and configured
if [ ! -f ".env" ]; then
  echo -e "${YELLOW}No .env found. Running setup_env.py to create one...${NC}"
  python3 setup_env.py
  echo -e "${RED}Edit .env (APP_URL, ALLOWED_ORIGINS, DB_*, SMTP, ...) then re-run: bash deploy.sh${NC}"
  exit 1
fi

if grep -qE "your_secret_key_here|your_encryption_key_here|YOUR_DOMAIN|change_me" .env; then
  echo -e "${RED}.env still has placeholder values. Fix them (python3 setup_env.py generates keys).${NC}"
  exit 1
fi

# Production must not boot half-configured: these have safe-looking defaults
# that are wrong for a real deployment.
missing=0
for var in SECRET_KEY ENCRYPTION_KEY DB_PASS ALLOWED_ORIGINS APP_URL HONEYPOT_REPORT_SECRET; do
  if ! grep -qE "^${var}=.+" .env; then
    echo -e "${RED}.env is missing a value for ${var}${NC}"
    missing=1
  fi
done
if grep -qE "^ALLOWED_ORIGINS=\*" .env; then
  echo -e "${RED}ALLOWED_ORIGINS=* is not acceptable in production${NC}"; missing=1
fi
[ "$missing" = 0 ] || exit 1

# 2. Pull exactly what CI tested (fast-forward only: never merge on the host)
echo -e "\n${GREEN}Updating source...${NC}"
git pull --ff-only origin main

# 3. Build and (re)start
echo -e "\n${GREEN}Building images...${NC}"
$COMPOSE build app

echo -e "\n${GREEN}Starting services...${NC}"
# --no-deps keeps the base file's local `db` out of the prod stack (the app
# talks to the managed database via DB_HOST); clamav is named explicitly
# because uploads fail closed in production without it.
$COMPOSE up -d --no-deps app nginx certbot clamav honeypot

# 4. Health: the app is only exposed on the internal network, so ask it
#    from inside its own container.
echo -e "\n${GREEN}Waiting for health check...${NC}"
MAX=30; COUNT=0
until $COMPOSE exec -T app curl -fsk https://localhost:5000/healthz >/dev/null 2>&1; do
  COUNT=$((COUNT+1))
  if [ "$COUNT" -ge "$MAX" ]; then
    echo -e "${RED}Health check failed after $((MAX*2))s${NC}"
    $COMPOSE logs --tail 40 app || true
    exit 1
  fi
  echo -n "."
  sleep 2
done

echo -e "\n${GREEN}Deployment successful.${NC}"
echo "   App URL: $(grep -E '^APP_URL=' .env | cut -d= -f2-)"
echo "   Next: bash scripts/verify_audit_chain.py is run by the audit timer; see SECURITY_HARDENING.md"
