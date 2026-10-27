"""The login and registration pages put their form on the right and the
HRzest promo panel (templates/_auth_promo.html, static/auth_split.css) on
the left. Every such page has to carry both: one that loses the stylesheet
renders the promo as unstyled text above the form."""
import pytest
from flask import render_template

PROMO = 'class="auth-promo"'
CSS = "/static/auth_split.css"

# The wizard steps past /create_org need a live application to reach, so
# those templates are rendered directly; the rest are fetched by route.
ROUTES = ["/login", "/super_admin/login", "/admin_forgot_password", "/employee_forgot_password", "/create_org"]
TEMPLATES = [
    "mfa_verify.html", "admin_reset_password.html", "employee_reset_password.html",
    "create_org_verify_otp.html", "create_org_upload_documents.html",
    "create_org_pay.html", "create_org_setup_trial.html",
]


@pytest.mark.parametrize("path", ROUTES)
def test_page_has_promo_beside_form(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert PROMO in page and CSS in page
    assert page.index(PROMO) < page.index("<form")  # promo first: left column


@pytest.mark.parametrize("template", TEMPLATES)
def test_template_has_promo(client, template):
    app = client.application
    with app.test_request_context("/"):
        app.preprocess_request()
        page = render_template(template, username="admin", company_name="Acme", application_id=1,
                               sent_to="admin@acme.test", per_employee_paise=4900, employee_count=1)
    assert PROMO in page and CSS in page
