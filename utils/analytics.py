# -*- coding: utf-8 -*-
"""Lightweight traffic tracking for the public marketing pages (landing
page, get-started, create_org) -- one counter row per (path, day), no
cookies/fingerprinting/IP storage. Feeds the Platform Admin dashboard's
traffic stat cards (blueprints/platform_admin.py).
"""
import datetime
from database import get_master_db
from extensions import app_log
# The platform's calendar day (IST), not the database's CURRENT_DATE (UTC):
# a view at 01:00 IST belongs to today's count, not yesterday's.
from utils.helpers import platform_today


def track_page_view(path: str):
    """Best-effort increment of today's view counter for `path`. Never
    raises -- a tracking failure must never break the page it's tracking."""
    try:
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO page_views (path, view_date, count) VALUES (%s, %s, 1) "
            "ON CONFLICT (path, view_date) DO UPDATE SET count = page_views.count + 1",
            (path, platform_today())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.warning("track_page_view failed for %r: %s", path, exc)


def get_traffic_stats():
    """Returns a dict of simple traffic numbers for the Platform Admin
    dashboard: today's total, the last 7 days' total, and a per-day trend
    list (oldest first) for a tiny sparkline. Fails to all-zero rather than
    raising, since a broken stats widget shouldn't take the whole dashboard
    down with it."""
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        today = platform_today()
        week_ago = today - datetime.timedelta(days=6)

        cur.execute("SELECT COALESCE(SUM(count), 0) FROM page_views WHERE view_date = %s", (today,))
        today_total = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(count), 0) FROM page_views WHERE view_date >= %s", (week_ago,))
        week_total = cur.fetchone()[0]

        cur.execute(
            "SELECT view_date, SUM(count) FROM page_views WHERE view_date >= %s "
            "GROUP BY view_date ORDER BY view_date", (week_ago,)
        )
        by_day = {row[0]: row[1] for row in cur.fetchall()}
        trend = [by_day.get(week_ago + datetime.timedelta(days=i), 0) for i in range(7)]

        cur.execute(
            "SELECT path, SUM(count) c FROM page_views WHERE view_date >= %s "
            "GROUP BY path ORDER BY c DESC LIMIT 5", (week_ago,)
        )
        top_pages = cur.fetchall()

        cur.close()
        conn.close()
        return {
            "today_total": today_total, "week_total": week_total,
            "trend": trend, "top_pages": top_pages,
        }
    except Exception as exc:
        app_log.warning("get_traffic_stats failed: %s", exc)
        return {"today_total": 0, "week_total": 0, "trend": [0] * 7, "top_pages": []}
