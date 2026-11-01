"""/healthz?commit=<sha>: lets deploy tooling (Jenkins) confirm which commit
production runs, answering only yes/no -- the running version is never
printed (blueprints/health.py)."""
import subprocess

import blueprints.health as health


def _head():
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True, cwd=health.os.path.dirname(health.os.path.dirname(health.__file__))).stdout.strip()


def test_reads_the_checked_out_commit():
    health._running_commit.cache_clear()
    assert health._running_commit() == _head()


def test_answers_yes_for_the_running_commit(client):
    head = _head()
    for asked in (head, head[:12], head[:7].upper()):
        body = client.get(f"/healthz?commit={asked}").get_json()
        assert body["status"] == "ok" and body["commit_match"] is True


def test_answers_no_for_another_commit(client):
    body = client.get("/healthz?commit=0000000000000000000000000000000000000000").get_json()
    assert body["commit_match"] is False


def test_never_prints_the_version(client):
    head = _head()
    plain = client.get("/healthz")
    assert plain.get_json() == {"status": "ok", "db": "connected"}
    assert head[:7] not in client.get(f"/healthz?commit={'0' * 40}").get_data(as_text=True)


def test_ignores_anything_that_isnt_a_commit(client):
    for asked in ("", "abc", "not-a-sha!", "g" * 40):
        assert "commit_match" not in client.get(f"/healthz?commit={asked}").get_json()
