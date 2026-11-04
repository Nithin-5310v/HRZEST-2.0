#!/usr/bin/env bash
# install_ec2_autodeploy.sh -- install or upgrade the pull-based auto-deploy
# (scripts/ec2_autodeploy.sh) on the EC2 host. Run it as the app user from
# the app checkout:
#
#   bash scripts/install_ec2_autodeploy.sh                      # keep the current mode
#   bash scripts/install_ec2_autodeploy.sh --production-branch  # deploy what Jenkins approved
#   bash scripts/install_ec2_autodeploy.sh --jenkins            # deploy only over SSH (older)
#
# Installs the script as /usr/local/bin/hrzest-autodeploy, adds the
# hrzest-autodeploy service and its 2-minute timer, and starts the timer.
# The mode is remembered in /etc/hrzest, so a reinstall (which a deploy does
# by itself when these files change) keeps it:
#
# --production-branch (/etc/hrzest/autodeploy.env: HRZEST_BRANCH=production):
#   every 2 minutes, deploy whatever commit the `production` branch on GitHub
#   points at. Jenkins moves that branch only after someone approves the
#   main build (ci/jenkins/deploy_prod.ps1), so this host needs no incoming
#   SSH to be deployed. The commit must also be on main.
# --jenkins (/etc/hrzest/jenkins-deploys): no timer; Jenkins runs
#   hrzest-autodeploy with HRZEST_DEPLOY_SHA over SSH.
# Neither: deploy origin/main every 2 minutes once its CI passes.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
jenkins_marker=/etc/hrzest/jenkins-deploys
env_file=/etc/hrzest/autodeploy.env

case "${1:-}" in
  --production-branch)
    sudo mkdir -p /etc/hrzest
    sudo rm -f "$jenkins_marker"
    echo "HRZEST_BRANCH=production" | sudo tee "$env_file" >/dev/null
    ;;
  --jenkins)
    sudo mkdir -p /etc/hrzest
    sudo rm -f "$env_file"
    sudo touch "$jenkins_marker"
    ;;
  "") ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

sudo install -m 755 "$here/ec2_autodeploy.sh" /usr/local/bin/hrzest-autodeploy
sudo install -m 644 "$here/hrzest-autodeploy.service" "$here/hrzest-autodeploy.timer" /etc/systemd/system/
sudo systemctl daemon-reload

# Host nginx hardening (scripts/nginx-hardening.conf: server_tokens off). Only
# ever applied if nginx accepts it: a config error here must never take the
# site down or stop the rest of this install, so every failure path puts the
# previous state back and carries on with a warning.
install_nginx_hardening() {
  local src="$here/nginx-hardening.conf" dest=/etc/nginx/conf.d/hrzest-hardening.conf
  local backup="" conf=""
  [ -f "$src" ] || return 0
  if ! command -v nginx >/dev/null 2>&1 || [ ! -d /etc/nginx/conf.d ]; then
    echo "nginx hardening: no host nginx with /etc/nginx/conf.d -- skipped"
    return 0
  fi
  if cmp -s "$src" "$dest"; then
    return 0
  fi
  if ! sudo nginx -t >/dev/null 2>&1; then
    echo "WARNING nginx hardening: nginx -t already fails before any change -- skipped" >&2
    return 0
  fi
  if [ -e "$dest" ]; then
    backup="$(mktemp)"
    sudo cp "$dest" "$backup"
  fi
  sudo install -m 644 "$src" "$dest"
  # Captured first, not piped into grep -q: under pipefail an early grep exit
  # SIGPIPEs nginx -T and the whole test would read as a failure.
  conf="$(sudo nginx -T 2>/dev/null)" || conf=""
  if sudo nginx -t >/dev/null 2>&1 && grep -qF "configuration file $dest" <<<"$conf" \
      && sudo systemctl reload nginx; then
    echo "nginx hardening: installed $dest and reloaded nginx"
    [ -z "$backup" ] || rm -f "$backup"
    return 0
  fi
  # Rejected (e.g. nginx.conf already sets server_tokens: "duplicate
  # directive"), not included by nginx.conf, or the reload failed.
  if [ -n "$backup" ]; then
    sudo install -m 644 "$backup" "$dest"
    rm -f "$backup"
  else
    sudo rm -f "$dest"
  fi
  echo "WARNING nginx hardening: nginx rejected $dest or doesn't include conf.d/ -- left nginx as it was." >&2
  echo "  Set 'server_tokens off;' in the http {} block of /etc/nginx/nginx.conf by hand, then:" >&2
  echo "  sudo nginx -t && sudo systemctl reload nginx" >&2
  return 0
}
install_nginx_hardening || true

if [ -e "$jenkins_marker" ]; then
  sudo systemctl disable --now hrzest-autodeploy.timer
  echo "Installed. Jenkins mode: the 2-minute timer is off; production deploys come from Jenkins."
  exit 0
fi
sudo systemctl enable --now hrzest-autodeploy.timer

if [ -e "$env_file" ]; then
  echo "Installed. Production-branch mode: deploys what the production branch points at (moved by Jenkins after approval)."
fi
echo "Installed. Next check:"
systemctl list-timers hrzest-autodeploy.timer --no-pager
