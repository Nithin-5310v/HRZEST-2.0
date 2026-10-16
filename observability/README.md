# Self-hosted log analysis (Loki + Grafana)

> **Note (infrastructure code):** this guide and the config comments refer to a
> `terraform/observability.tf` that is **not in this repository**. Until that
> module is (re)added, provision the equivalents by hand: a log-server host
> with a security-group rule allowing port 3100 **only** from the application
> host's private address, an S3 bucket + instance role for Loki chunk storage,
> and no public exposure of Loki/Grafana without TLS or an authenticating
> proxy. Everything else below (compose files, Promtail/Loki config) is real.


A completely separate, self-hosted log pipeline for HRZEST — an independent
EC2 instance running Grafana + Loki, receiving logs shipped from the app
server by Promtail. Nothing here runs on the app server except Promtail
itself (a small, read-only sidecar container), so a heavy Grafana query or
a log storm can never compete with the HRMS app for CPU, memory, or disk.

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│  App server (existing EC2)  │         │  Log server (NEW EC2)        │
│                              │  HTTP   │                              │
│  app container ──stdout──▶  │ push    │  Loki :3100 ──▶ S3 (chunks) │
│  (JSON logs, unchanged)     │ :3100   │       │                      │
│                              │ ──────▶ │  Grafana :3000 ◀── you      │
│  promtail (reads via         │         │       (queries Loki)        │
│   docker socket, ships out)  │         │                              │
└─────────────────────────────┘         └──────────────────────────────┘
        security group:                          security group:
        outbound only needed                     3100 from app SG only
                                                   3000/22 from your IP only
```

## Why this shape

- **App logging stays exactly as it is.** `extensions.py` already writes
  one JSON object per log line to stdout via `python-json-logger` — that
  *is* the recommended pattern (see part 1 below). No code change needed.
- **Promtail, not Fluent Bit**, because it's the natural pairing with Loki
  (same label model, same LogQL-shaped pipeline stages) and needs no extra
  language runtime.
- **A second EC2 instance, not a container bolted onto the app's own
  compose stack**, so log analysis genuinely can't affect HRMS performance
  — the explicit ask.
- **S3 for storage, not local disk**, because Loki's chunk data grows
  indefinitely and S3 is both cheaper and doesn't risk filling the log
  server's own root volume.

---

## 1. Application logging (already done — here's why it's correct)

`extensions.py` already does this:

```python
from pythonjsonlogger import jsonlogger

_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(levelname)s %(module)s %(message)s",
    rename_fields={"asctime": "time", "levelname": "level"},
))
app_log = logging.getLogger("attendance")
app_log.addHandler(_log_handler)
```

Producing lines like:

```json
{"time": "2026-09-15 10:22:41,102", "level": "INFO", "module": "database", "message": "Connected to PostgreSQL"}
```

This is the correct pattern for a containerized app, for three reasons:

1. **Log to stdout, never to a file.** The app container runs
   `read_only: true` (see `compose.yaml`) — there's no writable filesystem
   to log a file to even if you wanted to, and it means the app doesn't
   need to know or care where its logs end up. The container runtime
   (Docker/Podman) already captures stdout to disk with rotation
   (`logging: driver: json-file, max-size: "10m", max-file: "3"` — also
   already in `compose.yaml`).
2. **One JSON object per line, not human-formatted text.** A log shipper
   parsing `2026-09-15 10:22:41 INFO Connected to PostgreSQL` has to guess
   field boundaries with regex; parsing JSON is exact and free. This is
   the single highest-leverage decision in a logging setup — get the
   format right at the source and every tool downstream (Promtail, Loki,
   Grafana, `grep`) becomes trivial.
3. **`utils/dlp.py`'s masking already runs before data reaches a
   response** — but that's masking for *HTTP responses*, not for *log
   lines*. This matters: **never `app_log.info()` a raw PII field**
   (aadhar, PAN, bank account, salary) even at DEBUG level. Loki has no
   field-level encryption or redaction — once something is logged, it's
   in Grafana in plaintext to anyone with dashboard access. If you ever
   need to log an employee record for debugging, log the employee ID, not
   the record.

**One optional enhancement**, not required but worth doing before you rely
on this for real triage: add a `request_id` to every log line within one
request, so you can pull the full story of a single failed request out of
Loki instead of guessing which lines belong together under load. Flask's
`g` object plus a `before_request`/`after_request` pair is the standard
way — out of scope for this guide since it's a separate, small change to
`app.py`, not part of the logging pipeline itself.

---

## 2. Log shipper: Promtail (runs on the APP server)

Files: [`promtail/promtail-config.yaml`](promtail/promtail-config.yaml),
[`promtail/compose.promtail.yml`](promtail/compose.promtail.yml).

**Why Docker/Podman service-discovery instead of tailing a file path:**
the app container has no log *file* at all (see above) — its logs exist
only as the container runtime's captured stdout. Promtail's
`docker_sd_configs` talks to the Docker-compatible API socket, discovers
the running `app` container (matched by the `com.docker.compose.service`
label Compose sets automatically — robust to container name/project name
changes), and tails its output directly. This also means Promtail needs
**zero changes** whenever the app container is rebuilt/redeployed — it
re-discovers it automatically.

### One-time host setup

- **Docker:** nothing extra, `/var/run/docker.sock` already exists.
- **Rootless Podman** (this repo's `compose.yaml` comments indicate
  production runs rootless Podman):
  ```bash
  systemctl --user enable --now podman.socket
  ```
  Then in `compose.promtail.yml`, comment out the Docker socket mount and
  uncomment the Podman one (already marked in the file).

### Deploy

```bash
# On the app server, from the repo root:
docker compose -f compose.yaml -f compose.prod.yaml \
  -f observability/promtail/compose.promtail.yml up -d --no-deps promtail
```

Before that, edit `observability/promtail/promtail-config.yaml` and
replace `LOKI_SERVER_PRIVATE_IP` with the log server's actual private IP
(Terraform outputs this as `log_server_private_ip` — see part 3).

**Why only `level` and `security_event` become Loki labels**, not every
field: Loki's index is keyed by label, and a label with high cardinality
(one value per user, per IP, per request path) makes that index itself
the bottleneck — the exact opposite of Loki's design, where the *log body*
is cheap to store and only labels need to be selective. `identifier`,
`ip`, `path`, `module`, etc. are still fully present in every log line's
JSON and queryable in Grafana via LogQL's `| json` pipe — just not
pre-indexed. Example query once this is running:

```logql
{app="hrms", level="ERROR"} | json | line_format "{{.message}}"
```

or, to find everything for one security event type:

```logql
{app="hrms", security_event="true"} | json | event_type="auth.failure"
```

---

## 3. Storage engine: Grafana + Loki (runs on the NEW log server)

Files: [`log-server/compose.yml`](log-server/compose.yml),
[`log-server/loki-config.yaml`](log-server/loki-config.yaml),
[`log-server/grafana-provisioning/`](log-server/grafana-provisioning/),
[`../terraform/observability.tf`](../terraform/observability.tf).

### 3a. Provision the AWS side (Terraform)

`terraform/observability.tf` adds, alongside your existing `terraform/`
config:

- An **S3 bucket** for Loki's chunks/index (private, encrypted, lifecycle
  rule as a backstop behind Loki's own retention).
- An **IAM role** scoped to read/write only that one bucket — the log
  server authenticates to S3 via its instance profile, the same pattern
  `main.tf`'s `ec2_app_role` already uses for the app server's own backups
  bucket. No static AWS keys anywhere.
- A **security group** — see part 4 below.
- The **EC2 instance** itself (`t3.small` by default — resize via
  `log_server_instance_type` if Grafana ever feels slow; unlikely for a
  single small app's log volume).

```bash
cd terraform
# New variable this file adds — see its description in observability.tf
# for why it must be a PUBLIC subnet, unlike var.subnet_ids:
terraform apply -var="log_server_subnet_id=subnet-xxxxxxxx"
terraform output log_server_private_ip   # -> promtail-config.yaml
terraform output log_server_public_ip    # -> your own SSH/browser access
```

### 3b. Install Docker and run the stack

The instance's `user_data` already installs Docker + Compose on first
boot. From your own machine:

```bash
scp -r observability/log-server ec2-user@<log_server_public_ip>:~/log-server
ssh ec2-user@<log_server_public_ip>
cd log-server
cp .env.example .env
nano .env   # fill in LOKI_S3_BUCKET (terraform output loki_s3_bucket),
            # AWS_REGION, and a real GRAFANA_ADMIN_PASSWORD
docker compose up -d
```

Open `http://<log_server_public_ip>:3000`, log in as `admin` with the
password you set — the Loki datasource is already provisioned, so
Explore → Loki works immediately with no manual setup.

**Why S3 and not local disk:** Loki's chunks grow with every log line
ever ingested. Local disk means babysitting free space on the log
server's own volume forever; S3 is pay-per-GB (a few cents/month for a
single HRMS app's log volume) and completely decoupled from the
instance's own storage.

---

## 4. Security: connecting the two servers inside the VPC

This is the part that matters most given the data involved — HR system
logs can contain names, employee IDs, IPs, and (if anyone's careless
about part 1's warning) worse.

**The core control is the security group on the log server**
(`terraform/observability.tf`'s `aws_security_group.log_server`), not TLS
or application-level auth:

| Port | Source | Purpose |
|---|---|---|
| 3100 (Loki push) | **The app server's security group ID only** (`var.ec2_security_group_id`) — an SG-to-SG reference, not a CIDR | Nothing else on the internet, or even elsewhere in the VPC, can reach Loki's ingest endpoint at all |
| 3000 (Grafana UI) | `var.trusted_admin_cidrs` | Same restriction your app server's own SSH/RDP/DB ports already use (`main.tf`'s `app_firewall`) |
| 22 (SSH) | `var.trusted_admin_cidrs` | Same |
| everything else | *(no rule)* | Security groups default-deny — this is what "closed" means |

This SG-to-SG pattern (`security_groups = [var.ec2_security_group_id]`
instead of a CIDR block) is exactly what `main.tf`'s RDS security group
already does for the app server's database access — it's the strongest
practical control available: Loki's ingest port is invisible to anything
that isn't literally an instance with that exact security group attached,
regardless of what IP it has or which subnet it's in.

**Why plain HTTP between the two servers is acceptable here, not a gap:**
the traffic never leaves your VPC's private address space (Promtail's
`clients.url` points at the log server's *private* IP, never a public
one) and the security group above means nothing else can even open a
connection to that port. This is the same trust model this codebase
already applies to its own app↔RDS traffic. If you want defense-in-depth
beyond that, put a TLS-terminating reverse proxy (nginx/Caddy) in front of
Loki's port with a self-signed or private-CA cert — a reasonable next
step, not a requirement to get this working securely.

**Two things NOT to do**, both easy mistakes with this stack:
- Don't expose Grafana's UI (3000) to `0.0.0.0/0` "just for now" — it has
  read access to every log line ever shipped, including anything logged
  under part 1's DLP warning.
- Don't put a NAT/public IP in front of Loki's port 3100 for convenience.
  There's no legitimate reason for anything outside the app server's own
  security group to ever reach it.

---

## Verifying end to end

1. `docker compose logs -f promtail` on the app server — should show no
   connection errors to the log server.
2. Trigger a log line (any request against the app, or restart the app
   container to see its startup logs).
3. In Grafana, Explore → Loki → run `{app="hrms"}` — the line should
   appear within a few seconds.

---

## Migrating from Promtail to Grafana Alloy

**Promtail is end-of-life** (Grafana ended support in March 2026 and points to Alloy as the
replacement), so the `grafana/promtail:3.3.2` sidecar no longer receives security fixes.
`observability/alloy/` is a like-for-like replacement:

| | Promtail | Alloy |
|---|---|---|
| Config | `observability/promtail/promtail-config.yaml` | `observability/alloy/config.alloy` |
| Compose | `observability/promtail/compose.promtail.yml` | `observability/alloy/compose.alloy.yml` |

The Alloy config was checked with `alloy fmt` and `alloy validate` (v1.20.1) and compared against
Alloy's own `alloy convert --source-format=promtail` output of the old config: same container
filter, relabel, JSON fields, labels (`level`, `security_event`, `app`, `env`) and Loki endpoint.
It has **not** been run against a live Docker/Podman socket and Loki here.

Cutover (no log gap needed beyond a few seconds):
1. Put your Loki address into `observability/alloy/config.alloy` (`LOKI_SERVER_PRIVATE_IP`).
2. `podman-compose -f compose.yaml -f observability/alloy/compose.alloy.yml up -d alloy`
3. In Grafana, confirm new lines arrive with `{app="hrms"}` and the same labels; the
   `AuditAnchorsStopped` alert in `log-server/rules/hrzest-security.yaml` will fire if anchors
   stop flowing, which is your safety net.
4. Stop and remove the Promtail container. Keep the old files for rollback until you're satisfied.

Loki and Grafana were also moved to currently patched lines (Loki 3.6.18, Grafana 12.4.13;
config format is unchanged within Loki 3.x). Roll the log server first, then the sidecar.
