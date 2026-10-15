"""SEC-01: an image built from a working copy can't pick up private data.

The Containerfile does `COPY . .`; .containerignore didn't exclude employee
uploads, DB dumps or data directories, virtualenvs, or .env files below the
repo root (tests/.env holds real SMTP credentials locally). No database
needed (runnable with --noconftest).
"""
import fnmatch
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── SEC-01: a build from a working copy can't pick up private data ───────────

def _ignored(path, patterns):
    """Simplified .containerignore/.dockerignore matching: last matching
    pattern wins, `!` re-includes, `**/` matches at any depth, and a pattern
    matching a directory excludes everything under it."""
    parts = path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    result = False
    for raw in patterns:
        negate = raw.startswith("!")
        pat = raw[1:] if negate else raw
        pat = pat.rstrip("/")
        if pat.startswith("**/"):
            tail = pat[3:]
            hit = any(fnmatch.fnmatch("/".join(parts[i:j]), tail)
                      for i in range(len(parts)) for j in range(i + 1, len(parts) + 1))
        else:
            hit = any(fnmatch.fnmatch(c, pat) for c in candidates)
        if hit:
            result = not negate
    return result


@pytest.fixture(scope="module")
def ignore_patterns():
    lines = (ROOT / ".containerignore").read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


@pytest.mark.parametrize("path", [
    ".env", "tests/.env", "mobile/.env", "observability/log-server/.env",
    "private_uploads/abc/aadhaar.pdf", "backups/att_2026-10-01.sql.gz", "pg_data/base/1/123",
    ".venv/Lib/site.py", ".venv_test/Lib/site.py", "venv/bin/python",
    "instance/attendance.db", "qa_audit/baseline/coverage.json", "dataset/E1/face.jpg",
    ".pytest_cache/v/cache/lastfailed", "htmlcov/index.html", ".coverage", "db_dump.sql",
    "cert.pem", "key.pem",
])
def test_private_paths_are_excluded_from_the_image(path, ignore_patterns):
    assert _ignored(path, ignore_patterns), path


@pytest.mark.parametrize("path", ["app.py", "wsgi.py", "blueprints/core.py", "templates/admin.html",
                                  ".env.example", "requirements.txt", "migrations/master.py"])
def test_application_files_are_still_included(path, ignore_patterns):
    assert not _ignored(path, ignore_patterns), path
