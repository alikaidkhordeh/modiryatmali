"""Small "smart" helpers, all pure functions (tested in tests/test_core.py).

  split_day_hint   "دیروز بنزین ۵۰" -> (1, "بنزین ۵۰")
  forecast         month-end projection from what was spent so far
  crossed          which budget threshold (80% / 100%) an expense just crossed
  parse_recurring  "۵ قسط ماشین ۲ میلیون" -> (5, "قسط ماشین ۲ میلیون")
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from .parser import parse_amount
from .textutil import normalize

MAX_DAYS_BACK = 60
THRESHOLDS = (80, 100)

_WORD_HINTS = (
    (r"پس\s*پریروز", 3),
    (r"پریروز", 2),
    (r"پریشب", 2),
    (r"دیروز", 1),
    (r"دیشب", 1),
    (r"امروز", 0),
    (r"امشب", 0),
)
_N_DAYS = re.compile(r"(\d{1,2})\s*روز\s*(?:پیش|قبل)")


def split_day_hint(text: str) -> Tuple[int, str]:
    """Find a day word and return (days back, text without it). Runs before the amount parser,
    so the number in «۳ روز پیش» is not read as the amount."""
    s = normalize(text)
    m = _N_DAYS.search(s)
    if m:
        days = min(int(m.group(1)), MAX_DAYS_BACK)
        return days, _clean(s[:m.start()] + " " + s[m.end():])
    for pattern, days in _WORD_HINTS:
        m = re.search(rf"(?<!\w){pattern}(?!\w)", s)
        if m:
            # «دیشب شام» keeps «شام»; the hint word itself is dropped
            return days, _clean(s[:m.start()] + " " + s[m.end():])
    return 0, text.strip()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" -–—:،,.")


def forecast(total: int, elapsed: int, length: int) -> Optional[int]:
    """Month-end estimate at the current pace. None when it is too early (or the month is over)."""
    if elapsed < 3 or elapsed >= length or total <= 0:
        return None
    return int(round(total / elapsed * length))


def percent(spent: int, budget: int) -> int:
    return int(round(spent / budget * 100)) if budget > 0 else 0


def crossed(before: int, after: int, budget: int) -> Optional[int]:
    """The highest threshold (80 / 100 %) passed by going from `before` to `after`."""
    if budget <= 0:
        return None
    hit = None
    for t in THRESHOLDS:
        limit = budget * t / 100
        if before < limit <= after:
            hit = t
    return hit


def status_icon(pct: int) -> str:
    return "🟢" if pct < 80 else ("🟡" if pct < 100 else "🔴")


_REC = re.compile(r"^(?:روز\s*)?(\d{1,2})\s*(?:ام|م)?\s*[:\-،,]?\s+(.+)$")


def parse_recurring(text: str) -> Optional[Tuple[int, str]]:
    """'۵ قسط ماشین ۲ میلیون' / 'روز ۵ قسط …' -> (5, 'قسط ماشین ۲ میلیون'). The rest must contain an amount."""
    s = normalize(text)
    m = _REC.match(s)
    if not m:
        return None
    day, rest = int(m.group(1)), m.group(2).strip()
    if not 1 <= day <= 31 or parse_amount(rest).amount is None:
        return None
    return day, rest
