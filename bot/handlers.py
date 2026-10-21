"""Telegram handlers (aiogram 3).

Interface: a persistent bottom keyboard (reports) + inline buttons under every message.
Slash commands still work but are not advertised.

Callback data (max 64 bytes):
  c:<exp> pick category   s:<exp>:<cat id> set it   n:<exp> new category for it   b:<exp> back
  a:<exp> edit amount     d:<exp> delete
  r:<month> report   t:<month> biggest   p:<month> people
  o:<undo|export|help>  more-menu actions
  g  category manager   g:o:<id> one category   g:a add   g:r:<id> edit   g:d:<id> ask delete   g:y:<id> delete
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from .categories import UNKNOWN
from .categorizer import Categorizer
from .config import Settings
from .db import Database
from .jdate import month_key, month_label, next_month_key, parse_month_key, prev_month_key, to_jalali
from .parser import parse_amount
from .reports import day_report, esc, expense_line, month_report, person_report, top_report
from .textutil import fa_digits, fmt_amount, normalize
from .webapp import build_payload, build_url

log = logging.getLogger(__name__)

BTN_MONTH = "📊 گزارش ماه"
BTN_TODAY = "📅 امروز"
BTN_APP = "📈 داشبورد"
BTN_PEOPLE = "👤 افراد"
BTN_LAST = "🕘 آخرین‌ها"
BTN_MORE = "⚙️ بیشتر"
MENU_LABELS = {BTN_MONTH, BTN_TODAY, BTN_APP, BTN_PEOPLE, BTN_LAST, BTN_MORE}

WELCOME = (
    "✨ <b>دستیار مالی</b>\n\n"
    "هزینه را همان‌طور که می‌گویی بنویس؛ مبلغ و دسته را خودم تشخیص می‌دهم.\n\n"
    "<blockquote>بنزین ۵۰ تومن\nقسط ماشین ۲ میلیون\nشام با خانمم ۳۸۰</blockquote>\n"
    "زیر هر هزینه دکمهٔ تغییر دسته، ویرایش مبلغ و حذف هست. "
    "گزارش‌ها و نمودار را از منوی پایین باز کن."
)

TIPS = (
    "💡 <b>نکته‌ها</b>\n"
    "• عدد زیر ۱۰۰۰ یعنی هزار تومان (۳۸۰ = ۳۸۰٬۰۰۰).\n"
    "• «میلیون»، «هزار» و «ریال» هم فهمیده می‌شود.\n"
    "• چند هزینه را در چند خط بفرست.\n"
    "• اگر دسته را اشتباه زدم اصلاحش کن؛ دفعهٔ بعد یاد می‌گیرم.\n"
    "• نام‌ها (خانمم، مامان، …) خودکار به‌عنوان شخص ثبت می‌شوند.\n"
    "• دسته‌ها را از ⚙️ بیشتر ← 🏷 دسته‌ها اضافه، ویرایش یا حذف کن."
)

MAX_CAT_NAME = 20


# ---------- helpers ----------------------------------------------------------
def parse_cat_input(text: str) -> Tuple[Optional[str], str]:
    """'🎮 بازی' -> ('🎮', 'بازی');  'بازی' -> (None, 'بازی')."""
    text = re.sub(r"\s+", " ", text.strip())
    parts = text.split(" ", 1)
    if len(parts) == 2 and not re.search(r"\w", parts[0]):
        return parts[0][:8], parts[1].strip()
    if not re.search(r"\w", text):  # only an emoji, no name
        return None, ""
    return None, text


def card(e, emap: Optional[Dict[str, str]] = None, title: str = "✅ ثبت شد") -> str:
    emoji = (emap or {}).get(e["category"], "🏷")
    lines = [title, f"{emoji} <b>{esc(e['category'])}</b> · <b>{fmt_amount(e['amount'])}</b> تومان"]
    if e["note"]:
        lines.append(f"📝 {esc(e['note'])}")
    if e["person"]:
        lines.append(f"👤 {esc(e['person'])}")
    return "\n".join(lines)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _grid(buttons: List[InlineKeyboardButton], per_row: int = 2) -> List[List[InlineKeyboardButton]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


# ---------- keyboards ----------------------------------------------------------
def kb_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MONTH), KeyboardButton(text=BTN_TODAY)],
            [KeyboardButton(text=BTN_APP), KeyboardButton(text=BTN_PEOPLE)],
            [KeyboardButton(text=BTN_LAST), KeyboardButton(text=BTN_MORE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="هزینه را بنویس؛ مثلاً: بنزین ۵۰ تومن",
    )


def kb_more() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🏷 دسته‌ها", "g")],
        [_btn("📤 خروجی اکسل", "o:export"), _btn("↩️ حذف آخرین هزینه", "o:undo")],
        [_btn("💡 راهنما", "o:help")],
    ])


def kb_main(exp_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn("🏷 دسته", f"c:{exp_id}"),
        _btn("✏️ مبلغ", f"a:{exp_id}"),
        _btn("🗑 حذف", f"d:{exp_id}"),
    ]])


def kb_categories(exp_id: int, cats) -> InlineKeyboardMarkup:
    rows = _grid([_btn(f"{c['emoji']} {c['name']}", f"s:{exp_id}:{c['id']}") for c in cats])
    rows.append([_btn("➕ دستهٔ جدید", f"n:{exp_id}"), _btn("↩️ برگشت", f"b:{exp_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- router ------------------------------------------------------------------
def build_routers(settings: Settings, db: Database, cat: Categorizer) -> Tuple[Router, Router]:
    main = Router(name="main")
    guest = Router(name="guest")
    allowed = settings.allowed_ids

    main.message.filter(F.from_user.id.in_(allowed))
    main.callback_query.filter(F.from_user.id.in_(allowed))
    guest.message.filter(~F.from_user.id.in_(allowed))

    # what the bot is waiting for from this user:
    #   ("amount", exp_id, card_message_id) | ("cat_add",) | ("cat_new_for", exp_id) | ("cat_edit", cat_id)
    pending: Dict[int, tuple] = {}

    def now() -> datetime:
        return datetime.now(settings.tz)

    def current_key() -> str:
        return month_key(now())

    def pick_month(args: Optional[str]) -> str:
        if args:
            a = normalize(args)
            if a in ("prev", "last", "قبل", "قبلی", "ماه قبل"):
                return prev_month_key(current_key())
            parsed = parse_month_key(a)
            if parsed:
                return parsed
        return current_key()

    def dash_button(uid: int, key: str) -> Optional[InlineKeyboardButton]:
        if not settings.webapp_url:
            return None
        n = now()
        upto = to_jalali(n)[2] if key == month_key(n) else None
        url = build_url(settings.webapp_url, build_payload(db, uid, key, upto))
        return InlineKeyboardButton(text="📈 باز کردن داشبورد", web_app=WebAppInfo(url=url))

    def month_view(uid: int, key: str):
        n = now()
        cur = month_key(n)
        elapsed = to_jalali(n)[2] if key == cur else None
        text = month_report(db, uid, key, elapsed)
        rows = []
        dash = dash_button(uid, key) if db.month_expenses(uid, key) else None
        if dash:
            rows.append([dash])
        nav = [_btn("⏮ ماه قبل", f"r:{prev_month_key(key)}")]
        if key < cur:
            nav.append(_btn("ماه بعد ⏭", f"r:{next_month_key(key)}"))
        rows.append(nav)
        rows.append([_btn("🔝 بزرگ‌ترین‌ها", f"t:{key}"), _btn("👤 افراد", f"p:{key}")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def back_to_report(key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[_btn("↩️ برگشت به گزارش", f"r:{key}")]])

    # ----- category manager views ---------------------------------------------------
    def cats_view(uid: int, note: str = ""):
        cats = db.categories(uid)
        text = (note + "\n\n" if note else "") + "🏷 <b>مدیریت دسته‌ها</b>\nروی یک دسته بزن تا ویرایش یا حذفش کنی."
        rows = _grid([_btn(f"{c['emoji']} {c['name']}", f"g:o:{c['id']}") for c in cats])
        rows.append([_btn("➕ دستهٔ جدید", "g:a")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def cat_panel(uid: int, cid: int):
        c = db.get_category(uid, cid)
        if not c:
            return cats_view(uid, "این دسته پیدا نشد.")
        n = db.category_expense_count(uid, c["name"])
        text = f"{c['emoji']} <b>{esc(c['name'])}</b>\n{fa_digits(n)} هزینه در این دسته ثبت شده."
        rows = [[_btn("✏️ ویرایش نام و ایموجی", f"g:r:{cid}")]]
        if c["name"] != UNKNOWN:
            rows[0].append(_btn("🗑 حذف", f"g:d:{cid}"))
        rows.append([_btn("↩️ برگشت", "g")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def edit_card(cq: CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup]):
        if isinstance(cq.message, Message):
            try:
                await cq.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest:
                pass  # message not modified

    # ----- shared actions (used by menu buttons and typed commands) ------------------
    async def send_month(msg: Message, uid: int, key: str):
        text, kb = month_view(uid, key)
        await msg.answer(text, reply_markup=kb)

    async def send_people(msg: Message, uid: int, name: Optional[str] = None):
        key = current_key()
        await msg.answer(person_report(db, uid, key, name), reply_markup=back_to_report(key))

    async def send_dashboard(msg: Message, uid: int):
        key = current_key()
        btn = dash_button(uid, key)
        if btn is None:
            await msg.answer("داشبورد هنوز تنظیم نشده: آدرس صفحه را در فایل .env جلوی WEBAPP_URL بگذار.")
            return
        await msg.answer(f"📈 <b>داشبورد {month_label(key)}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]))

    async def send_last(msg: Message, uid: int):
        rows = db.last_expenses(uid, 5)
        if not rows:
            await msg.answer("📭 هنوز هزینه‌ای ثبت نشده.")
            return
        emap = db.emoji_map(uid)
        for e in reversed(rows):
            await msg.answer(card(e, emap, f"#{fa_digits(e['id'])}"), reply_markup=kb_main(e["id"]))

    async def do_undo(msg: Message, uid: int):
        rows = db.last_expenses(uid, 1)
        if not rows:
            await msg.answer("📭 چیزی برای حذف نیست.")
            return
        e = rows[0]
        db.delete_expense(uid, e["id"])
        await msg.answer("🗑 حذف شد:\n" + expense_line(e, db.emoji_map(uid)))

    async def send_export(msg: Message, uid: int):
        rows = db.all_expenses(uid)
        if not rows:
            await msg.answer("📭 هنوز هزینه‌ای ثبت نشده.")
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "date", "amount_toman", "category", "person", "note"])
        for r in rows:
            w.writerow([r["id"], r["spent_at"], r["amount"], r["category"], r["person"] or "", r["note"]])
        data = ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM so Excel shows Persian correctly
        await msg.answer_document(BufferedInputFile(data, filename="expenses.csv"))

    # ----- guests ------------------------------------------------------------------
    @guest.message()
    async def guest_any(msg: Message):
        uid = msg.from_user.id if msg.from_user else 0
        await msg.answer(
            f"این ربات خصوصی است.\nشناسهٔ عددی شما: <code>{uid}</code>\n"
            "اگر صاحب ربات هستی، این عدد را در فایل .env جلوی ALLOWED_USER_IDS بگذار و ربات را ریستارت کن."
        )

    # ----- typed commands (hidden aliases) ---------------------------------------------
    @main.message(Command("start", "help"))
    async def cmd_start(msg: Message):
        await msg.answer(WELCOME, reply_markup=kb_reply())

    @main.message(Command("cancel"))
    async def cmd_cancel(msg: Message):
        pending.pop(msg.from_user.id, None)
        await msg.answer("باشه، لغو شد.")

    @main.message(Command("today"))
    async def cmd_today(msg: Message):
        await msg.answer(day_report(db, msg.from_user.id, now().strftime("%Y-%m-%d")))

    @main.message(Command("month"))
    async def cmd_month(msg: Message, command: CommandObject):
        await send_month(msg, msg.from_user.id, pick_month(command.args))

    @main.message(Command("lastmonth"))
    async def cmd_lastmonth(msg: Message):
        await send_month(msg, msg.from_user.id, prev_month_key(current_key()))

    @main.message(Command("top"))
    async def cmd_top(msg: Message, command: CommandObject):
        await msg.answer(top_report(db, msg.from_user.id, pick_month(command.args)))

    @main.message(Command("person"))
    async def cmd_person(msg: Message, command: CommandObject):
        await send_people(msg, msg.from_user.id, (command.args or "").strip() or None)

    @main.message(Command("app"))
    async def cmd_app(msg: Message):
        await send_dashboard(msg, msg.from_user.id)

    @main.message(Command("last"))
    async def cmd_last(msg: Message):
        await send_last(msg, msg.from_user.id)

    @main.message(Command("undo"))
    async def cmd_undo(msg: Message):
        await do_undo(msg, msg.from_user.id)

    @main.message(Command("export"))
    async def cmd_export(msg: Message):
        await send_export(msg, msg.from_user.id)

    @main.message(Command("cats"))
    async def cmd_cats(msg: Message):
        text, kb = cats_view(msg.from_user.id)
        await msg.answer(text, reply_markup=kb)

    @main.message(Command("addword"))
    async def cmd_addword(msg: Message, command: CommandObject):
        uid = msg.from_user.id
        args = (command.args or "").strip()
        names = db.category_names(uid)
        category = next((c for c in sorted(names, key=len, reverse=True) if args.endswith(c)), None)
        word = normalize(args[: -len(category)]) if category else ""
        if not category or not word:
            await msg.answer(
                "مثال: <code>/addword کافی‌نت آموزش</code>\n"
                "دسته باید یکی از این‌ها باشد: " + "، ".join(names)
            )
            return
        db.set_keyword(uid, word, category)
        await msg.answer(f"✅ از این به بعد «{esc(word)}» می‌شود {db.emoji_map(uid).get(category, '🏷')} {esc(category)}")

    @main.message(Command("addperson"))
    async def cmd_addperson(msg: Message, command: CommandObject):
        parts = (command.args or "").split(None, 1)
        if len(parts) != 2:
            await msg.answer("مثال: <code>/addperson سارا سارا</code>\n(کلمه‌ای که می‌نویسی، نامی که در گزارش بیاید)")
            return
        db.set_person(msg.from_user.id, normalize(parts[0]), parts[1].strip())
        await msg.answer(f"✅ هر جا «{esc(parts[0])}» بنویسی، به نام «{esc(parts[1].strip())}» ثبت می‌شود.")

    # ----- bottom menu buttons ---------------------------------------------------------
    @main.message(F.text == BTN_MONTH)
    async def btn_month(msg: Message):
        pending.pop(msg.from_user.id, None)
        await send_month(msg, msg.from_user.id, current_key())

    @main.message(F.text == BTN_TODAY)
    async def btn_today(msg: Message):
        pending.pop(msg.from_user.id, None)
        await cmd_today(msg)

    @main.message(F.text == BTN_APP)
    async def btn_app(msg: Message):
        pending.pop(msg.from_user.id, None)
        await send_dashboard(msg, msg.from_user.id)

    @main.message(F.text == BTN_PEOPLE)
    async def btn_people(msg: Message):
        pending.pop(msg.from_user.id, None)
        await send_people(msg, msg.from_user.id)

    @main.message(F.text == BTN_LAST)
    async def btn_last(msg: Message):
        pending.pop(msg.from_user.id, None)
        await send_last(msg, msg.from_user.id)

    @main.message(F.text == BTN_MORE)
    async def btn_more(msg: Message):
        pending.pop(msg.from_user.id, None)
        await msg.answer("⚙️ <b>بیشتر</b>", reply_markup=kb_more())

    # ----- expense text ------------------------------------------------------------------
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
        emap = db.emoji_map(uid)
        if cl.resolved:
            await msg.answer(card(e, emap), reply_markup=kb_main(exp_id))
        else:
            await msg.answer(
                card(e, emap, "✅ ثبت شد، ولی دسته را نشناختم. یکی را انتخاب کن (دفعهٔ بعد یاد می‌گیرم):"),
                reply_markup=kb_categories(exp_id, db.categories(uid)),
            )

    async def handle_category_input(msg: Message, state: tuple) -> bool:
        """Returns True when the message was consumed as a category name."""
        uid = msg.from_user.id
        emoji, name = parse_cat_input(msg.text)
        if not name:
            await msg.answer("اسم دسته را بفرست (می‌توانی اول آن ایموجی بگذاری، مثلاً: 🎮 بازی) یا /cancel")
            return True
        if len(name) > MAX_CAT_NAME:
            await msg.answer(f"اسم دسته حداکثر {fa_digits(MAX_CAT_NAME)} حرف باشد.")
            return True
        kind = state[0]

        if kind == "cat_add":
            if db.add_category(uid, name, emoji or "🏷") is None:
                await msg.answer(f"دستهٔ «{esc(name)}» از قبل هست. اسم دیگری بفرست یا /cancel")
                return True
            pending.pop(uid, None)
            text, kb = cats_view(uid, f"✅ دستهٔ «{esc(name)}» اضافه شد.")
            await msg.answer(text, reply_markup=kb)

        elif kind == "cat_new_for":
            exp_id = state[1]
            e = db.get_expense(uid, exp_id)
            pending.pop(uid, None)
            if not e:
                await msg.answer("این هزینه پیدا نشد.")
                return True
            existing = db.category_by_name(uid, name)
            if not existing:
                db.add_category(uid, name, emoji or "🏷")
            db.update_category(uid, exp_id, name)
            cat.learn(uid, e["note"], name)
            await msg.answer(
                card(db.get_expense(uid, exp_id), db.emoji_map(uid), "🏷 دستهٔ جدید ساخته و ثبت شد"),
                reply_markup=kb_main(exp_id),
            )

        elif kind == "cat_edit":
            cid = state[1]
            old = db.get_category(uid, cid)
            if not old:
                pending.pop(uid, None)
                await msg.answer("این دسته پیدا نشد.")
                return True
            if not db.edit_category(uid, cid, name, emoji or old["emoji"]):
                await msg.answer(f"دستهٔ «{esc(name)}» از قبل هست. اسم دیگری بفرست یا /cancel")
                return True
            pending.pop(uid, None)
            text, kb = cats_view(uid, f"✅ ویرایش شد: «{esc(old['name'])}» ← «{esc(name)}»" if name != old["name"] and old["name"] != UNKNOWN else "✅ ویرایش شد.")
            await msg.answer(text, reply_markup=kb)
        return True

    @main.message(F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_LABELS))
    async def on_text(msg: Message):
        uid = msg.from_user.id
        state = pending.get(uid)

        if state:
            res = parse_amount(msg.text)
            if state[0] == "amount":
                exp_id, card_msg_id = state[1], state[2]
                if res.amount is not None and not res.note:
                    pending.pop(uid, None)
                    db.update_amount(uid, exp_id, res.amount)
                    e = db.get_expense(uid, exp_id)
                    if e:
                        text = card(e, db.emoji_map(uid), "✏️ مبلغ اصلاح شد")
                        try:
                            await msg.bot.edit_message_text(
                                text, chat_id=msg.chat.id, message_id=card_msg_id, reply_markup=kb_main(exp_id))
                        except TelegramBadRequest:
                            await msg.answer(text, reply_markup=kb_main(exp_id))
                    return
                pending.pop(uid, None)  # it was a new expense, not an amount
            elif res.amount is None:
                if await handle_category_input(msg, state):
                    return
            else:
                pending.pop(uid, None)  # looks like an expense: stop waiting for a category name

        # one expense per line; a Persian comma also separates expenses, except inside numbers (۱،۵۰۰)
        lines = [ln.strip() for ln in re.split(r"\n|،(?!\d)", msg.text) if ln.strip()]
        if len(lines) > 15:
            await msg.answer("حداکثر ۱۵ خط در هر پیام.")
            return
        for ln in lines:
            await save_line(msg, ln)

    # ----- expense card buttons ------------------------------------------------------------
    def owned(cq: CallbackQuery, exp_id: int):
        return db.get_expense(cq.from_user.id, exp_id)

    @main.callback_query(F.data.startswith("c:"))
    async def cb_pick_category(cq: CallbackQuery):
        exp_id = int(cq.data.split(":")[1])
        if not owned(cq, exp_id):
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        if isinstance(cq.message, Message):
            await cq.message.edit_reply_markup(reply_markup=kb_categories(exp_id, db.categories(cq.from_user.id)))
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
        uid = cq.from_user.id
        _, raw_exp, raw_cid = cq.data.split(":")
        exp_id = int(raw_exp)
        e = owned(cq, exp_id)
        c = db.get_category(uid, int(raw_cid))
        if not e or not c:
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        db.update_category(uid, exp_id, c["name"])
        learned = cat.learn(uid, e["note"], c["name"])
        e = db.get_expense(uid, exp_id)
        await edit_card(cq, card(e, db.emoji_map(uid), "🏷 دسته عوض شد"), kb_main(exp_id))
        await cq.answer("یاد گرفتم: " + "، ".join(learned) if learned else "انجام شد")

    @main.callback_query(F.data.startswith("n:"))
    async def cb_new_category_for(cq: CallbackQuery):
        exp_id = int(cq.data.split(":")[1])
        if not owned(cq, exp_id) or not isinstance(cq.message, Message):
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        pending[cq.from_user.id] = ("cat_new_for", exp_id)
        await cq.message.answer("➕ اسم دستهٔ جدید را بفرست (می‌توانی اول آن ایموجی بگذاری، مثلاً: 🎮 بازی) یا /cancel")
        await cq.answer()

    @main.callback_query(F.data.startswith("a:"))
    async def cb_edit_amount(cq: CallbackQuery):
        exp_id = int(cq.data.split(":")[1])
        if not owned(cq, exp_id) or not isinstance(cq.message, Message):
            await cq.answer("این مورد پیدا نشد.", show_alert=True)
            return
        pending[cq.from_user.id] = ("amount", exp_id, cq.message.message_id)
        await cq.message.answer("✏️ مبلغ جدید را بفرست (مثلاً <code>۴۵ تومن</code>) یا /cancel")
        await cq.answer()

    @main.callback_query(F.data.startswith("d:"))
    async def cb_delete(cq: CallbackQuery):
        uid = cq.from_user.id
        exp_id = int(cq.data.split(":")[1])
        e = owned(cq, exp_id)
        if not e:
            await cq.answer("قبلاً حذف شده.", show_alert=True)
            return
        db.delete_expense(uid, exp_id)
        await edit_card(cq, "🗑 حذف شد:\n" + expense_line(e, db.emoji_map(uid)), None)
        await cq.answer("حذف شد")

    # ----- report navigation ------------------------------------------------------------------
    @main.callback_query(F.data.regexp(r"^r:\d{4}-\d{2}$"))
    async def cb_report(cq: CallbackQuery):
        text, kb = month_view(cq.from_user.id, cq.data[2:])
        await edit_card(cq, text, kb)
        await cq.answer()

    @main.callback_query(F.data.regexp(r"^t:\d{4}-\d{2}$"))
    async def cb_top(cq: CallbackQuery):
        key = cq.data[2:]
        await edit_card(cq, top_report(db, cq.from_user.id, key), back_to_report(key))
        await cq.answer()

    @main.callback_query(F.data.regexp(r"^p:\d{4}-\d{2}$"))
    async def cb_people(cq: CallbackQuery):
        key = cq.data[2:]
        await edit_card(cq, person_report(db, cq.from_user.id, key), back_to_report(key))
        await cq.answer()

    @main.callback_query(F.data.startswith("o:"))
    async def cb_more_action(cq: CallbackQuery):
        action = cq.data[2:]
        uid = cq.from_user.id
        await cq.answer()
        if not isinstance(cq.message, Message):
            return
        if action == "undo":
            await do_undo(cq.message, uid)
        elif action == "export":
            await send_export(cq.message, uid)
        elif action == "help":
            await cq.message.answer(TIPS)

    # ----- category manager ---------------------------------------------------------------------
    @main.callback_query(F.data == "g")
    async def cb_cats(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        text, kb = cats_view(cq.from_user.id)
        await edit_card(cq, text, kb)
        await cq.answer()

    @main.callback_query(F.data.regexp(r"^g:o:\d+$"))
    async def cb_cat_open(cq: CallbackQuery):
        text, kb = cat_panel(cq.from_user.id, int(cq.data.split(":")[2]))
        await edit_card(cq, text, kb)
        await cq.answer()

    @main.callback_query(F.data == "g:a")
    async def cb_cat_add(cq: CallbackQuery):
        if not isinstance(cq.message, Message):
            await cq.answer()
            return
        pending[cq.from_user.id] = ("cat_add",)
        await cq.message.answer("➕ اسم دستهٔ جدید را بفرست (می‌توانی اول آن ایموجی بگذاری، مثلاً: 🎮 بازی) یا /cancel")
        await cq.answer()

    @main.callback_query(F.data.regexp(r"^g:r:\d+$"))
    async def cb_cat_edit(cq: CallbackQuery):
        uid, cid = cq.from_user.id, int(cq.data.split(":")[2])
        c = db.get_category(uid, cid)
        if not c or not isinstance(cq.message, Message):
            await cq.answer("این دسته پیدا نشد.", show_alert=True)
            return
        pending[uid] = ("cat_edit", cid)
        extra = "\n(نام «سایر» ثابت است؛ فقط ایموجی‌اش عوض می‌شود.)" if c["name"] == UNKNOWN else ""
        await cq.message.answer(
            f"✏️ نام تازه برای «{esc(c['name'])}» را بفرست. اگر ایموجی هم بخواهی اول بگذار (مثلاً: 🎮 بازی) یا /cancel{extra}")
        await cq.answer()

    @main.callback_query(F.data.regexp(r"^g:d:\d+$"))
    async def cb_cat_ask_delete(cq: CallbackQuery):
        uid, cid = cq.from_user.id, int(cq.data.split(":")[2])
        c = db.get_category(uid, cid)
        if not c or c["name"] == UNKNOWN:
            await cq.answer("این دسته حذف نمی‌شود.", show_alert=True)
            return
        n = db.category_expense_count(uid, c["name"])
        text = f"🗑 دستهٔ «{esc(c['name'])}» حذف شود؟"
        if n:
            text += f"\n{fa_digits(n)} هزینهٔ آن به «{UNKNOWN}» منتقل می‌شود."
        await edit_card(cq, text, InlineKeyboardMarkup(inline_keyboard=[[
            _btn("✅ بله، حذف کن", f"g:y:{cid}"), _btn("↩️ نه", f"g:o:{cid}")]]))
        await cq.answer()

    @main.callback_query(F.data.regexp(r"^g:y:\d+$"))
    async def cb_cat_delete(cq: CallbackQuery):
        uid, cid = cq.from_user.id, int(cq.data.split(":")[2])
        c = db.get_category(uid, cid)
        name = c["name"] if c else ""
        moved = db.delete_category(uid, cid)
        if moved < 0:
            await cq.answer("این دسته حذف نمی‌شود.", show_alert=True)
            return
        note = f"🗑 «{esc(name)}» حذف شد." + (f" {fa_digits(moved)} هزینه به «{UNKNOWN}» رفت." if moved else "")
        text, kb = cats_view(uid, note)
        await edit_card(cq, text, kb)
        await cq.answer("حذف شد")

    return main, guest
