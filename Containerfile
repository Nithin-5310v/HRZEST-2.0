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
COPY requirements.txt .
# --prefix isolates the installed tree so stage 2 can copy just this, not
# pip's cache or the compiler toolchain above.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

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
RUN (find / -xdev -type d \( -iname 'wheel-*' -o -iname 'jaraco.context-*' \) -exec rm -rf {} + || true) && \
    pip install --no-cache-dir --upgrade wheel jaraco.context

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
