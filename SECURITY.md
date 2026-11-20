# Security policy

HRZEST holds payroll, bank, identity and attendance data for the companies and
employees who use it. If you think you've found a security vulnerability,
please tell us privately. We'll work with you to fix it.

The same policy is published at <https://hrzest.com/security-policy>, with
machine-readable contact details at
<https://hrzest.com/.well-known/security.txt> (RFC 9116).

## Reporting a vulnerability

Email **security@hrzest.com**. Please don't open a public GitHub issue, pull
request or discussion for a security problem.

Include:

- what you found and where (URL, API endpoint, or app screen and version);
- steps to reproduce it, and what an attacker could do with it;
- your name or handle if you'd like to be credited.

Don't include anyone's personal data beyond what you need to show the problem.

## What you can expect from us

| Step | Target |
|---|---|
| We acknowledge your report | within 3 business days |
| We confirm whether it's a vulnerability and how severe it is | within 10 business days |
| Critical or high severity fixed in production | within 30 days |
| Medium or low severity fixed | in a normal release, usually within 90 days |

We'll keep you updated while we investigate, and tell you when it's fixed. If
we decide it isn't a vulnerability, we'll explain why.

With your permission, we'll credit you once the fix is live.

## Scope

In scope:

- `hrzest.com` and its company portals (`hrzest.com/<company>/…`);
- the HRZEST mobile app (Android and iOS);
- the HRZEST API (`/api/…`).

Out of scope: denial-of-service, spam or social-engineering findings;
reports that only come from automated scanners without a demonstrated impact;
missing best-practice headers with no exploit; and third-party services we use
(Microsoft 365, Razorpay, Cloudflare Turnstile). Report those to the vendor.

## Rules for testing

- Test only against your own account or a test company you registered. Never
  access, change or delete another person's or company's data.
- Don't run denial-of-service tests or high-volume automated scans against
  production.
- Stop and tell us as soon as you reach data that isn't yours.
- Give us a reasonable time to fix the issue before telling anyone else.

We won't take legal action against research done in good faith that follows
this policy.

## Supported versions

HRZEST is a hosted service, so there is one supported version: what runs at
`hrzest.com` (deployed from the `production` branch) and the latest mobile app
release in the stores. Fixes are deployed there; older app builds are asked to
update.
