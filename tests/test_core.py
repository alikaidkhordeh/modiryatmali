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


def test_category_management():
    db, cat = make()
    names = db.category_names(1)
    assert "ماشین" in names and names[-1] == "سایر"
    cid = db.add_category(1, "بازی", "🎮")
    assert cid and db.add_category(1, "بازی", "🎮") is None
    db.add_expense(1, 1000, "بازی", None, "گیم", "2026-10-21T10:00:00", "1405-07")
    # rename moves expenses; the fallback category keeps its name
    assert db.edit_category(1, cid, "گیم", "🕹")
    assert db.month_expenses(1, "1405-07")[0]["category"] == "گیم"
    other = db.category_by_name(1, "خوراک")["id"]
    assert not db.edit_category(1, other, "گیم", "🍽")  # name taken
    fallback = db.category_by_name(1, "سایر")["id"]
    db.edit_category(1, fallback, "چیز دیگر", "📦")
    assert db.category_by_name(1, "سایر") is not None
    assert db.delete_category(1, fallback) == -1
    # delete moves expenses to «سایر»
    assert db.delete_category(1, cid) == 1
    assert db.month_expenses(1, "1405-07")[0]["category"] == "سایر"
    # built-in rename is followed by the keyword dictionary; deleted built-ins are not suggested
    db.edit_category(1, db.category_by_name(1, "ماشین")["id"], "خودرو", "🚗")
    assert classify(cat, "بنزین").category == "خودرو"
    db.delete_category(1, db.category_by_name(1, "قبوض")["id"])
    assert classify(cat, "قبض برق").resolved is False
    # users are independent
    assert "گیم" not in db.category_names(2) and "قبوض" in db.category_names(2)


def test_payload_has_category_names():
    db = Database(":memory:")
    seed(db)
    db.edit_category(1, db.category_by_name(1, "ماشین")["id"], "خودرو", "🚙")
    # (expenses saved before the rename were moved by edit_category)
    p = build_payload(db, 1, "1405-07", upto_day=29)
    names = {c[0]: c for c in p["c"]}
    assert names["خودرو"][3] == "🚙" and isinstance(names["خودرو"][4], int)
    assert p["x"][0][0] == "قسط" and p["x"][0][4] == "💳"


def test_parse_cat_input():
    from bot.handlers import parse_cat_input

    assert parse_cat_input("🎮 بازی") == ("🎮", "بازی")
    assert parse_cat_input("بازی ویدیویی") == (None, "بازی ویدیویی")
    assert parse_cat_input("🎮") == (None, "")


def test_jalali_back_and_month_length():
    from bot.jdate import day_label, jalali_to_gregorian, month_length, weekday_index

    for d in (dt.date(2026, 10, 21), dt.date(2026, 3, 20), dt.date(2026, 3, 21), dt.date(2025, 3, 20), dt.date(2030, 1, 1)):
        assert jalali_to_gregorian(*gregorian_to_jalali(d.year, d.month, d.day)) == d
    assert month_length("1405-01") == 31 and month_length("1405-07") == 30
    assert month_length("1403-12") == 30  # leap year
    assert month_length("1404-12") == 29
    assert weekday_index(dt.date(2026, 10, 24)) == 0  # Saturday
    assert day_label(dt.date(2026, 10, 21)) == "چهارشنبه ۲۹ مهر"


def test_day_hints():
    from bot.smart import split_day_hint

    assert split_day_hint("دیروز بنزین ۵۰") == (1, "بنزین 50")
    assert split_day_hint("شام دیشب با خانمم ۳۸۰") == (1, "شام با خانمم 380")
    assert split_day_hint("پریروز نون ۳۰")[0] == 2
    assert split_day_hint("۳ روز پیش کفش ۵۰۰") == (3, "کفش 500")
    assert split_day_hint("بنزین ۵۰") == (0, "بنزین ۵۰")
    assert split_day_hint("امروزی ۵۰")[0] == 0  # not a whole word
    assert parse_amount(split_day_hint("۳ روز پیش کفش ۵۰۰")[1]).amount == 500_000


def test_forecast_and_budget_thresholds():
    from bot.smart import crossed, forecast, parse_recurring

    assert forecast(1_000_000, 10, 30) == 3_000_000
    assert forecast(1_000_000, 2, 30) is None and forecast(1_000_000, 30, 30) is None
    assert crossed(700, 850, 1000) == 80
    assert crossed(700, 1200, 1000) == 100
    assert crossed(850, 900, 1000) is None
    assert crossed(0, 10, 0) is None
    assert parse_recurring("۵ قسط ماشین ۲ میلیون") == (5, "قسط ماشین 2 میلیون")
    assert parse_recurring("روز ۱۵ اجاره ۸ میلیون") == (15, "اجاره 8 میلیون")
    assert parse_recurring("قسط ماشین ۲ میلیون") is None
    assert parse_recurring("۴۰ اجاره ۸ میلیون") is None
    assert parse_recurring("۵ اجاره") is None


def test_budgets_recurring_settings_backup():
    db = Database(":memory:")
    seed(db)
    db.set_budget(1, "", 3_000_000)
    db.set_budget(1, "خوراک", 300_000)
    text = month_report(db, 1, "1405-07", 29)
    assert "بودجه" in text and "۸۱٪" in text
    from bot.reports import budget_report, category_month_report, search_report

    b = budget_report(db, 1, "1405-07", 29)
    assert "🔴" in b and "خوراک" in b  # 380k of 300k
    # budgets follow a renamed category and disappear with a deleted one
    cid = db.category_by_name(1, "خوراک")["id"]
    db.edit_category(1, cid, "غذا", "🍽")
    assert db.budgets(1).get("غذا") == 300_000
    db.delete_category(1, cid)
    assert "غذا" not in db.budgets(1)
    assert "قسط" in category_month_report(db, 1, "1405-07", "قسط")
    rows = db.search(1, "بنزین")
    assert len(rows) == 1 and "بنزین" in search_report(rows, "بنزین", db.emoji_map(1))

    rid = db.add_recurring(1, 5, "قسط ماشین 2 میلیون")
    assert db.get_recurring(1, rid)["day"] == 5 and db.get_recurring(2, rid) is None
    db.mark_recurring(rid, "1405-08")
    assert db.recurring(1)[0]["last_month"] == "1405-08"
    assert db.delete_recurring(1, rid) and not db.recurring(1)

    assert db.setting(1, "monthly_report") and not db.setting(1, "night_reminder")
    assert db.toggle_setting(1, "night_reminder") and db.setting(1, "night_reminder")
    assert db.backup_bytes().startswith(b"SQLite format 3")


def test_payload_new_keys():
    db = Database(":memory:")
    seed(db)
    db.set_budget(1, "", 5_000_000)
    p = build_payload(db, 1, "1405-07", upto_day=29, made_at=dt.datetime(2026, 10, 21, 14, 5))
    assert p["l"] == 30 and p["e"] == 29 and p["b"] == 5_000_000
    assert p["f"] == round(2_430_000 / 29 * 30)
    assert sum(p["w"]) == 2_430_000 and p["s"] == "1405-07-29 14:05"
    assert len(p["c"][0]) == 6
    past = build_payload(db, 1, "1405-07")  # finished month: full length, no forecast
    assert len(past["d"]) == 30 and past["f"] == 0


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
