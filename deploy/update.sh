#!/usr/bin/env bash
# Pull the newest code from GitHub and restart the bot. Your .env and data/ are never touched.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git pull --ff-only
venv/bin/pip install -r requirements.txt -q
sudo systemctl restart modiryatmali
sleep 2
sudo systemctl status modiryatmali --no-pager | head -n 8
