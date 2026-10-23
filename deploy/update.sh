#!/usr/bin/env bash
# Pull the newest code from GitHub and restart the bot. Your .env and data/ are never touched;
# a copy of the database is saved in data/backups/ first (the last 10 are kept).
# Everything is inside main() so bash reads the whole file before `git pull` replaces it.
set -euo pipefail

main() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.."

  if [ -f data/expenses.db ]; then
    mkdir -p data/backups
    cp data/expenses.db "data/backups/expenses-$(date +%F-%H%M).db"
    ls -1t data/backups/expenses-*.db | tail -n +11 | xargs -r rm --
    echo "بکاپ دیتابیس: data/backups/"
  fi

  git pull --ff-only
  venv/bin/pip install -r requirements.txt -q
  sudo systemctl restart modiryatmali
  sleep 3
  sudo systemctl status modiryatmali --no-pager | head -n 8 || true
  echo
  echo "آخرین لاگ‌ها:"
  sudo journalctl -u modiryatmali -n 5 --no-pager || true
}

main "$@"
exit
