"""The account-seeding code: scripts/seed_platform_admin.py, the console
session check a reset relies on, and migrations/seed.py's first-start admin."""
import io
import time
from unittest import mock

import pytest

from scripts import seed_platform_admin as seed_script
from utils.auth import check_password_hash

USER = "seed_test_operator"
GOOD_PW = "Wq7#rTz2!kLm9vXp"
OTHER_PW = "Hn4$cVb8@pQs2mYe"


_first_event_id = [0]  # security_events is append-only: read only this test's rows


@pytest.fixture
def clean_operator(db_engine):
    cur = db_engine.cursor()

    def wipe():
        cur.execute("DELETE FROM att_master.platform_admins WHERE username=%s", (USER,))
        cur.execute("DELETE FROM att_master.login_otp_challenges WHERE username=%s", (USER,))
    wipe()
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM public.security_events")
    _first_event_id[0] = cur.fetchone()[0]
    yield cur
    wipe()
    cur.close()


def _row(cur):
    cur.execute("SELECT password, email, password_changed_at FROM att_master.platform_admins "
                "WHERE username=%s", (USER,))
    return cur.fetchone()


def _events(cur):
    cur.execute("SELECT event_type FROM public.security_events WHERE identifier=%s AND id > %s "
                "AND event_type LIKE 'auth.platform_admin_%%' AND event_type NOT LIKE '%%login%%' ORDER BY id",
                (USER, _first_event_id[0]))
    return [r[0] for r in cur.fetchall()]


def _run(*argv, password=GOOD_PW):
    return seed_script.main(["--username", USER, *argv, "--password-stdin"], stdin=io.StringIO(password + "\n"))


class TestSeedScript:
    def test_creates_an_account_and_records_it(self, clean_operator):
        assert _run("--email", " Ops@Example.COM ") == 0
        hashed, email, changed_at = _row(clean_operator)
        assert check_password_hash(hashed, GOOD_PW)
        assert email == "ops@example.com"
        assert changed_at is None
        assert _events(clean_operator) == ["auth.platform_admin_created"]

    def test_existing_account_is_left_alone_without_reset(self, clean_operator, capsys):
        assert _run("--email", "ops@example.com") == 0
        before = _row(clean_operator)
        assert _run("--email", "attacker@example.com", password=OTHER_PW) == 1
        assert _row(clean_operator) == before
        assert "--reset" in capsys.readouterr().err

    def test_reset_changes_password_and_cancels_pending_code(self, clean_operator):
        assert _run("--email", "ops@example.com") == 0
        clean_operator.execute(
            "INSERT INTO att_master.login_otp_challenges (id, kind, username, code_hash, expires_at) "
            "VALUES ('seedtestchallenge', 'platform_admin', %s, 'x', NOW() + INTERVAL '5 minutes')", (USER,))
        assert _run("--reset", password=OTHER_PW) == 0
        hashed, email, changed_at = _row(clean_operator)
        assert check_password_hash(hashed, OTHER_PW)
        assert email == "ops@example.com"  # kept: --email not given
        assert changed_at is not None
        clean_operator.execute("SELECT COUNT(*) FROM att_master.login_otp_challenges WHERE username=%s", (USER,))
        assert clean_operator.fetchone()[0] == 0
        assert _events(clean_operator) == ["auth.platform_admin_created", "auth.platform_admin_password_reset"]

    def test_reset_can_move_the_email(self, clean_operator, capsys):
        assert _run("--email", "ops@example.com") == 0
        assert _run("--reset", "--email", "new-ops@example.com", password=OTHER_PW) == 0
        assert _row(clean_operator)[1] == "new-ops@example.com"
        assert "instead of ops@example.com" in capsys.readouterr().out

    @pytest.mark.parametrize("password", [
        "short1!A",                       # under 12 characters
        "seed_test_operator-Xy9#",        # contains the username
        " Wq7#rTz2!kLm9vXp",              # leading space: the sign-in form strips it
    ])
    def test_unusable_passwords_are_refused(self, clean_operator, password):
        assert _run("--email", "ops@example.com", password=password) == 1
        assert _row(clean_operator) is None

    @pytest.mark.parametrize("username", ["ab", "two words", "x" * 101, "opеrator"])
    def test_bad_usernames_are_refused(self, clean_operator, username, capsys):
        rc = seed_script.main(["--username", username, "--email", "ops@example.com", "--password-stdin"],
                              stdin=io.StringIO(GOOD_PW + "\n"))
        assert rc == 1
        assert "Nothing was changed" in capsys.readouterr().err

    @pytest.mark.parametrize("email", ["", "ops", "ops@example", "a b@example.com", "x@y.z,evil@example.com x"])
    def test_bad_emails_are_refused(self, clean_operator, email):
        assert _run("--email", email) == 1
        assert _row(clean_operator) is None

    def test_new_account_needs_an_email(self, clean_operator):
        assert _run() == 1
        assert _row(clean_operator) is None

    def test_mismatched_prompt_entries_are_refused(self, clean_operator, monkeypatch):
        answers = iter([GOOD_PW, OTHER_PW])
        monkeypatch.setattr(seed_script.getpass, "getpass", lambda _prompt: next(answers))
        assert seed_script.main(["--username", USER, "--email", "ops@example.com"]) == 1
        assert _row(clean_operator) is None

    def test_password_on_command_line_still_works_but_warns(self, clean_operator, capsys):
        assert seed_script.main(["--username", USER, "--email", "ops@example.com", "--password", GOOD_PW]) == 0
        assert "shell history" in capsys.readouterr().err

    def test_seed_rechecks_inside_the_transaction(self, clean_operator):
        assert _run("--email", "ops@example.com") == 0
        with pytest.raises(seed_script.SeedError):
            seed_script.seed(USER, OTHER_PW, email="x@example.com", reset=False)
        assert check_password_hash(_row(clean_operator)[0], GOOD_PW)


@pytest.mark.real_platform_admin_check
class TestResetEndsConsoleSessions:
    def _sign_in(self, client):
        resp = client.post("/super_admin/login", data={"username": USER, "password": GOOD_PW})
        assert resp.status_code in (301, 302), "MANDATORY_PLATFORM_ADMIN_MFA is off under tests/conftest.py"

    def test_session_from_before_a_reset_is_ended(self, client, clean_operator):
        assert _run("--email", "ops@example.com") == 0
        self._sign_in(client)
        assert client.get("/super_admin").status_code == 200

        assert _run("--reset", password=OTHER_PW) == 0
        assert client.get("/super_admin").status_code == 404
        with client.session_transaction() as sess:
            assert not sess.get("platform_admin_logged_in")

    def test_signing_in_after_a_reset_works(self, client, clean_operator):
        assert _run("--email", "ops@example.com") == 0
        assert _run("--reset", password=GOOD_PW + "x") == 0
        resp = client.post("/super_admin/login", data={"username": USER, "password": GOOD_PW + "x"})
        assert resp.status_code in (301, 302)
        assert client.get("/super_admin").status_code == 200

    def test_sessions_from_before_this_change_stay_signed_in(self, client, clean_operator):
        # Sessions made before the column existed carry no version; the
        # column is NULL until a reset, so they must not all be signed out.
        assert _run("--email", "ops@example.com") == 0
        with client.session_transaction() as sess:
            sess["platform_admin_logged_in"] = True
            sess["platform_admin_username"] = USER
            sess["platform_admin_last_activity"] = time.time()
        assert client.get("/super_admin").status_code == 200

    def test_database_error_fails_open(self, monkeypatch):
        import blueprints.platform_admin as pa

        def boom(_username):
            raise RuntimeError("db down")
        monkeypatch.setattr(pa, "platform_admin_password_version", boom)
        assert pa._platform_admin_session_current(USER, "") is True


class _FakeCursor:
    def __init__(self, counts=()):
        self.counts = list(counts)
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchone(self):
        return (self.counts.pop(0),)


class TestEnvAdminSeed:
    @pytest.fixture
    def seed(self, monkeypatch):
        from migrations import seed as seed_mod
        log = mock.MagicMock()
        monkeypatch.setattr(seed_mod, "app_log", log)
        monkeypatch.setenv("ADMIN_USERNAME", "boss")
        monkeypatch.setenv("ADMIN_EMAIL", "boss@example.com")
        return seed_mod, log

    def _inserts(self, cur):
        return [p for s, p in cur.sql if s.startswith("INSERT INTO admin_users")]

    @pytest.mark.parametrize("username, password", [
        ("admin", "your_admin_password"),  # .env.example as shipped
        ("boss", "admin123"),              # too short
        ("boss", "boss-Strong#Pass1"),     # contains the username
    ])
    def test_weak_password_creates_no_admin(self, seed, monkeypatch, username, password):
        seed_mod, log = seed
        monkeypatch.setenv("ADMIN_USERNAME", username)
        monkeypatch.setenv("ADMIN_PASSWORD", password)
        cur, db = _FakeCursor(), mock.MagicMock()
        assert seed_mod._create_env_admin(cur, db) is False
        assert self._inserts(cur) == []
        assert "No admin account was created" in log.error.call_args[0][0]

    def test_strong_password_creates_admin_and_says_to_remove_it(self, seed, monkeypatch):
        seed_mod, log = seed
        monkeypatch.setenv("ADMIN_PASSWORD", GOOD_PW)
        cur, db = _FakeCursor(), mock.MagicMock()
        assert seed_mod._create_env_admin(cur, db) is True
        (username, hashed, email), = self._inserts(cur)
        assert (username, email) == ("boss", "boss@example.com")
        assert hashed != GOOD_PW and check_password_hash(hashed, GOOD_PW)
        db.commit.assert_called_once()
        warnings = " ".join(c[0][0] for c in log.warning.call_args_list)
        assert "Remove ADMIN_PASSWORD from .env" in warnings
        assert all("boss@example.com" not in str(c) for c in log.info.call_args_list)

    def test_malformed_email_is_not_stored(self, seed, monkeypatch):
        seed_mod, _log = seed
        monkeypatch.setenv("ADMIN_PASSWORD", GOOD_PW)
        monkeypatch.setenv("ADMIN_EMAIL", "boss@example.com, other@example.com")
        cur = _FakeCursor()
        assert seed_mod._create_env_admin(cur, mock.MagicMock()) is True
        assert self._inserts(cur)[0][2] is None

    def test_missing_password_creates_no_admin(self, seed, monkeypatch):
        seed_mod, log = seed
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        cur = _FakeCursor()
        assert seed_mod._create_env_admin(cur, mock.MagicMock()) is False
        assert self._inserts(cur) == []
        assert "/setup" not in log.warning.call_args[0][0]

    def test_existing_install_skips_the_password_check(self, seed, monkeypatch):
        # Every migration run on a live install would otherwise look the
        # password up in the breach list for nothing.
        seed_mod, _log = seed
        monkeypatch.setenv("ADMIN_PASSWORD", "anything")
        monkeypatch.setattr(seed_mod, "validate_new_password",
                            mock.MagicMock(side_effect=AssertionError("must not run")))
        cur = _FakeCursor(counts=[1, 1])  # company_settings row, one admin
        seed_mod._seed_defaults_and_admin(cur, mock.MagicMock())
        assert self._inserts(cur) == []
