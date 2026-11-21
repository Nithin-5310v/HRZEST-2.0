"""New-password rules: utils/auth.py's check_new_password() and the Have I
Been Pwned lookup in utils/pwned_passwords.py. The HTTP call is always
mocked -- the suite never reaches the real API (conftest.py also sets
PASSWORD_BREACH_CHECK=false for every other test)."""
import hashlib
import io
import urllib.error

import pytest

import utils.pwned_passwords as pwned
from utils.auth import PASSWORD_ERRORS, check_new_password, validate_new_password


def _sha1(password):
    return hashlib.sha1(password.encode("utf-8")).hexdigest().upper()


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def hibp(monkeypatch):
    """Turns the check on and answers range queries like the real API:
    every suffix under the requested prefix, plus a zero-count padding row.
    Returns (known, requests): known maps password -> breach count."""
    known, requests = {}, []

    def fake_urlopen(req, timeout=None):
        requests.append(req)
        prefix = req.full_url.rsplit("/", 1)[1]
        lines = [f"{_sha1(pw)[5:]}:{n}" for pw, n in known.items() if _sha1(pw)[:5] == prefix]
        lines.append("0" * 35 + ":0")
        return _FakeResponse("\r\n".join(lines).encode())

    monkeypatch.setenv("PASSWORD_BREACH_CHECK", "true")
    monkeypatch.setattr(pwned.urllib.request, "urlopen", fake_urlopen)
    return known, requests


class TestBreachCheck:
    def test_known_password_is_rejected(self, hibp):
        known, _ = hibp
        known["Summer2024!x"] = 48213
        assert check_new_password("Summer2024!x") == (False, "breached")
        assert validate_new_password("Summer2024!x") == (False, PASSWORD_ERRORS["breached"])

    def test_unknown_password_is_accepted(self, hibp):
        assert check_new_password("kettle-orbit-Planet-81") == (True, None)

    def test_only_a_five_character_hash_prefix_leaves_the_server(self, hibp):
        _, requests = hibp
        check_new_password("kettle-orbit-Planet-81")
        assert len(requests) == 1
        sent = requests[0].full_url.rsplit("/", 1)[1]
        assert sent == _sha1("kettle-orbit-Planet-81")[:5]
        assert "kettle" not in requests[0].full_url
        assert requests[0].get_header("Add-padding") == "true"

    def test_padding_rows_are_not_matches(self, hibp):
        known, _ = hibp
        known["Padded-Only-Entry-7"] = 0
        assert check_new_password("Padded-Only-Entry-7") == (True, None)

    def test_api_outage_fails_open(self, monkeypatch):
        def down(req, timeout=None):
            raise urllib.error.URLError("no route to host")
        monkeypatch.setenv("PASSWORD_BREACH_CHECK", "true")
        monkeypatch.setattr(pwned.urllib.request, "urlopen", down)
        assert pwned.times_pwned("kettle-orbit-Planet-81") is None
        assert check_new_password("kettle-orbit-Planet-81") == (True, None)

    def test_disabled_makes_no_request(self, hibp, monkeypatch):
        known, requests = hibp
        known["Summer2024!x"] = 48213
        monkeypatch.setenv("PASSWORD_BREACH_CHECK", "false")
        assert check_new_password("Summer2024!x") == (True, None)
        assert requests == []


class TestSocConsolePasswords:
    """The SOC console keeps its own rules (utils/soc_auth.password_problem:
    12+ characters, not the email) and shares the leaked-password check."""

    def test_breached_password_is_refused(self, hibp):
        from utils.soc_auth import password_problem
        known, _ = hibp
        known["Correct-Horse-Battery-1"] = 9120
        assert "data breach" in password_problem("Correct-Horse-Battery-1", "analyst@example.com")

    def test_unknown_long_password_is_fine(self, hibp):
        from utils.soc_auth import password_problem
        assert password_problem("kettle-orbit-Planet-81", "analyst@example.com") is None


class TestLocalRules:
    def test_too_short(self):
        assert check_new_password("Ab1!xyz") == (False, "short")
        assert check_new_password("") == (False, "short")

    def test_minimum_is_twelve_characters(self):
        """Security review 2026-11-20: 12, not 8 (NIST SP 800-63B)."""
        assert check_new_password("Orbit-77kett") == (True, None)        # 12
        assert check_new_password("Orbit-77ket") == (False, "short")     # 11

    def test_absurdly_long_password_is_refused(self):
        assert check_new_password("k" * 257) == (False, "long")

    def test_company_signup_uses_the_shared_rule(self):
        from blueprints.org.validation import _validate_signup_account
        msg = _validate_signup_account("Policy Test Co", "policy@example.com", "9876543210", "Elevenchars")
        assert msg and "12 characters" in msg

    @pytest.mark.parametrize("password, context", [
        ("RaviKumar#2025", ("ravi.kumar",)),            # username, punctuation ignored
        ("priya!Secure99", ("priya@acme.in",)),         # email local part
        ("Acme@Strong991", ("Acme Technologies Pvt Ltd",)),  # one word of the company name
        ("tst001-Winter!", ("TST001",)),                # employee id
        ("MyHRzest#991x", ()),                          # always: the product name
    ])
    def test_personal_words_are_rejected(self, password, context):
        assert check_new_password(password, context) == (False, "personal")

    def test_short_or_generic_context_words_are_ignored(self):
        # "ab", "ltd", "x" are under 4 characters; an email's domain never counts.
        assert check_new_password("ltd-ab-gmail-Orbit77", ("ab", "Ltd", "x@gmail.com")) == (True, None)

    def test_empty_context_values_are_ignored(self):
        assert check_new_password("kettle-orbit-Planet-81", (None, "", "   ")) == (True, None)


class TestRoutes:
    def _emp_session(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
            sess["employee_name"] = seed_employee["name"]

    def test_change_password_reports_a_breached_password(self, client, seed_employee, hibp):
        known, _ = hibp
        known["Summer2024!x"] = 48213
        self._emp_session(client, seed_employee)
        rv = client.post("/change_password", data={
            "current_password": seed_employee["password"],
            "new_password": "Summer2024!x",
            "confirm_password": "Summer2024!x",
        })
        assert rv.status_code == 302
        assert "pwd_error=breached" in rv.headers["Location"]

    def test_change_password_rejects_the_employee_id(self, client, seed_employee):
        self._emp_session(client, seed_employee)
        new = f"{seed_employee['employee_id']}-Winter!"
        rv = client.post("/change_password", data={
            "current_password": seed_employee["password"],
            "new_password": new,
            "confirm_password": new,
        })
        assert rv.status_code == 302
        assert "pwd_error=personal" in rv.headers["Location"]
