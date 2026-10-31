#!/usr/bin/env bash
# install_ec2_autodeploy.sh -- install or upgrade the pull-based auto-deploy
# (scripts/ec2_autodeploy.sh) on the EC2 host. Run it as the app user from
# the app checkout:
#
#   bash scripts/install_ec2_autodeploy.sh            # deploy main every 2 minutes
#   bash scripts/install_ec2_autodeploy.sh --jenkins  # deploy only when Jenkins says so
#
# Installs the script as /usr/local/bin/hrzest-autodeploy, adds the
# hrzest-autodeploy service and its 2-minute timer, and starts the timer.
#
# --jenkins (remembered in /etc/hrzest/jenkins-deploys): production deploys
# come from Jenkins after approval (ci/jenkins/deploy_prod.ps1 runs
# hrzest-autodeploy with HRZEST_DEPLOY_SHA over SSH), so the timer stays off,
# including when a deploy reinstalls these files.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
jenkins_marker=/etc/hrzest/jenkins-deploys

if [ "${1:-}" = "--jenkins" ]; then
  sudo mkdir -p "$(dirname "$jenkins_marker")"
  sudo touch "$jenkins_marker"
fi

sudo install -m 755 "$here/ec2_autodeploy.sh" /usr/local/bin/hrzest-autodeploy
sudo install -m 644 "$here/hrzest-autodeploy.service" "$here/hrzest-autodeploy.timer" /etc/systemd/system/
sudo systemctl daemon-reload

if [ -e "$jenkins_marker" ]; then
  sudo systemctl disable --now hrzest-autodeploy.timer
  echo "Installed. Jenkins mode: the 2-minute timer is off; production deploys come from Jenkins."
  exit 0
fi
sudo systemctl enable --now hrzest-autodeploy.timer

echo "Installed. Next check:"
systemctl list-timers hrzest-autodeploy.timer --no-pager
