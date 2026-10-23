# -*- coding: utf-8 -*-
"""Scheduler entry point for personal-data retention (utils/privacy.py),
iterating every tenant schema the way utils/detections.py does."""


def run_retention_purge():
    from extensions import app, app_log
    from database import get_db_connection, get_master_db, get_tenant_db
    from utils import privacy

    results = {}

    def _purge(open_conn, label):
        try:
            db = open_conn()
            cur = db.cursor()
            try:
                results[label] = privacy.purge_expired(cur, app.config["UPLOAD_FOLDER"])
            finally:
                cur.close()
                db.close()
        except Exception:
            app_log.warning("retention purge failed for %s", label, exc_info=True)

    _purge(get_db_connection, "public")
    try:
        mdb = get_master_db()
        mcur = mdb.cursor()
        mcur.execute("SELECT db_name FROM tenants")
        schemas = [r[0] for r in mcur.fetchall()]
        mcur.close()
        mdb.close()
    except Exception:
        app_log.warning("retention purge: could not list tenants", exc_info=True)
        schemas = []
    for schema in schemas:
        _purge(lambda s=schema: get_tenant_db(s), schema)
    app_log.info("privacy retention purge complete: %s", results)
    return results
