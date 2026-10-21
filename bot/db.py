from __future__ import annotations

import os
import sqlite3
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
"""


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
        self.conn.execute("DELETE FROM category_alias WHERE user_id=? AND new=?", (user_id, name))
        self.conn.execute("DELETE FROM categories WHERE id=?", (cid,))
        self.conn.commit()
        return moved

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
