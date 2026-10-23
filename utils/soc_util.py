# -*- coding: utf-8 -*-
"""Small pure helpers shared by the SOC console modules."""
import csv
import datetime
import io
import re

# A spreadsheet treats a cell starting with one of these as a formula. Event
# data (paths, identifiers, user agents) is attacker-controlled, and analysts
# open exports in Excel, so every exported text cell is neutralised.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    if text.startswith(_FORMULA_LEAD):
        return "'" + text
    return text


def to_csv(header, rows):
    """-> CSV text. `rows` is an iterable of sequences aligned with `header`."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([csv_safe(h) for h in header])
    for row in rows:
        w.writerow([csv_safe(c) for c in row])
    return buf.getvalue()


def clamp_int(value, default, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def like_escape(text):
    """Escape LIKE/ILIKE metacharacters so analyst input is matched literally
    (use with `ESCAPE '\\'`)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_IP_CHARS = re.compile(r"^[0-9A-Fa-f:.]{2,45}$")


def looks_like_ip(text):
    if not text or not _IP_CHARS.match(text):
        return False
    import ipaddress
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def iso(dt):
    if isinstance(dt, (datetime.datetime, datetime.date)):
        return dt.isoformat(sep=" ", timespec="seconds") if isinstance(dt, datetime.datetime) else dt.isoformat()
    return dt


def row_dict(columns, row):
    return {c: iso(v) for c, v in zip(columns, row)}


def percent(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0
