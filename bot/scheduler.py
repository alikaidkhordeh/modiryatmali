"""Background jobs (checked every 5 minutes, Tehran time):

  21:00 last day of the Jalali month   monthly report            (setting monthly_report)
  10:00 on the day of a reminder       "record this installment?" (recurring table)
  22:00                                 nudge if nothing recorded today (setting night_reminder, off by default)
  Friday 22:30                          database backup to the admin (setting weekly_backup)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .config import Settings
from .db import Database
from .jdate import is_last_day_of_month, month_key, month_length, to_jalali
from .reports import esc, month_report

log = logging.getLogger(__name__)

REPORT_HOUR = 21
RECURRING_HOUR = 10
NIGHT_HOUR = 22
BACKUP_HOUR, BACKUP_MINUTE = 22, 30
TICK = 300


def due_recurring_day(day: int, today_jday: int, length: int) -> bool:
    """A reminder for day 31 fires on the last day of a 30-day month (and so on)."""
    return min(day, length) == today_jday


async def _send(bot: Bot, uid: int, text: str, **kw) -> None:
    try:
        await bot.send_message(uid, text, **kw)
    except Exception as exc:  # user blocked the bot, network …
        log.warning("could not message %s: %s", uid, exc)


async def run_jobs(bot: Bot, settings: Settings, db: Database, now: datetime) -> None:
    key = month_key(now)
    jday = to_jalali(now)[2]
    stamp = now.strftime("%Y-%m-%d")

    for uid in settings.allowed_ids:
        # month-end report
        if now.hour >= REPORT_HOUR and is_last_day_of_month(now.date()):
            flag = f"report_sent_{key}_{uid}"
            if not db.meta_get(flag) and not db.meta_get(f"report_sent_{key}"):
                if db.setting(uid, "monthly_report") and db.month_expenses(uid, key):
                    await _send(bot, uid, "🗓 پایان ماه!\n\n" + month_report(db, uid, key, jday))
                db.meta_set(flag, now.isoformat())

        # installment / subscription reminders
        if now.hour >= RECURRING_HOUR:
            for r in db.recurring(uid):
                if r["last_month"] != key and due_recurring_day(r["day"], jday, month_length(key)):
                    db.mark_recurring(r["id"], key)
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✅ ثبت کن", callback_data=f"R:y:{r['id']}"),
                        InlineKeyboardButton(text="✖️ این ماه نه", callback_data=f"R:n:{r['id']}"),
                    ]])
                    await _send(bot, uid, f"🔁 <b>یادآور ماهانه</b>\n{esc(r['text'])}\nامروز روزش است؛ ثبت کنم؟",
                                reply_markup=kb)

        # gentle nudge
        if now.hour >= NIGHT_HOUR and db.setting(uid, "night_reminder"):
            flag = f"night_{stamp}_{uid}"
            if not db.meta_get(flag):
                db.meta_set(flag, "1")
                if not db.expenses_on_day(uid, stamp):
                    await _send(bot, uid, "🌙 امروز هزینه‌ای ثبت نکردی. چیزی جا نمانده؟ (همین‌جا بنویس)")

    # weekly backup (Friday) for the admin
    admin = settings.admin_id
    if admin and now.weekday() == 4 and (now.hour, now.minute) >= (BACKUP_HOUR, BACKUP_MINUTE):
        flag = f"backup_{stamp}"
        if not db.meta_get(flag):
            db.meta_set(flag, "1")
            if db.setting(admin, "weekly_backup"):
                try:
                    await bot.send_document(
                        admin, BufferedInputFile(db.backup_bytes(), filename=f"expenses-backup-{stamp}.db"),
                        caption="💾 بکاپ هفتگی دیتابیس (خاموش کردن: 🛠 پنل ← ⚙️ تنظیمات)")
                except Exception as exc:
                    log.warning("weekly backup failed: %s", exc)


async def scheduler_loop(bot: Bot, settings: Settings, db: Database) -> None:
    while True:
        try:
            await run_jobs(bot, settings, db, datetime.now(settings.tz))
        except Exception:  # never let the loop die
            log.exception("scheduled job failed")
        await asyncio.sleep(TICK)


monthly_report_loop = scheduler_loop  # old name
