"""Text reports (HTML for Telegram)."""
from __future__ import annotations

import datetime as dt
import html
from typing import Dict, List, Optional

from .categories import EMOJI, UNKNOWN
from .db import Database
from .jdate import day_label, month_label, month_length, prev_month_key, to_jalali
from .smart import forecast, percent, status_icon
from .textutil import fa_digits, fmt_amount, normalize

MAX_LINES = 40


def esc(s: Optional[str]) -> str:
    return html.escape(s or "")


def expense_line(e, emap=None) -> str:
    emoji = (emap or EMOJI).get(e["category"], "🏷")
    text = esc(e["note"]) or esc(e["category"])
    line = f"{emoji} {fmt_amount(e['amount'])} — {text}"
    if e["person"]:
        line += f" 👤{esc(e['person'])}"
    return line


def bar(percent_: float, width: int = 10) -> str:
    filled = max(0, min(width, round(percent_ / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def jday(e) -> int:
    """Jalali day of the month of an expense row."""
    return to_jalali(dt.date.fromisoformat(e["spent_at"][:10]))[2]


def change_text(now: int, before: int) -> str:
    """'▲ ۱۲٪ بیشتر' / '▼ ۸٪ کمتر' / 'برابر'"""
    if before <= 0:
        return ""
    pct = round((now - before) / before * 100)
    if pct == 0:
        return "تقریباً برابر"
    return f"{'▲' if pct > 0 else '▼'} {fa_digits(abs(pct))}٪ {'بیشتر' if pct > 0 else 'کمتر'}"


def day_report(db: Database, user_id: int, day: str, title: Optional[str] = None) -> str:
    d = dt.date.fromisoformat(day)
    head = f"{title} · {day_label(d)}" if title else day_label(d)
    rows = db.expenses_on_day(user_id, day)
    if not rows:
        return f"📅 <b>{head}</b>\n\n📭 هزینه‌ای ثبت نشده."
    emap = db.emoji_map(user_id)
    total = sum(r["amount"] for r in rows)
    lines = [f"📅 <b>{head}</b>", f"💰 جمع: <b>{fmt_amount(total)}</b> تومان ({fa_digits(len(rows))} مورد)", ""]
    for r in rows[:MAX_LINES]:
        lines.append(f"{fa_digits(r['spent_at'][11:16])}  {expense_line(r, emap)}")
    if len(rows) > MAX_LINES:
        lines.append(f"… و {fa_digits(len(rows) - MAX_LINES)} مورد دیگر")
    return "\n".join(lines)


def month_report(db: Database, user_id: int, key: str, days_elapsed: Optional[int] = None) -> str:
    """days_elapsed is set only for the running month (today's Jalali day)."""
    cats = db.month_by_category(user_id, key)
    if not cats:
        return f"📭 برای {month_label(key)} هزینه‌ای ثبت نشده."
    emap = db.emoji_map(user_id)
    total = sum(c["total"] for c in cats)
    count = sum(c["n"] for c in cats)
    length = month_length(key)

    lines = [
        f"📊 <b>گزارش {month_label(key)}</b>",
        f"💰 جمع کل: <b>{fmt_amount(total)}</b> تومان ({fa_digits(count)} مورد)",
        f"📆 میانگین روزانه: {fmt_amount(total // (days_elapsed or length))} تومان",
    ]

    prev = prev_month_key(key)
    if days_elapsed:
        # compare with the same point of last month, not with the whole of it
        before = sum(e["amount"] for e in db.month_expenses(user_id, prev) if jday(e) <= days_elapsed)
        ch = change_text(total, before)
        if ch:
            lines.append(f"↔️ نسبت به همین موقع ماه قبل: {ch}")
        f = forecast(total, days_elapsed, length)
        if f:
            lines.append(f"🔮 پیش‌بینی تا آخر ماه: حدود <b>{fmt_amount(f)}</b> تومان")
    else:
        ch = change_text(total, db.month_total(user_id, prev))
        if ch:
            lines.append(f"↔️ نسبت به ماه قبل: {ch}")

    budget = db.budgets(user_id).get("")
    if budget:
        pct = percent(total, budget)
        left = budget - total
        line = f"🎯 بودجه: {status_icon(pct)} {fa_digits(pct)}٪ مصرف شد"
        if left > 0:
            line += f" · {fmt_amount(left)} مانده"
            if days_elapsed and length > days_elapsed:
                line += f" (روزی {fmt_amount(left // (length - days_elapsed + 1))})"
        else:
            line += f" · {fmt_amount(-left)} بیشتر از بودجه"
        lines.append(line)

    lines += ["", "<b>به ترتیب بیشترین مصرف:</b>"]
    for i, c in enumerate(cats, 1):
        pct = c["total"] / total * 100
        emoji = emap.get(c["category"], "🏷")
        lines.append(
            f"{fa_digits(i)}. {emoji} {esc(c['category'])} — <b>{fmt_amount(c['total'])}</b> "
            f"({fa_digits(round(pct))}٪)\n    {bar(pct)}"
        )

    people = db.month_by_person(user_id, key)
    if people:
        lines += ["", "<b>👤 به تفکیک افراد:</b>"]
        for p in people:
            lines.append(f"• {esc(p['person'])} — {fmt_amount(p['total'])}")

    top = db.month_top(user_id, key, 3)
    lines += ["", "<b>🔝 بزرگ‌ترین هزینه‌ها:</b>"]
    for e in top:
        lines.append(f"• {expense_line(e, emap)}")
    return "\n".join(lines)


def top_report(db: Database, user_id: int, key: str, n: int = 10) -> str:
    rows = db.month_top(user_id, key, n)
    if not rows:
        return f"📭 برای {month_label(key)} هزینه‌ای ثبت نشده."
    emap = db.emoji_map(user_id)
    lines = [f"🔝 <b>بزرگ‌ترین هزینه‌های {month_label(key)}</b>", ""]
    for i, e in enumerate(rows, 1):
        lines.append(f"{fa_digits(i)}. {expense_line(e, emap)} · روز {fa_digits(jday(e))}")
    return "\n".join(lines)


def person_report(db: Database, user_id: int, key: str, name: Optional[str] = None) -> str:
    if not name:
        people = db.month_by_person(user_id, key)
        if not people:
            return f"📭 برای {month_label(key)} هزینه‌ای با نام شخص ثبت نشده."
        lines = [f"👤 <b>هزینه‌ها به تفکیک افراد — {month_label(key)}</b>", ""]
        for p in people:
            lines.append(f"• {esc(p['person'])} — <b>{fmt_amount(p['total'])}</b> ({fa_digits(p['n'])} مورد)")
        lines += ["", "برای جزئیات روی نام بزن."]
        return "\n".join(lines)

    want = normalize(name)
    rows = [
        r for r in db.month_expenses(user_id, key)
        if r["person"] and (normalize(r["person"]) == want or normalize(r["person"]).startswith(want))
    ]
    if not rows:
        return f"📭 برای «{esc(name)}» در {month_label(key)} چیزی ثبت نشده."
    emap = db.emoji_map(user_id)
    total = sum(r["amount"] for r in rows)
    lines = [f"👤 <b>{esc(name)} — {month_label(key)}</b>", f"💰 جمع: <b>{fmt_amount(total)}</b> تومان", ""]
    for r in rows[:MAX_LINES]:
        lines.append(expense_line(r, emap))
    return "\n".join(lines)


def category_month_report(db: Database, user_id: int, key: str, category: str) -> str:
    rows = db.month_category_expenses(user_id, key, category)
    emoji = db.emoji_map(user_id).get(category, "🏷")
    if not rows:
        return f"{emoji} <b>{esc(category)} — {month_label(key)}</b>\n\n📭 هزینه‌ای ثبت نشده."
    total = sum(r["amount"] for r in rows)
    lines = [f"{emoji} <b>{esc(category)} — {month_label(key)}</b>",
             f"💰 جمع: <b>{fmt_amount(total)}</b> تومان ({fa_digits(len(rows))} مورد)"]
    ch = change_text(total, db.month_total(user_id, prev_month_key(key), category))
    if ch:
        lines.append(f"↔️ نسبت به ماه قبل: {ch}")
    budget = db.budgets(user_id).get(category)
    if budget:
        pct = percent(total, budget)
        lines.append(f"🎯 بودجه: {status_icon(pct)} {fa_digits(pct)}٪ از {fmt_amount(budget)}")
    lines.append("")
    for r in rows[:MAX_LINES]:
        text = esc(r["note"]) or esc(category)
        lines.append(f"• {fmt_amount(r['amount'])} — {text} · روز {fa_digits(jday(r))}")
    return "\n".join(lines)


def budget_report(db: Database, user_id: int, key: str, days_elapsed: Optional[int] = None) -> str:
    budgets = db.budgets(user_id)
    lines = [f"🎯 <b>بودجهٔ {month_label(key)}</b>", ""]
    if not budgets:
        lines += [
            "هنوز بودجه‌ای تعیین نکرده‌ای.",
            "با «🎯 بودجهٔ کل ماه» سقف خرج ماه را بگذار؛ برای هر دسته هم جدا می‌شود.",
            "وقتی به ۸۰٪ و ۱۰۰٪ برسی خبرت می‌کنم.",
        ]
        return "\n".join(lines)
    length = month_length(key)
    emap = db.emoji_map(user_id)
    total_budget = budgets.get("")
    if total_budget:
        spent = db.month_total(user_id, key)
        pct = percent(spent, total_budget)
        lines.append(f"<b>کل ماه</b> {status_icon(pct)} {fa_digits(pct)}٪")
        lines.append(f"{bar(min(pct, 100))}  {fmt_amount(spent)} از {fmt_amount(total_budget)}")
        left = total_budget - spent
        if left > 0 and days_elapsed and length >= days_elapsed:
            lines.append(f"مانده: {fmt_amount(left)} · تا آخر ماه روزی {fmt_amount(left // (length - days_elapsed + 1))}")
        elif left <= 0:
            lines.append(f"⚠️ {fmt_amount(-left)} بیشتر از بودجه")
        lines.append("")
    for name, amount in sorted(((k, v) for k, v in budgets.items() if k), key=lambda kv: -kv[1]):
        spent = db.month_total(user_id, key, name)
        pct = percent(spent, amount)
        lines.append(f"{emap.get(name, '🏷')} <b>{esc(name)}</b> {status_icon(pct)} {fa_digits(pct)}٪")
        lines.append(f"{bar(min(pct, 100))}  {fmt_amount(spent)} از {fmt_amount(amount)}")
    return "\n".join(lines)


def search_report(rows, query: str, emap: Dict[str, str]) -> str:
    if not rows:
        return f"🔎 برای «{esc(query)}» چیزی پیدا نشد."
    total = sum(r["amount"] for r in rows)
    lines = [f"🔎 <b>نتیجهٔ «{esc(query)}»</b> — {fa_digits(len(rows))} مورد · جمع {fmt_amount(total)}", ""]
    for r in rows:
        d = dt.date.fromisoformat(r["spent_at"][:10])
        jy, jm, jd = to_jalali(d)
        lines.append(f"#{fa_digits(r['id'])} · {fa_digits(f'{jm}/{jd}')}  {expense_line(r, emap)}")
    return "\n".join(lines)


def keywords_report(db: Database, user_id: int) -> str:
    words = db.keywords(user_id)
    lines = ["🧠 <b>کلمه‌هایی که یاد گرفته‌ام</b>", ""]
    if not words:
        lines.append("هنوز چیزی یاد نگرفته‌ام. وقتی دستهٔ یک هزینه را عوض کنی، کلمه‌هایش را یاد می‌گیرم.")
        return "\n".join(lines)
    emap = db.emoji_map(user_id)
    by_cat: Dict[str, List[str]] = {}
    for w, c in words.items():
        by_cat.setdefault(c, []).append(w)
    shown = 0
    for c in sorted(by_cat, key=lambda x: (x == UNKNOWN, x)):
        ws = sorted(by_cat[c])
        lines.append(f"{emap.get(c, '🏷')} <b>{esc(c)}</b>: " + "، ".join(esc(w) for w in ws[:25]))
        shown += 1
        if shown >= 30:
            break
    return "\n".join(lines)


def people_list_report(db: Database, user_id: int) -> str:
    from .categories import DEFAULT_PEOPLE

    own = db.people(user_id)
    lines = ["👤 <b>اشخاص</b>", "", "این کلمه‌ها در متن هزینه، شخص را مشخص می‌کنند:"]
    if own:
        lines += ["", "<b>تعریف‌شده توسط تو:</b>"]
        lines += [f"• {esc(w)} ← {esc(n)}" for w, n in sorted(own.items())]
    lines += ["", "<b>پیش‌فرض:</b> " + "، ".join(esc(w) for w in DEFAULT_PEOPLE)]
    return "\n".join(lines)


def recurring_report(db: Database, user_id: int) -> str:
    items = db.recurring(user_id)
    lines = ["🔁 <b>یادآور ماهانه</b> (قسط، اجاره، اشتراک…)", ""]
    if not items:
        lines += [
            "هنوز چیزی اضافه نکرده‌ای.",
            "روزش که برسد (ساعت ۱۰ صبح) یادآوری می‌کنم و با یک دکمه ثبت می‌شود.",
        ]
        return "\n".join(lines)
    for r in items:
        lines.append(f"• روز {fa_digits(r['day'])} هر ماه: {esc(r['text'])}")
    return "\n".join(lines)
