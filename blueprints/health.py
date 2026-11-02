# -*- coding: utf-8 -*-
"""Health blueprint -- /healthz and /favicon.ico.

Migrated from app.py (lines 132–151). These routes carry no business logic
and no auth requirements, so they're the cleanest starting point for the
blueprint split.
"""
import functools
import os
import re
from flask import Blueprint, jsonify, request, send_from_directory, current_app
from database import get_db_connection
from extensions import app_log

health_bp = Blueprint("health", __name__)

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


@functools.lru_cache(maxsize=1)
def _running_commit():
    """The git commit this process was started from ('' when it isn't a git
    checkout). The production host runs a checkout of the deployed commit
    and restarts the app on every deploy, so reading it once per process is
    exact. Plain file reads, no git binary: .git may be a directory, or a
    worktree's "gitdir:" file whose branch refs live in its common dir."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gitdir = os.path.join(root, ".git")
    try:
        if os.path.isfile(gitdir):
            with open(gitdir, encoding="utf-8") as f:
                gitdir = os.path.join(root, f.read().split("gitdir:", 1)[1].strip())
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        common = gitdir
        if os.path.isfile(os.path.join(gitdir, "commondir")):
            with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
                common = os.path.join(gitdir, f.read().strip())
        loose = os.path.join(common, *ref.split("/"))
        if os.path.isfile(loose):
            with open(loose, encoding="utf-8") as f:
                return f.read().strip()
        with open(os.path.join(common, "packed-refs"), encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except (OSError, IndexError):
        pass
    return ""


@health_bp.route("/favicon.ico")
def favicon():
    static = current_app.static_folder
    ico = os.path.join(static, "favicon.ico")
    if os.path.exists(ico):
        return send_from_directory(static, "favicon.ico", mimetype="image/x-icon")
    return ("", 204)


@health_bp.route("/healthz")
@health_bp.route("/api/health")
def healthz():
    """Health check endpoint used by Podman, nginx, load balancers, and SecOps monitoring."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        body = {"status": "ok", "db": "connected"}
        # Deploy tooling (Jenkins, ci/jenkins/deploy_prod.ps1) asks whether a
        # given commit is live. Only a yes/no comes back -- the running
        # version itself is never printed.
        asked = (request.args.get("commit") or "").strip().lower()
        if _COMMIT_RE.match(asked):
            body["commit_match"] = _running_commit().startswith(asked)
        return jsonify(body), 200
    except Exception as e:
        app_log.error("Health check DB error: %s", e)
        return jsonify({"status": "error", "db": "unavailable"}), 503
