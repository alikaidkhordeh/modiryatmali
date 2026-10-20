from __future__ import annotations

import re

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"

_TO_ASCII = {}
for _i, _c in enumerate(FA_DIGITS):
    _TO_ASCII[ord(_c)] = str(_i)
for _i, _c in enumerate(AR_DIGITS):
    _TO_ASCII[ord(_c)] = str(_i)

_LETTER_FIX = {ord("ي"): "ی", ord("ك"): "ک", ord("ى"): "ی"}
_TO_FA = str.maketrans("0123456789", FA_DIGITS)


def to_ascii_digits(s: str) -> str:
    return s.translate(_TO_ASCII)


def normalize(s: str) -> str:
    """Lowercase, ASCII digits, unified Persian letters, ZWNJ -> space."""
    s = to_ascii_digits(s)
    s = s.translate(_LETTER_FIX)
    s = s.replace("‌", " ").replace("‏", "").replace("‎", "")
    s = re.sub(r"[ً-ْـ]", "", s)  # harakat and tatweel
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def tokens(s: str) -> list:
    return re.findall(r"\w+", s)


def fa_digits(s) -> str:
    return str(s).translate(_TO_FA)


def fmt_amount(n: int) -> str:
    """50000 -> ۵۰٬۰۰۰"""
    return fa_digits(f"{int(n):,}").replace(",", "٬")
