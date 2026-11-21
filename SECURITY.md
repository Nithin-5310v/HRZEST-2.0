# Security Policy

## Reporting a vulnerability

Please email **security@hrzest.com**. Don't open a public GitHub issue for a
security problem.

Include:

- what you found and where (URL, endpoint or screen);
- steps to reproduce it, and what an attacker could do with it;
- your name or handle if you'd like to be credited.

Please don't include anyone's personal data beyond what you need to show the
problem.

## What to expect

- We acknowledge reports within **3 business days**.
- We keep you updated while we investigate and fix it, and tell you when it's
  resolved.
- We won't take legal action against research done in good faith that follows
  this policy.
- With your permission, we credit you once the fix is live.

## Rules for testing

- Test only against your own account or a test company you registered. Never
  access, change or delete another person's or company's data.
- No denial-of-service tests, spam, social engineering or physical attacks,
  and no high-volume automated scans against production.
- Give us reasonable time to fix the issue before telling anyone else.
- Stop and tell us as soon as you reach data that isn't yours.

## Scope

hrzest.com and its company portals, the HRZEST mobile app, and the HRZEST API.
HRZEST is a hosted service, so the supported version is always the one running
in production plus the current mobile app release.

The same policy is published at <https://hrzest.com/security-policy>, with
machine-readable contact details at
<https://hrzest.com/.well-known/security.txt>. Keep the three in step: the
contact address lives in `blueprints/security_txt.py`.
