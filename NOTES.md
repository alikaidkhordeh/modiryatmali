# Developer notes (for future updates)

Personal Persian Telegram expense bot. Python 3.9+, aiogram 3, SQLite, no paid services by default.

## Layout
```
bot/
  main.py         entry point:  python -m bot.main   (polling, removes the slash-command menu, starts scheduler)
  config.py       Settings from .env (BOT_TOKEN, ALLOWED_USER_IDS, WEBAPP_URL, DB_PATH, optional ANTHROPIC_*)
  handlers.py     all Telegram handlers + keyboards (bottom menu + inline buttons; callback data is documented at the top of the file). Two routers: `main` (allowed users) and `guest` (shows their id)
  parser.py       parse_amount(text) -> ParseResult(amount in toman, note). Pure function, well tested
  categories.py   CATEGORIES, EMOJI, KEYWORDS (priority order), DEFAULT_PEOPLE, STOPWORDS  <- edit data here
  categorizer.py  learned words -> keywords -> (optional Claude API) -> "سایر"; learn() from button corrections
  db.py           SQLite wrapper (expenses, keywords, people, meta). Amounts are integers in toman
  reports.py      text reports (HTML for Telegram)
  webapp.py       builds the Mini App link: summary JSON -> base64url in ?d=
  jdate.py        Jalali calendar helpers (no dependency); month key looks like "1405-07"
  scheduler.py    month-end report loop (21:00 on the last Jalali day)
  textutil.py     normalize() (digits, Arabic/Persian letters, ZWNJ), fa_digits(), fmt_amount()
docs/index.html   Mini App (static, GitHub Pages, read-only charts). Demo data when opened without ?d=
deploy/           setup.sh, install_service.sh, update.sh, modiryatmali.service (systemd)
tests/test_core.py  python tests/test_core.py   (or pytest) - covers everything except aiogram handlers
```

## Conventions
- Money = integer toman everywhere. Dates: `spent_at` is local ISO time (Asia/Tehran), `j_month` is the Jalali month key.
- Keep logic out of handlers.py; put it in a pure module and add a test.
- Matching rule for keywords: 4+ letters match inside a word, up to 3 letters must be a whole word (optionally with م/ش/ت…). Multi-word phrases are checked before single words; categories are checked in the order of KEYWORDS.

## Common changes
- **New keyword:** add it to the right list in `categories.py::KEYWORDS`.
- **Categories are per user and editable in the bot** (table `categories`; seeded from `categories.py::CATEGORIES/EMOJI` on first use). `categories.py::KEYWORDS` maps words to *default* category names; a renamed default is followed through table `category_alias`, a deleted one is skipped. "سایر" (`UNKNOWN`) is the fallback and cannot be deleted or renamed. To add a new *default* category: add it to `CATEGORIES`, `EMOJI`, `KEYWORDS` (only affects new users).
- **Change the Mini App data:** edit `webapp.build_payload` and `docs/index.html` together (keys are documented at the top of `webapp.py`). The link is trimmed to <= 1900 chars.
- **Claude API categorization (paid, off by default):** `pip install -r requirements-ai.txt` and set `ANTHROPIC_API_KEY` in `.env`.

## Status / known limits
- The handler flows (add/edit/delete category, expense card buttons, reports, pending input) were exercised end to end with a small fake aiogram in the build sandbox (no PyPI access there), not yet with real Telegram. After changing handlers, try them by hand in Telegram and check `journalctl -u modiryatmali -f`.
- The Mini App page was checked for JS errors with a stubbed DOM (demo and real payload); it needs GitHub Pages enabled to open inside Telegram.
- The Mini App is read-only; edits happen in the bot (/last).

## Ideas for later
Reordering categories, monthly budget per category with warnings, recurring expenses (monthly installments added automatically), search (`/find`), note editing, charts of previous months inside the Mini App, automatic DB backup to Telegram.
