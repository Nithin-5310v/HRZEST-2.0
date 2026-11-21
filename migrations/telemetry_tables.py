# -*- coding: utf-8 -*-
"""att_master tables behind request telemetry (utils/http_telemetry.py) and
server / database health (utils/health_telemetry.py).

  http_minute_stats      per minute x route pattern x method x status x company:
                         request count, total and slowest duration, and a
                         response-time histogram (bucket bounds in
                         utils/http_telemetry.BUCKETS_MS). Every worker adds its
                         own counts to the same row, so the totals are exact.
  http_telemetry_flushes one row per worker process: when it last wrote. Tells a
                         quiet site apart from a collector that stopped.
  health_snapshots       one row per source (host, postgres, app) per minute: the
                         readings as JSON, or ok = false and a generic reason.

Platform-wide like the SOC tables, never per company: a company admin has no
business reading other companies' traffic.
"""

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS http_minute_stats (
        minute     TIMESTAMPTZ  NOT NULL,
        endpoint   VARCHAR(160) NOT NULL,
        method     VARCHAR(8)   NOT NULL,
        status     SMALLINT     NOT NULL,
        tenant     VARCHAR(63)  NOT NULL DEFAULT '',
        hits       INTEGER      NOT NULL,
        total_ms   BIGINT       NOT NULL,
        max_ms     INTEGER      NOT NULL,
        buckets    INTEGER[]    NOT NULL,
        PRIMARY KEY (minute, endpoint, method, status, tenant)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_http_minute_stats_status ON http_minute_stats (status, minute)",
    """
    CREATE TABLE IF NOT EXISTS http_telemetry_flushes (
        host       VARCHAR(64)  NOT NULL,
        pid        INTEGER      NOT NULL,
        flushed_at TIMESTAMPTZ  NOT NULL,
        PRIMARY KEY (host, pid)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS health_snapshots (
        id        BIGSERIAL    PRIMARY KEY,
        taken_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        host      VARCHAR(64)  NOT NULL,
        source    VARCHAR(20)  NOT NULL CHECK (source IN ('host', 'postgres', 'app')),
        ok        BOOLEAN      NOT NULL,
        metrics   JSONB        NOT NULL DEFAULT '{}'::jsonb,
        error     VARCHAR(300)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_health_snapshots_source ON health_snapshots (source, taken_at DESC)",
]


def ensure_telemetry_tables(cursor):
    """Idempotent. `cursor` must be on the att_master search_path."""
    for stmt in STATEMENTS:
        cursor.execute(stmt)
