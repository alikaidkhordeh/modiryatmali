from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Dict, List, Optional

from .categories import CATEGORIES, EMOJI, UNKNOWN

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    amount    INTEGER NOT NULL,          -- toman
    category  TEXT NOT NULL,
    person    TEXT,
    note      TEXT NOT NULL DEFAULT '',
    spent_at  TEXT NOT NULL,             -- local time, ISO: 2026-10-21T02:48:00
    j_month   TEXT NOT NULL              -- Jalali month: 1405-07
);
CREATE INDEX IF NOT EXISTS idx_exp_user_month ON expenses(user_id, j_month);
CREATE INDEX IF NOT EXISTS idx_exp_user_date ON expenses(user_id, spent_at);

CREATE TABLE IF NOT EXISTS keywords (
    user_id  INTEGER NOT NULL,
    word     TEXT NOT NULL,
    category TEXT NOT NULL,
    PRIMARY KEY (user_id, word)
);

CREATE TABLE IF NOT EXISTS people (
    user_id INTEGER NOT NULL,
    word    TEXT NOT NULL,
    name    TEXT NOT NULL,
    PRIMARY KEY (user_id, word)
);

CREATE TABLE IF NOT EXISTS categories (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    name     TEXT NOT NULL,
    emoji    TEXT NOT NULL DEFAULT '🏷',
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, name)
);

-- when a built-in category is renamed: old built-in name -> the user's new name
CREATE TABLE IF NOT EXISTS category_alias (
    user_id INTEGER NOT NULL,
    old     TEXT NOT NULL,
    new     TEXT NOT NULL,
    PRIMARY KEY (user_id, old)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- monthly budgets; category '' = the whole month
CREATE TABLE IF NOT EXISTS budgets (
    user_id  INTEGER NOT NULL,
    category TEXT NOT NULL,
    amount   INTEGER NOT NULL,
    PRIMARY KEY (user_id, category)
);

-- monthly reminders (installments, subscriptions): text is a normal expense line
CREATE TABLE IF NOT EXISTS recurring (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    day        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    last_month TEXT NOT NULL DEFAULT ''   -- month key of the last reminder
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""

# per-user switches shown in the panel (key -> default)
SETTINGS_DEFAULTS = {
    "monthly_report": "1",   # report on the last evening of the month
    "budget_alerts": "1",    # warn at 80% / 100% of a budget
    "weekly_backup": "1",    # Friday night database backup (admin only)
    "night_reminder": "0",   # 22:00 nudge when nothing was recorded today
}


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            folder = os.path.dirname(os.path.abspath(path))
            os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- expenses -------------------------------------------------------
    def add_expense(
        self, user_id: int, amount: int, category: str, person: Optional[str],
        note: str, spent_at: str, j_month: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO expenses (user_id, amount, category, person, note, spent_at, j_month) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, amount, category, person, note, spent_at, j_month),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_expense(self, user_id: int, exp_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM expenses WHERE id=? AND user_id=?", (exp_id, user_id)
        ).fetchone()

    def update_category(self, user_id: int, exp_id: int, category: str) -> None:
        self.conn.execute(
            "UPDATE expenses SET category=? WHERE id=? AND user_id=?", (category, exp_id, user_id)
        )
        self.conn.commit()

    def update_amount(self, user_id: int, exp_id: int, amount: int) -> None:
        self.conn.execute(
            "UPDATE expenses SET amount=? WHERE id=? AND user_id=?", (amount, exp_id, user_id)
        )
        self.conn.commit()

    def update_note(self, user_id: int, exp_id: int, note: str, person: Optional[str]) -> None:
        self.conn.execute(
            "UPDATE expenses SET note=?, person=? WHERE id=? AND user_id=?", (note, person, exp_id, user_id)
        )
        self.conn.commit()

    def update_date(self, user_id: int, exp_id: int, spent_at: str, j_month: str) -> None:
        self.conn.execute(
            "UPDATE expenses SET spent_at=?, j_month=? WHERE id=? AND user_id=?",
            (spent_at, j_month, exp_id, user_id),
        )
        self.conn.commit()

    def search(self, user_id: int, query: str, limit: int = 15) -> List[sqlite3.Row]:
        like = f"%{query}%"
        return self.conn.execute(
            "SELECT * FROM expenses WHERE user_id=? AND (note LIKE ? OR category LIKE ? OR person LIKE ?) "
            "ORDER BY id DESC LIMIT ?",
            (user_id, like, like, like, limit),
        ).fetchall()

    def find_recent_same(self, user_id: int, amount: int, note: str, since: str, exclude_id: int) -> bool:
        """Was the same amount + note already saved after `since`? (accidental double send)"""
        row = self.conn.execute(
            "SELECT 1 FROM expenses WHERE user_id=? AND amount=? AND note=? AND spent_at>=? AND id<>? LIMIT 1",
            (user_id, amount, note, since, exclude_id),
        ).fetchone()
        return row is not None

    def month_total(self, user_id: int, j_month: str, category: Optional[str] = None) -> int:
        if category is None:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=? AND j_month=?", (user_id, j_month)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=? AND j_month=? AND category=?",
                (user_id, j_month, category),
            ).fetchone()
        return int(row[0])

    def month_category_expenses(self, user_id: int, j_month: str, category: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM expenses WHERE user_id=? AND j_month=? AND category=? ORDER BY amount DESC",
            (user_id, j_month, category),
        ).fetchall()

    def stats(self, user_id: int) -> Dict[str, object]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total, MIN(spent_at) AS first, "
            "COUNT(DISTINCT j_month) AS months FROM expenses WHERE user_id=?", (user_id,)
        ).fetchone()
        words = self.conn.execute("SELECT COUNT(*) FROM keywords WHERE user_id=?", (user_id,)).fetchone()[0]
        return {"n": row["n"], "total": row["total"], "first": row["first"], "months": row["months"], "words": words}

    def delete_expense(self, user_id: int, exp_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM expenses WHERE id=? AND user_id=?", (exp_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def last_expenses(self, user_id: int, n: int = 5) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM expenses WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, n)
        ).fetchall()

    def expenses_on_day(self, user_id: int, day: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM expenses WHERE user_id=? AND substr(spent_at,1,10)=? ORDER BY id",
            (user_id, day),
        ).fetchall()

    def month_expenses(self, user_id: int, j_month: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM expenses WHERE user_id=? AND j_month=? ORDER BY id",
            (user_id, j_month),
        ).fetchall()

    def month_by_category(self, user_id: int, j_month: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT category, SUM(amount) AS total, COUNT(*) AS n FROM expenses "
            "WHERE user_id=? AND j_month=? GROUP BY category ORDER BY total DESC",
            (user_id, j_month),
        ).fetchall()

    def month_by_person(self, user_id: int, j_month: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT person, SUM(amount) AS total, COUNT(*) AS n FROM expenses "
            "WHERE user_id=? AND j_month=? AND person IS NOT NULL AND person<>'' "
            "GROUP BY person ORDER BY total DESC",
            (user_id, j_month),
        ).fetchall()

    def month_top(self, user_id: int, j_month: str, n: int = 10) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM expenses WHERE user_id=? AND j_month=? ORDER BY amount DESC LIMIT ?",
            (user_id, j_month, n),
        ).fetchall()

    def month_totals(self, user_id: int, keys: List[str]) -> Dict[str, int]:
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        rows = self.conn.execute(
            f"SELECT j_month, SUM(amount) AS total FROM expenses "
            f"WHERE user_id=? AND j_month IN ({marks}) GROUP BY j_month",
            (user_id, *keys),
        ).fetchall()
        return {r["j_month"]: r["total"] for r in rows}

    def all_expenses(self, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM expenses WHERE user_id=? ORDER BY id", (user_id,)
        ).fetchall()

    # ---- categories (per user, editable) ---------------------------------
    def categories(self, user_id: int) -> List[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM categories WHERE user_id=? ORDER BY position, id", (user_id,)
        ).fetchall()
        if not rows:  # first use: seed the defaults
            for i, name in enumerate(CATEGORIES):
                self.conn.execute(
                    "INSERT OR IGNORE INTO categories (user_id, name, emoji, position) VALUES (?,?,?,?)",
                    (user_id, name, EMOJI.get(name, "🏷"), i),
                )
            self.conn.commit()
            rows = self.conn.execute(
                "SELECT * FROM categories WHERE user_id=? ORDER BY position, id", (user_id,)
            ).fetchall()
        return rows

    def category_names(self, user_id: int) -> List[str]:
        return [r["name"] for r in self.categories(user_id)]

    def emoji_map(self, user_id: int) -> Dict[str, str]:
        return {r["name"]: r["emoji"] for r in self.categories(user_id)}

    def get_category(self, user_id: int, cid: int) -> Optional[sqlite3.Row]:
        self.categories(user_id)
        return self.conn.execute(
            "SELECT * FROM categories WHERE id=? AND user_id=?", (cid, user_id)
        ).fetchone()

    def category_by_name(self, user_id: int, name: str) -> Optional[sqlite3.Row]:
        self.categories(user_id)
        return self.conn.execute(
            "SELECT * FROM categories WHERE user_id=? AND name=?", (user_id, name)
        ).fetchone()

    def add_category(self, user_id: int, name: str, emoji: str) -> Optional[int]:
        """Returns the new id, or None if the name already exists."""
        if self.category_by_name(user_id, name):
            return None
        pos = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM categories WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO categories (user_id, name, emoji, position) VALUES (?,?,?,?)",
            (user_id, name, emoji, pos),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def edit_category(self, user_id: int, cid: int, name: str, emoji: str) -> bool:
        """Rename and/or change emoji. Old expenses and learned words follow the new name.
        Returns False if another category already uses that name."""
        cat = self.get_category(user_id, cid)
        if not cat:
            return False
        old = cat["name"]
        if name != old:
            if self.category_by_name(user_id, name):
                return False
            if old == UNKNOWN:  # the fallback category keeps its name
                name = old
        self.conn.execute("UPDATE categories SET name=?, emoji=? WHERE id=?", (name, emoji, cid))
        if name != old:
            self.conn.execute(
                "UPDATE expenses SET category=? WHERE user_id=? AND category=?", (name, user_id, old))
            self.conn.execute(
                "UPDATE keywords SET category=? WHERE user_id=? AND category=?", (name, user_id, old))
            self.conn.execute(
                "UPDATE budgets SET category=? WHERE user_id=? AND category=?", (name, user_id, old))
            self.conn.execute(
                "UPDATE category_alias SET new=? WHERE user_id=? AND new=?", (name, user_id, old))
            self.conn.execute(
                "INSERT INTO category_alias (user_id, old, new) VALUES (?,?,?) "
                "ON CONFLICT(user_id, old) DO UPDATE SET new=excluded.new", (user_id, old, name))
        self.conn.commit()
        return True

    def delete_category(self, user_id: int, cid: int) -> int:
        """Delete a category; its expenses move to «سایر». Returns how many expenses moved (-1 = refused)."""
        cat = self.get_category(user_id, cid)
        if not cat or cat["name"] == UNKNOWN:
            return -1
        name = cat["name"]
        if not self.category_by_name(user_id, UNKNOWN):
            self.add_category(user_id, UNKNOWN, EMOJI.get(UNKNOWN, "📦"))
        moved = self.conn.execute(
            "UPDATE expenses SET category=? WHERE user_id=? AND category=?", (UNKNOWN, user_id, name)
        ).rowcount
        self.conn.execute("DELETE FROM keywords WHERE user_id=? AND category=?", (user_id, name))
        self.conn.execute("DELETE FROM budgets WHERE user_id=? AND category=?", (user_id, name))
        self.conn.execute("DELETE FROM category_alias WHERE user_id=? AND new=?", (user_id, name))
        self.conn.execute("DELETE FROM categories WHERE id=?", (cid,))
        self.conn.commit()
        return moved

    def move_category(self, user_id: int, cid: int, step: int) -> bool:
        """Move a category one place up (-1) or down (+1) in the list."""
        cats = list(self.categories(user_id))
        idx = next((i for i, c in enumerate(cats) if c["id"] == cid), None)
        if idx is None or not 0 <= idx + step < len(cats):
            return False
        cats[idx], cats[idx + step] = cats[idx + step], cats[idx]
        for pos, c in enumerate(cats):
            self.conn.execute("UPDATE categories SET position=? WHERE id=?", (pos, c["id"]))
        self.conn.commit()
        return True

    def category_expense_count(self, user_id: int, name: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE user_id=? AND category=?", (user_id, name)
        ).fetchone()[0]

    def aliases(self, user_id: int) -> Dict[str, str]:
        rows = self.conn.execute(
            "SELECT old, new FROM category_alias WHERE user_id=?", (user_id,)).fetchall()
        return {r["old"]: r["new"] for r in rows}

    # ---- learned keywords / people ------------------------------------
    def set_keyword(self, user_id: int, word: str, category: str) -> None:
        self.conn.execute(
            "INSERT INTO keywords (user_id, word, category) VALUES (?,?,?) "
            "ON CONFLICT(user_id, word) DO UPDATE SET category=excluded.category",
            (user_id, word, category),
        )
        self.conn.commit()

    def keywords(self, user_id: int) -> Dict[str, str]:
        rows = self.conn.execute(
            "SELECT word, category FROM keywords WHERE user_id=?", (user_id,)
        ).fetchall()
        return {r["word"]: r["category"] for r in rows}

    def set_person(self, user_id: int, word: str, name: str) -> None:
        self.conn.execute(
            "INSERT INTO people (user_id, word, name) VALUES (?,?,?) "
            "ON CONFLICT(user_id, word) DO UPDATE SET name=excluded.name",
            (user_id, word, name),
        )
        self.conn.commit()

    def people(self, user_id: int) -> Dict[str, str]:
        rows = self.conn.execute(
            "SELECT word, name FROM people WHERE user_id=?", (user_id,)
        ).fetchall()
        return {r["word"]: r["name"] for r in rows}

    def delete_keyword(self, user_id: int, word: str) -> bool:
        cur = self.conn.execute("DELETE FROM keywords WHERE user_id=? AND word=?", (user_id, word))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_person(self, user_id: int, word: str) -> bool:
        cur = self.conn.execute("DELETE FROM people WHERE user_id=? AND word=?", (user_id, word))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- budgets ---------------------------------------------------------
    def set_budget(self, user_id: int, category: str, amount: int) -> None:
        """category '' = budget for the whole month."""
        self.conn.execute(
            "INSERT INTO budgets (user_id, category, amount) VALUES (?,?,?) "
            "ON CONFLICT(user_id, category) DO UPDATE SET amount=excluded.amount",
            (user_id, category, amount),
        )
        self.conn.commit()

    def delete_budget(self, user_id: int, category: str) -> None:
        self.conn.execute("DELETE FROM budgets WHERE user_id=? AND category=?", (user_id, category))
        self.conn.commit()

    def budgets(self, user_id: int) -> Dict[str, int]:
        rows = self.conn.execute("SELECT category, amount FROM budgets WHERE user_id=?", (user_id,)).fetchall()
        return {r["category"]: r["amount"] for r in rows}

    # ---- recurring reminders ------------------------------------------
    def add_recurring(self, user_id: int, day: int, text: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO recurring (user_id, day, text) VALUES (?,?,?)", (user_id, day, text))
        self.conn.commit()
        return int(cur.lastrowid)

    def recurring(self, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM recurring WHERE user_id=? ORDER BY day, id", (user_id,)).fetchall()

    def get_recurring(self, user_id: int, rid: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM recurring WHERE id=? AND user_id=?", (rid, user_id)).fetchone()

    def delete_recurring(self, user_id: int, rid: int) -> bool:
        cur = self.conn.execute("DELETE FROM recurring WHERE id=? AND user_id=?", (rid, user_id))
        self.conn.commit()
        return cur.rowcount > 0

    def mark_recurring(self, rid: int, j_month: str) -> None:
        self.conn.execute("UPDATE recurring SET last_month=? WHERE id=?", (j_month, rid))
        self.conn.commit()

    # ---- per-user settings --------------------------------------------
    def setting(self, user_id: int, key: str) -> bool:
        row = self.conn.execute(
            "SELECT value FROM user_settings WHERE user_id=? AND key=?", (user_id, key)).fetchone()
        return (row["value"] if row else SETTINGS_DEFAULTS.get(key, "0")) == "1"

    def toggle_setting(self, user_id: int, key: str) -> bool:
        value = not self.setting(user_id, key)
        self.conn.execute(
            "INSERT INTO user_settings (user_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (user_id, key, "1" if value else "0"),
        )
        self.conn.commit()
        return value

    # ---- backup ----------------------------------------------------------
    def backup_bytes(self) -> bytes:
        """A consistent copy of the whole database file (safe while the bot is running)."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            dest = sqlite3.connect(path)
            with dest:
                self.conn.backup(dest)
            dest.close()
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.remove(path)

    # ---- meta ------------------------------------------------------------
    def meta_get(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()
