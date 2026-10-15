"""CONFIG-01: gunicorn reads the worker/thread counts it is given.

compose.lowmem.yaml sets GUNICORN_WORKERS=1 / GUNICORN_THREADS=2 because each
worker loads its own face_recognition/dlib/OpenCV, but gunicorn.conf.py never
read them, so a low-memory host still ran up to 8 workers. No database needed
(runnable with --noconftest).
"""
import pathlib
import runpy

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── CONFIG-01: gunicorn reads the worker/thread counts it's given ────────────

def _gunicorn_conf(monkeypatch, **env):
    for key in ("GUNICORN_WORKERS", "GUNICORN_THREADS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return runpy.run_path(str(ROOT / "gunicorn.conf.py"))


def test_low_memory_profile_worker_and_thread_counts_are_honoured(monkeypatch):
    conf = _gunicorn_conf(monkeypatch, GUNICORN_WORKERS="1", GUNICORN_THREADS="2")
    assert (conf["workers"], conf["threads"]) == (1, 2)


def test_defaults_are_unchanged_without_the_variables(monkeypatch):
    import multiprocessing
    conf = _gunicorn_conf(monkeypatch)
    assert conf["workers"] == min(multiprocessing.cpu_count() * 2 + 1, 8)
    assert conf["threads"] == 4
