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
