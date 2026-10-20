"""Run with:  python -m pytest -q   (or:  python tests/test_core.py)"""
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.categorizer import Categorizer, parse_ai_reply  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.jdate import gregorian_to_jalali, is_last_day_of_month, month_key, month_label, prev_month_key  # noqa: E402
from bot.parser import parse_amount  # noqa: E402
from bot.reports import day_report, month_report, person_report  # noqa: E402
from bot.webapp import build_payload, build_url, encode  # noqa: E402


# ---- parser ---------------------------------------------------------------
def amount(text):
    return parse_amount(text).amount


def test_amounts():
    assert amount("بنزین ۵۰ تومن") == 50_000
    assert amount("قسط ماشین ۲ میلیون") == 2_000_000
    assert amount("شام با خانمم ۳۸۰") == 380_000
    assert amount("۲ میلیون و ۵۰۰ هزار") == 2_500_000
    assert amount("۲ میلیون و ۵۰۰") == 2_500_000
    assert amount("نون ۱٬۲۰۰٬۰۰۰ تومان") == 1_200_000
    assert amount("taxi 150000") == 150_000
    assert amount("اجاره ۲.۵ میلیون") == 2_500_000
    assert amount("۵۰۰۰۰ ریال") == 5_000
    assert amount("۵۰ هزار تومن بنزین") == 50_000
    assert amount("۲ تا نون ۳۰ تومن") == 30_000
    assert amount("بنزین") is None
    assert amount("۱,۵۰۰,۰۰۰ قسط") == 1_500_000


def test_note():
    assert parse_amount("هزینه بنزین ۵۰ تومن").note == "بنزین"
    assert parse_amount("۴۵ تومن").note == ""


# ---- jalali ----------------------------------------------------------------
def test_jalali():
    assert gregorian_to_jalali(2026, 10, 21) == (1405, 7, 29)
    assert gregorian_to_jalali(2026, 3, 21) == (1405, 1, 1)
    assert month_key(dt.date(2026, 10, 21)) == "1405-07"
    assert prev_month_key("1405-01") == "1404-12"
    assert month_label("1405-07") == "مهر ۱۴۰۵"
    assert is_last_day_of_month(dt.date(2026, 10, 22))  # 30 Mehr
    assert not is_last_day_of_month(dt.date(2026, 10, 21))


# ---- categorizer -------------------------------------------------------------
def make():
    db = Database(":memory:")
    return db, Categorizer(db)


def classify(cat, text, uid=1):
    return asyncio.run(cat.classify(uid, text))


def test_categories():
    db, cat = make()
    assert classify(cat, "بنزین").category == "ماشین"
    assert classify(cat, "قسط ماشین").category == "قسط"
    assert classify(cat, "قسطم").category == "قسط"
    assert classify(cat, "شارژ ساختمان").category == "خانه"
    assert classify(cat, "قبض برق").category == "قبوض"
    assert classify(cat, "اسنپ فود").category == "خوراک"
    assert classify(cat, "اسنپ").category == "حمل و نقل"
    assert classify(cat, "شامپو").category == "زیبایی"  # not "شام" (food)
    assert classify(cat, "نتفلیکس").category == "تفریح"  # not "نت" (bills)
    r = classify(cat, "شام با خانمم")
    assert (r.category, r.person) == ("خوراک", "خانمم")
    assert classify(cat, "چیز عجیب").resolved is False


def test_learning():
    db, cat = make()
    assert not classify(cat, "بیژن").resolved
    cat.learn(1, "بیژن", "آموزش")
    assert classify(cat, "بیژن").category == "آموزش"
    assert classify(cat, "بیژن", uid=2).resolved is False  # per user
    db.set_person(1, "سارا", "سارا")
    assert classify(cat, "با سارا شام").person == "سارا"


def test_ai_reply_parsing():
    assert parse_ai_reply('x {"category": "خوراک", "person": null} y') == {"category": "خوراک", "person": None}
    assert parse_ai_reply('{"category": "نامعتبر"}') is None
    assert parse_ai_reply("no json") is None


# ---- db / reports / webapp ------------------------------------------------------
def seed(db):
    rows = [
        (50_000, "ماشین", None, "بنزین", "2026-10-20T10:00:00"),
        (2_000_000, "قسط", None, "قسط ماشین", "2026-10-21T09:00:00"),
        (380_000, "خوراک", "خانمم", "شام با خانمم", "2026-10-21T21:00:00"),
    ]
    for amt, c, p, note, at in rows:
        db.add_expense(1, amt, c, p, note, at, month_key(dt.date.fromisoformat(at[:10])))


def test_reports_and_payload():
    db = Database(":memory:")
    seed(db)
    text = month_report(db, 1, "1405-07", 29)
    assert "۲٬۴۳۰٬۰۰۰" in text
    assert text.index("قسط") < text.index("خوراک") < text.index("ماشین")  # biggest first
    assert "خانمم" in person_report(db, 1, "1405-07")
    assert "۲۱:۰۰" in day_report(db, 1, "2026-10-21")
    assert "هزینه‌ای ثبت نشده" in month_report(db, 1, "1400-01")

    payload = build_payload(db, 1, "1405-07", upto_day=29)
    assert payload["t"] == 2_430_000 and payload["n"] == 3 and len(payload["d"]) == 29
    assert payload["d"][28] == 2_380_000  # 29 Mehr
    url = build_url("https://example.github.io/app/", payload)
    assert len(url) <= 1900 and url.startswith("https://example.github.io/app/?d=")


def test_url_is_trimmed_when_too_long():
    db = Database(":memory:")
    for i in range(300):
        db.add_expense(1, 1000 + i, "خوراک", "شخص" * 5, "یادداشت طولانی " * 3, "2026-10-21T10:00:00", "1405-07")
    url = build_url("https://x.io/", build_payload(db, 1, "1405-07", 29))
    assert len(url) <= 1900


def test_encode_roundtrip():
    import base64

    payload = {"m": "1405-07", "u": [["خانمم", 5]]}
    s = encode(payload)
    back = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8"))
    assert back == payload


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
