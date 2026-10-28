"""Drives the web company signup (blueprints/org.py) the way a browser
does. Signup is one step: the company name and the admin's account --
email (also the login username), mobile number, password -- at
POST /create_org, then the emailed code on the same page
(POST /create_org/verify_otp), which creates the company and logs its
admin into the dashboard. The rest is the dashboard's registration
checklist: company details (GSTIN) and documents. signup_company() runs
all of it; the step helpers let a test stop at, or fail, any one step."""
import io
import re
import secrets

FAKE_PDF = b"%PDF-1.4\n" + b"x" * 20
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
MOBILE = "+91 98765 43210"
PASSWORD = "password123"


def random_gst():
    """A syntactically valid, but not otherwise meaningful, 15-character
    GSTIN -- fresh each call so unrelated tests never collide on
    check_duplicate_gst()."""
    letters = ''.join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(5))
    return (
        f"{secrets.randbelow(100):02d}{letters}{secrets.randbelow(10000):04d}"
        f"{secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{secrets.choice('123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
        f"Z{secrets.choice('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
    )


def company_slug(company_name):
    """The company URL org._slug_for_company() picks for a name nobody has
    taken yet."""
    return re.sub(r'[^a-z0-9]+', '-', company_name.lower()).strip('-')[:40].strip('-')


def capture_codes(monkeypatch):
    """Swaps out the signup-code email; returns the list each sent code
    is appended to."""
    import blueprints.org as org_module
    codes = []
    monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                        lambda email, company, otp: codes.append(otp) or True)
    return codes


def start_signup(client, email, company_name=None, mobile=MOBILE, password=PASSWORD, **extra):
    """Step 1. A successful response redirects to the code entry, whose URL
    carries the application id (application_id_from). Without a
    company_name, a fresh one is made up."""
    data = {"company_name": company_name or f"Signup Test {secrets.token_hex(4)}",
            "admin_email": email, "admin_mobile": mobile, "admin_password": password}
    data.update(extra)
    return client.post("/create_org", data=data, follow_redirects=False)


def application_id_from(resp):
    return int(resp.headers["Location"].rsplit("=", 1)[-1])


def verify_email(client, application_id, code):
    return client.post("/create_org/verify_otp", data={"application_id": application_id, "otp_code": code},
                       follow_redirects=False)


def signup_verified(client, monkeypatch, email, company_name, **account):
    """Step 1 and its code: the company exists and its admin is logged in.
    Returns (application_id, company URL slug)."""
    codes = capture_codes(monkeypatch)
    resp = start_signup(client, email, company_name, **account)
    assert resp.status_code == 302 and "/create_org/verify_otp" in resp.headers["Location"], resp.headers.get(
        "Location")
    application_id = application_id_from(resp)
    assert codes, "the verification code was never emailed"
    resp = verify_email(client, application_id, codes[-1])
    location = resp.headers.get("Location", "")
    assert resp.status_code == 302 and location.endswith("/admin"), location
    return application_id, location.split("/")[1]


def save_company_details(client, slug, gst_number=None, **extra):
    """The checklist's company details (GSTIN, optional logo)."""
    data = {"gst_number": gst_number or random_gst()}
    data.update(extra)
    return client.post(f"/{slug}/settings/company_details", data=data, content_type="multipart/form-data",
                       follow_redirects=False)


def _document_files():
    return {
        "registration_cert": (io.BytesIO(FAKE_PDF), "cert.pdf"),
        "address_proof": (io.BytesIO(FAKE_PDF), "address.pdf"),
        "visiting_card": (io.BytesIO(FAKE_PNG), "card.png"),
        "name_board_photo": (io.BytesIO(FAKE_PNG), "board.png"),
    }


def upload_documents(client, slug, **extra):
    """The checklist's documents, from the dashboard."""
    data = _document_files()
    data.update(extra)
    return client.post(f"/{slug}/settings/company_verification", data=data, content_type="multipart/form-data",
                       follow_redirects=False)


def upload_application_documents(client, application_id, **extra):
    """Documents through the pre-login page (/create_org/upload_documents),
    still reached from a rejection email's re-upload link."""
    data = {"application_id": str(application_id)}
    data.update(_document_files())
    data.update(extra)
    return client.post("/create_org/upload_documents", data=data, content_type="multipart/form-data",
                       follow_redirects=False)


def submit_profile(client, application_id, company_name, subdomain, gst_number=None, **extra):
    """The old company-profile step, still served for an application whose
    email was verified before step 1 asked for the company name."""
    data = {"application_id": str(application_id), "company_name": company_name, "subdomain": subdomain,
            "gst_number": gst_number or random_gst()}
    data.update(extra)
    return client.post("/create_org/profile", data=data, content_type="multipart/form-data",
                       follow_redirects=False)


def signup_company(client, monkeypatch, email, company_name, gst_number=None, details=True, documents=True,
                   **account):
    """Signup plus the checklist's company details and documents (unless
    details=False / documents=False). The company URL is company_slug(
    company_name) -- pick a name nobody else uses. Returns the application
    id; the admin stays logged in."""
    application_id, slug = signup_verified(client, monkeypatch, email, company_name, **account)
    assert slug == company_slug(company_name), slug
    if details:
        resp = save_company_details(client, slug, gst_number)
        assert resp.headers.get("Location") == f"/{slug}/admin", resp.headers.get("Location")
    if documents:
        resp = upload_documents(client, slug)
        assert resp.headers.get("Location") == f"/{slug}/settings/company_verification", resp.headers.get("Location")
    return application_id
