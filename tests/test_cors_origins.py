"""CORS on /api/* never allows arbitrary origins outside development.

ZAP 10098 (Cross-Domain Misconfiguration) on https://hrzest.com/api/:
production ran with ALLOWED_ORIGINS=*, so flask-cors sent
Access-Control-Allow-Origin: * (and echoed back any Origin it was given).
extensions._cors_origins now drops wildcards outside development.
"""
import pytest
from flask import Flask
from flask_cors import CORS

from extensions import _cors_origins


@pytest.mark.parametrize("env", ["production", "staging", "test"])
def test_wildcard_is_ignored_outside_development(env):
    assert _cors_origins("*", env) == []


def test_only_explicit_http_origins_survive():
    assert _cors_origins(
        ["https://hrzest.com", "*", "https://*.example.com", "null", "http://localhost:3000"],
        "production",
    ) == ["https://hrzest.com", "http://localhost:3000"]


def test_development_keeps_its_configuration():
    assert _cors_origins("*", "development") == "*"


def _acao(origins, origin):
    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": origins}})
    app.add_url_rule("/api/", "api_root", lambda: "ok")
    headers = {"Origin": origin} if origin else {}
    return app.test_client().get("/api/", headers=headers).headers.get("Access-Control-Allow-Origin")


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_production_wildcard_sends_no_allow_origin(origin):
    assert _acao(_cors_origins("*", "production"), origin) is None


def test_listed_origin_still_allowed_and_others_refused():
    origins = _cors_origins(["https://hrzest.com"], "production")
    assert _acao(origins, "https://hrzest.com") == "https://hrzest.com"
    assert _acao(origins, "https://evil.example") is None
