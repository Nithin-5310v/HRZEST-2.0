# -*- coding: utf-8 -*-
"""Internal messaging between the platform operator and a company's own
admin/HR staff -- one shared thread per tenant, stored in att_master's
chat_messages table (see app.py's init_master_db()) since platform admin has
no tenant schema of its own. Company admin and HR both read/write the same
thread for their company; sender_kind records who actually wrote each line.
"""
from database import get_master_db

_MAX_MESSAGE_LEN = 2000


def list_messages(tenant_schema, limit=200):
    """created_at is a plain TIMESTAMP written in the database session's time
    zone; reading it back AT TIME ZONE that same zone gives an offset-carrying
    ISO string, so the browser shows each message's real local time rather
    than guessing which zone a bare timestamp was in. The read_by_* flags drive
    the sent/seen ticks on each side's own messages. Returns the newest
    `limit` messages, oldest first."""
    conn = get_master_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, sender_kind, sender_name, message, created_at, read_by_platform, read_by_company FROM ("
            "SELECT id, sender_kind, sender_name, message, "
            "created_at AT TIME ZONE current_setting('TimeZone') AS created_at, read_by_platform, read_by_company "
            "FROM chat_messages WHERE tenant_schema=%s ORDER BY created_at DESC, id DESC LIMIT %s"
            ") newest ORDER BY created_at ASC, id ASC",
            (tenant_schema, limit),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()
    return [
        {"id": r[0], "sender_kind": r[1], "sender_name": r[2], "message": r[3],
         "created_at": r[4].isoformat() if r[4] else None,
         "read_by_platform": bool(r[5]), "read_by_company": bool(r[6])}
        for r in rows
    ]


def send_message(tenant_schema, sender_kind, sender_name, message):
    message = (message or "").strip()[:_MAX_MESSAGE_LEN]
    if not message:
        return None
    from_platform = sender_kind == "platform_admin"
    conn = get_master_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO chat_messages (tenant_schema, sender_kind, sender_name, message, "
            "read_by_platform, read_by_company) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at",
            (tenant_schema, sender_kind, sender_name, message, 1 if from_platform else 0, 0 if from_platform else 1),
        )
        new_id, created_at = cursor.fetchone()
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    return {"id": new_id, "created_at": created_at.isoformat() if created_at else None}


def mark_read(tenant_schema, reader_side):
    """reader_side is 'platform' or 'company' -- marks every message in this
    thread read from that side, called when that side opens/polls it."""
    conn = get_master_db()
    cursor = conn.cursor()
    try:
        if reader_side == "platform":
            cursor.execute(
                "UPDATE chat_messages SET read_by_platform=1 WHERE tenant_schema=%s AND read_by_platform=0",
                (tenant_schema,),
            )
        else:
            cursor.execute(
                "UPDATE chat_messages SET read_by_company=1 WHERE tenant_schema=%s AND read_by_company=0",
                (tenant_schema,),
            )
        conn.commit()
    finally:
        cursor.close()
        conn.close()
