"""Build the link that opens the Telegram Mini App (docs/index.html).

The page is static (GitHub Pages, free). It gets its numbers from the link itself:
the bot packs a small summary of the month into the `d` query parameter
(JSON -> base64url). No server, domain or tunnel is needed.

Keep the keys below in sync with docs/index.html (see NOTES.md).
  m  month key "1405-07"            t  month total       n  number of expenses
  p  previous month total           c  [[name, total, count, emoji, color slot], ...] biggest first
  d  [total of Jalali day 1, 2, ...]  h  [[month key, total], ...] last months
  u  [[person, total], ...]         x  [[name, amount, note, day, emoji, color slot], ...] biggest
"""
from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Any, Dict, List, Optional

from .db import Database
from .jdate import prev_month_key, to_jalali

MAX_URL_LEN = 1900  # keep well below Telegram's URL limits


def _month_keys_back(key: str, count: int) -> List[str]:
    keys = [key]
    while len(keys) < count:
        keys.append(prev_month_key(keys[-1]))
    return list(reversed(keys))


def build_payload(db: Database, user_id: int, key: str, upto_day: Optional[int] = None) -> Dict[str, Any]:
    rows = db.month_expenses(user_id, key)
    total = sum(r["amount"] for r in rows)

    daily: Dict[int, int] = {}
    for r in rows:
        day = to_jalali(dt.date.fromisoformat(r["spent_at"][:10]))[2]
        daily[day] = daily.get(day, 0) + r["amount"]
    length = max(list(daily) + [upto_day or 0, 1])

    hist_keys = _month_keys_back(key, 6)
    totals = db.month_totals(user_id, hist_keys + [prev_month_key(hist_keys[0])])

    cats = db.categories(user_id)
    slot = {r["name"]: i for i, r in enumerate(cats)}
    emoji = {r["name"]: r["emoji"] for r in cats}

    def info(name: str):
        return emoji.get(name, "🏷"), slot.get(name, len(slot))

    return {
        "m": key,
        "t": total,
        "n": len(rows),
        "p": totals.get(prev_month_key(key), 0),
        "c": [[c["category"], c["total"], c["n"], *info(c["category"])] for c in db.month_by_category(user_id, key)],
        "d": [daily.get(i, 0) for i in range(1, length + 1)],
        "h": [[k, totals.get(k, 0)] for k in hist_keys],
        "u": [[p["person"], p["total"]] for p in db.month_by_person(user_id, key)[:6]],
        "x": [
            [e["category"], e["amount"], (e["note"] or "")[:16],
             to_jalali(dt.date.fromisoformat(e["spent_at"][:10]))[2], *info(e["category"])]
            for e in db.month_top(user_id, key, 6)
        ],
    }


def encode(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def build_url(base_url: str, payload: Dict[str, Any]) -> str:
    """Return the Mini App URL, trimming optional parts if the link would be too long."""
    payload = dict(payload)
    for drop in (None, "x", "u", "h"):
        if drop:
            payload[drop] = []
        url = f"{base_url}{'&' if '?' in base_url else '?'}d={encode(payload)}"
        if len(url) <= MAX_URL_LEN:
            return url
    return url
