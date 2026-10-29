# Security hardening — what was built, how to turn it on, and what it does *not* do

This document accompanies the hardening work done after the security review.
Every control below exists in code with tests; each section says what changed,
the settings that govern it, how to roll it out safely, and — importantly —
its limits. Controls that are only partly effective are called out rather
than rounded up.

> **Upgrade checklist (things that change behaviour on deploy)**
> 1. Set `HONEYPOT_REPORT_SECRET` (long random string) in `.env`.
> 2. Backups now refuse to write plaintext. Set `BACKUP_ENCRYPTION` (+ recipient)
>    or the nightly job will fail until you do. See *Backups*.
> 3. Mobile/API check-in routes now enforce the company's location setting
>    (`MOBILE_REQUIRE_LOCATION`, default on). Employees who deny location
>    permission will be asked to grant it.
> 4. `LIVENESS_MODE` ships as `warn`; flip to `enforce` after the mobile app
>    release is in users' hands. See *Attendance integrity*.
> 5. Outside `APP_ENV=development` the database layer no longer silently falls
>    back to SQLite (`ALLOW_SQLITE_FALLBACK` restores it).

---

## 1. Real client IP, bans and rate limits

**Problem.** Behind rootless Podman's `slirp4netns` every visitor reaches nginx
from one internal gateway address. IP bans, the WAF auto-ban, the honeypot ban
and Flask-Limiter all keyed on that address, so five hostile requests — or one
scanner hit on `/.env` — banned *everyone*. The WAF's `--\s` pattern also
blocked ordinary prose ("Great work -- keep it up") and fed that counter.

**What changed** (`utils/client_ip.py`, `utils/waf.py`, `app.py`, `extensions.py`,
`blueprints/honeypot_routes.py`)

| Control | Behaviour now |
|---|---|
| Never-ban list | Loopback, RFC1918, CGNAT, link-local, ULA, unparseable → never IP-banned. Add office/probe CIDRs with `WAF_NEVER_BAN_CIDRS`. |
| Existing bans | A stale ban row on a protected address is ignored by `_enforce_ip_ban`. |
| Ban scope | Automated bans go to `public.banned_ips` so an attacker can't hop tenant URLs; enforcement checks tenant + public. |
| Ban semantics | One write path (`waf.ban_ip`): never shortens a longer/permanent ban. Honeypot bans expire (`HONEYPOT_BAN_MINUTES`, default 7 d) instead of being permanent. |
| Rate-limit key | Routable IP → the IP. Shared address → IP + signed-session identity (anonymous traffic stays in one bucket; no client-controlled discriminator). |
| Proxy trust | `TRUSTED_PROXY_HOPS` (default 1) replaces the hard-coded ProxyFix. Never set it higher than the real proxy count. |
| WAF patterns | Comment markers only match when they terminate a value or follow SQL syntax; `UN/**/ION`, `/*!…*/` and `;--` are still caught. |
| Visibility | A production request from a non-routable address logs one CRITICAL line; withheld bans log `waf.ban_skipped_shared_ip`. |

A latent bug was fixed on the way: `log_security_event(..., ip=...)` at ERROR
level raised `TypeError`, so **every HTTP honeypot hit returned 500 and never
banned or alerted**.

**Fix the root cause.** The above is a safety net. To get real client IPs:
Podman ≥ 5 with `pasta` (`containers.conf`: `[network] default_rootless_network_cmd="pasta"`),
or host networking for nginx, or a trusted edge that forwards the real address
(then set `TRUSTED_PROXY_HOPS=2`). `scripts/host_hardening.sh` reports which
mode the host is in.

**Limits.** With a collapsed address, IP-keyed *detections* (credential
stuffing by source, recon→probe) cannot fire; account lockout and the
per-account rules still work.

---

## 2. Honeypot isolation

**Problem.** The internet-facing decoy container was given the entire
production `.env` because `utils.alerts` imported the Flask app just for a logger.

**What changed.** `utils/alerts.py` no longer imports `extensions`; the honeypot
service in `compose.yaml` has an explicit environment allow-list (alert
webhook + a dedicated `HONEYPOT_REPORT_SECRET`) and no `env_file`. Hits are
reported to the app over a signed channel (`/webhooks/honeypot`, HMAC over
`timestamp.body`, 5-minute skew, payload = `{ip, port, protocol, bytes}` only —
never attacker bytes). The app records `honeypot.tcp_decoy_hit` and bans the
source (`HONEYPOT_TCP_BAN_MINUTES`, default 24 h).

**Limits.** The honeypot still uses the app *image*, so the code is present
(secrets are not). It runs read-only, `cap_drop: ALL`, no backend network.

---

## 3. Audit-log integrity and database roles

**Problem.** The "immutable" audit log was protected by a trigger that the
application's own role (the Postgres superuser by default) could drop, and by a
session setting (`audit.bypass`) that any connection could flip. Nothing would
have revealed tampering.

**What changed**

1. **Hash chain** (`migrations/audit_integrity.py`). A `BEFORE INSERT` trigger
   stores `prev_hash` and `row_hash = SHA-256(prev_hash ‖ row)` for
   `audit_logs` and `security_events`, serialised by a row lock on
   `audit_chain_head` so concurrent writers cannot fork it. Applied to **every**
   tenant at boot, not just new ones.
2. **Off-box anchors** (`utils/audit_jobs.py`). The head hash of every chain is
   logged each 10 minutes → stdout → Promtail → Loki on another host. An
   attacker who rewrites rows *and* recomputes the chain in the database cannot
   rewrite the copy they can't reach. A Loki rule (`observability/log-server/rules/`)
   alerts if anchors stop (dead-man's switch).
3. **Verification.** Daily in-app (`audit.chain_violation` at CRITICAL), and
   `scripts/verify_audit_chain.py [--anchors file.jsonl]` on demand.
4. **Bypass compiled out.** `audit.bypass` only exists in the trigger function
   when `APP_ENV=development` or `FLASK_ENV=testing`.
5. **Split database roles** (opt-in; `database.py`, `scripts/provision_db_roles.py`).
   `DB_USER` becomes a non-owner DML role; `DB_OWNER_USER` is used only inside
   `owner_privileges()` for migrations, tenant provisioning/deletion and
   verification. The runtime role has no DDL, can only `INSERT`/`SELECT` the
   audit tables, and cannot read the chain head (the chain trigger is
   `SECURITY DEFINER`).

**Roll-out.**
```bash
PG_ADMIN_USER=postgres PG_ADMIN_PASS=… DB_NAME=… DB_HOST=… \
HRZEST_OWNER_ROLE=hrzest_owner HRZEST_OWNER_PASS=… \
HRZEST_APP_ROLE=hrzest_app     HRZEST_APP_PASS=… \
  python scripts/provision_db_roles.py --dry-run     # read it, then run without --dry-run
# .env: DB_USER=hrzest_app DB_PASS=…  DB_OWNER_USER=hrzest_owner DB_OWNER_PASS=…
```
Verified: the tenancy, platform-admin, onboarding, auth, audit and WAF suites
(442 tests) pass when run as the restricted, non-superuser role against
Postgres 16 (this exercised tenant provisioning, tenant `DROP SCHEMA` and audit
writes under the split). `tests/test_db_role_split.py` asserts the privilege
boundary directly but has **not yet been run in that mode** -- run it
(`DB_USER=<runtime> DB_OWNER_USER=<owner> pytest tests/test_db_role_split.py`)
before relying on the split.

**Honest limits.**
* The web process still holds the owner credential (migrations and signup-time
  tenant provisioning run in-process). The split defeats **SQL injection and a
  stolen runtime password**; it does not defeat full compromise of the app
  process. Moving provisioning/migrations to a separate privileged worker is the
  next step and is not done here.
* A database superuser can still disable triggers — which is what the
  off-box anchors are for.
* Rows written before the chain existed are reported as `unchained_legacy_rows`;
  they cannot be retroactively proven.
* **Row-level security was not added.** Tenant isolation is schema-per-tenant;
  RLS on the few shared tables would need `SET app.company_id` threaded through
  every request path and cannot be validated safely without the full suite as a
  safety net. It remains a recommendation.

---

## 4. Attendance integrity

**Problem.** A face check-in compared any uploaded JPEG's encoding to the
registered one: a photo of the employee passed, a captured request could be
resent, GPS was client-supplied, a client could skip the geofence by omitting
coordinates, and no evidence of a punch was kept. The landing page advertised
"Liveness Detection: Active".

**What changed** (`utils/liveness.py`, `utils/punch_integrity.py`, hooks in
`blueprints/attendance.py` and `blueprints/employee_portal.py`, web + mobile clients)

| Layer | Detail |
|---|---|
| Challenge | `POST /api/attendance/challenge` issues a single-use, employee-bound, 2-minute nonce and a **server-chosen** action (blink / turn head / smile). Nonce stored hashed. |
| Liveness | The client submits ~12 small frames; the server measures eye-aspect-ratio, nose-vs-eye-line parallax and mouth width across them, requires the *requested* action, rejects multi-face frames, and requires the frames to match the registered face. |
| Replay | Exact SHA-256 of the submitted photo checked against 90 days of evidence (blocked); 256-bit difference hash near-duplicates are flagged. |
| Environment | Android `mocked` flag refused (`MOCK_LOCATION_POLICY`); optional per-company office public-IP allow-list (flag-only if the IP is unverifiable). |
| Missing location | `/api/employee/checkin`, `/api/attendance/checkin` and offline sync no longer skip the geofence when coordinates are omitted. A rejected online punch is no longer parked in the offline queue. |
| Evidence | One append-only `punch_evidence` row per attempt (channel, device id, IP, coordinates, liveness verdict, photo fingerprints, flags, outcome). HR review queue: `GET /api/admin/attendance/flagged`. |

Settings: `LIVENESS_MODE=off|warn|enforce` (default `warn`),
`PHOTO_REPLAY_MODE=off|flag|block` (default `block`),
`MOCK_LOCATION_POLICY=flag|block` (default `block`),
`MOBILE_REQUIRE_LOCATION` (default `true`). Office networks:
`POST /api/admin/attendance/office_networks {"cidrs":[…],"require":true}`.

**Roll-out.** Ship with `warn`; watch `attendance.liveness_*` events and the
flagged queue; release the updated mobile app; then set `LIVENESS_MODE=enforce`.
Thresholds (`utils/liveness.py`) are conservative defaults chosen from published
ranges — tune them against real kiosk captures before enforcing.

**What this does *not* stop** — say so to anyone who relies on it:
* A **pre-recorded video** of the real employee performing the *requested*
  action, replayed to the camera. Randomising the action raises the cost; it is
  not presentation-attack-detection hardware.
* A **real-time deepfake**, or a client that hooks the camera/location APIs.
  `mocked` and `device_id` are client-reported and are trivially omitted by a
  modified client.
* **Device attestation (Play Integrity / App Attest) was not built.** It needs
  Google/Apple credentials and server-side verification that I can't validate in
  this environment; shipping unverifiable crypto would be worse than naming the
  gap. It is the correct next control for the mobile channel.
* The landing-page "99.9%" figure is not backed by any measurement; remove or
  substantiate it.

---

## 5. Personal-data lifecycle and key management

**What changed** (`utils/privacy.py`, `blueprints/privacy.py`, `migrations/column_migrations.py`)

* **Retention.** Daily job purges check-in face photos (`FACE_LOG_RETENTION_DAYS`,
  30), punch evidence (`PUNCH_EVIDENCE_RETENTION_DAYS`, 180) and spent challenges.
* **Subject access.** `GET /api/employee/privacy/export` (own data, decrypted,
  secrets omitted) and `GET /api/admin/privacy/employee/<id>/export` (audited).
* **Erasure.** `POST /api/admin/privacy/employee/<id>/erase {"confirm":"<id>"}`
  (admin role only, refuses administrator accounts): deletes biometric files,
  documents, tokens, push tokens, evidence; anonymises the employee row and
  disables login; keeps attendance/payroll rows required for statutory
  retention. Returns a manifest. Employees can *request* erasure
  (`/api/employee/privacy/erasure_request`).
* **Consent ledger.** Append-only `consent_records`
  (`/api/employee/consent`). Enforcement `BIOMETRIC_CONSENT_REQUIRED` is **off**
  by default because no consent screen exists in the clients yet — build the
  screen, then enable it.
* **Key rotation.** `ENCRYPTION_KEY` encrypts; `ENCRYPTION_KEY_PREVIOUS`
  (comma-separated) only decrypts. `scripts/rotate_encryption_key.py` finds
  every Fernet token in every text column and re-encrypts it (dry-run first).
* **Backups** (`scripts/backup_db.sh`, `scripts/restore_test.sh`). Encrypted to a
  *public* key (`BACKUP_ENCRYPTION=gpg|age`) so the host can write but not read
  history; SHA-256 sidecar; offsite copy (`BACKUP_REMOTE=s3://…|rclone:…`);
  refuses plaintext unless `BACKUP_ALLOW_PLAINTEXT=1`. `restore_test.sh`
  decrypts on a trusted machine, restores into a scratch DB, recomputes the audit
  chain. Run it on a schedule — an untested backup is a hope.

**Limits.** Retention windows and the "kept" list are policy decisions; have
counsel confirm them for your jurisdictions (DPDP Act 2023, GDPR Art. 9). The
erasure routine blanks known free-text columns (`reason`, `subject`,
`description`, `message`) but cannot know about free text added later. Backups
taken before this change are plaintext.

---

## 6. Detection engineering

`utils/detections.py` correlates what the platform already records; it runs
every `DETECTIONS_INTERVAL_MINUTES` (5). ERROR-level rules page through the
existing webhook/email alerting; all persist to `security_events` (SOC
dashboard). Duplicate suppression is a per-(rule, key) cooldown claimed with an
atomic upsert, so every gunicorn worker can schedule it.

| Rule | Fires when |
|---|---|
| `credential_stuffing` | one IP fails against ≥5 accounts / ≥8 attempts in 10 min |
| `distributed_bruteforce` | one account fails from ≥5 IPs in 10 min |
| `tenant_boundary_probe` | ≥3 cross-tenant session mismatches in 15 min |
| `privilege_probe` | ≥5 role-denied (ERROR) requests by one identity in 15 min |
| `recon_then_probe` | an address touched a honeypot **and then** hit WAF/auth failures |
| `waf_burst_distributed` | ≥50 WAF blocks from ≥5 IPs in 5 min |
| `pii_reveal_burst` | ≥20 masked-data reveals by one user in 10 min |
| `mass_export` | ≥5 export/download actions by one actor in 10 min |
| `sensitive_admin_change` | role / bank / payout / erase / payroll-unlock audit actions |
| `impossible_travel` | one employee's accepted punches ≥200 km apart within 60 min |
| `shared_device` | one device id punched for ≥3 employees in 24 h |
| `repeat_mock_location` / `repeat_liveness_failure` / `photo_replay` | repeated spoofing signals per employee |

Every threshold is an env var (`DETECT_*`). Loki rules for the dead-man's switch,
detections, honeypot hits and "non-routable client IP" are in
`observability/log-server/rules/hrzest-security.yaml`.

**Limits.** These are thresholds on telemetry, not a SIEM: they need tuning
against your baseline, and `security_events` is a single platform-level table
(written by a background thread with no tenant context).

---

## 7. Supply chain and deployment

* **Actions pinned by commit SHA** in all workflows (Dependabot keeps them
  current); least-privilege `permissions:`; the archived `semgrep-action` replaced
  by the CLI; `snyk/actions@master` pinned.
* **Build once, sign, deploy by digest** (`deploy.yml`): CI builds the image,
  pushes to GHCR, signs it keylessly (cosign/Sigstore), attaches a CycloneDX
  SBOM, and the host runs `scripts/deploy_verified_image.sh`, which refuses
  tag-addressed or unsigned images. The host no longer builds.
* **DAST.** `.github/workflows/dast.yml` — ZAP baseline against `STAGING_URL`
  (a repository variable; no-op until set). Never aim it at production.
* **Infrastructure that existed only in comments** is now code:
  `scripts/host_hardening.sh` (ufw + NAT redirects + podman auto-update/restart +
  rootless-network check); `deploy.sh` rewritten to match the real Podman stack.
  The `terraform/` directory cited by older comments is **not in this
  repository**; those comments were corrected, not the infrastructure invented.
* **Database fail-closed.** No silent SQLite fallback outside development.

**Limits / not done.** Base images are tag-pinned, not digest-pinned (nothing
bumps digests automatically for a `Containerfile`; Trivy scans every build
instead). The deploy path and `host_hardening.sh` could not be exercised
end-to-end from this environment — run them on a staging host first
(`--dry-run` is supported). The three overlapping CI workflows were pinned and
aligned, not merged, to avoid breaking required-status-check names.

---

## 8. Mobile and desktop agent

* The app now sends a per-install `device_id` and the `mocked` flag, and no
  longer queues a punch the server explicitly refused.
* **Not built:** certificate pinning and root/jailbreak detection need native
  modules and a rebuild/EAS cycle that can't be validated here; the web-build
  token fallback to `AsyncStorage` is unchanged (native builds use SecureStore).
* The Wi-Fi posture agent's score is client-asserted (relayed by the user's own
  browser). Treat `device.wifi_risk_reported` as telemetry, not a control; it can
  only ever terminate the reporter's own session.

---

## Verification performed (and what was not)

Executed here, against PostgreSQL 16, all passing:

| Suite | Result |
|---|---|
| `test_waf`, `test_client_ip_trust`, `test_redis_cache`, `test_rate_limit_defense`, `test_honeypot` | passed |
| `test_honeypot_isolation`, `test_audit_integrity`, `test_punch_integrity`, `test_route_auth_guards` | 112 passed |
| `test_detections`, `test_privacy`, `test_encryption_rotation`, `test_database_failclosed` | 57 passed |
| `test_liveness` | passed |
| 442-test subset (org, platform admin, auth, security events, helpers, onboarding, WAF, IP trust, honeypot) **as the restricted DB role** | passed |

Also: `flake8 .` clean; `bandit -ll -iii` (the CI gate) reports 0 issues; shell scripts
`bash -n` + behavioural checks with a stubbed `pg_dump` (plaintext refused, empty dump
aborts, opt-in works) and `host_hardening.sh --dry-run`; workflows YAML-parsed; changed
mobile/web JS syntax-checked (Babel/Node); `wsgi` imported and each new scheduled job run
once against the test database.

**Not done -- please treat as open:**

* **The complete pre-existing test suite has not been re-run end to end after these
  changes.** Only the suites above were. Areas most likely to need test updates are
  anything that posts to `/api/employee/checkin`, `/api/attendance/checkin` or
  `/api/employee/sync_punches` *without coordinates* (now rejected when the company
  location setting is on; `MOBILE_REQUIRE_LOCATION=false` restores the old behaviour),
  and tests that patch `blueprints.platform_admin.get_master_db` for tenant deletion.
  Run `pytest tests/` before merging.
* The mobile app was not built or run (no device/EAS); its Jest suite was not run
  (dependencies not installed here).
* A real camera against the liveness thresholds; the signed-deploy path and
  `host_hardening.sh` on a real host; the Loki rules in a running Loki.
* `tests/test_db_role_split.py` in split mode (see section 3).
