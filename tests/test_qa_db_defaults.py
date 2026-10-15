"""CONFIG-04: the DB connect timeout defaults to .env.example's 10 s.

database.py fell back to 1 s when DB_CONNECT_TIMEOUT was unset -- short
enough for spurious connection failures under load or across a network,
which since BUG-02 surface as 503s. database.py loads .env on import, so
the code default is checked statically (runnable with --noconftest).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_connect_timeout_default_matches_env_example():
    code = (ROOT / "database.py").read_text(encoding="utf-8")
    default = re.search(r'os\.environ\.get\("DB_CONNECT_TIMEOUT",\s*"(\d+)"\)', code).group(1)
    example = re.search(r"^DB_CONNECT_TIMEOUT=(\d+)", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M).group(1)
    assert default == example == "10"
