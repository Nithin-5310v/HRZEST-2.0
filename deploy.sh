#!/usr/bin/env bash
# deploy.sh — One-command VPS deployment for HRzest.com
# Usage: bash deploy.sh
# Requires: Docker + Docker Compose installed on the VPS

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${GREEN}🚀 HRzest.com — Deployment Script${NC}"
echo "=================================================="

# 1. Check .env exists and is configured
if [ ! -f ".env" ]; then
  echo -e "${YELLOW}⚠️  No .env found. Running setup_env.py to create one...${NC}"
  python3 setup_env.py
  echo -e "${RED}📝 Please edit .env and fill in APP_URL, DB credentials, SMTP (the office location is set per company in Settings).${NC}"
  echo "   Then re-run: bash deploy.sh"
  exit 1
fi

if grep -q "your_secret_key_here\|your_encryption_key_here" .env; then
  echo -e "${RED}❌ .env still has placeholder keys. Run: python3 setup_env.py${NC}"
  exit 1
fi

# 2. Pull latest code
echo -e "\n${GREEN}📦 Pulling latest code...${NC}"
git pull origin main

# 3. Build and restart containers
echo -e "\n${GREEN}🐳 Building Docker images...${NC}"
docker compose build --no-cache

echo -e "\n${GREEN}🔄 Restarting services...${NC}"
docker compose down
docker compose up -d

# 4. Wait for health check
echo -e "\n${GREEN}⏳ Waiting for health check...${NC}"
MAX=30; COUNT=0
until curl -sf http://localhost:5000/healthz | grep -q '"status":"ok"'; do
  COUNT=$((COUNT+1))
  [ $COUNT -ge $MAX ] && echo -e "${RED}❌ Health check failed after ${MAX}s${NC}" && docker compose logs app | tail -20 && exit 1
  echo -n "."
  sleep 2
done

echo -e "\n${GREEN}✅ Deployment successful!${NC}"
echo "   App is live at: $(grep APP_URL .env | cut -d= -f2)"
echo "   Health: http://localhost:5000/healthz"
