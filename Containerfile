# ── Stage 1: builder ─────────────────────────────────────────────────────────
# Compilers/headers needed only to build dlib (face_recognition) and psycopg2
# extension wheels. None of this belongs in the image that actually runs.
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ make \
    libboost-all-dev \
    libopenblas-dev liblapack-dev \
    libx11-dev \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt requirements.lock ./
# Default = requirements.txt (floors; resolves to the newest releases on the
# day of the build -- the behaviour before the lockfile existed).
# --build-arg REQUIREMENTS_FILE=requirements.lock = the exact, hash-verified
# set from scripts/relock.sh: reproducible builds, and pip aborts if any
# downloaded file doesn't match its recorded hash (supply-chain tamper check).
ARG REQUIREMENTS_FILE=requirements.txt
# --prefix isolates the installed tree so stage 2 can copy just this, not
# pip's cache or the compiler toolchain above.
RUN if [ "$REQUIREMENTS_FILE" = "requirements.lock" ]; then \
        pip install --no-cache-dir --prefix=/install --require-hashes -r requirements.lock; \
    else \
        pip install --no-cache-dir --prefix=/install -r "$REQUIREMENTS_FILE"; \
    fi

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
# Same base so glibc/ABI matches the builder; no compilers, no -dev headers,
# no build tools — only the shared libs the compiled wheels dlopen at import
# time (verify with `ldd` against your built .so files if this list drifts).
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0-pthread liblapack3 \
    libx11-6 \
    libgl1 libglib2.0-0 \
    libssl3 \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid appuser --no-create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /install /usr/local

# The base image's own preinstalled pip/setuptools toolchain (ensurepip),
# not anything from requirements.txt -- entrypoint.sh never invokes pip at
# runtime, so this exists here only as unused attack surface. A plain
# `pip install --upgrade` (tried first) reported the pre-upgrade version
# as 0.46.3 and "succeeded", yet Trivy's filesystem scan still found a
# 0.45.1 dist-info sitting on disk -- an orphaned copy from the base
# image's own site-packages that COPY --from=builder layered another
# version on top of without removing, which pip's own bookkeeping never
# sees but Trivy's raw directory scan does. Delete any leftover
# wheel*/jaraco.context* dist-info dirs first so only the freshly
# installed one remains (CVE-2026-24049, CVE-2026-23949).
#
# Then pip itself goes: it is only needed for that upgrade. pip vendors its
# own msgpack, urllib3 and setuptools (pkg_resources) and describes them in
# an SBOM inside its dist-info, which Trivy reads -- the base image's pip
# 26.2.2 carried msgpack 1.1.2 (GHSA-6v7p-g79w-8964), urllib3 2.7.0
# (CVE-2026-97687, CVE-2026-97689) and setuptools 70.3.0 (CVE-2025-47273).
# Nothing runs pip after the build (entrypoint.sh, the app and its
# pkg_resources shim in utils/face_utils.py don't). The installed
# setuptools (from requirements.txt, not pip's vendored copy) is clean
# and stays.
RUN (find / -xdev -type d \( -iname 'wheel-*' -o -iname 'jaraco.context-*' \) -exec rm -rf {} + || true) && \
    pip install --no-cache-dir --upgrade wheel jaraco.context && \
    python -m pip uninstall -y pip && \
    (find / -xdev -type d -name 'pip-*.dist-info' -exec rm -rf {} + || true) && \
    rm -rf /usr/local/lib/python3.13/site-packages/pip /root/.cache/pip

WORKDIR /app
COPY --chown=appuser:appuser . .

# Runtime directories that are gitignored — compose.yaml mounts named
# volumes over these, so ownership here only matters for `podman run`
# without compose (defense in depth, not the primary permission story).
RUN mkdir -p static/qrcodes static/employee_docs dataset \
    && chown -R appuser:appuser static dataset

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Baked-in non-root, on top of (not instead of) compose.yaml's explicit
# `user: "1001:1001"` — that line pins the UID compose's volume mounts are
# built around; this USER line is what protects a bare `podman run` (no
# compose, no explicit --user flag) from silently running as root.
USER appuser

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
