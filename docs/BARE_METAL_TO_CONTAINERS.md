# Moving a bare-metal host to the container stack

This guide is for a host that runs HRZEST directly: Gunicorn under systemd as a
login user (for example `kali`), with distro nginx in front using the root
`nginx.conf`. It moves that host to the deployment the rest of the repo
assumes: rootless Podman, `compose.yaml` + `compose.prod.yaml`, and
`nginx/nginx.conf.template`.

## Why move

The container stack already covers most of the infrastructure-hardening
recommendations:

| Recommendation | Bare metal today | Container stack |
| --- | --- | --- |
| Least-privilege service account | App runs as a login user | Containers run as `appuser` (`nologin`), rootless Podman runs as the `attendance` user |
| Rate limiting | Root `nginx.conf` (added Nov 2026) | `nginx/nginx.conf.template` (`login` and `general` zones) |
| TLS + certificate renewal | Manual `certbot --nginx` | `init-letsencrypt.sh` plus the `certbot` renewal container |
| Log rotation | Needs a `logrotate` entry on the host | Container logs capped at 3 × 10 MB per service |
| Restart / rollback | systemd restart only | `podman auto-update` rolls back when the healthcheck fails |
| Filesystem / capabilities | Full user access | Read-only root FS, `cap_drop: ALL`, `no-new-privileges` |
| Deploys | Manual SSH edits | `.github/workflows/deploy.yml` → `scripts/deploy_verified_image.sh` |

Unix sockets for Gunicorn are not part of this. Inside the stack nginx reaches
the app over the container network, so Gunicorn binds `0.0.0.0:5000` inside
its container and is not published on the host.

## Before you start

- **Distro.** `scripts/host_hardening.sh` targets Ubuntu/Debian. Kali is
  Debian-based and has `ufw` and `podman` in apt, but a general-purpose
  server distro is a better long-term choice for a production host.
- **DNS.** `hrzest.com` (and `www`, if used) must resolve to this host's
  public IP, and ports 80/443 must reach it from the internet. HTTP-01
  validation failed last time because of this. Check from **outside** the
  network: `dig +short hrzest.com` should print the host's public IP, and
  `curl -I http://hrzest.com` from another network should reach the host.
- **Maintenance window.** nginx moves from host ports 80/443 to the
  rootless container on 8080/8443 (with NAT redirects), so expect a short
  outage while you switch over.

## Steps

### 1. Back up

```bash
sudo -u kali bash scripts/backup_db.sh        # encrypted pg_dump, see the script header
sudo tar czf /root/hrzest-files-$(date +%F).tgz \
    -C /path/to/current/checkout .env private_uploads instance
```

Keep `.env` safe. Above all, `ENCRYPTION_KEY` must carry over unchanged, or
encrypted columns and uploads become unreadable.

### 2. Create the service account and checkout

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin attendance
sudo apt-get install -y podman podman-compose git curl
sudo git clone <repo-url> /opt/employee-attendance
sudo cp /path/to/current/checkout/.env /opt/employee-attendance/.env
sudo cp -a /path/to/current/checkout/private_uploads /opt/employee-attendance/
sudo chown -R attendance:attendance /opt/employee-attendance
sudo chmod 600 /opt/employee-attendance/.env
```

`init-letsencrypt.sh` expects `/opt/employee-attendance` and the
`attendance` user. If you change either, change it there too.

### 3. Decide where the database lives

`compose.prod.yaml` expects a managed database reached through `DB_HOST` in
`.env`, and `deploy.sh` does not start the local `db` service. Pick one:

- **Managed PostgreSQL (recommended).** Restore the backup from step 1 into
  it and set `DB_HOST`, `DB_USER`, `DB_PASS` and `DB_NAME` in `.env`.
- **Keep PostgreSQL on this host.** Set `DB_HOST=host.containers.internal`,
  make PostgreSQL listen on the Podman gateway address, and add a
  `pg_hba.conf` line for the Podman subnet only. Do not use `0.0.0.0/0`.

### 4. Stop the bare-metal services

```bash
sudo systemctl disable --now <your-gunicorn-unit(s)>   # e.g. hrzest@5001..5003
sudo systemctl disable --now nginx
```

Leave the old checkout in place until step 7 passes, so you can roll back.

### 5. Harden the host and start the stack

```bash
cd /opt/employee-attendance
sudo bash scripts/host_hardening.sh --user attendance --dry-run   # review first
sudo bash scripts/host_hardening.sh --user attendance
sudo -u attendance bash deploy.sh
```

`host_hardening.sh` sets ufw to default-deny, adds the 80→8080 and 443→8443
NAT redirects, enables lingering and `podman auto-update`, and reports
whether nginx will see real client IPs. If it says the network backend is
`slirp4netns`, switch to `pasta`. Otherwise every visitor shares a single
rate-limit bucket (see the comment above `limit_req_zone` in the template).

### 6. Get the certificate

```bash
sudo ./init-letsencrypt.sh hrzest.com <ops-email>
```

If HTTP-01 still fails after the DNS check above, switch to DNS-01 using
certbot's DNS plugin for your provider (for example `certbot-dns-cloudflare`
with a token limited to that one zone).

### 7. Verify

```bash
curl -fsS https://hrzest.com/healthz                       # {"status":"ok","db":"connected"}
curl -sI https://hrzest.com/ | grep -iE 'strict-transport|content-security'
for i in $(seq 1 30); do curl -s -o /dev/null -w '%{http_code}\n' https://hrzest.com/login; done | sort | uniq -c
```

The last command should show some `429`s once the burst is used up. Then log
in as an admin and an employee, and open an uploaded document to confirm
`private_uploads` and `ENCRYPTION_KEY` carried over.

### 8. Clean up

Once the stack has run cleanly for a few days:

- Delete the old systemd units and the old checkout.
- Lock the old login account if nothing else uses it: `sudo usermod -L kali`.
- Switch routine deploys to CI (`.github/workflows/deploy.yml`) instead of
  SSH edits.

## Rolling back

```bash
sudo -u attendance podman-compose -f compose.yaml -f compose.prod.yaml down
# delete the "# BEGIN hrzest port redirects" ... "# END hrzest port redirects"
# block from /etc/ufw/before.rules so ports 80/443 stop redirecting, then:
sudo ufw reload
sudo systemctl enable --now nginx <your-gunicorn-unit(s)>
```

## Staying on bare metal

If the move has to wait, the root `nginx.conf` now has login and general
rate limits and an `upstream` block (add one `server` line per Gunicorn
port). Two more things to do on the host:

- **Service account.** Run the Gunicorn units as a dedicated `nologin`
  user that owns the checkout, not as a login user. In the unit file, set
  `User=`/`Group=`, plus `NoNewPrivileges=yes`, `ProtectSystem=strict`,
  `ReadWritePaths=` for the upload and log directories, and `PrivateTmp=yes`.
- **Log rotation.** Distro nginx usually ships `/etc/logrotate.d/nginx`.
  Check that it exists. App logs go to journald, so cap them with
  `SystemMaxUse=` in `/etc/systemd/journald.conf`.
