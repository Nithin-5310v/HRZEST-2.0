#!/usr/bin/env python3
"""
setup_env.py — One-time local environment setup helper.

Copies .env.example → .env and fills in:
  - SECRET_KEY     (cryptographically random 32-byte hex)
  - ENCRYPTION_KEY (Fernet key for PII encryption at rest)

Run once before first launch:
  python setup_env.py
"""
import os
import secrets
import sys

try:
    from cryptography.fernet import Fernet
except ImportError:
    print("❌ cryptography package not installed. Run: pip install cryptography")
    sys.exit(1)

ENV_EXAMPLE = ".env.example"
ENV_FILE = ".env"

if not os.path.exists(ENV_EXAMPLE):
    print(f"❌ {ENV_EXAMPLE} not found — are you in the project root?")
    sys.exit(1)

if os.path.exists(ENV_FILE):
    ans = input(f"⚠️  {ENV_FILE} already exists. Overwrite? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted — existing .env left untouched.")
        sys.exit(0)

content = open(ENV_EXAMPLE).read()

# Generate real keys
new_secret = secrets.token_hex(32)
new_enc = Fernet.generate_key().decode()

content = content.replace("your_secret_key_here",   new_secret)
content = content.replace("your_encryption_key_here", new_enc)

with open(ENV_FILE, "w") as f:
    f.write(content)

print("✅ .env created with auto-generated SECRET_KEY and ENCRYPTION_KEY")
print()
print("📝 Still required — edit .env and fill in:")
print("   APP_URL=https://your-domain.com")
print("   DB_HOST / DB_PASS / DB_USER (if using PostgreSQL)")
print("   SMTP_* variables (if email features are needed)")
print()
print("Then run:  docker compose up --build -d")
