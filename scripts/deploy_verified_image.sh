#!/usr/bin/env bash
# Deploy the CI-built image by digest, after verifying its signature.
#
#   APP_IMAGE=ghcr.io/<owner>/<repo>/app@sha256:<digest> bash scripts/deploy_verified_image.sh
#
# Fails closed: a tag-addressed image, a missing cosign, or a signature that
# was not produced by this repository's deploy workflow on main stops the
# deploy before anything is pulled or started.
set -euo pipefail

: "${APP_IMAGE:?APP_IMAGE must be registry/name@sha256:<digest>}"

case "$APP_IMAGE" in
  *@sha256:????????????????????????????????????????????????????????????????) ;;
  *) echo "refusing to deploy: APP_IMAGE must be addressed by digest (…@sha256:<64 hex>), got: $APP_IMAGE" >&2; exit 1 ;;
esac

# Who is allowed to have signed it: this repo's deploy workflow on main,
# via GitHub's OIDC issuer. Override COSIGN_IDENTITY_REGEXP for a fork.
: "${COSIGN_IDENTITY_REGEXP:=^https://github\.com/[^/]+/[^/]+/\.github/workflows/deploy\.yml@refs/heads/main$}"
: "${COSIGN_OIDC_ISSUER:=https://token.actions.githubusercontent.com}"

if ! command -v cosign >/dev/null 2>&1; then
  if [ "${ALLOW_UNVERIFIED:-0}" = "1" ]; then
    echo "WARNING: cosign not installed and ALLOW_UNVERIFIED=1 -- deploying WITHOUT signature verification" >&2
  else
    echo "refusing to deploy: cosign is not installed (install it, or set ALLOW_UNVERIFIED=1 to override knowingly)" >&2
    exit 1
  fi
else
  echo "verifying signature of $APP_IMAGE"
  cosign verify \
    --certificate-identity-regexp "$COSIGN_IDENTITY_REGEXP" \
    --certificate-oidc-issuer "$COSIGN_OIDC_ISSUER" \
    "$APP_IMAGE" >/dev/null
  echo "signature OK"
fi

podman pull "$APP_IMAGE"
export APP_IMAGE

COMPOSE="podman-compose -f compose.yaml -f compose.prod.yaml -f compose.image.yaml"
# clamav must be named explicitly (--no-deps): without it running, uploads
# fail closed in production. honeypot runs the same image.
$COMPOSE up -d --no-deps --no-build app nginx certbot clamav honeypot
podman image prune -f >/dev/null
echo "deployed $APP_IMAGE"
