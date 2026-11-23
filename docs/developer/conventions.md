# Conventions

Rules this code base depends on that you can't see from any single file. Most
of them exist because breaking them caused a real bug; the reason is given so
you can judge edge cases.

## Branches and pull requests

- Branch off **`develop`**: `git checkout -b fix/short-description origin/develop`.
- Open the pull request into **`develop`**. Never push to `main` directly:
  Jenkins only releases a `main` commit that is the merge commit of a pull
  request, so a direct push blocks every release until someone repairs it (see
  [ci-and-release.md](ci-and-release.md)).
- Release = a pull request from `develop` into `main`, merged with **Create a
  merge commit**.
- Before opening a PR: the full back-end suite passes, the mobile tests pass if
  you touched `mobile/`, and `python -m flake8 .` is clean over the **whole**
  repo. See [testing.md](testing.md).
- One concern per PR, and say in the description what changed for users, why,
  and how it was tested. Reviewers merge quickly, so a PR should be ready when
  it's opened.

## URLs inside a company

Every company lives under its own path prefix, `/<company>/...` (see
[architecture.md](architecture.md#which-company-a-request-belongs-to)).

| Need | Use | Not |
| --- | --- | --- |
| A link or redirect in a page or route | `tpath("/employees")` (`utils/helpers/urls.py`) | a bare `"/employees"` (lands in no company) |
| A link in an email or anything opened outside the browser session | `company_url("/employee_reset_password/" + token)` | `request.url_root + ...` (loses the company, so the link reports "invalid") |
| A static file | `static_url("app.css")` | a hand-built `/static/...` path |
| A `fetch()` from a page | a path the page already prefixes, or `{{ tpath('/api/...') }}` | assuming the page is at the site root |

## Adding a public page

Public pages (home, `/signup`, `/privacy`, `/terms`, `/security-policy`) are
the only ones search engines may index. A new one needs all of these:

1. Its path in `INDEXABLE_PATHS` (`blueprints/seo.py`). That also puts it in the
   sitemap and counts its page views; any other page gets `noindex`.
2. Its first path segment in `RESERVED_PATH_SEGMENTS` (`utils/tenant_routing.py`),
   so no company can take it as its address.
3. Its endpoint in the allow-list in `tests/test_route_auth_guards.py`.
4. A unique `<title>` and description, one `<h1>`, and
   `{{ public_meta(title, description, path) }}` from `templates/_public_meta.html`
   (canonical, social preview, icons). Policy-style pages can extend
   `templates/legal_layout.html`, which adds breadcrumbs and the footer links.
5. A line in `/llms.txt` (`blueprints/seo.py`) if it's a main page.

## Database access

- **Always parameterised SQL:** `cursor.execute("... WHERE id=%s", (id,))`. Only
  fixed identifiers (table or column names chosen in code) may be formatted into
  SQL, and that line then carries `# nosec B608 -- <why it's safe>`.
- Use `with _db() as (cursor, conn):` (`utils/helpers/db.py`) for short work;
  it returns the connection to the pool. Commit explicitly.
- `get_db_connection()` already points at the current company's schema. Don't
  pass schema names around; switch company with the helpers in `database.py`.
- **Employee email and phone are encrypted at rest.** Never write
  `WHERE email = %s` for employees. Look them up by blind index:
  `WHERE email_bidx = ANY(%s)` with `contact_lookup_keys("email", value)`, and
  store with `encrypt_contact("email", value)` (`utils/helpers/crypto.py`).
- New tables and columns go through migrations; see
  [database.md](database.md#adding-a-table-or-column).

## Errors and responses

- JSON responses use `{"ok": true, ...}` on success and
  `{"ok": false, "msg": "<plain-language reason>"}` on failure, with a real
  HTTP status (400 bad input, 401 not signed in, 403 not allowed, 404 not found,
  409 conflict, 429 rate limited, 503 service down).
- Error handlers already answer `/api/` and `fetch()` requests with JSON
  (`wants_json_response()` in `utils/helpers/http.py`). The sign-in guards do
  the same through `_reject()` in `utils/auth.py`. **Never answer an API call
  with a redirect**: `fetch()` follows it to an HTML page it can't read, and the
  user sees a misleading error.
- Front-end code that calls the API reads `data.msg` for errors (and `reply`
  where an endpoint uses it), and handles a non-JSON answer (gateway error or
  timeout) separately.
- Write user-facing messages in plain words that say what happened and what to
  do next: "Your session has ended. Please sign in again.", not "401".

## Security habits

- **Every route has a guard decorator** (`@admin_required`,
  `@employee_required`, `@api_required`, ...). A route without one fails
  `tests/test_route_auth_guards.py` unless it's listed in `ALLOWED_UNGUARDED`
  with the reason it's public. See [auth-and-security.md](auth-and-security.md).
- **Random values a browser or the app sends back** (codes, link tokens, OAuth
  state, temporary passwords): use `waf_safe_token(n)` (`utils/tokens.py`), not
  `secrets.token_urlsafe`. A urlsafe value sometimes contains `0x` followed by
  hex characters, and the site's firewall blocks that as SQL injection, so the
  user's request fails at random. `tests/test_waf_safe_tokens.py` fails on any
  new raw `token_urlsafe` for such values. Values that are only hashed and
  never sent back may use `secrets.token_hex`.
- **The firewall checks every field** of every request (`utils/waf.py`):
  query string, form fields, JSON body (including nested history), path
  segments. Don't design features that send code-like text back to the server
  unless the endpoint is deliberately exempt.
- **Never log secrets.** No passwords, tokens, OTP codes or `.env` values in
  logs, exceptions, test assertions or security events. Mask identifiers with
  `_mask_identifier()`. Record security-relevant events with
  `log_security_event(...)` (`extensions.py`).
- **Pages:** put user text into the page with `textContent`, never `innerHTML`.
  The Content-Security-Policy blocks inline event handlers and style attributes
  created by JavaScript; inline `<script>` tags get a nonce automatically, and
  `fetch()` POSTs get the CSRF token automatically (`app_setup/csrf_csp.py`).

## Dates and times

- The server clock is UTC. Business dates (attendance day, payroll month, "late"
  or "on time") are in the **company's** time zone: use `company_today()` and
  `company_now()` (`utils/helpers/settings.py`), not `datetime.date.today()`.
- Store timestamps in UTC and convert for display.

## Writing style for code

- Python: flake8-clean with the repo's `.flake8` (maximum line length 160;
  wrap continuation lines with a hanging indent, since E127/E128 have failed CI
  before). Standard library first, and no new dependency without a reason in
  the PR. Every dependency is pinned in `requirements.lock`.
- Comments explain **why**, especially for anything that looks wrong but is
  deliberate. Many functions in this code base carry a docstring with the bug or
  decision behind them; keep that habit.
- Prefer small, focused helpers in `utils/` over growing a route function.
