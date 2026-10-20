"""Run with:  python -m bot.main"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .categorizer import Categorizer
from .config import load_settings
from .db import Database
from .handlers import build_routers
from .scheduler import monthly_report_loop

COMMANDS = [
    ("today", "هزینه‌های امروز"),
    ("month", "گزارش این ماه"),
    ("lastmonth", "گزارش ماه قبل"),
    ("app", "داشبورد با نمودار"),
    ("top", "بزرگ‌ترین هزینه‌ها"),
    ("person", "هزینه به تفکیک افراد"),
    ("last", "۵ هزینهٔ آخر (ویرایش/حذف)"),
    ("undo", "حذف آخرین هزینه"),
    ("export", "خروجی CSV"),
    ("help", "راهنما"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("bot")

    settings = load_settings()
    if not settings.allowed_ids:
        log.warning("ALLOWED_USER_IDS خالی است؛ ربات فقط شناسهٔ عددی کاربر را نشان می‌دهد.")

    db = Database(settings.db_path)
    categorizer = Categorizer(db, settings.anthropic_api_key, settings.anthropic_model)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    main_router, guest_router = build_routers(settings, db, categorizer)
    dp.include_router(main_router)
    dp.include_router(guest_router)

    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    scheduler_task = asyncio.create_task(monthly_report_loop(bot, settings, db))
    try:
        log.info("Bot started.")
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
