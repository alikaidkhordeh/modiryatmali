"""Text reports (HTML for Telegram)."""
from __future__ import annotations

import html
from typing import Optional

from .categories import EMOJI
from .db import Database
from .jdate import month_label
from .textutil import fa_digits, fmt_amount, normalize

MAX_LINES = 40


def esc(s: Optional[str]) -> str:
    return html.escape(s or "")


def expense_line(e) -> str:
    emoji = EMOJI.get(e["category"], "📦")
    text = esc(e["note"]) or esc(e["category"])
    line = f"{emoji} {fmt_amount(e['amount'])} — {text}"
    if e["person"]:
        line += f" 👤{esc(e['person'])}"
    return line


def bar(percent: float, width: int = 10) -> str:
    filled = max(0, min(width, round(percent / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def day_report(db: Database, user_id: int, day: str, title: str = "امروز") -> str:
    rows = db.expenses_on_day(user_id, day)
    if not rows:
        return f"📭 {title} هزینه‌ای ثبت نشده."
    total = sum(r["amount"] for r in rows)
    lines = [f"📅 <b>{title}</b>", f"💰 جمع: <b>{fmt_amount(total)}</b> تومان ({fa_digits(len(rows))} مورد)", ""]
    for r in rows[:MAX_LINES]:
        lines.append(f"{fa_digits(r['spent_at'][11:16])}  {expense_line(r)}")
    if len(rows) > MAX_LINES:
        lines.append(f"… و {fa_digits(len(rows) - MAX_LINES)} مورد دیگر")
    return "\n".join(lines)


def month_report(db: Database, user_id: int, key: str, days_elapsed: Optional[int] = None) -> str:
    cats = db.month_by_category(user_id, key)
    if not cats:
        return f"📭 برای {month_label(key)} هزینه‌ای ثبت نشده."
    total = sum(c["total"] for c in cats)
    count = sum(c["n"] for c in cats)

    lines = [
        f"📊 <b>گزارش {month_label(key)}</b>",
        f"💰 جمع کل: <b>{fmt_amount(total)}</b> تومان ({fa_digits(count)} مورد)",
    ]
    if days_elapsed:
        lines.append(f"📆 میانگین روزانه: {fmt_amount(total // days_elapsed)} تومان")
    lines += ["", "<b>به ترتیب بیشترین مصرف:</b>"]
    for i, c in enumerate(cats, 1):
        pct = c["total"] / total * 100
        emoji = EMOJI.get(c["category"], "📦")
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
        lines.append(f"• {expense_line(e)}")
    return "\n".join(lines)


def top_report(db: Database, user_id: int, key: str, n: int = 10) -> str:
    rows = db.month_top(user_id, key, n)
    if not rows:
        return f"📭 برای {month_label(key)} هزینه‌ای ثبت نشده."
    lines = [f"🔝 <b>بزرگ‌ترین هزینه‌های {month_label(key)}</b>", ""]
    for i, e in enumerate(rows, 1):
        lines.append(f"{fa_digits(i)}. {expense_line(e)}")
    return "\n".join(lines)


def person_report(db: Database, user_id: int, key: str, name: Optional[str] = None) -> str:
    if not name:
        people = db.month_by_person(user_id, key)
        if not people:
            return f"📭 برای {month_label(key)} هزینه‌ای با نام شخص ثبت نشده."
        lines = [f"👤 <b>هزینه‌ها به تفکیک افراد — {month_label(key)}</b>", ""]
        for p in people:
            lines.append(f"• {esc(p['person'])} — <b>{fmt_amount(p['total'])}</b> ({fa_digits(p['n'])} مورد)")
        lines += ["", "برای جزئیات: <code>/person خانمم</code>"]
        return "\n".join(lines)

    want = normalize(name)
    rows = [
        r for r in db.month_expenses(user_id, key)
        if r["person"] and (normalize(r["person"]) == want or normalize(r["person"]).startswith(want))
    ]
    if not rows:
        return f"📭 برای «{esc(name)}» در {month_label(key)} چیزی ثبت نشده."
    total = sum(r["amount"] for r in rows)
    lines = [f"👤 <b>{esc(name)} — {month_label(key)}</b>", f"💰 جمع: <b>{fmt_amount(total)}</b> تومان", ""]
    for r in rows[:MAX_LINES]:
        lines.append(expense_line(r))
    return "\n".join(lines)
