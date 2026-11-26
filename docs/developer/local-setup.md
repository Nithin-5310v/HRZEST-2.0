# Local setup

How to get the server, the tests and the mobile app running on your machine.
The [README](../../README.md#local-development-setup) has the short version;
this page adds what you'll actually run into.

## Server

### 1. Get the code

```bash
git clone https://github.com/Nithin-5310v/HRZEST-2.0.git
cd HRZEST-2.0
git checkout develop          # all work branches off develop
```

### 2. Python environment

CI and production use **Python 3.14** (`.python-version`). Python 3.12 also
works for development. Create a virtual environment and install both
requirement files:

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
source .venv/bin/activate           # macOS / Linux
pip install -r requirements.txt -r requirements-dev.txt
```

**Face recognition on Windows.** `face-recognition` needs `dlib`, which has no
Windows wheel to build from source easily. Install the prebuilt `dlib-bin` first,
then `face-recognition` without its dependencies:

```bash
pip install dlib-bin
pip install face-recognition --no-deps
```

If Windows Smart App Control blocks the dlib DLL, the app still starts and logs
"face_recognition unavailable ... Face features disabled". Everything except face
matching works, and the tests that need it are skipped or mocked.

### 3. PostgreSQL

Install PostgreSQL 16 and create a login role for the app. The app creates its
own database on first start if the role is allowed to. For tests you can instead
use the disposable container in `docker-compose.test.yml`; see
[testing.md](testing.md).

### 4. Configuration (`.env`)

```bash
python scripts/setup_env.py     # copies .env.example to .env and fills in SECRET_KEY and ENCRYPTION_KEY
```

Then edit `.env`: at least the `DB_*` settings and `ADMIN_PASSWORD`. Every
variable is listed and grouped in [configuration.md](configuration.md). For local
work leave payments, AI, OAuth sign-up and email unset; each feature then says it
isn't configured instead of failing.

**Keep your `ENCRYPTION_KEY`.** Employee email and phone numbers are encrypted
with it. A new key makes existing rows unreadable (rotate with
`scripts/rotate_encryption_key.py`, never by just editing `.env`).

### 5. Run it

```bash
python run_dev.py
```

- Serves `http://127.0.0.1:5000` (or `PORT` from `.env`), on all interfaces so a
  phone on the same Wi-Fi can reach it.
- Sets `APP_ENV=development`, so cookies work over plain HTTP.
- Serves HTTPS instead if `cert.pem` and `key.pem` exist
  (`python scripts/generate_cert.py` makes a self-signed pair). Passkeys and the
  phone camera in a browser need HTTPS or `localhost`.
- The first start creates every table and the default company. Sign in at
  `/login` with username `admin` and the `ADMIN_PASSWORD` from `.env`.

To try the multi-company paths, register a company at `/signup`. It gets its
own address, `http://127.0.0.1:5000/<company>/login`, and its own schema.

### Platform admin and SOC console

- **Platform admin** (`/super_admin`, the operator panel over all companies): no
  account exists until you create one:
  `python scripts/seed_platform_admin.py --username <user> --email <email>`
  (asks for the password twice). It won't touch an existing account unless you
  add `--reset`, which sets a new password and signs out that operator's sessions.
- **SOC console** (`/socperson`): see [docs/SOC.md](../SOC.md).

## Mobile app

```bash
cd mobile
npm ci
npx expo start --lan
```

- Point the app at your server with `EXPO_PUBLIC_API_BASE_URL` in
  `mobile/.env` (copy `mobile/.env.example`), for example
  `http://192.168.1.9:5000`. Use your PC's LAN address, not `localhost`; the
  phone has its own `localhost`.
- Open it on a phone with **Expo Go** (it must match the project's Expo SDK
  version; see `mobile/package.json`), or in a browser with `w` for a quick look.
- Development builds may use plain HTTP; release builds refuse any API address
  that isn't HTTPS.

More in [mobile.md](mobile.md).

## Editor

Open `HRZEST.code-workspace` in VS Code. It groups the folders by block (UI,
back end, database, mobile, ...) as described in
[PROJECT_STRUCTURE.md](../../PROJECT_STRUCTURE.md), and hides caches and
virtual environments.
