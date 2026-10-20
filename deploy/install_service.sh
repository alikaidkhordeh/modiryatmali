#!/usr/bin/env bash
# Install and start the systemd service "modiryatmali" (runs the bot in the background, restarts on crash/reboot).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"

if [ ! -x "$DIR/venv/bin/python" ]; then
  echo "اول  bash deploy/setup.sh  را اجرا کن."; exit 1
fi
if ! grep -qE '^BOT_TOKEN=.+' "$DIR/.env"; then
  echo "BOT_TOKEN در .env خالی است."; exit 1
fi

sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" "$DIR/deploy/modiryatmali.service" \
  | sudo tee /etc/systemd/system/modiryatmali.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now modiryatmali
sleep 2
sudo systemctl status modiryatmali --no-pager || true
echo
echo "لاگ زنده:  sudo journalctl -u modiryatmali -f"
