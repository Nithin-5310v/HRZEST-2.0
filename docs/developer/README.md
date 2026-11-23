# HRzest developer guide

Start here if you're working on the HRzest code. HRzest is a multi-company HR
platform (attendance with QR/face/GPS check-in, leave, payroll, onboarding,
performance, billing) built as one Flask application on PostgreSQL, with an Expo
/ React Native mobile app. Production runs at https://hrzest.com.

## Read in this order

1. **[Local setup](local-setup.md)**: run the server, the tests and the app on
   your machine.
2. **[Architecture](architecture.md)**: start-up, the request pipeline, how a
   request finds its company, blueprints, background jobs, logging.
3. **[Conventions](conventions.md)**: the rules the code depends on (branches,
   URLs inside a company, database access, error responses, security habits).
   Read this before your first pull request.
4. **[Testing](testing.md)**: how the test database works, fixtures, and how to
   write a test for a change.
5. **[CI and release](ci-and-release.md)**: GitHub Actions, Jenkins, the live
   server's auto-deploy, rollback.

## Reference

| Topic | Page |
| --- | --- |
| Sign-in flows, route guards, roles and permissions, passwords, request-level protection | [auth-and-security.md](auth-and-security.md) |
| Schemas, connections, migrations, encrypted personal data, backups | [database.md](database.md) |
| Every environment variable, grouped | [configuration.md](configuration.md) |
| The mobile app: API client, session, offline punches, builds, OTA updates | [mobile.md](mobile.md) |
| Failures we've actually hit, and their fixes | [troubleshooting.md](troubleshooting.md) |
| Where every folder and file belongs | [PROJECT_STRUCTURE.md](../../PROJECT_STRUCTURE.md) |

## Specialist documents

| Document | About |
| --- | --- |
| [SECURITY.md](../../SECURITY.md) | Security policy and reporting |
| [SECURITY_HARDENING.md](../SECURITY_HARDENING.md) | Server hardening, audit anchors, liveness rollout, what each control does and doesn't cover |
| [ENCRYPTION_AT_REST.md](../ENCRYPTION_AT_REST.md) | Personal-data encryption and key rotation |
| [FIREWALL.md](../FIREWALL.md) | Host firewall agent and the app firewall |
| [SOC.md](../SOC.md) | The security operations console |
| [RESTORE.md](../RESTORE.md) | Restoring from backup |
| [TESTING_DATABASE.md](../TESTING_DATABASE.md) | Test database details |
| [ci/jenkins/README.md](../../ci/jenkins/README.md) | Running and maintaining Jenkins |
| [observability/README.md](../../observability/README.md) | Logs and monitoring stack |
| [tests_e2e/README.md](../../tests_e2e/README.md) | Browser end-to-end tests |
| [QA_AUDIT.md](../QA_AUDIT.md) | The October 2026 QA audit and the owner's decisions |

## The short version

- Branch from `develop`, open the pull request into `develop`, never push to
  `main`.
- Every route has a guard decorator; API calls get JSON errors, never redirects.
- Links inside a company use `tpath()`; emailed links use `company_url()`.
- Employee email and phone are encrypted: look them up by blind index.
- Run the whole test suite and `python -m flake8 .` before opening a PR.
- A release is a `develop` → `main` pull request plus an approval in Jenkins.

## Keeping these docs right

These pages describe the code as of November 2026. When you change something
they describe (a guard, the release flow, a migration pattern, an environment
variable), update the page in the same pull request. If you find a page that's
wrong, fix it; a wrong doc is worse than none.
