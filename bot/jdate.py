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


def next_month_key(key: str) -> str:
    jy, jm = int(key[:4]), int(key[5:7])
    jm += 1
    if jm == 13:
        jy, jm = jy + 1, 1
    return f"{jy:04d}-{jm:02d}"


def month_label(key: str) -> str:
    jy, jm = int(key[:4]), int(key[5:7])
    return f"{J_MONTHS[jm - 1]} {fa_digits(jy)}"


def is_last_day_of_month(d: _dt.date) -> bool:
    tomorrow = d + _dt.timedelta(days=1)
    return to_jalali(tomorrow)[1] != to_jalali(d)[1]


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> _dt.date:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    month_days = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > month_days[gm]:
        gd -= month_days[gm]
        gm += 1
    return _dt.date(gy, gm, gd)


def month_length(key: str) -> int:
    """Number of days in a Jalali month (29/30 for Esfand, depending on the leap year)."""
    jy, jm = int(key[:4]), int(key[5:7])
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    first = jalali_to_gregorian(jy, 12, 1)
    nxt = jalali_to_gregorian(jy + 1, 1, 1)
    return (nxt - first).days


WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


def weekday_index(d) -> int:
    """0 = Saturday … 6 = Friday (the Persian week)."""
    return (d.weekday() + 2) % 7


def day_label(d) -> str:
    """'سه‌شنبه ۲۹ مهر'"""
    _, jm, jd = to_jalali(d)
    return f"{WEEKDAYS[weekday_index(d)]} {fa_digits(jd)} {J_MONTHS[jm - 1]}"
