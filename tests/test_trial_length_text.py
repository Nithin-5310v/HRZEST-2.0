"""The sign-up pages state the free-trial length from
blueprints/trial_billing.TRIAL_DURATION_DAYS (via the trial_days context
variable, app_setup/templating.py), never a hand-typed number. The
/create_org page once offered a "2-Day Free Trial" while the code granted
30 days and the text below it said "1-month"."""
import pathlib
import re

from flask import render_template

from blueprints.trial_billing import TRIAL_DURATION_DAYS

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
# "2-Day Free Trial", "1-month trial", "14 day free trial", "1 month, free" ...
HARDCODED = re.compile(r"\b(\d+|one|two|thirty)[- ](day|days|month|months|week|weeks)\b,?[- ](free[- ])?(trial|free)",
                       re.IGNORECASE)


def test_trial_is_30_days():
    assert TRIAL_DURATION_DAYS == 30


def test_no_template_hardcodes_a_trial_length():
    offenders = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if HARDCODED.search(line):
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, "use {{ trial_days }} instead:\n" + "\n".join(offenders)


def _render(client, template, **ctx):
    app = client.application
    with app.test_request_context("/"):
        app.preprocess_request()
        return render_template(template, **ctx)


def test_setup_trial_page_states_the_real_length(client):
    page = _render(client, "create_org_setup_trial.html", username="admin", company_name="Acme",
                   application_id=1, per_employee_paise=4900, employee_count=1)
    assert f"set up your {TRIAL_DURATION_DAYS}-day free trial" in page
    assert f"{TRIAL_DURATION_DAYS} days, free" in page
    assert "1-month" not in page and "1 month" not in page


def test_create_org_trial_option_states_the_real_length(client):
    page = _render(client, "create_org.html", profile_step=True, payments_enabled=True,
                   free_registration=False, application={"id": 1, "company_name": "Acme"},
                   portal_base="")
    assert f"{TRIAL_DURATION_DAYS}-Day Free Trial" in page
    assert f"Your {TRIAL_DURATION_DAYS}-day trial starts" in page
    assert "2-Day" not in page and "1-month" not in page
