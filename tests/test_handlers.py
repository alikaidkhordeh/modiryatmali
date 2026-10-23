"""End-to-end button tests with a fake Telegram (needs aiogram:  venv/bin/python -m pytest -q).

Every press must be answered (no endless spinner) and must not hit the error handler.
"""
import asyncio
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

aiogram = pytest.importorskip("aiogram")

from aiogram import Dispatcher  # noqa: E402
from fake_telegram import GUEST, UID, FakeSession, Tg, make_bot  # noqa: E402

from bot.categorizer import Categorizer  # noqa: E402
from bot.config import Settings  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.handlers import BTN_LAST, BTN_MONTH, BTN_PANEL, BTN_PEOPLE, BTN_TODAY, LEGACY_MORE, build_routers  # noqa: E402
from bot.jdate import month_key, to_jalali  # noqa: E402

TZ = ZoneInfo("Asia/Tehran")


def setup(webapp="https://example.github.io/app/"):
    s = FakeSession()
    bot = make_bot(s)
    db = Database(":memory:")
    st = Settings("x", frozenset({UID}), None, "m", ":memory:", TZ, webapp, admin_id=UID)
    dp = Dispatcher()
    main, guest = build_routers(st, db, Categorizer(db))
    dp.include_router(main)
    dp.include_router(guest)
    return Tg(dp, bot, s), db, st


def run(coro):
    asyncio.run(coro)


def today():
    return dt.datetime.now(TZ).date()


def test_expense_card_buttons_and_back():
    async def go():
        tg, db, _ = setup()
        await tg.send("بنزین ۵۰ تومن")
        card = tg.last()
        exp = tg.data(card, "دسته")[2:]
        await tg.press(card, f"c:{exp}")
        assert any("قسط" in t for t, _ in tg.btns(card))
        await tg.press(card, f"b:{exp}")
        assert tg.btns(card)[0][1] == f"c:{exp}"
        await tg.press(card, f"b:{exp}")  # double tap: "message is not modified" must not break it
        await tg.press(card, f"c:{exp}", old=True)  # message older than 48 h
        assert any("قسط" in t for t, _ in tg.btns(card))
        await tg.press(card, f"b:{exp}", old=True)
        assert tg.btns(card)[0][1] == f"c:{exp}"

        # date menu -> yesterday
        await tg.press(card, f"j:{exp}")
        await tg.press(card, f"j:{exp}:1")
        e = db.get_expense(UID, int(exp))
        assert e["spent_at"][:10] == (today() - dt.timedelta(days=1)).isoformat()
        assert "تاریخ ثبت شد" in tg.text(card)

        # amount and note
        await tg.press(card, f"a:{exp}")
        await tg.send("۶۰")
        assert db.get_expense(UID, int(exp))["amount"] == 60_000 and "۶۰٬۰۰۰" in tg.text(card)
        await tg.press(card, f"w:{exp}")
        await tg.send("بنزین سوپر با مامان")
        e = db.get_expense(UID, int(exp))
        assert e["note"] == "بنزین سوپر با مامان" and e["person"] == "مامان"

        # delete needs a confirmation, "نه" goes back
        await tg.press(card, f"d:{exp}")
        assert db.get_expense(UID, int(exp)) is not None
        await tg.press(card, tg.data(card, "نه"))
        await tg.press(card, f"d:{exp}")
        await tg.press(card, tg.data(card, "بله"))
        assert db.get_expense(UID, int(exp)) is None and "حذف شد" in tg.text(card)
        await tg.press(card, f"dy:{exp}")  # already deleted: answered with an alert
    run(go())


def test_unknown_category_new_category_and_learning():
    async def go():
        tg, db, _ = setup()
        await tg.send("بیژن ۲۰۰")
        card = tg.last()
        assert "نشناختم" in tg.text(card)
        exp = tg.btns(card)[0][1].split(":")[1]
        await tg.press(card, tg.data(card, "آموزش"))
        assert db.get_expense(UID, int(exp))["category"] == "آموزش"
        await tg.send("بیژن ۳۰۰")
        assert "آموزش" in tg.text(tg.last())  # learned

        await tg.send("چیز عجیب ۷۰")
        c2 = tg.last()
        await tg.press(c2, tg.data(c2, "دستهٔ جدید"))
        await tg.send("🎮 بازی")
        assert "بازی" in db.category_names(UID)
        assert "دستهٔ جدید ساخته" in tg.text(tg.last())
    run(go())


def test_day_words_multiline_duplicate_and_budget_alert():
    async def go():
        tg, db, _ = setup()
        db.set_budget(UID, "", 100_000)
        await tg.send("دیروز شام ۸۵")
        e = db.last_expenses(UID, 1)[0]
        assert e["spent_at"][:10] == (today() - dt.timedelta(days=1)).isoformat() and e["note"] == "شام"
        if month_key(today() - dt.timedelta(days=1)) == month_key(today()):
            assert "۸۰٪" in tg.text(tg.last())
        await tg.send("نون ۳۰\nنون ۳۰")
        texts = [m["text"] for m in list(tg.s.msgs.values())[-3:]]
        assert any("تکراری" in t for t in texts)
        assert "۲ هزینه ثبت شد" in tg.text(tg.last())
    run(go())


def test_reports_navigation():
    async def go():
        tg, db, _ = setup()
        await tg.send("بنزین ۵۰\nشام با خانمم ۳۸۰\nقسط ماشین ۲ میلیون")
        await tg.send(BTN_MONTH)
        rep = tg.last()
        key = month_key(today())
        assert any(d.startswith("webapp:") for _, d in tg.btns(rep))
        for to in ("k:", "t:", "p:", "u:"):
            await tg.press(rep, f"{to}{key}")
            if to == "k:":
                await tg.press(rep, tg.btns(rep)[0][1])  # one category
                assert "جمع" in tg.text(rep)
                await tg.press(rep, tg.data(rep, "برگشت"))
            if to == "p:":
                await tg.press(rep, tg.data(rep, "خانمم"))
                assert "خانمم" in tg.text(rep)
                await tg.press(rep, tg.data(rep, "برگشت"))
            await tg.press(rep, tg.data(rep, "برگشت"))
            assert tg.text(rep).startswith("📊")
        await tg.press(rep, tg.data(rep, "ماه قبل"))
        await tg.press(rep, tg.data(rep, "ماه جاری"))
        assert tg.text(rep).startswith("📊")

        await tg.send(BTN_TODAY)
        day = tg.last()
        await tg.press(day, tg.data(day, "روز قبل"))
        assert "دیروز" in tg.text(day)
        await tg.press(day, tg.data(day, "امروز"))
        await tg.press(day, tg.data(day, "گزارش ماه"))

        await tg.send(BTN_PEOPLE)
        await tg.send(BTN_LAST)
        assert tg.text(tg.last()).startswith("#")
    run(go())


def test_panel_every_button():
    async def go():
        tg, db, _ = setup()
        await tg.send("بنزین ۵۰")
        await tg.send(BTN_PANEL)
        p = tg.last()
        labels = [t for t, _ in tg.btns(p)]
        assert any("بکاپ" in t for t in labels) and any("یادآور" in t for t in labels)

        # categories: open, move, back, back
        await tg.press(p, "g")
        await tg.press(p, tg.btns(p)[1][1])
        await tg.press(p, tg.data(p, "بالاتر"))
        assert db.category_names(UID)[0] == "ماشین"
        await tg.press(p, tg.data(p, "برگشت"))
        await tg.press(p, tg.data(p, "برگشت"))
        assert "پنل مدیریت" in tg.text(p)

        # budget: total and one category
        await tg.press(p, "B")
        await tg.press(p, "B:t")
        await tg.send("۲۰ میلیون")
        assert db.budgets(UID)[""] == 20_000_000
        b = tg.last()
        await tg.press(b, "B:c")
        await tg.press(b, tg.data(b, "ماشین"))
        await tg.send("۵ میلیون")
        assert db.budgets(UID)["ماشین"] == 5_000_000
        await tg.press(tg.last(), "B:t")
        await tg.send("۰")
        assert "" not in db.budgets(UID)

        # learned words
        await tg.press(p, "L")
        await tg.press(p, "L:a")
        await tg.send("کافی‌نت")
        pick = tg.last()
        await tg.press(pick, tg.data(pick, "آموزش"))
        assert db.keywords(UID)["کافی نت"] == "آموزش"
        await tg.press(pick, "L:d")
        await tg.send("کافی نت")
        assert "کافی نت" not in db.keywords(UID)

        # people
        await tg.press(p, "U")
        await tg.press(p, "U:a")
        await tg.send("نامزدم = خانمم")
        assert db.people(UID)["نامزدم"] == "خانمم"
        await tg.press(p, "U:d")
        await tg.send("نامزدم")
        assert "نامزدم" not in db.people(UID)

        # recurring
        await tg.press(p, "R")
        await tg.press(p, "R:a")
        await tg.send("بنزین ۵۰")  # wrong format: asks again
        assert "متوجه نشدم" in tg.text(tg.last())
        await tg.send("۵ قسط ماشین ۲ میلیون")
        r = tg.last()
        rid = db.recurring(UID)[0]["id"]
        await tg.press(r, f"R:o:{rid}")
        await tg.press(r, f"R:y:{rid}")
        assert db.last_expenses(UID, 1)[0]["category"] == "قسط"
        await tg.press(r, "R")
        await tg.press(r, f"R:d:{rid}")
        assert not db.recurring(UID)

        # settings, help, stats, export, backup, search, undo
        await tg.press(p, "S")
        await tg.press(p, "S:night_reminder")
        assert db.setting(UID, "night_reminder") and "✅" in tg.btns(p)[-2][0]
        await tg.press(p, tg.data(p, "برگشت"))
        for action in ("o:help", "o:stats"):
            await tg.press(p, action)
            await tg.press(p, tg.data(p, "برگشت"))
        await tg.press(p, "o:export")
        await tg.press(p, "o:backup")
        assert tg.s.docs[0].endswith(".csv") and tg.s.docs[1].endswith(".db")
        await tg.press(p, "o:search")
        await tg.send("بنزین")
        res = tg.last()
        assert "نتیجه" in tg.text(res)
        await tg.press(res, tg.btns(res)[0][1])  # open the card
        assert tg.text(tg.last()).startswith("#")
        n = len(db.all_expenses(UID))
        await tg.press(p, "o:undo")
        await tg.press(p, tg.data(p, "بله"))
        assert len(db.all_expenses(UID)) == n - 1 and "پنل مدیریت" in tg.text(p)

        # cancel button closes the question
        await tg.press(p, "B:t")
        await tg.press(tg.last(), "X")
        await tg.send("بنزین ۴۰")
        assert db.last_expenses(UID, 1)[0]["amount"] == 40_000  # a normal expense again
    run(go())


def test_old_keyboard_old_buttons_and_guests():
    async def go():
        tg, db, _ = setup()
        await tg.send(LEGACY_MORE)
        assert "پنل مدیریت" in tg.text(tg.last())
        old = tg.last()
        await tg.press(old, "o:help", old=True)  # button of the old "⚙️ بیشتر" menu
        await tg.press(old, "zz:1")  # unknown/obsolete data is answered too
        await tg.send("/start")
        await tg.send("/panel")
        await tg.send("/budget")
        await tg.send("/find بنزین")
        await tg.send("/cancel")

        guest = Tg(tg.dp, tg.bot, tg.s, uid=GUEST)
        await guest.send("سلام")
        assert "خصوصی" in tg.text(tg.last())
        await guest.press(tg.last(), "P")
        assert "پنل" not in tg.text(tg.last())
    run(go())


def test_scheduler_jobs():
    from bot.scheduler import due_recurring_day, run_jobs

    async def go():
        tg, db, st = setup()
        db.add_recurring(UID, 5, "قسط ماشین 2 میلیون")
        db.add_expense(UID, 1000, "خوراک", None, "نون", "2026-10-22T10:00:00", "1405-07")
        # 5 Aban 1405 = 2026-10-27, 10:00 -> reminder once
        await run_jobs(tg.bot, st, db, dt.datetime(2026, 10, 27, 10, 5, tzinfo=TZ))
        await run_jobs(tg.bot, st, db, dt.datetime(2026, 10, 27, 11, 0, tzinfo=TZ))
        reminders = [m for m in tg.s.msgs.values() if "یادآور ماهانه" in m["text"]]
        assert len(reminders) == 1
        # 30 Mehr 1405 (last day) 21:00 -> month report
        await run_jobs(tg.bot, st, db, dt.datetime(2026, 10, 22, 21, 1, tzinfo=TZ))
        assert any("پایان ماه" in m["text"] for m in tg.s.msgs.values())
        # Friday 2026-10-23 22:30 -> backup
        await run_jobs(tg.bot, st, db, dt.datetime(2026, 10, 23, 22, 31, tzinfo=TZ))
        assert tg.s.docs and tg.s.docs[-1].endswith(".db")

    run(go())
    assert due_recurring_day(31, 30, 30) and not due_recurring_day(31, 29, 30) and due_recurring_day(5, 5, 31)
    assert to_jalali(dt.date(2026, 10, 27)) == (1405, 8, 5)
