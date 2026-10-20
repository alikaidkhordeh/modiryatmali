from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional

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
