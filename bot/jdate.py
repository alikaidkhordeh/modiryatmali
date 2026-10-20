"""Small Jalali (Persian) calendar helpers, no third-party dependency."""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional, Tuple

from .textutil import fa_digits

J_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

_G_D_M = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666
        + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd
        + _G_D_M[gm - 1]
    )
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def to_jalali(d) -> Tuple[int, int, int]:
    return gregorian_to_jalali(d.year, d.month, d.day)


def month_key(d) -> str:
    jy, jm, _ = to_jalali(d)
    return f"{jy:04d}-{jm:02d}"


def parse_month_key(s: str) -> Optional[str]:
    m = re.fullmatch(r"\s*(\d{4})[-/](\d{1,2})\s*", s)
    if not m:
        return None
    jy, jm = int(m.group(1)), int(m.group(2))
    if not 1 <= jm <= 12:
        return None
    return f"{jy:04d}-{jm:02d}"


def prev_month_key(key: str) -> str:
    jy, jm = int(key[:4]), int(key[5:7])
    jm -= 1
    if jm == 0:
        jy, jm = jy - 1, 12
    return f"{jy:04d}-{jm:02d}"


def month_label(key: str) -> str:
    jy, jm = int(key[:4]), int(key[5:7])
    return f"{J_MONTHS[jm - 1]} {fa_digits(jy)}"


def is_last_day_of_month(d: _dt.date) -> bool:
    tomorrow = d + _dt.timedelta(days=1)
    return to_jalali(tomorrow)[1] != to_jalali(d)[1]
