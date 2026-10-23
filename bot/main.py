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
from .handlers import build_routers, kb_reply
from .scheduler import scheduler_loop

VERSION = "2.0"
WHATS_NEW = (
    "🎉 <b>ربات به‌روز شد (نسخهٔ ۲)</b>\n\n"
    "• دکمه‌های برگشت درست شد و روی پیام‌های قدیمی هم کار می‌کنند\n"
    "• 🛠 <b>پنل مدیریت</b> در منوی پایین: دسته‌ها، بودجه، یادآور قسط، جستجو، کلمه‌ها، اشخاص، بکاپ، تنظیمات\n"
    "• 🎯 بودجهٔ ماه و هر دسته با هشدار ۸۰٪ و ۱۰۰٪\n"
    "• 🔁 یادآور ماهانه برای قسط و اجاره: روزش با یک دکمه ثبت می‌شود\n"
    "• «دیروز»، «دیشب»، «پریروز» یا «۳ روز پیش» را بنویسی، با همان تاریخ ثبت می‌شود\n"
    "• زیر هر هزینه: 📝 متن و 📅 تاریخ هم اضافه شد؛ حذف تأیید می‌خواهد\n"
    "• گزارش ماه: مقایسه با همین موقع ماه قبل و پیش‌بینی آخر ماه\n"
    "• داشبورد جدید: بودجه، پیش‌بینی، روزهای هفته و مقایسهٔ دسته‌ها\n"
    "• 💾 بکاپ هفتگی دیتابیس جمعه‌شب برای تو"
)

COMMANDS = [
    BotCommand(command="start", description="منوی اصلی"),
    BotCommand(command="panel", description="پنل مدیریت"),
    BotCommand(command="month", description="گزارش این ماه"),
    BotCommand(command="today", description="هزینه‌های امروز"),
    BotCommand(command="last", description="آخرین هزینه‌ها"),
    BotCommand(command="budget", description="بودجه"),
    BotCommand(command="find", description="جستجو در هزینه‌ها"),
    BotCommand(command="app", description="داشبورد نمودار"),
    BotCommand(command="cancel", description="لغو سؤال باز"),
]


async def announce_update(bot: Bot, settings, db: Database, log: logging.Logger) -> None:
    """Once per version: tell the users what changed and give them the new bottom keyboard."""
    if db.meta_get("announced_version") == VERSION:
        return
    for uid in settings.allowed_ids:
        try:
            await bot.send_message(uid, WHATS_NEW, reply_markup=kb_reply())
        except Exception as exc:  # the user has not started the bot yet, …
            log.warning("could not announce the update to %s: %s", uid, exc)
    db.meta_set("announced_version", VERSION)


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

    try:
        await bot.set_my_commands(COMMANDS)
    except Exception as exc:
        log.warning("set_my_commands failed: %s", exc)
    await announce_update(bot, settings, db, log)
    scheduler_task = asyncio.create_task(scheduler_loop(bot, settings, db))
    try:
        log.info("Bot %s started.", VERSION)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
