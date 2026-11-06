"""
Run once to generate a self-signed SSL certificate for the dev/kiosk server.
Requires: pip install cryptography

Usage:
    python scripts/generate_cert.py

Produces: cert.pem  key.pem  (in the project root)
"""

import datetime
import ipaddress
import os
import socket


from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# Run from anywhere: work relative to the repo root (this file lives in scripts/).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)

# ── detect local IP ──────────────────────────────────────────────────────────
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    local_ip = s.getsockname()[0]
    s.close()
except Exception:
    local_ip = "127.0.0.1"

print(f"Detected local IP: {local_ip}")

# ── generate private key ─────────────────────────────────────────────────────
key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())

# ── build certificate ────────────────────────────────────────────────────────
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "HRzest.com"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Local"),
    x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv4Address(local_ip)),
        ]),
        critical=False,
    )
    .sign(key, hashes.SHA256(), default_backend())
)

# ── write files ──────────────────────────────────────────────────────────────
with open("cert.pem", "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

with open("key.pem", "wb") as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))

print("✅  cert.pem and key.pem created.")
print(f"   Valid for: localhost, 127.0.0.1, {local_ip}")
print()
print("Next steps:")
print("  1. python app.py          (Flask will start on https://0.0.0.0:5000)")
print(f"  2. Open https://{local_ip}:5000 in Chrome")
print("  3. Accept the 'Your connection is not private' warning once")
print("     (click Advanced → Proceed to ... (unsafe))")
print("  4. WebAuthn / fingerprint will now work on all devices on the LAN")
