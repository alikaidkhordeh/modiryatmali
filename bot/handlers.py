"""Telegram handlers (aiogram 3)."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from typing import Dict, Optional, Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from .categories import CATEGORIES, EMOJI
from .categorizer import Categorizer
from .config import Settings
from .db import Database
from .jdate import month_key, month_label, parse_month_key, prev_month_key, to_jalali
from .parser import parse_amount
from .reports import day_report, esc, expense_line, month_report, person_report, top_report
from .textutil import fa_digits, fmt_amount, normalize
from .webapp import build_payload, build_url

log = logging.getLogger(__name__)

HELP = (
    "👋 <b>ربات مدیریت هزینه</b>\n\n"
    "فقط هزینه را کوتاه بنویس، خودم مبلغ و دسته را تشخیص می‌دهم:\n"
    "<code>بنزین ۵۰ تومن</code>\n"
    "<code>قسط ماشین ۲ میلیون</code>\n"
    "<code>شام با خانمم ۳۸۰</code>\n"
    "چند هزینه را هم می‌توانی در چند خط بفرستی.\n\n"
    "💡 عدد زیر ۱۰۰۰ یعنی هزار تومان (۳۸۰ = ۳۸۰٬۰۰۰ تومان). "
    "«میلیون»، «هزار» و «ریال» هم فهمیده می‌شود.\n\n"
    "<b>دستورها</b>\n"
    "/today — هزینه‌های امروز\n"
    "/month — گزارش این ماه (مرتب بر اساس بیشترین مصرف)\n"
    "/lastmonth — گزارش ماه قبل\n"
    "/top — بزرگ‌ترین هزینه‌های ماه\n"
    "/person — جمع هزینه‌ها به تفکیک افراد (یا <code>/person خانمم</code>)\n"
    "/app — داشبورد با نمودار\n"
    "/last — ۵ هزینهٔ آخر (با دکمهٔ ویرایش و حذف)\n"
    "/undo — حذف آخرین هزینه\n"
    "/export — فایل اکسل (CSV) همهٔ هزینه‌ها\n"
    "/addword — یاد دادن کلمه: <code>/addword کافی‌نت آموزش</code>\n"
    "/addperson — اسم شخص: <code>/addperson سارا سارا</code>\n"
    "/cats — فهرست دسته‌ها"
)


# ---------- keyboards ----------------------------------------------------
def kb_main(exp_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏷 دسته", callback_data=f"c:{exp_id}"),
        InlineKeyboardButton(text="✏️ مبلغ", callback_data=f"a:{exp_id}"),
        InlineKeyboardButton(text="🗑 حذف", callback_data=f"d:{exp_id}"),
    ]])


def kb_categories(exp_id: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for idx, name in enumerate(CATEGORIES):
        row.append(InlineKeyboardButton(text=f"{EMOJI[name]} {name}", callback_data=f"s:{exp_id}:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="↩️ برگشت", callback_data=f"b:{exp_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card(e, title: str = "✅ ثبت شد") -> str:
    lines = [title, f"{EMOJI.get(e['category'], '📦')} <b>{esc(e['category'])}</b> · <b>{fmt_amount(e['amount'])}</b> تومان"]
    if e["note"]:
        lines.append(f"📝 {esc(e['note'])}")
    if e["person"]:
        lines.append(f"👤 {esc(e['person'])}")
    return "\n".join(lines)


# ---------- router -------------------------------------------------------
def build_routers(settings: Settings, db: Database, cat: Categorizer) -> Tuple[Router, Router]:
    main = Router(name="main")
    guest = Router(name="guest")
    allowed = settings.allowed_ids

    main.message.filter(F.from_user.id.in_(allowed))
    main.callback_query.filter(F.from_user.id.in_(allowed))
    guest.message.filter(~F.from_user.id.in_(allowed))

    # user_id -> (expense id, message id of its card) while waiting for a new amount
    pending: Dict[int, Tuple[int, int]] = {}

    def now() -> datetime:
        return datetime.now(settings.tz)

    def current_key() -> str:
        return month_key(now())

    def dashboard_markup(uid: int, key: str) -> Optional[InlineKeyboardMarkup]:
        if not settings.webapp_url:
            return None
        n = now()
        upto = to_jalali(n)[2] if key == month_key(n) else None
        url = build_url(settings.webapp_url, build_payload(db, uid, key, upto))
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📊 داشبورد با نمودار", web_app=WebAppInfo(url=url))
        ]])

    def pick_month(args: Optional[str]) -> str:
        if args:
            a = normalize(args)
            if a in ("prev", "last", "قبل", "قبلی", "ماه قبل"):
                return prev_month_key(current_key())
            parsed = parse_month_key(a)
            if parsed:
                return parsed
        return current_key()

    # ----- guests ---------------------------------------------------------
    @guest.message()
    async def guest_any(msg: Message):
        uid = msg.from_user.id if msg.from_user else 0
        await msg.answer(
            f"این ربات خصوصی است.\nشناسهٔ عددی شما: <code>{uid}</code>\n"
            "اگر صاحب ربات هستی، این عدد را در فایل .env جلوی ALLOWED_USER_IDS بگذار و ربات را ریستارت کن."
        )

    # ----- commands ---------------------------------------------------------
    @main.message(Command("start", "help"))
    async def cmd_start(msg: Message):
        await msg.answer(HELP)

    @main.message(Command("cancel"))
    async def cmd_cancel(msg: Message):
        pending.pop(msg.from_user.id, None)
        await msg.answer("باشه، لغو شد.")

    @main.message(Command("today"))
    async def cmd_today(msg: Message):
        day = now().strftime("%Y-%m-%d")
        await msg.answer(day_report(db, msg.from_user.id, day))

    async def send_month(msg: Message, key: str):
        uid = msg.from_user.id
        n = now()
        elapsed = to_jalali(n)[2] if key == month_key(n) else None
        await msg.answer(month_report(db, uid, key, elapsed), reply_markup=dashboard_markup(uid, key))

    @main.message(Command("month"))
    async def cmd_month(msg: Message, command: CommandObject):
        await send_month(msg, pick_month(command.args))

    @main.message(Command("lastmonth"))
    async def cmd_lastmonth(msg: Message):
        await send_month(msg, prev_month_key(current_key()))

    @main.message(Command("top"))
    async def cmd_top(msg: Message, command: CommandObject):
        await msg.answer(top_report(db, msg.from_user.id, pick_month(command.args)))

    @main.message(Command("person"))
    async def cmd_person(msg: Message, command: CommandObject):
        name = (command.args or "").strip() or None
        await msg.answer(person_report(db, msg.from_user.id, current_key(), name))

    @main.message(Command("app"))
    async def cmd_app(msg: Message):
        uid = msg.from_user.id
        kb = dashboard_markup(uid, current_key())
        if kb is None:
            await msg.answer("داشبورد هنوز تنظیم نشده. آدرس صفحه را در فایل .env جلوی WEBAPP_URL بگذار.")
            return
        await msg.answer("📊 داشبورد این ماه:", reply_markup=kb)

    @main.message(Command("last"))
    async def cmd_last(msg: Message):
        rows = db.last_expenses(msg.from_user.id, 5)
        if not rows:
            await msg.answer("📭 هنوز هزینه‌ای ثبت نشده.")
            return
        for e in reversed(rows):
            await msg.answer(card(e, f"#{fa_digits(e['id'])}"), reply_markup=kb_main(e["id"]))

    @main.message(Command("undo"))
    async def cmd_undo(msg: Message):
        rows = db.last_expenses(msg.from_user.id, 1)
        if not rows:
            await msg.answer("📭 چیزی برای حذف نیست.")
            return
        e = rows[0]
        db.delete_expense(msg.from_user.id, e["id"])
        await msg.answer("🗑 حذف شد:\n" + expense_line(e))

    @main.message(Command("export"))
    async def cmd_export(msg: Message):
        rows = db.all_expenses(msg.from_user.id)
        if not rows:
            await msg.answer("📭 هنوز هزینه‌ای ثبت نشده.")
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "date", "amount_toman", "category", "person", "note"])
        for r in rows:
            w.writerow([r["id"], r["spent_at"], r["amount"], r["category"], r["person"] or "", r["note"]])
        data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel shows Persian correctly
        await msg.answer_document(BufferedInputFile(data, filename="expenses.csv"))

    @main.message(Command("cats"))
    async def cmd_cats(msg: Message):
        await msg.answer("🏷 <b>دسته‌ها</b>\n" + "\n".join(f"{EMOJI[c]} {c}" for c in CATEGORIES))

    @main.message(Command("addword"))
    async def cmd_addword(msg: Message, command: CommandObject):
        args = (command.args or "").strip()
        category = next((c for c in sorted(CATEGORIES, key=len, reverse=True) if args.endswith(c)), None)
        word = normalize(args[: -len(category)]) if category else ""
        if not category or not word:
            await msg.answer(
                "مثال: <code>/addword کافی‌نت آموزش</code>\n"
                "دسته باید یکی از این‌ها باشد: " + "، ".join(CATEGORIES)
            )
            return
        db.set_keyword(msg.from_user.id, word, category)
        await msg.answer(f"✅ از این به بعد «{esc(word)}» می‌شود {EMOJI[category]} {category}")

    @main.message(Command("addperson"))
    async def cmd_addperson(msg: Message, command: CommandObject):
        parts = (command.args or "").split(None, 1)
        if len(parts) != 2:
            await msg.answer("مثال: <code>/addperson سارا سارا</code>\n(کلمه‌ای که می‌نویسی، نامی که در گزارش بیاید)")
            return
        db.set_person(msg.from_user.id, normalize(parts[0]), parts[1].strip())
        await msg.answer(f"✅ هر جا «{esc(parts[0])}» بنویسی، به نام «{esc(parts[1].strip())}» ثبت می‌شود.")

    # ----- expense messages -------------------------------------------------
    async def save_line(msg: Message, line: str) -> None:
        uid = msg.from_user.id
        res = parse_amount(line)
        if res.amount is None:
            await msg.answer(f"❓ مبلغ پیدا نشد: {esc(line)}\nمثال: <code>بنزین ۵۰ تومن</code>")
            return
        cl = await cat.classify(uid, res.note)
        n = now()
        exp_id = db.add_expense(
            uid, res.amount, cl.category, cl.person, res.note,
            n.strftime("%Y-%m-%dT%H:%M:%S"), month_key(n),
        )
        e = db.get_expense(uid, exp_id)
        if cl.resolved:
            await msg.answer(card(e), reply_markup=kb_main(exp_id))
        else:
            await msg.answer(
                card(e, "✅ ثبت شد، ولی دسته را نشناختم. یکی را انتخاب کن (دفعهٔ بعد یاد می‌گیرم):"),
                reply_markup=kb_categories(exp_id),
            )

    @main.message(F.text & ~F.text.startswith("/"))
    async def on_text(msg: Message):
        uid = msg.from_user.id

        # waiting for a new amount after pressing "✏️ مبلغ"
        if uid in pending:
            exp_id, card_msg_id = pending[uid]
            res = parse_amount(msg.text)
            if res.amount is not None and not res.note:
                pending.pop(uid, None)
                db.update_amount(uid, exp_id, res.amount)
                e = db.get_expense(uid, exp_id)
                if e:
                    try:
                        await msg.bot.edit_message_text(
                            card(e, "✏️ مبلغ اصلاح شد"), chat_id=msg.chat.id,
                            message_id=card_msg_id, reply_markup=kb_main(exp_id),
                        )
                    except TelegramBadRequest:
                        await msg.answer(card(e, "✏️ مبلغ اصلاح شد"), reply_markup=kb_main(exp_id))
                return
            pending.pop(uid, None)  # it was a new expense, not an amount

        # one expense per line; a Persian comma also separates expenses, except inside numbers (۱،۵۰۰)
        lines = [ln.strip() for ln in re.split(r"\n|،(?!\d)", msg.text) if ln.strip()]
        if len(lines) > 15:
            await msg.answer("حداکثر ۱۵ خط در هر پیام.")
            return
        for ln in lines:
            await save_line(msg, ln)

    # ----- buttons ----------------------------------------------------------
    def owned(cq: CallbackQuery, exp_id: int):
        return db.get_expense(cq.from_user.id, exp_id)

    async def edit_card(cq: CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup]):
        if isinstance(cq.message, Message):
            try:
                await cq.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest:
                pass  # message not modified

    @main.callback_query(F.data.startswith("c:"))
    async def cb_pick_category(cq: CallbackQuery):
        exp_id = int(cq.data.split(":")[1])
        if not owned(cq, exp_id):
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        if isinstance(cq.message, Message):
            await cq.message.edit_reply_markup(reply_markup=kb_categories(exp_id))
        await cq.answer()

    @main.callback_query(F.data.startswith("b:"))
    async def cb_back(cq: CallbackQuery):
        exp_id = int(cq.data.split(":")[1])
        if not owned(cq, exp_id):
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        if isinstance(cq.message, Message):
            await cq.message.edit_reply_markup(reply_markup=kb_main(exp_id))
        await cq.answer()

    @main.callback_query(F.data.startswith("s:"))
    async def cb_set_category(cq: CallbackQuery):
        _, raw_id, raw_idx = cq.data.split(":")
        exp_id, idx = int(raw_id), int(raw_idx)
        e = owned(cq, exp_id)
        if not e or not 0 <= idx < len(CATEGORIES):
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        new_cat = CATEGORIES[idx]
        db.update_category(cq.from_user.id, exp_id, new_cat)
        learned = cat.learn(cq.from_user.id, e["note"], new_cat)
        e = db.get_expense(cq.from_user.id, exp_id)
        await edit_card(cq, card(e, "🏷 دسته عوض شد"), kb_main(exp_id))
        await cq.answer("یاد گرفتم: " + "، ".join(learned) if learned else "انجام شد")

    @main.callback_query(F.data.startswith("a:"))
    async def cb_edit_amount(cq: CallbackQuery):
        exp_id = int(cq.data.split(":")[1])
        if not owned(cq, exp_id) or not isinstance(cq.message, Message):
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        pending[cq.from_user.id] = (exp_id, cq.message.message_id)
        await cq.message.answer("✏️ مبلغ جدید را بفرست (مثلاً <code>۴۵ تومن</code>) یا /cancel")
        await cq.answer()

    @main.callback_query(F.data.startswith("d:"))
    async def cb_delete(cq: CallbackQuery):
        exp_id = int(cq.data.split(":")[1])
        e = owned(cq, exp_id)
        if not e:
            await cq.answer("قبلاً حذف شده.", show_alert=True)
            return
        db.delete_expense(cq.from_user.id, exp_id)
        await edit_card(cq, "🗑 حذف شد:\n" + expense_line(e), None)
        await cq.answer("حذف شد")

    return main, guest
