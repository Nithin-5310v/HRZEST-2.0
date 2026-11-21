"""The host and edge firewall configuration in the repository stays in step
with the app it protects (docs/FIREWALL.md). These run in CI without nginx,
ModSecurity, fail2ban or AWS: they catch drift -- a new sign-in route the
rate limits don't cover, a photo field the WAF would refuse, an audit log
that starts recording cookies -- before it reaches the server.

The configurations themselves were exercised on Ubuntu 24.04 (nginx 1.24,
libmodsecurity 3.0.12, OWASP CRS 4.30.0, fail2ban 1.0.2, ufw 0.36) and with
`terraform validate` (AWS provider 6.x); see the PR description.
"""
import importlib.util
import pathlib
import re

import pytest

from extensions import app as flask_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
HH = ROOT / "scripts" / "host-hardening"
MODSEC = HH / "modsecurity"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _text(path):
    return path.read_bytes().decode()


def _login_paths():
    """Every route the app answers a sign-in POST on."""
    out = set()
    for rule in flask_app.url_map.iter_rules():
        r = rule.rule
        if "POST" in (rule.methods or ()) and re.search(r"(?:^|/)(?:[a-z_-]*login|signin)(?:/verify)?$", r) \
                and "<" not in r and "wp-login" not in r:
            out.add(r)
    return out


def _nginx_login_regex():
    m = re.search(r"location ~ (\^/\(\?:\[a-z0-9\]\[a-z0-9-\]\*/\)\?\(\?:[^ ]*login[^ ]*\)\$) \{", _text(HH / "nginx" / "hrzest-site.conf"))
    assert m, "login location not found in hrzest-site.conf"
    return re.compile(m.group(1))


def _terraform_regexes(local_name):
    body = _text(ROOT / "infra" / "terraform" / "edge" / "main.tf")
    # The closing bracket on its own line: the regexes themselves contain "]".
    block = re.search(local_name + r" = \[\n(.*?)\n\s*\]", body, re.S).group(1)
    return [re.compile(s.replace("\\\\", "\\")) for s in re.findall(r'"([^"]+)"', block)]


class TestSignInCoverage:
    def test_the_app_has_the_sign_in_routes_we_expect(self):
        paths = _login_paths()
        assert {"/login", "/admin_login", "/api/login", "/api/employee/login", "/socperson/login"} <= paths

    @pytest.mark.parametrize("prefix", ["", "/acme"])
    def test_every_sign_in_route_is_rate_limited_by_nginx_and_the_edge(self, prefix):
        nginx = _nginx_login_regex()
        edge = _terraform_regexes("login_path_regexes")
        for path in _login_paths():
            if path.startswith(("/super_admin", "/socperson")) and prefix:
                continue        # platform consoles have no company prefix
            full = prefix + path
            assert nginx.match(full), f"nginx login location misses {full}"
            assert any(r.match(full) for r in edge), f"edge rate-login misses {full}"

    def test_edge_patterns_fit_aws_limits(self):
        for name in ("login_path_regexes", "payroll_path_regexes"):
            for r in _terraform_regexes(name):
                assert len(r.pattern) <= 200, f"AWS WAF caps a regex at 200 characters: {r.pattern}"
                assert "(" not in r.pattern.replace("(?:", ""), "AWS WAF rejects capturing groups"

    def test_payroll_routes_are_covered(self):
        edge = _terraform_regexes("payroll_path_regexes")
        payroll = [r.rule for r in flask_app.url_map.iter_rules() if "payroll" in r.rule and "<" not in r.rule]
        assert payroll
        for path in payroll:
            assert any(r.match(path) for r in edge), path


class TestModSecurityExclusions:
    def test_photo_block_is_what_the_generator_writes(self):
        gen = _load(MODSEC / "gen_photo_exclusions.py", "gen_photo_exclusions")
        current = _text(MODSEC / "hrzest-before-crs.conf")
        assert gen.render(current) == current, "run scripts/host-hardening/modsecurity/gen_photo_exclusions.py"

    def test_every_liveness_frame_the_app_reads_is_covered(self):
        from utils import liveness
        gen = _load(MODSEC / "gen_photo_exclusions.py", "gen_photo_exclusions")
        assert gen.FRAMES == liveness.MAX_FRAMES

    def test_photo_fields_are_the_ones_the_app_reads(self):
        gen = _load(MODSEC / "gen_photo_exclusions.py", "gen_photo_exclusions")
        src = "".join(_text(p) for p in (ROOT / "blueprints").rglob("*.py"))
        for field in gen.FIELDS:
            assert re.search(r'\.get\(\s*"' + field + '"', src), f"no route reads {field!r} any more"

    def test_rule_ids_are_unique_and_in_this_apps_range(self):
        ids = []
        for f in ("modsecurity.conf", "hrzest-before-crs.conf", "hrzest-after-crs.conf"):
            ids += re.findall(r'"id:(\d+)', _text(MODSEC / f))
        assert len(ids) == len(set(ids))
        assert all(i.startswith(("2000", "1001", "1002")) for i in ids)

    def test_credential_fields_are_never_inspected(self):
        before = _text(MODSEC / "hrzest-before-crs.conf")
        for field in ("password", "new_password", "confirm_password", "current_password", "admin_password", "pin",
                      "otp_code", "totp_code"):
            assert f"OWASP_CRS;ARGS:{field}," in before or f"OWASP_CRS;ARGS:{field}\"" in before, field
            assert f"OWASP_CRS;ARGS:json.{field}" in before, field

    def test_the_audit_log_never_records_headers_or_bodies(self):
        conf = _text(MODSEC / "modsecurity.conf")
        parts = re.search(r"^SecAuditLogParts (\S+)$", conf, re.M).group(1)
        assert not set(parts) & set("BCIEFJK"), parts
        assert "SecResponseBodyAccess Off" in conf
        assert re.search(r"^SecRuleEngine __ENGINE__$", conf, re.M)

    def test_crs_pin(self):
        script = _text(HH / "install_nginx_waf.sh")
        version = re.search(r"^CRS_VERSION=(\S+)$", script, re.M).group(1)
        assert re.fullmatch(r"[0-9a-f]{64}", re.search(r"^CRS_SHA256=(\S+)$", script, re.M).group(1))
        assert f"ver:'OWASP_CRS/{version}'" in _text(MODSEC / "crs-setup.conf")


class TestNginx:
    def test_no_name_collides_with_an_existing_config(self):
        http = _text(HH / "nginx" / "hrzest-waf-http.conf")
        for zone in re.findall(r"zone=(\w+):", http):
            assert zone.startswith("hzwaf_"), zone
        # Ubuntu's nginx.conf sets these in http{}; a second one there fails to load.
        for directive in ("keepalive_timeout", "ssl_protocols", "ssl_prefer_server_ciphers", "server_tokens",
                          "limit_req_status", "sendfile"):
            assert not re.search(rf"^\s*{directive}\b", http, re.M), directive

    def test_the_auth_filter_matches_the_access_log_format(self):
        http = _text(HH / "nginx" / "hrzest-waf-http.conf")
        fmt = "".join(re.findall(r"'([^']*)'", re.search(r"log_format hzwaf_json escape=json(.*?);", http, re.S).group(1)))
        values = {"time_iso8601": "2026-11-21T06:21:24+00:00", "remote_addr": "203.0.113.7", "request_method": "POST",
                  "request_uri": "/acme/api/login", "status": "401", "body_bytes_sent": "40", "request_time": "0.012",
                  "host": "hrzest.com", "http_user_agent": "okhttp/4.12", "http_referer": "", "request_id": "abc",
                  "upstream_status": "401"}
        line = re.sub(r"\$(\w+)", lambda m: values[m.group(1)], fmt)
        failregex = re.search(r"^failregex = (.*)$", _text(HH / "fail2ban" / "filter-hrzest-nginx-auth.conf"), re.M).group(1)
        rx = re.compile(failregex.replace("<HOST>", r"(?P<host>[0-9a-fA-F.:]+)"))
        m = rx.search(line)
        assert m and m.group("host") == "203.0.113.7"
        assert not rx.search(line.replace('"status":401', '"status":200'))


class TestScripts:
    SCRIPTS = [ROOT / "scripts" / "harden_host_firewall.sh", HH / "install_nginx_waf.sh",
               HH / "install_fail2ban_web.sh", ROOT / "scripts" / "firewall_agent" / "install_firewall_agent.sh"]

    @pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
    def test_strict_mode_and_lf_endings(self, path):
        raw = path.read_bytes()
        assert b"\r\n" not in raw
        text = raw.decode()
        assert text.startswith("#!/usr/bin/env bash\n")
        assert "\nset -euo pipefail\n" in text
        assert "--dry-run" in text

    def test_the_bare_metal_script_never_carries_the_container_nat_redirects(self):
        text = _text(ROOT / "scripts" / "harden_host_firewall.sh")
        assert "REDIRECT --to-port" not in text and "8443" in text      # it refuses them, it never adds them

    def test_agent_layers_are_ones_the_app_accepts(self):
        from utils import firewall_events
        agent = _text(ROOT / "scripts" / "firewall_agent" / "hrzest_fw_agent.py")
        layers = set(re.findall(r'"layer": "(\w+)"', agent))
        assert layers and layers <= set(firewall_events.REPORTER_LAYERS)
