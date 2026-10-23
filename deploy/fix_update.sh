#!/bin/sh
# One-shot repair + update on the server (use it when `deploy/update.sh` did not change the bot).
#   curl -fsSL https://raw.githubusercontent.com/alikaidkhordeh/modiryatmali/main/deploy/fix_update.sh -o /tmp/mm_fix.sh && sh /tmp/mm_fix.sh
# What it does (other bots on the server are never touched):
#   1. finds the folder the systemd service really runs from
#   2. backs up the database
#   3. brings the code to GitHub main (local edits are kept in `git stash`, old code in branch backup-before-update)
#   4. installs requirements and checks that the new code imports; if not, it rolls back
#   5. stops stray copies of THIS bot running outside the service (they answer with the old code)
#   6. restarts the service and prints a short report (version, errors, "another copy uses this token")
# POSIX sh on purpose: runs with sh, dash or bash.

SERVICE="${SERVICE:-modiryatmali}"
REPO_URL="https://github.com/alikaidkhordeh/modiryatmali.git"

say() { printf '\n== %s\n' "$*"; }
ok()  { printf '   OK  %s\n' "$*"; }
bad() { printf '   !!  %s\n' "$*"; }

main() {
  ME="$(id -un)"
  SUDO=""
  [ "$ME" != "root" ] && SUDO="sudo"

  say "1) پیدا کردن پوشهٔ ربات"
  UNIT_DIR="$(systemctl show -p WorkingDirectory --value "$SERVICE" 2>/dev/null)"
  DIR=""
  for c in "$UNIT_DIR" "$HOME/bots/modiryatmali" "$HOME/modiryatmali" "$(pwd)"; do
    if [ -n "$c" ] && [ -f "$c/bot/main.py" ] && [ -d "$c/.git" ]; then DIR="$c"; break; fi
  done
  if [ -z "$DIR" ]; then
    found="$(find "$HOME" /opt /srv -maxdepth 5 -type f -path '*/bot/categorizer.py' 2>/dev/null | head -n 1)"
    [ -n "$found" ] && DIR="$(dirname "$(dirname "$found")")"
  fi
  if [ -z "$DIR" ]; then
    bad "پوشهٔ ربات پیدا نشد. داخل پوشهٔ ربات برو (cd …/modiryatmali) و دوباره همین دستور را بزن."
    return 1
  fi
  DIR="$(cd "$DIR" && pwd -P)"
  cd "$DIR" || return 1
  OWNER="$(stat -c %U "$DIR" 2>/dev/null || echo "$ME")"
  ok "پوشه: $DIR  (مالک: $OWNER)"
  if [ -n "$UNIT_DIR" ] && [ "$UNIT_DIR" != "$DIR" ]; then
    bad "سرویس از پوشهٔ «$UNIT_DIR» اجرا می‌شود ولی آن پوشه کد ربات ندارد."
  fi
  BEFORE="$(run git log -1 --format='%h %s' 2>/dev/null)"
  ok "کد فعلی: $BEFORE"

  say "2) بکاپ دیتابیس"
  DB="$(grep -E '^DB_PATH=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r" ')"
  [ -z "$DB" ] && DB="data/expenses.db"
  if [ -f "$DB" ]; then
    run mkdir -p data/backups
    BK="data/backups/expenses-$(date +%F-%H%M%S)-before-update.db"
    if run cp "$DB" "$BK"; then ok "$BK"; else bad "کپی دیتابیس نشد"; fi
  else
    bad "دیتابیس ($DB) پیدا نشد؛ اگر ربات تازه نصب شده مشکلی نیست."
  fi

  say "3) گرفتن کد جدید از گیت‌هاب"
  if run git fetch -q origin main 2>/dev/null; then
    TARGET="origin/main"
  elif run git fetch -q "$REPO_URL" main; then
    TARGET="FETCH_HEAD"
  else
    bad "اتصال به گیت‌هاب نشد (اینترنت/DNS سرور را چک کن)."
    return 1
  fi
  if [ -n "$(run git status --porcelain --untracked-files=no)" ]; then
    run git stash push -q -m "local-changes-before-update-$(date +%F)" && ok "تغییرات دستی قبلی در git stash نگه داشته شد"
  fi
  run git branch -f backup-before-update HEAD >/dev/null 2>&1
  if run git checkout -q -B main "$TARGET"; then
    [ "$TARGET" = "origin/main" ] && run git branch -q --set-upstream-to=origin/main main 2>/dev/null
    AFTER="$(run git log -1 --format='%h %s')"
    ok "کد جدید: $AFTER"
  else
    bad "عوض کردن کد نشد (خروجی بالا را بفرست)."
    return 1
  fi

  say "4) نصب پیش‌نیازها و تست اجرای کد"
  if [ ! -x venv/bin/python ]; then
    bad "venv نبود؛ ساختمش"
    run python3 -m venv venv || return 1
  fi
  run venv/bin/pip install -q -r requirements.txt || bad "pip خطا داد (بالا را ببین)"
  VERSION="$(run venv/bin/python -c 'import bot.handlers, bot.scheduler, bot.main as m; print(m.VERSION)' 2>/tmp/mm_import_err)"
  if [ -n "$VERSION" ]; then
    ok "کد جدید بدون خطا بارگذاری شد (نسخه $VERSION)"
  else
    bad "کد جدید روی این سرور اجرا نشد؛ برمی‌گردم به نسخهٔ قبلی تا ربات خاموش نماند. این خطا را برایم بفرست:"
    tail -n 15 /tmp/mm_import_err
    run git checkout -q -B main backup-before-update
    $SUDO systemctl restart "$SERVICE" 2>/dev/null
    return 1
  fi

  say "5) خاموش کردن نسخه‌های اضافهٔ همین ربات (بیرون از سرویس)"
  MAINPID="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null)"
  STRAY=0
  for pid in $(pgrep -f 'bot[.]main' 2>/dev/null); do
    [ "$pid" = "$MAINPID" ] && continue
    cwd="$($SUDO readlink -f "/proc/$pid/cwd" 2>/dev/null)"
    if [ "$cwd" = "$DIR" ]; then
      $SUDO kill "$pid" 2>/dev/null && STRAY=$((STRAY + 1)) && bad "نسخهٔ اضافه با PID $pid خاموش شد (همین جواب قدیمی می‌داد)"
    fi
  done
  [ "$STRAY" = 0 ] && ok "نسخهٔ اضافه‌ای روشن نبود"

  say "6) ریستارت سرویس"
  STARTED="$(date '+%Y-%m-%d %H:%M:%S')"
  if systemctl cat "$SERVICE" >/dev/null 2>&1; then
    $SUDO systemctl restart "$SERVICE"
  else
    bad "سرویس $SERVICE نصب نبود؛ نصبش می‌کنم"
    run bash deploy/install_service.sh
  fi
  sleep 12
  LOG="$($SUDO journalctl -u "$SERVICE" --since "$STARTED" --no-pager 2>/dev/null)"

  say "گزارش"
  echo "   قبل:  $BEFORE"
  echo "   بعد:  $AFTER"
  if systemctl is-active --quiet "$SERVICE"; then ok "سرویس روشن است"; else bad "سرویس روشن نیست"; fi
  if printf '%s' "$LOG" | grep -q "Bot $VERSION started"; then ok "ربات نسخهٔ $VERSION بالا آمد"; fi
  if printf '%s' "$LOG" | grep -qiE 'conflict|terminated by other getUpdates'; then
    bad "یک نسخهٔ دیگر از همین ربات با همین توکن جای دیگری روشن است (سرور دیگر یا کامپیوتر)."
    bad "تا آن خاموش نشود، گاهی آن نسخهٔ قدیمی جواب می‌دهد. خاموشش کن یا در BotFather توکن را Revoke کن."
  fi
  if printf '%s' "$LOG" | grep -qE 'Traceback|ERROR'; then
    bad "در لاگ خطا هست؛ این چند خط را برایم بفرست:"
    printf '%s\n' "$LOG" | tail -n 20
  fi
  echo
  echo "   حالا در تلگرام به ربات /start بزن تا منوی جدید (🛠 پنل مدیریت) بیاید."
}

# run a command as the owner of the bot folder (so git/pip never break file permissions)
run() {
  if [ "$OWNER" = "$ME" ]; then "$@"; else sudo -u "$OWNER" -H "$@"; fi
}

main "$@"
exit $?
