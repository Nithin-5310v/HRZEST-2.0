"""Create the development copy's PostgreSQL database if it doesn't exist
yet. The app builds its tables itself on startup.

    python ci/jenkins/ensure_database.py C:\\HRZEST-dev\\app\\.env
"""
import sys

import psycopg2
from dotenv import dotenv_values
from psycopg2 import sql

cfg = dotenv_values(sys.argv[1])
name = cfg.get("DB_NAME")
if not name or "dev" not in name:
    sys.exit(f"refusing: DB_NAME in {sys.argv[1]} must name a development database (contain 'dev')")

conn = psycopg2.connect(
    host=cfg.get("DB_HOST") or "localhost", port=cfg.get("DB_PORT") or 5432,
    user=cfg.get("DB_USER"), password=cfg.get("DB_PASS"), dbname="postgres",
    connect_timeout=10,
)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
    if cur.fetchone():
        print(f"database {name} exists")
    else:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        print(f"created database {name}")
conn.close()
