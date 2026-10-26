#!/usr/bin/env bash
# install_ec2_autodeploy.sh -- install or upgrade the pull-based auto-deploy
# (scripts/ec2_autodeploy.sh) on the EC2 host. Run it as the app user from
# the app checkout:
#
#   bash scripts/install_ec2_autodeploy.sh
#
# Installs the script as /usr/local/bin/hrzest-autodeploy, adds the
# hrzest-autodeploy service and its 2-minute timer, and starts the timer.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

sudo install -m 755 "$here/ec2_autodeploy.sh" /usr/local/bin/hrzest-autodeploy
sudo install -m 644 "$here/hrzest-autodeploy.service" "$here/hrzest-autodeploy.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hrzest-autodeploy.timer

echo "Installed. Next check:"
systemctl list-timers hrzest-autodeploy.timer --no-pager
