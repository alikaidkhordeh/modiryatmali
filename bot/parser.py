"""Extract the amount (in toman) from a short Persian message.

Examples:
  "بنزین ۵۰ تومن"            -> 50_000
  "قسط ماشین ۲ میلیون"        -> 2_000_000
  "شام با خانمم ۳۸۰"          -> 380_000   (bare numbers below 1000 mean thousands of toman)
  "۲ میلیون و ۵۰۰ هزار"       -> 2_500_000
  "۵۰۰۰۰ ریال"                -> 5_000
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .textutil import normalize

_NUM = r"\d+(?:\.\d+)?"
_PATTERN = re.compile(
    rf"(?P<num>{_NUM})\s*"
    r"(?P<big>میلیارد|میلیون|ملیون|هزار|k)?\s*"
    r"(?P<cur>تومان|تومن|ریال|ت)?(?!\w)"
)

_BIG = {"میلیارد": 10**9, "میلیون": 10**6, "ملیون": 10**6, "هزار": 10**3, "k": 10**3}
_FILLER = {"هزینه", "هزینه‌ی", "خرج", "پرداخت", "پرداختی"}


@dataclass
class ParseResult:
    amount: Optional[int]  # toman, None if no amount was found
    note: str  # the message without the amount part


def _prep(text: str) -> str:
    s = normalize(text)
    s = s.replace("٫", ".")
    # 1,500,000 / 1٬500٬000 / 1،500،000 -> 1500000
    s = re.sub(r"(?<=\d)[,٬،](?=\d{3}(?!\d))", "", s)
    return s


def _group_value(pieces: List[dict]) -> int:
    total = 0.0
    prev_big: Optional[str] = None
    for p in pieces:
        num = float(p["num"])
        big = p["big"]
        if big:
            total += num * _BIG[big]
            prev_big = big
        else:
            if prev_big in ("میلیون", "ملیون", "میلیارد") and num < 1000:
                total += num * 1000  # "2 میلیون و 500" -> 2,500,000
            else:
                total += num
    return int(round(total))


def _strip_filler(note: str) -> str:
    words = [w for w in note.split(" ") if w and w not in _FILLER]
    note = " ".join(words)
    return re.sub(r"\s+", " ", note).strip(" -–—:،,.")


def parse_amount(text: str, small_multiplier: int = 1000, small_threshold: int = 1000) -> ParseResult:
    s = _prep(text)
    matches = list(_PATTERN.finditer(s))
    if not matches:
        return ParseResult(None, _strip_filler(s))

    # Build groups: consecutive matches joined only by "و" (and) when the first has a big unit
    groups: List[List[re.Match]] = []
    for m in matches:
        if groups:
            last = groups[-1][-1]
            between = s[last.end():m.start()]
            if last.group("big") in _BIG and re.fullmatch(r"\s*و\s*", between):
                groups[-1].append(m)
                continue
        groups.append([m])

    def has_unit(g: List[re.Match]) -> bool:
        return any(x.group("big") or x.group("cur") for x in g)

    with_unit = [g for g in groups if has_unit(g)]
    chosen = with_unit[-1] if with_unit else groups[-1]

    pieces = [m.groupdict() for m in chosen]
    total = _group_value(pieces)
    has_big = any(p["big"] for p in pieces)
    is_rial = any(p["cur"] == "ریال" for p in pieces)

    if is_rial:
        total = int(round(total / 10))
    elif not has_big and 0 < total < small_threshold:
        total *= small_multiplier

    if total <= 0:
        return ParseResult(None, _strip_filler(s))

    start, end = chosen[0].start(), chosen[-1].end()
    note = _strip_filler((s[:start] + " " + s[end:]))
    return ParseResult(total, note)
