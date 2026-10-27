"""Drives the web company-signup wizard (blueprints/org.py) the way a
browser does: the account (email -- also the login username -- mobile
number and password, POST /create_org), the emailed code, the company
profile (POST /create_org/profile, which provisions the company), then
the documents. signup_company() runs all of it; the step helpers let a
test stop at, or fail, any one step."""
import io
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


def capture_codes(monkeypatch):
    """Swaps out the signup-code email; returns the list each sent code
    is appended to."""
    import blueprints.org as org_module
    codes = []
    monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                        lambda email, company, otp: codes.append(otp) or True)
    return codes


def start_signup(client, email, mobile=MOBILE, password=PASSWORD, **extra):
    """Step 1. A successful response redirects to the code screen, whose
    URL carries the application id (application_id_from)."""
    data = {"admin_email": email, "admin_mobile": mobile, "admin_password": password}
    data.update(extra)
    return client.post("/create_org", data=data, follow_redirects=False)


def application_id_from(resp):
    return int(resp.headers["Location"].rsplit("=", 1)[-1])


def verify_email(client, application_id, code):
    return client.post("/create_org/verify_otp", data={"application_id": application_id, "otp_code": code},
                       follow_redirects=False)


def submit_profile(client, application_id, company_name, subdomain, gst_number=None, **extra):
    data = {"application_id": str(application_id), "company_name": company_name, "subdomain": subdomain,
            "gst_number": gst_number or random_gst()}
    data.update(extra)
    return client.post("/create_org/profile", data=data, content_type="multipart/form-data",
                       follow_redirects=False)


def upload_documents(client, application_id, **extra):
    data = {
        "application_id": str(application_id),
        "registration_cert": (io.BytesIO(FAKE_PDF), "cert.pdf"),
        "address_proof": (io.BytesIO(FAKE_PDF), "address.pdf"),
        "visiting_card": (io.BytesIO(FAKE_PNG), "card.png"),
        "name_board_photo": (io.BytesIO(FAKE_PNG), "board.png"),
    }
    data.update(extra)
    return client.post("/create_org/upload_documents", data=data, content_type="multipart/form-data",
                       follow_redirects=False)


def signup_to_profile(client, monkeypatch, email, **account):
    """Steps 1-2: returns the id of the verified application, now waiting
    for its company profile."""
    codes = capture_codes(monkeypatch)
    resp = start_signup(client, email, **account)
    assert resp.status_code == 302 and "/create_org/verify_otp" in resp.headers["Location"], resp.headers.get(
        "Location")
    application_id = application_id_from(resp)
    assert codes, "the verification code was never emailed"
    resp = verify_email(client, application_id, codes[-1])
    assert resp.headers["Location"] == f"/create_org/profile?application_id={application_id}"
    return application_id


def signup_company(client, monkeypatch, email, company_name, subdomain, gst_number=None, documents=True,
                   profile_extra=None, **account):
    """The whole wizard. Returns the application id; its company is
    provisioned (and its documents submitted unless documents=False)."""
    application_id = signup_to_profile(client, monkeypatch, email, **account)
    resp = submit_profile(client, application_id, company_name, subdomain, gst_number, **(profile_extra or {}))
    assert resp.headers["Location"] == f"/create_org/upload_documents?application_id={application_id}", \
        resp.headers.get("Location")
    if documents:
        resp = upload_documents(client, application_id)
        assert "/create_org/status" in resp.headers["Location"], resp.headers.get("Location")
    return application_id
