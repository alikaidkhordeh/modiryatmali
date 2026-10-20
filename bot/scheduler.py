"""Background job: on the last day of every Jalali month (21:00) send the monthly report."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot

from .config import Settings
from .db import Database
from .jdate import is_last_day_of_month, month_key, to_jalali
from .reports import month_report

log = logging.getLogger(__name__)

REPORT_HOUR = 21


async def monthly_report_loop(bot: Bot, settings: Settings, db: Database) -> None:
    while True:
        try:
            now = datetime.now(settings.tz)
            if now.hour >= REPORT_HOUR and is_last_day_of_month(now.date()):
                key = month_key(now)
                flag = f"report_sent_{key}"
                if not db.meta_get(flag):
                    for uid in settings.allowed_ids:
                        if db.month_expenses(uid, key):
                            text = "🗓 پایان ماه!\n\n" + month_report(db, uid, key, to_jalali(now)[2])
                            await bot.send_message(uid, text)
                    db.meta_set(flag, now.isoformat())
        except Exception:  # never let the loop die
            log.exception("monthly report failed")
        await asyncio.sleep(900)
