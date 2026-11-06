"""Pay amounts encrypted at rest (AES-256-GCM, utils/pii_crypto.py).

salary_config.salary_per_day / monthly_ctc / basic_pct, offer_letters.monthly_ctc,
employee_incentives.amount, overtime_records.ot_pay and the salary
disbursement amounts are TEXT columns holding "hz2:" ciphertext. The
plaintext carries a number tag, so database.py's decrypt-on-fetch hands the
app a Decimal (as the old DECIMAL columns did). A CHECK constraint on every
column rejects a plaintext write.
"""
from decimal import Decimal

import psycopg2
import pytest

from database import get_db_connection
from migrations.column_migrations import PAY_COLUMNS, pay_check_name, _run_pay_encryption_migration
from utils.helpers import amount, decrypt_pii, encrypt_amount
from utils.pii_crypto import NUM_TAG, cipher_from_env, decode_plaintext, encode_number


class TestNumberEncoding:
    @pytest.mark.parametrize("value,expected", [
        (1000, "1000.00"), (1000.5, "1000.50"), ("  2500.125 ", "2500.13"), (Decimal("0"), "0.00"), (-12.5, "-12.50"),
    ])
    def test_rounds_to_two_places_like_the_decimal_columns(self, value, expected):
        assert encode_number(value) == NUM_TAG + expected

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_is_none(self, value):
        assert encode_number(value) is None
        assert encrypt_amount(value) is None

    @pytest.mark.parametrize("value", ["abc", "1e", "NaN", "Infinity", float("inf"), True, [1]])
    def test_non_numbers_are_rejected(self, value):
        with pytest.raises(ValueError):
            encrypt_amount(value)

    def test_round_trip_gives_a_decimal(self):
        enc = encrypt_amount(1234.5)
        assert enc.startswith("hz2:") and "1234" not in enc
        assert decrypt_pii(enc) == Decimal("1234.50")

    def test_ordinary_text_is_not_turned_into_a_number(self):
        assert decode_plaintext("1234.50") == "1234.50"
        assert decode_plaintext(NUM_TAG + "not-a-number") == NUM_TAG + "not-a-number"

    def test_key_rotation_keeps_the_number(self):
        cipher = cipher_from_env()
        assert decrypt_pii(cipher.rotate(encrypt_amount(99.9))) == Decimal("99.90")

    @pytest.mark.parametrize("value,default,expected", [
        (None, 0, Decimal("0")), (None, 50, Decimal("50")), (Decimal("7.25"), 0, Decimal("7.25")),
        ("12.5", 0, Decimal("12.5")), ("[unable to decrypt]", 0, Decimal("0")),
    ])
    def test_amount_helper(self, value, default, expected):
        assert amount(value, default) == expected


class TestSchema:
    @pytest.mark.parametrize("table,column", PAY_COLUMNS)
    def test_every_pay_column_is_text_with_the_check(self, db_engine, table, column):
        cur = db_engine.cursor()
        cur.execute("SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name=%s AND column_name=%s", (table, column))
        assert cur.fetchone()[0] == "text"
        cur.execute("SELECT 1 FROM pg_constraint WHERE conname=%s", (pay_check_name(table, column),))
        assert cur.fetchone() is not None
        cur.close()

    def test_plaintext_salary_is_rejected(self, db_engine, seed_employee):
        cur = db_engine.cursor()
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s, '1000')",
                        (seed_employee["employee_id"],))
        cur.close()

    def test_stored_as_ciphertext_read_back_as_decimal(self, db_engine, seed_employee):
        emp = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc) VALUES (%s,%s,%s) "
                    "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=EXCLUDED.salary_per_day, "
                    "monthly_ctc=EXCLUDED.monthly_ctc", (emp, encrypt_amount(1500), encrypt_amount(39000)))
        cur.execute("SELECT salary_per_day, monthly_ctc FROM salary_config WHERE employee_id=%s", (emp,))
        raw = cur.fetchone()
        cur.close()
        assert all(v.startswith("hz2:") for v in raw)
        assert not any("1500" in v or "39000" in v for v in raw)

        db = get_db_connection()
        try:
            c = db.cursor()
            c.execute("SELECT salary_per_day, monthly_ctc FROM salary_config WHERE employee_id=%s", (emp,))
            assert c.fetchone() == (Decimal("1500.00"), Decimal("39000.00"))
            c.close()
        finally:
            db.close()


class TestMigration:
    """An install from before this change: DECIMAL columns full of numbers."""
    SCHEMA = "pay_enc_mig_test"

    @pytest.fixture
    def old_schema(self, db_engine):
        s = self.SCHEMA
        cur = db_engine.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
        cur.execute(f"CREATE SCHEMA {s}")
        cur.execute(f"CREATE TABLE {s}.salary_config (id SERIAL PRIMARY KEY, employee_id VARCHAR(50), "
                    "salary_per_day DECIMAL(10,2) DEFAULT 0, monthly_ctc DECIMAL(12,2) DEFAULT 0, "
                    "basic_pct INT DEFAULT 50)")
        cur.execute(f"CREATE TABLE {s}.employee_incentives (id SERIAL PRIMARY KEY, "
                    "amount DECIMAL(10,2) NOT NULL DEFAULT 0)")
        cur.execute(f"INSERT INTO {s}.salary_config (employee_id, salary_per_day, monthly_ctc, basic_pct) "
                    "VALUES ('A', 1234.50, 32097.00, 40), ('B', 800, NULL, 50)")
        cur.execute(f"INSERT INTO {s}.employee_incentives (amount) VALUES (750)")
        cur.execute(f"SET search_path TO {s}")
        try:
            yield cur
        finally:
            cur.execute("RESET search_path")
            cur.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
            cur.close()

    def test_converts_and_encrypts_existing_values(self, db_engine, old_schema):
        cur = old_schema
        _run_pay_encryption_migration(cur, db_engine)
        _run_pay_encryption_migration(cur, db_engine)          # idempotent: second run is a no-op

        cur.execute("SELECT column_name, data_type, column_default FROM information_schema.columns "
                    "WHERE table_schema=%s AND column_name IN ('salary_per_day','monthly_ctc','basic_pct','amount')",
                    (self.SCHEMA,))
        cols = cur.fetchall()
        assert len(cols) == 4
        assert all(dt == "text" and default is None for _, dt, default in cols)

        cur.execute("SELECT employee_id, salary_per_day, monthly_ctc, basic_pct FROM salary_config ORDER BY employee_id")
        a, b = cur.fetchall()
        assert all(v.startswith("hz2:") for v in a[1:])
        assert [decrypt_pii(v) for v in a[1:]] == [Decimal("1234.50"), Decimal("32097.00"), Decimal("40.00")]
        assert decrypt_pii(b[1]) == Decimal("800.00") and b[2] is None    # NULL stays NULL
        cur.execute("SELECT amount FROM employee_incentives")
        assert decrypt_pii(cur.fetchone()[0]) == Decimal("750.00")

        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("UPDATE salary_config SET salary_per_day='999' WHERE employee_id='A'")


class TestRoutes:
    def test_api_rejects_a_non_number_salary(self, client, seed_admin, seed_employee):
        from tests.test_payroll import _admin_token
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/salary_config", json={"employee_id": seed_employee["employee_id"],
                                                     "salary_per_day": "lots"},
                         headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 400

    def test_overtime_pay_is_encrypted_and_summed_in_python(self, client, db_engine, seed_employee):
        import datetime
        emp = seed_employee["employee_id"]
        cur = db_engine.cursor()
        today = datetime.date.today()
        for i, pay in enumerate((100.25, 50)):
            cur.execute("INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, "
                        "ot_pay, status) VALUES (%s,%s,'18:00','19:00',60,%s,'Approved')",
                        (emp, today - datetime.timedelta(days=i * 40), encrypt_amount(pay)))
        cur.close()
        with client.session_transaction() as sess:
            sess["employee_id"] = emp
            sess["role"] = "employee"
        rv = client.get(f"/my_payslip_summary/{today.year}/{today.month}")
        assert rv.status_code == 200
        assert rv.get_json()["ot_pay"] == 100.25        # only this month's approved record
