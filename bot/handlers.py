"""Telegram handlers (aiogram 3).

Interface: a persistent bottom keyboard + inline buttons under every message.
Every inline screen is edited in place and has a "↩️ برگشت" button to its parent.
Buttons also work on old messages: Telegram hides an old message's content from the bot,
so screens are edited by chat/message id, with a new message as the fallback.
Slash commands still work (a short list is shown in Telegram's menu).

Callback data (max 64 bytes):
  expense card  c:<exp> pick category   s:<exp>:<cat id> set it   n:<exp> new category for it   b:<exp> back
                a:<exp> amount   w:<exp> note   j:<exp> date menu   j:<exp>:<days back> set date
                d:<exp> ask delete   dy:<exp>[:P] delete (P = came from the panel)   E:<exp> card as new message
  reports       r:<month> month   t:<month> biggest   p:<month> people   q:<month>:<i> one person
                k:<month> categories of the month   x:<month>:<cat id> one category   u:<month> budgets
                y:<YYYY-MM-DD> one day
  panel         P panel   o:<undo|export|help|backup|stats|search>   X cancel the open question
  categories    g list   g:o:<id> one   g:a add   g:r:<id> edit   g:d:<id> ask delete   g:y:<id> delete
                g:u:<id> / g:v:<id> move up / down   g:b:<id> its budget
  budget        B view   B:t monthly total   B:c pick a category
  words         L list   L:a teach a word   L:s:<cat id> category for it   L:d forget a word
  people        U list   U:a add   U:d delete
  recurring     R list   R:a add   R:o:<id> one   R:d:<id> delete   R:y:<id> record now   R:n:<id> skip
  settings      S list   S:<key> toggle
"""
from __future__ import annotations

import csv
import datetime as dt
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
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from .categories import DEFAULT_PEOPLE, UNKNOWN
from .categorizer import Categorizer
from .config import Settings
from .db import SETTINGS_DEFAULTS, Database
from .jdate import (
    day_label, month_key, month_label, next_month_key, parse_month_key, prev_month_key, to_jalali,
)
from .parser import parse_amount
from .reports import (
    budget_report, category_month_report, day_report, esc, expense_line, keywords_report, month_report,
    people_list_report, person_report, recurring_report, search_report, top_report,
)
from .smart import crossed, parse_recurring, split_day_hint
from .textutil import fa_digits, fmt_amount, normalize
from .webapp import build_payload, build_url

log = logging.getLogger(__name__)

BTN_MONTH = "📊 گزارش ماه"
BTN_TODAY = "📅 امروز"
BTN_APP = "📈 داشبورد"
BTN_PEOPLE = "👤 افراد"
BTN_LAST = "🕘 آخرین‌ها"
BTN_PANEL = "🛠 پنل مدیریت"
LEGACY_MORE = "⚙️ بیشتر"  # label of the old keyboard, still on screen until /start
MENU_LABELS = {BTN_MONTH, BTN_TODAY, BTN_APP, BTN_PEOPLE, BTN_LAST, BTN_PANEL, LEGACY_MORE}

BACK = "↩️ برگشت"

WELCOME = (
    "✨ <b>دستیار مالی</b>\n\n"
    "هزینه را همان‌طور که می‌گویی بنویس؛ مبلغ، دسته و شخص را خودم تشخیص می‌دهم.\n\n"
    "<blockquote>بنزین ۵۰ تومن\nقسط ماشین ۲ میلیون\nدیروز شام با خانمم ۳۸۰</blockquote>\n"
    "زیر هر هزینه دکمهٔ دسته، مبلغ، متن، تاریخ و حذف هست.\n"
    "گزارش‌ها از منوی پایین؛ بودجه، یادآور قسط، جستجو، بکاپ و تنظیمات در 🛠 پنل مدیریت."
)

TIPS = (
    "💡 <b>راهنما</b>\n"
    "• عدد زیر ۱۰۰۰ یعنی هزار تومان (۳۸۰ = ۳۸۰٬۰۰۰).\n"
    "• «میلیون»، «هزار» و «ریال» هم فهمیده می‌شود.\n"
    "• «دیروز»، «دیشب»، «پریروز» یا «۳ روز پیش» را بنویسی، با همان تاریخ ثبت می‌شود.\n"
    "• چند هزینه را در چند خط بفرست.\n"
    "• اگر دسته را اشتباه زدم اصلاحش کن؛ دفعهٔ بعد یاد می‌گیرم.\n"
    "• نام‌ها (خانمم، مامان، …) خودکار به‌عنوان شخص ثبت می‌شوند.\n"
    "• 🎯 بودجه بگذار تا در ۸۰٪ و ۱۰۰٪ خبرت کنم.\n"
    "• 🔁 قسط و اجاره را در «یادآور ماهانه» بگذار؛ روزش با یک دکمه ثبت می‌شود.\n"
    "• هر سؤالی که پرسیدم را با ✖️ لغو یا /cancel می‌بندی.\n\n"
    "<b>دستورها:</b> /panel /month /today /last /budget /find /export /backup /cancel"
)

SETTING_LABELS = {
    "monthly_report": "گزارش خودکار آخر ماه (ساعت ۲۱)",
    "budget_alerts": "هشدار بودجه (۸۰٪ و ۱۰۰٪)",
    "weekly_backup": "بکاپ هفتگی دیتابیس (جمعه شب)",
    "night_reminder": "یادآوری ساعت ۲۲ اگر امروز چیزی ثبت نشده",
}

MAX_CAT_NAME = 20
DELETE_WORDS = {"0", "حذف", "پاک", "هیچ", "بدون بودجه"}


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


def card(e, emap: Optional[Dict[str, str]] = None, title: str = "✅ ثبت شد", today: Optional[str] = None) -> str:
    emoji = (emap or {}).get(e["category"], "🏷")
    lines = [title, f"{emoji} <b>{esc(e['category'])}</b> · <b>{fmt_amount(e['amount'])}</b> تومان"]
    if e["note"]:
        lines.append(f"📝 {esc(e['note'])}")
    if e["person"]:
        lines.append(f"👤 {esc(e['person'])}")
    if today and e["spent_at"][:10] != today:
        lines.append(f"📅 {day_label(dt.date.fromisoformat(e['spent_at'][:10]))}")
    return "\n".join(lines)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _grid(buttons: List[InlineKeyboardButton], per_row: int = 2) -> List[List[InlineKeyboardButton]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def _kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short(s: str, n: int = 22) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------- keyboards ----------------------------------------------------------
def kb_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MONTH), KeyboardButton(text=BTN_TODAY)],
            [KeyboardButton(text=BTN_APP), KeyboardButton(text=BTN_LAST)],
            [KeyboardButton(text=BTN_PEOPLE), KeyboardButton(text=BTN_PANEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="هزینه را بنویس؛ مثلاً: بنزین ۵۰ تومن",
    )


def kb_main(exp_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [_btn("🏷 دسته", f"c:{exp_id}"), _btn("✏️ مبلغ", f"a:{exp_id}"), _btn("📝 متن", f"w:{exp_id}")],
        [_btn("📅 تاریخ", f"j:{exp_id}"), _btn("🗑 حذف", f"d:{exp_id}")],
    ])


def kb_categories(exp_id: int, cats) -> InlineKeyboardMarkup:
    rows = _grid([_btn(f"{c['emoji']} {c['name']}", f"s:{exp_id}:{c['id']}") for c in cats], 3)
    rows.append([_btn("➕ دستهٔ جدید", f"n:{exp_id}"), _btn(BACK, f"b:{exp_id}")])
    return _kb(rows)


def kb_dates(exp_id: int) -> InlineKeyboardMarkup:
    names = ["امروز", "دیروز", "پریروز"] + [f"{fa_digits(i)} روز پیش" for i in range(3, 9)]
    rows = _grid([_btn(n, f"j:{exp_id}:{i}") for i, n in enumerate(names)], 3)
    rows.append([_btn(BACK, f"b:{exp_id}")])
    return _kb(rows)


def kb_delete(exp_id: int, from_panel: bool = False) -> InlineKeyboardMarkup:
    tail = ":P" if from_panel else ""
    return _kb([[_btn("✅ بله، حذف کن", f"dy:{exp_id}{tail}"), _btn("↩️ نه", "P" if from_panel else f"b:{exp_id}")]])


def kb_cancel() -> InlineKeyboardMarkup:
    return _kb([[_btn("✖️ لغو", "X")]])


def kb_back(data: str, label: str = BACK) -> InlineKeyboardMarkup:
    return _kb([[_btn(label, data)]])


def kb_panel(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [_btn("🏷 دسته‌ها", "g"), _btn("🎯 بودجه", "B")],
        [_btn("🔁 یادآور ماهانه", "R"), _btn("🔎 جستجو", "o:search")],
        [_btn("🧠 کلمه‌های یادگرفته", "L"), _btn("👤 اشخاص", "U")],
        [_btn("📤 خروجی اکسل", "o:export")] + ([_btn("💾 بکاپ", "o:backup")] if is_admin else []),
        [_btn("↩️ حذف آخرین هزینه", "o:undo"), _btn("📈 آمار کلی", "o:stats")],
        [_btn("⚙️ تنظیمات", "S"), _btn("💡 راهنما", "o:help")],
    ]
    return _kb(rows)


# ---------- safe screen updates ---------------------------------------------------
async def show(cq: CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup]) -> None:
    """Replace the screen the button belongs to. Works on old messages too; if editing is
    impossible (deleted message …) the screen is sent as a new message."""
    m = cq.message
    if m is not None:
        try:
            await cq.bot.edit_message_text(
                text=text, chat_id=m.chat.id, message_id=m.message_id, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return
            log.warning("edit failed, sending a new message: %s", exc)
    await cq.bot.send_message(cq.from_user.id, text, reply_markup=markup)


async def show_markup(cq: CallbackQuery, markup: InlineKeyboardMarkup, fallback_text: str) -> None:
    """Change only the buttons (the card text stays)."""
    m = cq.message
    if m is not None:
        try:
            await cq.bot.edit_message_reply_markup(chat_id=m.chat.id, message_id=m.message_id, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return
            log.warning("markup edit failed, sending a new message: %s", exc)
    await cq.bot.send_message(cq.from_user.id, fallback_text, reply_markup=markup)


async def safe_answer(cq: CallbackQuery, text: Optional[str] = None, alert: bool = False) -> None:
    try:
        await cq.answer(text, show_alert=alert)
    except TelegramBadRequest:  # query too old / already answered
        pass


# ---------- router ------------------------------------------------------------------
def build_routers(settings: Settings, db: Database, cat: Categorizer) -> Tuple[Router, Router]:
    main = Router(name="main")
    guest = Router(name="guest")
    allowed = settings.allowed_ids

    main.message.filter(F.from_user.id.in_(allowed))
    main.callback_query.filter(F.from_user.id.in_(allowed))
    guest.message.filter(~F.from_user.id.in_(allowed))
    guest.callback_query.filter(~F.from_user.id.in_(allowed))

    # what the bot is waiting for from this user (see on_text):
    #   ("amount", exp, card msg) ("note", exp, card msg) ("cat_add",) ("cat_new_for", exp) ("cat_edit", cat id)
    #   ("budget", category or "") ("search",) ("kw_add",) ("kw_pick", word) ("kw_del",)
    #   ("person_add",) ("person_del",) ("rec_add",)
    pending: Dict[int, tuple] = {}

    def is_admin(uid: int) -> bool:
        return settings.admin_id is None or uid == settings.admin_id

    def now() -> datetime:
        return datetime.now(settings.tz)

    def today() -> str:
        return now().strftime("%Y-%m-%d")

    def current_key() -> str:
        return month_key(now())

    def elapsed(key: str) -> Optional[int]:
        """Today's Jalali day if `key` is the running month."""
        return to_jalali(now())[2] if key == current_key() else None

    def pick_month(args: Optional[str]) -> str:
        if args:
            a = normalize(args)
            if a in ("prev", "last", "قبل", "قبلی", "ماه قبل"):
                return prev_month_key(current_key())
            parsed = parse_month_key(a)
            if parsed:
                return parsed
        return current_key()

    async def ask(cq_or_msg, uid: int, state: tuple, text: str) -> None:
        """Ask a question; the answer comes as the next text message."""
        pending[uid] = state
        bot = cq_or_msg.bot
        await bot.send_message(uid, text, reply_markup=kb_cancel())

    # ----- views: each returns (text, keyboard) -----------------------------------------
    def dash_button(uid: int, key: str) -> Optional[InlineKeyboardButton]:
        if not settings.webapp_url:
            return None
        url = build_url(settings.webapp_url, build_payload(db, uid, key, elapsed(key), now()))
        return InlineKeyboardButton(text="📈 باز کردن داشبورد", web_app=WebAppInfo(url=url))

    def month_view(uid: int, key: str):
        text = month_report(db, uid, key, elapsed(key))
        rows = []
        has_data = bool(db.month_expenses(uid, key))
        dash = dash_button(uid, key) if has_data else None
        if dash:
            rows.append([dash])
        nav = [_btn("⏮ ماه قبل", f"r:{prev_month_key(key)}")]
        if key < current_key():
            nav.append(_btn("ماه بعد ⏭", f"r:{next_month_key(key)}"))
        rows.append(nav)
        if has_data:
            rows.append([_btn("🏷 ریز دسته‌ها", f"k:{key}"), _btn("🔝 بزرگ‌ترین‌ها", f"t:{key}")])
            rows.append([_btn("👤 افراد", f"p:{key}"), _btn("🎯 بودجه", f"u:{key}")])
        if key != current_key():
            rows.append([_btn("🔙 ماه جاری", f"r:{current_key()}")])
        return text, _kb(rows)

    def to_report(key: str) -> List[InlineKeyboardButton]:
        return [_btn("↩️ برگشت به گزارش", f"r:{key}")]

    def people_view(uid: int, key: str):
        people = db.month_by_person(uid, key)
        rows = _grid([_btn(f"👤 {_short(p['person'], 18)}", f"q:{key}:{i}") for i, p in enumerate(people[:20])])
        rows.append(to_report(key))
        return person_report(db, uid, key), _kb(rows)

    def month_cats_view(uid: int, key: str):
        by_cat = db.month_by_category(uid, key)
        emap = db.emoji_map(uid)
        ids = {c["name"]: c["id"] for c in db.categories(uid)}
        buttons = [
            _btn(f"{emap.get(c['category'], '🏷')} {c['category']} · {fmt_amount(c['total'])}", f"x:{key}:{ids[c['category']]}")
            for c in by_cat if c["category"] in ids
        ]
        rows = _grid(buttons, 1 if len(buttons) <= 6 else 2)
        rows.append(to_report(key))
        return f"🏷 <b>دسته‌های {month_label(key)}</b>\nروی هر دسته بزن تا ریز هزینه‌هایش را ببینی.", _kb(rows)

    def day_view(uid: int, day: str):
        title = None
        if day == today():
            title = "امروز"
        elif day == (now() - dt.timedelta(days=1)).strftime("%Y-%m-%d"):
            title = "دیروز"
        text = day_report(db, uid, day, title)
        d = dt.date.fromisoformat(day)
        nav = [_btn("⏮ روز قبل", f"y:{d - dt.timedelta(days=1)}")]
        if day < today():
            nav.append(_btn("روز بعد ⏭", f"y:{d + dt.timedelta(days=1)}"))
        rows = [nav]
        if day != today():
            rows.append([_btn("🔙 امروز", f"y:{today()}")])
        rows.append([_btn("📊 گزارش ماه", f"r:{month_key(d)}")])
        return text, _kb(rows)

    def panel_view(uid: int, note: str = ""):
        key = current_key()
        total = db.month_total(uid, key)
        n = len(db.month_expenses(uid, key))
        line = f"این ماه ({month_label(key)}): <b>{fmt_amount(total)}</b> تومان · {fa_digits(n)} مورد"
        budget = db.budgets(uid).get("")
        if budget:
            line += f" · {fa_digits(round(total / budget * 100))}٪ بودجه"
        text = (note + "\n\n" if note else "") + "🛠 <b>پنل مدیریت</b>\n" + line
        return text, kb_panel(is_admin(uid))

    def cats_view(uid: int, note: str = ""):
        cats = db.categories(uid)
        text = (note + "\n\n" if note else "") + "🏷 <b>مدیریت دسته‌ها</b>\nروی یک دسته بزن تا ویرایش، جابه‌جا یا حذفش کنی."
        rows = _grid([_btn(f"{c['emoji']} {c['name']}", f"g:o:{c['id']}") for c in cats], 3)
        rows.append([_btn("➕ دستهٔ جدید", "g:a"), _btn(BACK, "P")])
        return text, _kb(rows)

    def cat_panel(uid: int, cid: int, note: str = ""):
        c = db.get_category(uid, cid)
        if not c:
            return cats_view(uid, "این دسته پیدا نشد.")
        n = db.category_expense_count(uid, c["name"])
        budget = db.budgets(uid).get(c["name"])
        text = (note + "\n\n" if note else "") + f"{c['emoji']} <b>{esc(c['name'])}</b>\n{fa_digits(n)} هزینه در این دسته ثبت شده."
        if budget:
            text += f"\n🎯 بودجهٔ ماهانه: {fmt_amount(budget)} تومان"
        rows = [[_btn("✏️ ویرایش نام و ایموجی", f"g:r:{cid}")]]
        if c["name"] != UNKNOWN:
            rows[0].append(_btn("🗑 حذف", f"g:d:{cid}"))
        rows.append([_btn("🔼 بالاتر", f"g:u:{cid}"), _btn("🔽 پایین‌تر", f"g:v:{cid}")])
        rows.append([_btn("🎯 بودجهٔ این دسته", f"g:b:{cid}")])
        rows.append([_btn(BACK, "g")])
        return text, _kb(rows)

    def budget_view(uid: int, note: str = ""):
        key = current_key()
        text = (note + "\n\n" if note else "") + budget_report(db, uid, key, elapsed(key))
        return text, _kb([
            [_btn("🎯 بودجهٔ کل ماه", "B:t"), _btn("🏷 بودجهٔ یک دسته", "B:c")],
            [_btn(BACK, "P")],
        ])

    def words_view(uid: int, note: str = ""):
        text = (note + "\n\n" if note else "") + keywords_report(db, uid)
        return text, _kb([
            [_btn("➕ یاد دادن کلمه", "L:a"), _btn("🗑 فراموش کردن کلمه", "L:d")],
            [_btn(BACK, "P")],
        ])

    def people_admin_view(uid: int, note: str = ""):
        text = (note + "\n\n" if note else "") + people_list_report(db, uid)
        return text, _kb([
            [_btn("➕ تعریف شخص", "U:a"), _btn("🗑 حذف شخص", "U:d")],
            [_btn(BACK, "P")],
        ])

    def recurring_view(uid: int, note: str = ""):
        text = (note + "\n\n" if note else "") + recurring_report(db, uid)
        rows = [[_btn(f"روز {fa_digits(r['day'])} · {_short(r['text'], 24)}", f"R:o:{r['id']}")] for r in db.recurring(uid)]
        rows.append([_btn("➕ اضافه کردن", "R:a"), _btn(BACK, "P")])
        return text, _kb(rows)

    def settings_view(uid: int):
        rows = []
        for key in SETTINGS_DEFAULTS:
            if key == "weekly_backup" and not is_admin(uid):
                continue
            mark = "✅" if db.setting(uid, key) else "⬜️"
            rows.append([_btn(f"{mark} {SETTING_LABELS[key]}", f"S:{key}")])
        rows.append([_btn(BACK, "P")])
        return "⚙️ <b>تنظیمات</b>\nروی هر مورد بزن تا روشن/خاموش شود.", _kb(rows)

    def stats_text(uid: int) -> str:
        s = db.stats(uid)
        if not s["n"]:
            return "📈 هنوز هزینه‌ای ثبت نشده."
        first = dt.date.fromisoformat(str(s["first"])[:10])
        months = max(1, int(s["months"]))
        return "\n".join([
            "📈 <b>آمار کلی</b>",
            f"• تعداد هزینه‌ها: {fa_digits(s['n'])}",
            f"• جمع همهٔ هزینه‌ها: {fmt_amount(s['total'])} تومان",
            f"• از {day_label(first)} {fa_digits(to_jalali(first)[0])}",
            f"• میانگین هر ماه: {fmt_amount(int(s['total']) // months)} تومان ({fa_digits(months)} ماه)",
            f"• کلمه‌های یادگرفته: {fa_digits(s['words'])}",
        ])

    # ----- shared actions (used by menu buttons and typed commands) ------------------
    async def send_month(msg: Message, uid: int, key: str):
        text, kb = month_view(uid, key)
        await msg.answer(text, reply_markup=kb)

    async def send_day(msg: Message, uid: int, day: str):
        text, kb = day_view(uid, day)
        await msg.answer(text, reply_markup=kb)

    async def send_people(msg: Message, uid: int, name: Optional[str] = None):
        key = current_key()
        if name:
            await msg.answer(person_report(db, uid, key, name), reply_markup=_kb([to_report(key)]))
            return
        text, kb = people_view(uid, key)
        await msg.answer(text, reply_markup=kb)

    async def send_panel(msg: Message, uid: int):
        text, kb = panel_view(uid)
        await msg.answer(text, reply_markup=kb)

    async def send_dashboard(msg: Message, uid: int):
        key = current_key()
        btn = dash_button(uid, key)
        if btn is None:
            await msg.answer("داشبورد هنوز تنظیم نشده: آدرس صفحه را در فایل .env جلوی WEBAPP_URL بگذار.")
            return
        await msg.answer(
            f"📈 <b>داشبورد {month_label(key)}</b>\nاطلاعات تا همین لحظه است؛ برای دیدن هزینه‌های تازه دوباره از منو بزن.",
            reply_markup=_kb([[btn]]))

    async def send_card(bot, uid: int, exp_id: int, title: Optional[str] = None):
        e = db.get_expense(uid, exp_id)
        if not e:
            await bot.send_message(uid, "این هزینه پیدا نشد.")
            return
        await bot.send_message(uid, card(e, db.emoji_map(uid), title or f"#{fa_digits(e['id'])}", today()),
                               reply_markup=kb_main(exp_id))

    async def send_last(msg: Message, uid: int):
        rows = db.last_expenses(uid, 5)
        if not rows:
            await msg.answer("📭 هنوز هزینه‌ای ثبت نشده.")
            return
        emap = db.emoji_map(uid)
        for e in reversed(rows):
            await msg.answer(card(e, emap, f"#{fa_digits(e['id'])}", today()), reply_markup=kb_main(e["id"]))

    def export_file(uid: int) -> Optional[BufferedInputFile]:
        rows = db.all_expenses(uid)
        if not rows:
            return None
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "jalali_date", "date", "amount_toman", "category", "person", "note"])
        for r in rows:
            jy, jm, jd = to_jalali(dt.date.fromisoformat(r["spent_at"][:10]))
            w.writerow([r["id"], f"{jy:04d}/{jm:02d}/{jd:02d}", r["spent_at"], r["amount"], r["category"],
                        r["person"] or "", r["note"]])
        data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel shows Persian correctly
        return BufferedInputFile(data, filename=f"expenses-{current_key()}.csv")

    async def send_export(bot, uid: int):
        f = export_file(uid)
        if f is None:
            await bot.send_message(uid, "📭 هنوز هزینه‌ای ثبت نشده.")
            return
        await bot.send_document(uid, f, caption="📤 همهٔ هزینه‌ها (با اکسل باز می‌شود)")

    async def send_backup(bot, uid: int, caption: str = "💾 بکاپ دیتابیس"):
        if not is_admin(uid):
            await bot.send_message(uid, "بکاپ کامل فقط برای مدیر ربات است؛ از 📤 خروجی اکسل استفاده کن.")
            return
        stamp = now().strftime("%Y-%m-%d")
        await bot.send_document(
            uid, BufferedInputFile(db.backup_bytes(), filename=f"expenses-backup-{stamp}.db"),
            caption=caption + "\nبرای برگرداندن: همین فایل را جای data/expenses.db روی سرور بگذار.")

    # ----- saving expenses --------------------------------------------------------------
    def budget_warnings(uid: int, e) -> List[str]:
        """Lines for budgets this expense just pushed past 80% / 100%."""
        if not db.setting(uid, "budget_alerts"):
            return []
        budgets = db.budgets(uid)
        out = []
        for name in (e["category"], ""):
            limit = budgets.get(name)
            if not limit:
                continue
            after = db.month_total(uid, e["j_month"], name or None)
            hit = crossed(after - e["amount"], after, limit)
            if hit:
                label = f"بودجهٔ «{esc(name)}»" if name else "بودجهٔ کل ماه"
                if hit >= 100:
                    out.append(f"🔴 {label} تمام شد ({fmt_amount(after)} از {fmt_amount(limit)}).")
                else:
                    out.append(f"🟡 {fa_digits(hit)}٪ {label} مصرف شد؛ {fmt_amount(limit - after)} مانده.")
        return out

    async def save_line(msg: Message, line: str) -> Optional[int]:
        uid = msg.from_user.id
        days_back, rest = split_day_hint(line)
        res = parse_amount(rest)
        if res.amount is None:
            await msg.answer(f"❓ مبلغ پیدا نشد: {esc(line)}\nمثال: <code>بنزین ۵۰ تومن</code>")
            return None
        cl = await cat.classify(uid, res.note)
        when = now() - dt.timedelta(days=days_back)
        spent_at = when.strftime("%Y-%m-%dT%H:%M:%S")
        exp_id = db.add_expense(uid, res.amount, cl.category, cl.person, res.note, spent_at, month_key(when))
        e = db.get_expense(uid, exp_id)
        emap = db.emoji_map(uid)
        extra = budget_warnings(uid, e)
        if days_back == 0:
            since = (when - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
            if db.find_recent_same(uid, res.amount, res.note, since, exp_id):
                extra.append("⚠️ همین هزینه چند دقیقه پیش هم ثبت شده بود؛ اگر تکراری است 🗑 حذف را بزن.")
        tail = ("\n\n" + "\n".join(extra)) if extra else ""
        if cl.resolved:
            await msg.answer(card(e, emap, today=today()) + tail, reply_markup=kb_main(exp_id))
        else:
            await msg.answer(
                card(e, emap, "✅ ثبت شد، ولی دسته را نشناختم. یکی را انتخاب کن (دفعهٔ بعد یاد می‌گیرم):", today())
                + tail,
                reply_markup=kb_categories(exp_id, db.categories(uid)),
            )
        return exp_id

    async def record_recurring(bot, uid: int, rid: int) -> Optional[int]:
        """Save a recurring item as today's expense and send its card."""
        r = db.get_recurring(uid, rid)
        if not r:
            return None
        res = parse_amount(r["text"])
        if res.amount is None:
            return None
        cl = await cat.classify(uid, res.note)
        n = now()
        exp_id = db.add_expense(uid, res.amount, cl.category, cl.person, res.note,
                                n.strftime("%Y-%m-%dT%H:%M:%S"), month_key(n))
        db.mark_recurring(rid, month_key(n))
        e = db.get_expense(uid, exp_id)
        extra = budget_warnings(uid, e)
        await bot.send_message(uid, card(e, db.emoji_map(uid), "🔁 یادآور ماهانه ثبت شد", today())
                               + (("\n\n" + "\n".join(extra)) if extra else ""), reply_markup=kb_main(exp_id))
        return exp_id

    # ----- guests and errors ----------------------------------------------------------
    @guest.message()
    async def guest_any(msg: Message):
        uid = msg.from_user.id if msg.from_user else 0
        await msg.answer(
            f"این ربات خصوصی است.\nشناسهٔ عددی شما: <code>{uid}</code>\n"
            "اگر صاحب ربات هستی، این عدد را در فایل .env جلوی ALLOWED_USER_IDS بگذار و ربات را ریستارت کن."
        )

    @guest.callback_query()
    async def guest_button(cq: CallbackQuery):
        await safe_answer(cq, "این ربات خصوصی است.", alert=True)

    @main.errors()
    async def on_error(event: ErrorEvent):
        log.exception("handler failed: %s", event.exception, exc_info=event.exception)
        upd = event.update
        try:
            if upd.callback_query:
                await upd.callback_query.answer("⚠️ خطایی پیش آمد؛ دوباره امتحان کن.", show_alert=True)
            elif upd.message:
                await upd.message.answer("⚠️ خطایی پیش آمد؛ دوباره امتحان کن.")
        except Exception:  # noqa: BLE001 - never fail inside the error handler
            pass
        return True

    # ----- typed commands --------------------------------------------------------------
    @main.message(Command("start", "help"))
    async def cmd_start(msg: Message):
        pending.pop(msg.from_user.id, None)
        await msg.answer(WELCOME, reply_markup=kb_reply())

    @main.message(Command("cancel"))
    async def cmd_cancel(msg: Message):
        pending.pop(msg.from_user.id, None)
        await msg.answer("باشه، لغو شد.")

    @main.message(Command("panel", "admin", "settings"))
    async def cmd_panel(msg: Message):
        pending.pop(msg.from_user.id, None)
        await send_panel(msg, msg.from_user.id)

    @main.message(Command("today"))
    async def cmd_today(msg: Message):
        await send_day(msg, msg.from_user.id, today())

    @main.message(Command("yesterday"))
    async def cmd_yesterday(msg: Message):
        await send_day(msg, msg.from_user.id, (now() - dt.timedelta(days=1)).strftime("%Y-%m-%d"))

    @main.message(Command("month"))
    async def cmd_month(msg: Message, command: CommandObject):
        await send_month(msg, msg.from_user.id, pick_month(command.args))

    @main.message(Command("lastmonth"))
    async def cmd_lastmonth(msg: Message):
        await send_month(msg, msg.from_user.id, prev_month_key(current_key()))

    @main.message(Command("top"))
    async def cmd_top(msg: Message, command: CommandObject):
        key = pick_month(command.args)
        await msg.answer(top_report(db, msg.from_user.id, key), reply_markup=_kb([to_report(key)]))

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
        rows = db.last_expenses(msg.from_user.id, 1)
        if not rows:
            await msg.answer("📭 چیزی برای حذف نیست.")
            return
        e = rows[0]
        await msg.answer("🗑 آخرین هزینه حذف شود؟\n" + expense_line(e, db.emoji_map(msg.from_user.id)),
                         reply_markup=kb_delete(e["id"], from_panel=True))

    @main.message(Command("export"))
    async def cmd_export(msg: Message):
        await send_export(msg.bot, msg.from_user.id)

    @main.message(Command("backup"))
    async def cmd_backup(msg: Message):
        await send_backup(msg.bot, msg.from_user.id)

    @main.message(Command("cats"))
    async def cmd_cats(msg: Message):
        text, kb = cats_view(msg.from_user.id)
        await msg.answer(text, reply_markup=kb)

    @main.message(Command("budget"))
    async def cmd_budget(msg: Message):
        text, kb = budget_view(msg.from_user.id)
        await msg.answer(text, reply_markup=kb)

    @main.message(Command("find", "search"))
    async def cmd_find(msg: Message, command: CommandObject):
        uid = msg.from_user.id
        q = normalize(command.args or "")
        if not q:
            await ask(msg, uid, ("search",), "🔎 چه چیزی را جستجو کنم؟ (مثلاً: بنزین، خانمم، قسط)")
            return
        await do_search(msg, uid, q)

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
        await send_day(msg, msg.from_user.id, today())

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

    @main.message(F.text.in_({BTN_PANEL, LEGACY_MORE}))
    async def btn_panel(msg: Message):
        pending.pop(msg.from_user.id, None)
        if msg.text == LEGACY_MORE:  # the old keyboard is still on screen: replace it
            await msg.answer("منوی پایین به‌روز شد ✅", reply_markup=kb_reply())
        await send_panel(msg, msg.from_user.id)

    # ----- answers to questions (pending) ------------------------------------------------
    async def do_search(msg: Message, uid: int, q: str):
        rows = db.search(uid, q, 15)
        open_btns = [_btn(f"✏️ #{fa_digits(r['id'])}", f"E:{r['id']}") for r in rows[:8]]
        kb = _grid(open_btns, 4) + [[_btn("🔎 جستجوی دیگر", "o:search"), _btn("🛠 پنل", "P")]]
        await msg.answer(search_report(rows, q, db.emoji_map(uid)), reply_markup=_kb(kb))

    async def handle_category_input(msg: Message, state: tuple) -> None:
        uid = msg.from_user.id
        emoji, name = parse_cat_input(msg.text)
        if not name:
            await msg.answer("اسم دسته را بفرست (می‌توانی اول آن ایموجی بگذاری، مثلاً: 🎮 بازی)", reply_markup=kb_cancel())
            return
        if len(name) > MAX_CAT_NAME:
            await msg.answer(f"اسم دسته حداکثر {fa_digits(MAX_CAT_NAME)} حرف باشد.", reply_markup=kb_cancel())
            return
        kind = state[0]

        if kind == "cat_add":
            if db.add_category(uid, name, emoji or "🏷") is None:
                await msg.answer(f"دستهٔ «{esc(name)}» از قبل هست. اسم دیگری بفرست.", reply_markup=kb_cancel())
                return
            pending.pop(uid, None)
            text, kb = cats_view(uid, f"✅ دستهٔ «{esc(name)}» اضافه شد.")
            await msg.answer(text, reply_markup=kb)

        elif kind == "cat_new_for":
            exp_id = state[1]
            e = db.get_expense(uid, exp_id)
            pending.pop(uid, None)
            if not e:
                await msg.answer("این هزینه پیدا نشد.")
                return
            if not db.category_by_name(uid, name):
                db.add_category(uid, name, emoji or "🏷")
            db.update_category(uid, exp_id, name)
            cat.learn(uid, e["note"], name)
            await msg.answer(
                card(db.get_expense(uid, exp_id), db.emoji_map(uid), "🏷 دستهٔ جدید ساخته و ثبت شد", today()),
                reply_markup=kb_main(exp_id),
            )

        elif kind == "cat_edit":
            cid = state[1]
            old = db.get_category(uid, cid)
            if not old:
                pending.pop(uid, None)
                await msg.answer("این دسته پیدا نشد.")
                return
            if not db.edit_category(uid, cid, name, emoji or old["emoji"]):
                await msg.answer(f"دستهٔ «{esc(name)}» از قبل هست. اسم دیگری بفرست.", reply_markup=kb_cancel())
                return
            pending.pop(uid, None)
            renamed = name != old["name"] and old["name"] != UNKNOWN
            note = f"✅ ویرایش شد: «{esc(old['name'])}» ← «{esc(name)}»" if renamed else "✅ ویرایش شد."
            text, kb = cat_panel(uid, cid, note)
            await msg.answer(text, reply_markup=kb)

    async def edit_or_send_card(msg: Message, uid: int, exp_id: int, card_mid: int, title: str):
        e = db.get_expense(uid, exp_id)
        if not e:
            await msg.answer("این هزینه پیدا نشد.")
            return
        text = card(e, db.emoji_map(uid), title, today())
        try:
            await msg.bot.edit_message_text(text=text, chat_id=msg.chat.id, message_id=card_mid, reply_markup=kb_main(exp_id))
            await msg.answer(f"{title} ✔️")
        except TelegramBadRequest:
            await msg.answer(text, reply_markup=kb_main(exp_id))

    async def handle_pending(msg: Message, state: tuple) -> bool:
        """Use the message as the answer to the open question. False = it is a new expense."""
        uid = msg.from_user.id
        text = msg.text.strip()
        kind = state[0]

        if kind == "amount":
            res = parse_amount(text)
            pending.pop(uid, None)
            if res.amount is None or res.note:
                return False  # it was a new expense, not an amount
            db.update_amount(uid, state[1], res.amount)
            await edit_or_send_card(msg, uid, state[1], state[2], "✏️ مبلغ اصلاح شد")
            return True

        if kind == "note":
            pending.pop(uid, None)
            note = normalize(text)[:200]
            db.update_note(uid, state[1], note, cat.detect_person(uid, note))
            await edit_or_send_card(msg, uid, state[1], state[2], "📝 متن اصلاح شد")
            return True

        if kind in ("cat_add", "cat_new_for", "cat_edit"):
            if parse_amount(text).amount is not None:
                pending.pop(uid, None)  # looks like an expense: stop waiting for a category name
                return False
            await handle_category_input(msg, state)
            return True

        if kind == "budget":
            name = state[1]
            label = f"دستهٔ «{esc(name)}»" if name else "کل ماه"
            if normalize(text) in DELETE_WORDS:
                db.delete_budget(uid, name)
                pending.pop(uid, None)
                t, kb = budget_view(uid, f"🗑 بودجهٔ {label} برداشته شد.")
                await msg.answer(t, reply_markup=kb)
                return True
            res = parse_amount(text)
            if res.amount is None:
                await msg.answer("مبلغ را بفرست، مثلاً <code>۱۵ میلیون</code> (یا ۰ برای حذف).", reply_markup=kb_cancel())
                return True
            db.set_budget(uid, name, res.amount)
            pending.pop(uid, None)
            t, kb = budget_view(uid, f"✅ بودجهٔ {label}: {fmt_amount(res.amount)} تومان در ماه")
            await msg.answer(t, reply_markup=kb)
            return True

        if kind == "search":
            pending.pop(uid, None)
            await do_search(msg, uid, normalize(text))
            return True

        if kind == "kw_add":
            word = normalize(text)
            if not word or len(word) > 30:
                await msg.answer("یک کلمه یا عبارت کوتاه بفرست (مثلاً: کافی‌نت).", reply_markup=kb_cancel())
                return True
            pending[uid] = ("kw_pick", word)
            cats = db.categories(uid)
            rows = _grid([_btn(f"{c['emoji']} {c['name']}", f"L:s:{c['id']}") for c in cats], 3)
            rows.append([_btn("✖️ لغو", "X")])
            await msg.answer(f"«{esc(word)}» مال کدام دسته است؟", reply_markup=_kb(rows))
            return True

        if kind == "kw_pick":
            pending.pop(uid, None)
            return False

        if kind == "kw_del":
            pending.pop(uid, None)
            word = normalize(text)
            done = db.delete_keyword(uid, word)
            t, kb = words_view(uid, f"🗑 «{esc(word)}» فراموش شد." if done else f"«{esc(word)}» در کلمه‌های یادگرفته نبود.")
            await msg.answer(t, reply_markup=kb)
            return True

        if kind == "person_add":
            m = re.match(r"^(.+?)\s*(?:=|←|->)\s*(.+)$", text)
            left, right = (m.group(1), m.group(2)) if m else (text, "")
            word, name = normalize(left), (right.strip() or left.strip())
            if not word or " " in word or len(word) > 20:
                await msg.answer("یک کلمه بفرست، مثلاً <code>سارا</code> یا <code>نامزدم = خانمم</code>", reply_markup=kb_cancel())
                return True
            pending.pop(uid, None)
            db.set_person(uid, word, name[:30])
            t, kb = people_admin_view(uid, f"✅ هر جا «{esc(word)}» بنویسی، به نام «{esc(name[:30])}» ثبت می‌شود.")
            await msg.answer(t, reply_markup=kb)
            return True

        if kind == "person_del":
            pending.pop(uid, None)
            word = normalize(text)
            if db.delete_person(uid, word):
                note = f"🗑 «{esc(word)}» حذف شد."
            elif word in DEFAULT_PEOPLE:
                note = f"«{esc(word)}» جزو کلمه‌های پیش‌فرض است و حذف نمی‌شود."
            else:
                note = f"«{esc(word)}» پیدا نشد."
            t, kb = people_admin_view(uid, note)
            await msg.answer(t, reply_markup=kb)
            return True

        if kind == "rec_add":
            parsed = parse_recurring(text)
            if not parsed:
                await msg.answer(
                    "متوجه نشدم. اول روز ماه، بعد هزینه را بنویس؛ مثلاً:\n<code>۵ قسط ماشین ۲ میلیون</code>",
                    reply_markup=kb_cancel())
                return True
            pending.pop(uid, None)
            day, rest = parsed
            db.add_recurring(uid, day, rest)
            t, kb = recurring_view(uid, f"✅ اضافه شد: روز {fa_digits(day)} هر ماه — {esc(rest)}")
            await msg.answer(t, reply_markup=kb)
            return True

        pending.pop(uid, None)
        return False

    # ----- expense text ------------------------------------------------------------------
    @main.message(F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_LABELS))
    async def on_text(msg: Message):
        uid = msg.from_user.id
        state = pending.get(uid)
        if state and await handle_pending(msg, state):
            return

        # one expense per line; a Persian comma also separates expenses, except inside numbers (۱،۵۰۰)
        lines = [ln.strip() for ln in re.split(r"\n|،(?!\d)", msg.text) if ln.strip()]
        if len(lines) > 15:
            await msg.answer("حداکثر ۱۵ خط در هر پیام.")
            return
        saved = []
        for ln in lines:
            exp_id = await save_line(msg, ln)
            if exp_id:
                saved.append(exp_id)
        if len(saved) > 1:
            total = sum(db.get_expense(uid, i)["amount"] for i in saved)
            await msg.answer(f"✅ {fa_digits(len(saved))} هزینه ثبت شد · جمع <b>{fmt_amount(total)}</b> تومان")

    # ----- expense card buttons ------------------------------------------------------------
    def owned(cq: CallbackQuery, exp_id: int):
        return db.get_expense(cq.from_user.id, exp_id)

    def card_mid(cq: CallbackQuery) -> int:
        return cq.message.message_id if cq.message else 0

    async def gone(cq: CallbackQuery):
        await safe_answer(cq, "این هزینه پیدا نشد (شاید حذف شده).", alert=True)

    @main.callback_query(F.data.regexp(r"^c:\d+$"))
    async def cb_pick_category(cq: CallbackQuery):
        uid, exp_id = cq.from_user.id, int(cq.data[2:])
        e = owned(cq, exp_id)
        if not e:
            return await gone(cq)
        await show_markup(cq, kb_categories(exp_id, db.categories(uid)), card(e, db.emoji_map(uid), "🏷 دسته را انتخاب کن:", today()))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^b:\d+$"))
    async def cb_back(cq: CallbackQuery):
        uid, exp_id = cq.from_user.id, int(cq.data[2:])
        e = owned(cq, exp_id)
        if not e:
            return await gone(cq)
        await show_markup(cq, kb_main(exp_id), card(e, db.emoji_map(uid), f"#{fa_digits(exp_id)}", today()))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^s:\d+:\d+$"))
    async def cb_set_category(cq: CallbackQuery):
        uid = cq.from_user.id
        _, raw_exp, raw_cid = cq.data.split(":")
        exp_id = int(raw_exp)
        e = owned(cq, exp_id)
        c = db.get_category(uid, int(raw_cid))
        if not e or not c:
            return await gone(cq)
        db.update_category(uid, exp_id, c["name"])
        learned = cat.learn(uid, e["note"], c["name"])
        e = db.get_expense(uid, exp_id)
        extra = budget_warnings(uid, e)
        await show(cq, card(e, db.emoji_map(uid), "🏷 دسته عوض شد", today())
                   + (("\n\n" + "\n".join(extra)) if extra else ""), kb_main(exp_id))
        await safe_answer(cq, ("یاد گرفتم: " + "، ".join(learned)) if learned else "انجام شد")

    @main.callback_query(F.data.regexp(r"^n:\d+$"))
    async def cb_new_category_for(cq: CallbackQuery):
        exp_id = int(cq.data[2:])
        if not owned(cq, exp_id):
            return await gone(cq)
        await ask(cq, cq.from_user.id, ("cat_new_for", exp_id),
                  "➕ اسم دستهٔ جدید را بفرست (می‌توانی اول آن ایموجی بگذاری، مثلاً: 🎮 بازی)")
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^a:\d+$"))
    async def cb_edit_amount(cq: CallbackQuery):
        exp_id = int(cq.data[2:])
        if not owned(cq, exp_id):
            return await gone(cq)
        await ask(cq, cq.from_user.id, ("amount", exp_id, card_mid(cq)), "✏️ مبلغ جدید را بفرست (مثلاً <code>۴۵ تومن</code>)")
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^w:\d+$"))
    async def cb_edit_note(cq: CallbackQuery):
        exp_id = int(cq.data[2:])
        e = owned(cq, exp_id)
        if not e:
            return await gone(cq)
        now_note = f"\nمتن فعلی: <code>{esc(e['note'])}</code>" if e["note"] else ""
        await ask(cq, cq.from_user.id, ("note", exp_id, card_mid(cq)), "📝 متن تازهٔ این هزینه را بفرست." + now_note)
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^j:\d+$"))
    async def cb_date_menu(cq: CallbackQuery):
        uid, exp_id = cq.from_user.id, int(cq.data[2:])
        e = owned(cq, exp_id)
        if not e:
            return await gone(cq)
        await show_markup(cq, kb_dates(exp_id), card(e, db.emoji_map(uid), "📅 تاریخ را انتخاب کن:", today()))
        await safe_answer(cq, "این هزینه مال کدام روز است؟")

    @main.callback_query(F.data.regexp(r"^j:\d+:\d{1,2}$"))
    async def cb_set_date(cq: CallbackQuery):
        uid = cq.from_user.id
        _, raw_exp, raw_days = cq.data.split(":")
        exp_id = int(raw_exp)
        e = owned(cq, exp_id)
        if not e:
            return await gone(cq)
        d = (now() - dt.timedelta(days=min(int(raw_days), 60))).date()
        clock = e["spent_at"][11:19] or "12:00:00"
        db.update_date(uid, exp_id, f"{d.isoformat()}T{clock}", month_key(d))
        e = db.get_expense(uid, exp_id)
        await show(cq, card(e, db.emoji_map(uid), "📅 تاریخ ثبت شد: " + day_label(d)), kb_main(exp_id))
        await safe_answer(cq, "انجام شد")

    @main.callback_query(F.data.regexp(r"^d:\d+$"))
    async def cb_ask_delete(cq: CallbackQuery):
        uid, exp_id = cq.from_user.id, int(cq.data[2:])
        e = owned(cq, exp_id)
        if not e:
            return await gone(cq)
        await show_markup(cq, kb_delete(exp_id), card(e, db.emoji_map(uid), "🗑 حذف شود؟", today()))
        await safe_answer(cq, "مطمئنی؟ دوباره تأیید کن.")

    @main.callback_query(F.data.regexp(r"^dy:\d+(:P)?$"))
    async def cb_delete(cq: CallbackQuery):
        uid = cq.from_user.id
        parts = cq.data.split(":")
        exp_id, from_panel = int(parts[1]), len(parts) == 3
        e = owned(cq, exp_id)
        if not e:
            await safe_answer(cq, "قبلاً حذف شده.", alert=True)
            return
        db.delete_expense(uid, exp_id)
        note = "🗑 حذف شد:\n" + expense_line(e, db.emoji_map(uid))
        if from_panel:
            await show(cq, *panel_view(uid, note))
        else:
            await show(cq, note, None)
        await safe_answer(cq, "حذف شد")

    @main.callback_query(F.data.regexp(r"^E:\d+$"))
    async def cb_open_card(cq: CallbackQuery):
        await send_card(cq.bot, cq.from_user.id, int(cq.data[2:]))
        await safe_answer(cq)

    # ----- report navigation ------------------------------------------------------------------
    @main.callback_query(F.data.regexp(r"^r:\d{4}-\d{2}$"))
    async def cb_report(cq: CallbackQuery):
        await show(cq, *month_view(cq.from_user.id, cq.data[2:]))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^t:\d{4}-\d{2}$"))
    async def cb_top(cq: CallbackQuery):
        key = cq.data[2:]
        await show(cq, top_report(db, cq.from_user.id, key), _kb([to_report(key)]))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^p:\d{4}-\d{2}$"))
    async def cb_people(cq: CallbackQuery):
        await show(cq, *people_view(cq.from_user.id, cq.data[2:]))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^q:\d{4}-\d{2}:\d+$"))
    async def cb_person(cq: CallbackQuery):
        uid = cq.from_user.id
        _, key, raw_i = cq.data.split(":")
        people = db.month_by_person(uid, key)
        i = int(raw_i)
        if i >= len(people):
            await show(cq, *people_view(uid, key))
        else:
            await show(cq, person_report(db, uid, key, people[i]["person"]), kb_back(f"p:{key}"))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^k:\d{4}-\d{2}$"))
    async def cb_month_cats(cq: CallbackQuery):
        await show(cq, *month_cats_view(cq.from_user.id, cq.data[2:]))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^x:\d{4}-\d{2}:\d+$"))
    async def cb_month_cat(cq: CallbackQuery):
        uid = cq.from_user.id
        _, key, raw_cid = cq.data.split(":")
        c = db.get_category(uid, int(raw_cid))
        if not c:
            await show(cq, *month_cats_view(uid, key))
        else:
            await show(cq, category_month_report(db, uid, key, c["name"]), kb_back(f"k:{key}"))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^u:\d{4}-\d{2}$"))
    async def cb_month_budget(cq: CallbackQuery):
        key = cq.data[2:]
        await show(cq, budget_report(db, cq.from_user.id, key, elapsed(key)),
                   _kb([[_btn("🎯 تنظیم بودجه", "B")], to_report(key)]))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^y:\d{4}-\d{2}-\d{2}$"))
    async def cb_day(cq: CallbackQuery):
        day = cq.data[2:]
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            day = today()
        await show(cq, *day_view(cq.from_user.id, min(day, today())))
        await safe_answer(cq)

    # ----- panel ------------------------------------------------------------------------------
    @main.callback_query(F.data == "P")
    async def cb_panel(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        await show(cq, *panel_view(cq.from_user.id))
        await safe_answer(cq)

    @main.callback_query(F.data == "X")
    async def cb_cancel(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        await show(cq, "✖️ لغو شد.", None)
        await safe_answer(cq, "لغو شد")

    @main.callback_query(F.data.startswith("o:"))
    async def cb_panel_action(cq: CallbackQuery):
        action = cq.data[2:]
        uid = cq.from_user.id
        if action == "undo":
            rows = db.last_expenses(uid, 1)
            if not rows:
                await show(cq, *panel_view(uid, "📭 چیزی برای حذف نیست."))
            else:
                e = rows[0]
                d = dt.date.fromisoformat(e["spent_at"][:10])
                await show(cq, "🗑 <b>آخرین هزینه حذف شود؟</b>\n" + expense_line(e, db.emoji_map(uid))
                           + f"\n📅 {day_label(d)}", kb_delete(e["id"], from_panel=True))
            await safe_answer(cq)
        elif action == "export":
            await safe_answer(cq, "📤 فایل را می‌فرستم…")
            await send_export(cq.bot, uid)
        elif action == "backup":
            await safe_answer(cq, "💾 بکاپ را می‌فرستم…")
            await send_backup(cq.bot, uid)
        elif action == "help":
            await show(cq, TIPS, kb_back("P"))
            await safe_answer(cq)
        elif action == "stats":
            await show(cq, stats_text(uid), kb_back("P"))
            await safe_answer(cq)
        elif action == "search":
            await ask(cq, uid, ("search",), "🔎 چه چیزی را جستجو کنم؟ (مثلاً: بنزین، خانمم، قسط)")
            await safe_answer(cq)
        else:
            await safe_answer(cq)

    # ----- category manager ---------------------------------------------------------------------
    @main.callback_query(F.data == "g")
    async def cb_cats(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        await show(cq, *cats_view(cq.from_user.id))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^g:o:\d+$"))
    async def cb_cat_open(cq: CallbackQuery):
        await show(cq, *cat_panel(cq.from_user.id, int(cq.data.split(":")[2])))
        await safe_answer(cq)

    @main.callback_query(F.data == "g:a")
    async def cb_cat_add(cq: CallbackQuery):
        await ask(cq, cq.from_user.id, ("cat_add",),
                  "➕ اسم دستهٔ جدید را بفرست (می‌توانی اول آن ایموجی بگذاری، مثلاً: 🎮 بازی)")
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^g:r:\d+$"))
    async def cb_cat_edit(cq: CallbackQuery):
        uid, cid = cq.from_user.id, int(cq.data.split(":")[2])
        c = db.get_category(uid, cid)
        if not c:
            await safe_answer(cq, "این دسته پیدا نشد.", alert=True)
            return
        extra = "\n(نام «سایر» ثابت است؛ فقط ایموجی‌اش عوض می‌شود.)" if c["name"] == UNKNOWN else ""
        await ask(cq, uid, ("cat_edit", cid),
                  f"✏️ نام تازه برای «{esc(c['name'])}» را بفرست. اگر ایموجی هم بخواهی اول بگذار (مثلاً: 🎮 بازی){extra}")
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^g:d:\d+$"))
    async def cb_cat_ask_delete(cq: CallbackQuery):
        uid, cid = cq.from_user.id, int(cq.data.split(":")[2])
        c = db.get_category(uid, cid)
        if not c or c["name"] == UNKNOWN:
            await safe_answer(cq, "این دسته حذف نمی‌شود.", alert=True)
            return
        n = db.category_expense_count(uid, c["name"])
        text = f"🗑 دستهٔ «{esc(c['name'])}» حذف شود؟"
        if n:
            text += f"\n{fa_digits(n)} هزینهٔ آن به «{UNKNOWN}» منتقل می‌شود."
        await show(cq, text, _kb([[_btn("✅ بله، حذف کن", f"g:y:{cid}"), _btn("↩️ نه", f"g:o:{cid}")]]))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^g:y:\d+$"))
    async def cb_cat_delete(cq: CallbackQuery):
        uid, cid = cq.from_user.id, int(cq.data.split(":")[2])
        c = db.get_category(uid, cid)
        name = c["name"] if c else ""
        moved = db.delete_category(uid, cid)
        if moved < 0:
            await safe_answer(cq, "این دسته حذف نمی‌شود.", alert=True)
            await show(cq, *cats_view(uid))
            return
        note = f"🗑 «{esc(name)}» حذف شد." + (f" {fa_digits(moved)} هزینه به «{UNKNOWN}» رفت." if moved else "")
        await show(cq, *cats_view(uid, note))
        await safe_answer(cq, "حذف شد")

    @main.callback_query(F.data.regexp(r"^g:[uv]:\d+$"))
    async def cb_cat_move(cq: CallbackQuery):
        uid = cq.from_user.id
        _, way, raw = cq.data.split(":")
        moved = db.move_category(uid, int(raw), -1 if way == "u" else 1)
        await show(cq, *cat_panel(uid, int(raw), "↕️ جابه‌جا شد." if moved else "از این جلوتر نمی‌رود."))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^g:b:\d+$"))
    async def cb_cat_budget(cq: CallbackQuery):
        uid, cid = cq.from_user.id, int(cq.data.split(":")[2])
        c = db.get_category(uid, cid)
        if not c:
            await safe_answer(cq, "این دسته پیدا نشد.", alert=True)
            return
        now_b = db.budgets(uid).get(c["name"])
        cur = f"\nبودجهٔ فعلی: {fmt_amount(now_b)} تومان" if now_b else ""
        await ask(cq, uid, ("budget", c["name"]),
                  f"🎯 بودجهٔ ماهانهٔ {c['emoji']} «{esc(c['name'])}» را بفرست (مثلاً <code>۳ میلیون</code>). ۰ = حذف بودجه{cur}")
        await safe_answer(cq)

    # ----- budget ---------------------------------------------------------------------------------
    @main.callback_query(F.data == "B")
    async def cb_budget(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        await show(cq, *budget_view(cq.from_user.id))
        await safe_answer(cq)

    @main.callback_query(F.data == "B:t")
    async def cb_budget_total(cq: CallbackQuery):
        uid = cq.from_user.id
        now_b = db.budgets(uid).get("")
        cur = f"\nبودجهٔ فعلی: {fmt_amount(now_b)} تومان" if now_b else ""
        await ask(cq, uid, ("budget", ""), f"🎯 سقف خرج کل ماه را بفرست (مثلاً <code>۲۰ میلیون</code>). ۰ = حذف بودجه{cur}")
        await safe_answer(cq)

    @main.callback_query(F.data == "B:c")
    async def cb_budget_cats(cq: CallbackQuery):
        uid = cq.from_user.id
        budgets = db.budgets(uid)
        buttons = []
        for c in db.categories(uid):
            b = budgets.get(c["name"])
            buttons.append(_btn(f"{c['emoji']} {c['name']}" + (f" · {fmt_amount(b)}" if b else ""), f"g:b:{c['id']}"))
        rows = _grid(buttons, 2)
        rows.append([_btn(BACK, "B")])
        await show(cq, "🏷 بودجهٔ کدام دسته را می‌خواهی تعیین کنی؟", _kb(rows))
        await safe_answer(cq)

    # ----- learned words ---------------------------------------------------------------------------
    @main.callback_query(F.data == "L")
    async def cb_words(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        await show(cq, *words_view(cq.from_user.id))
        await safe_answer(cq)

    @main.callback_query(F.data == "L:a")
    async def cb_word_add(cq: CallbackQuery):
        await ask(cq, cq.from_user.id, ("kw_add",), "➕ کلمه را بفرست (مثلاً: کافی‌نت). بعد دسته‌اش را انتخاب می‌کنی.")
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^L:s:\d+$"))
    async def cb_word_pick(cq: CallbackQuery):
        uid = cq.from_user.id
        state = pending.get(uid)
        c = db.get_category(uid, int(cq.data.split(":")[2]))
        if not state or state[0] != "kw_pick" or not c:
            await safe_answer(cq, "اول از 🧠 کلمه‌ها ← ➕ یاد دادن کلمه، کلمه را بفرست.", alert=True)
            return
        pending.pop(uid, None)
        db.set_keyword(uid, state[1], c["name"])
        await show(cq, *words_view(uid, f"✅ از این به بعد «{esc(state[1])}» می‌شود {c['emoji']} {esc(c['name'])}"))
        await safe_answer(cq, "یاد گرفتم")

    @main.callback_query(F.data == "L:d")
    async def cb_word_del(cq: CallbackQuery):
        await ask(cq, cq.from_user.id, ("kw_del",), "🗑 کدام کلمه را فراموش کنم؟ همان کلمه را بفرست.")
        await safe_answer(cq)

    # ----- people -------------------------------------------------------------------------------------
    @main.callback_query(F.data == "U")
    async def cb_people_admin(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        await show(cq, *people_admin_view(cq.from_user.id))
        await safe_answer(cq)

    @main.callback_query(F.data == "U:a")
    async def cb_person_add(cq: CallbackQuery):
        await ask(cq, cq.from_user.id, ("person_add",),
                  "➕ کلمه‌ای که در متن می‌نویسی را بفرست، مثلاً <code>سارا</code>.\n"
                  "اگر می‌خواهی در گزارش با نام دیگری بیاید: <code>نامزدم = خانمم</code>")
        await safe_answer(cq)

    @main.callback_query(F.data == "U:d")
    async def cb_person_del(cq: CallbackQuery):
        await ask(cq, cq.from_user.id, ("person_del",), "🗑 کلمهٔ شخصی که باید حذف شود را بفرست.")
        await safe_answer(cq)

    # ----- recurring reminders ---------------------------------------------------------------------------
    @main.callback_query(F.data == "R")
    async def cb_recurring(cq: CallbackQuery):
        pending.pop(cq.from_user.id, None)
        await show(cq, *recurring_view(cq.from_user.id))
        await safe_answer(cq)

    @main.callback_query(F.data == "R:a")
    async def cb_recurring_add(cq: CallbackQuery):
        await ask(cq, cq.from_user.id, ("rec_add",),
                  "🔁 روز ماه و هزینه را بفرست، مثلاً:\n<code>۵ قسط ماشین ۲ میلیون</code>\n"
                  "(اگر روز ۳۱ بگذاری، در ماه‌های کوتاه‌تر روز آخر یادآوری می‌کنم.)")
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^R:o:\d+$"))
    async def cb_recurring_open(cq: CallbackQuery):
        uid, rid = cq.from_user.id, int(cq.data.split(":")[2])
        r = db.get_recurring(uid, rid)
        if not r:
            await show(cq, *recurring_view(uid, "این مورد پیدا نشد."))
            return await safe_answer(cq)
        done = "\n✅ این ماه ثبت/یادآوری شده." if r["last_month"] == current_key() else ""
        await show(cq, f"🔁 <b>روز {fa_digits(r['day'])} هر ماه</b>\n{esc(r['text'])}{done}", _kb([
            [_btn("✅ همین الان ثبت کن", f"R:y:{rid}"), _btn("🗑 حذف", f"R:d:{rid}")],
            [_btn(BACK, "R")],
        ]))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^R:d:\d+$"))
    async def cb_recurring_delete(cq: CallbackQuery):
        uid, rid = cq.from_user.id, int(cq.data.split(":")[2])
        db.delete_recurring(uid, rid)
        await show(cq, *recurring_view(uid, "🗑 حذف شد."))
        await safe_answer(cq, "حذف شد")

    @main.callback_query(F.data.regexp(r"^R:y:\d+$"))
    async def cb_recurring_record(cq: CallbackQuery):
        uid, rid = cq.from_user.id, int(cq.data.split(":")[2])
        r = db.get_recurring(uid, rid)
        if not r:
            await safe_answer(cq, "این یادآور پیدا نشد.", alert=True)
            return
        await show(cq, f"✅ ثبت شد: {esc(r['text'])}", kb_back("R", "🔁 یادآورها"))
        await record_recurring(cq.bot, uid, rid)
        await safe_answer(cq, "ثبت شد")

    @main.callback_query(F.data.regexp(r"^R:n:\d+$"))
    async def cb_recurring_skip(cq: CallbackQuery):
        uid, rid = cq.from_user.id, int(cq.data.split(":")[2])
        if db.get_recurring(uid, rid):
            db.mark_recurring(rid, current_key())
        await show(cq, "باشه، این ماه ثبت نکردم.", kb_back("R", "🔁 یادآورها"))
        await safe_answer(cq)

    # ----- settings ------------------------------------------------------------------------------------------
    @main.callback_query(F.data == "S")
    async def cb_settings(cq: CallbackQuery):
        await show(cq, *settings_view(cq.from_user.id))
        await safe_answer(cq)

    @main.callback_query(F.data.regexp(r"^S:\w+$"))
    async def cb_setting_toggle(cq: CallbackQuery):
        uid, key = cq.from_user.id, cq.data[2:]
        if key not in SETTINGS_DEFAULTS:
            return await safe_answer(cq)
        on = db.toggle_setting(uid, key)
        await show(cq, *settings_view(uid))
        await safe_answer(cq, ("روشن شد: " if on else "خاموش شد: ") + SETTING_LABELS[key])

    @main.callback_query()
    async def cb_unknown(cq: CallbackQuery):
        """Buttons of messages from an older version that no longer exist."""
        await safe_answer(cq, "این دکمه قدیمی است؛ از منوی پایین دوباره باز کن.", alert=True)

    return main, guest
