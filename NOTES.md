# Developer notes (for future updates)

Personal Persian Telegram expense bot. Python 3.9+, aiogram 3, SQLite, no paid services by default.

## Layout
```
bot/
  main.py         entry point:  python -m bot.main   (polling, sets the short / command menu, announces a new VERSION once, starts scheduler)
  config.py       Settings from .env (BOT_TOKEN, ALLOWED_USER_IDS -> allowed_ids + admin_id = first id, WEBAPP_URL, DB_PATH, optional ANTHROPIC_*)
  handlers.py     all Telegram handlers + keyboards (bottom menu + inline screens; callback data is documented at the top of the file).
                  Two routers: `main` (allowed users) and `guest` (shows their id). `pending` = the open question per user
  parser.py       parse_amount(text) -> ParseResult(amount in toman, note). Pure function, well tested
  smart.py        split_day_hint («دیروز»/«۳ روز پیش»), forecast, budget thresholds, parse_recurring. Pure
  categories.py   CATEGORIES, EMOJI, KEYWORDS (priority order), DEFAULT_PEOPLE, STOPWORDS  <- edit data here
  categorizer.py  learned words -> keywords -> (optional Claude API) -> "سایر"; learn() from button corrections
  db.py           SQLite wrapper (expenses, keywords, people, categories, budgets, recurring, user_settings, meta). Amounts are integers in toman
  reports.py      text reports (HTML for Telegram)
  webapp.py       builds the Mini App link: summary JSON -> base64url in ?d=
  jdate.py        Jalali calendar helpers both ways (no dependency); month key looks like "1405-07"; month_length, day_label
  scheduler.py    every 5 min: month-end report, recurring reminders (10:00), night nudge (22:00, off), Friday backup to admin
  textutil.py     normalize() (digits, Arabic/Persian letters, ZWNJ), fa_digits(), fmt_amount()
docs/index.html   Mini App (static, GitHub Pages, read-only charts). Demo data when opened without ?d=
deploy/           setup.sh, install_service.sh, update.sh (backs up the DB first), modiryatmali.service (systemd)
tests/test_core.py      pure logic (no aiogram needed)
tests/test_handlers.py  every button end to end against tests/fake_telegram.py (needs aiogram). Run all:  venv/bin/python -m pytest -q
```

## Conventions
- Money = integer toman everywhere. Dates: `spent_at` is local ISO time (Asia/Tehran), `j_month` is the Jalali month key.
- Keep logic out of handlers.py; put it in a pure module and add a test. New button -> add it to test_handlers.py.
- Screens are edited with `show()` / `show_markup()` (handlers.py): they edit by chat/message id, so old messages
  (InaccessibleMessage, > 48 h) work, ignore "message is not modified" and fall back to a new message.
  Always answer a callback (`safe_answer`) or the button spins. Every inline screen has a back button.
- Questions to the user go through `ask()` (sets `pending`, adds a ✖️ لغو button); the answer is handled in `handle_pending`.
- Matching rule for keywords: 4+ letters match inside a word, up to 3 letters must be a whole word (optionally with م/ش/ت…). Multi-word phrases are checked before single words; categories are checked in the order of KEYWORDS.
- New tables: add `CREATE TABLE IF NOT EXISTS` to `db.SCHEMA` (runs on every start, so old databases get them).

## Common changes
- **New keyword:** add it to the right list in `categories.py::KEYWORDS`.
- **Categories are per user and editable in the bot** (table `categories`; seeded from `categories.py::CATEGORIES/EMOJI` on first use). `categories.py::KEYWORDS` maps words to *default* category names; a renamed default is followed through table `category_alias`, a deleted one is skipped. "سایر" (`UNKNOWN`) is the fallback and cannot be deleted or renamed. Budgets follow renames.
- **Change the Mini App data:** edit `webapp.build_payload` and `docs/index.html` together (keys are documented at the top of both). The link is trimmed to <= 1900 chars. Old links must keep working (missing keys = feature hidden).
- **New per-user switch:** add it to `db.SETTINGS_DEFAULTS` and `handlers.SETTING_LABELS`.
- **Release note:** bump `main.VERSION` and edit `WHATS_NEW`; users get it once with the fresh bottom keyboard.
- **Claude API categorization (paid, off by default):** `pip install -r requirements-ai.txt` and set `ANTHROPIC_API_KEY` in `.env`.

## Status / known limits
- v2 (2026-10-23): handlers were tested end to end with the fake Telegram in tests/ (aiogram 3.31), not yet by hand in real Telegram.
  After changing handlers, try them in Telegram and check `journalctl -u modiryatmali -f`.
- The Mini App was checked in a browser at phone size (new and old link format, no JS errors); inside Telegram it needs GitHub Pages.
- The Mini App is read-only; edits happen in the bot.
- `pending` questions live in memory: a restart forgets an open question (the user just asks again).

## Ideas for later
Charts of previous months inside the Mini App (month switcher), income / savings tracking, splitting a shared expense, voice messages (speech to text), receipts from photos.
