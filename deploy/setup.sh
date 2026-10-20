#!/usr/bin/env bash
# One-time setup inside the project folder: virtualenv + dependencies + .env template.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 یا بالاتر لازم است. نسخهٔ فعلی: $(python3 --version)"
  exit 1
fi

python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "فایل .env ساخته شد. حالا آن را ویرایش کن:  nano .env"
fi
echo "نصب تمام شد."
