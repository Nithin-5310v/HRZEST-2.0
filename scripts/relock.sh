#!/usr/bin/env bash
# Regenerate requirements.lock -- the hash-pinned, reproducible dependency set
# for the production image (Linux, Python 3.13).
#
#   bash scripts/relock.sh            # keep existing pins where requirements.txt still allows them
#   bash scripts/relock.sh --upgrade  # move every pin to the newest release allowed
#
# requirements.txt only has `>=` floors, so without a lock every image build
# resolves to whatever is newest *that day* -- two builds of the same commit can
# differ, and a freshly published (or hijacked) release flows straight into
# production. The lock pins exact versions AND every file hash; pip refuses
# anything that doesn't match (`--require-hashes`).
#
# Needs `uv` (pip install uv). It resolves for the production target no matter
# what OS/Python you run it on.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

UPGRADE=()
[ "${1:-}" = "--upgrade" ] && UPGRADE=(--upgrade)

uv pip compile requirements.txt \
  --universal --python-version 3.13 --generate-hashes \
  "${UPGRADE[@]}" -o requirements.lock

echo "wrote requirements.lock ($(grep -c '^[a-z]' requirements.lock) packages)."
echo "Build with it:  podman build --build-arg REQUIREMENTS_FILE=requirements.lock -t hrzest ."
