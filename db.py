"""
db.py — SQLite database layer.
All reads/writes go through here. One file: /data/bot.db
"""

import sqlite3
import os
from typing import Optional, List

DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")

CATEGORIES = [500, 1000, 2000, 4000]


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id  INTEGER PRIMARY KEY,
            username     TEXT    DEFAULT '',
            cookies      TEXT,
            created_at   TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS coupons (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL,
            code         TEXT    NOT NULL,
            category     INTEGER NOT NULL,   -- 500 / 1000 / 2000 / 4000
            status       TEXT    DEFAULT 'valid',  -- valid / retrieved
            added_at     TEXT    DEFAULT (datetime('now')),
            retrieved_at TEXT,
            UNIQUE(telegram_id, code),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
        );
    """)
    c.commit()
    c.close()
    print(f"✅ DB ready at {DB_PATH}")


# ── USERS ─────────────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, username: str):
    c = _conn()
    c.execute("""
        INSERT INTO users (telegram_id, username)
        VALUES (?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username
    """, (telegram_id, username or ""))
    c.commit()
    c.close()


def get_cookies(telegram_id: int) -> Optional[str]:
    c = _conn()
    row = c.execute("SELECT cookies FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    c.close()
    return row["cookies"] if row else None


def set_cookies(telegram_id: int, cookies: str):
    c = _conn()
    c.execute("UPDATE users SET cookies=? WHERE telegram_id=?", (cookies, telegram_id))
    c.commit()
    c.close()


def clear_cookies(telegram_id: int):
    c = _conn()
    c.execute("UPDATE users SET cookies=NULL WHERE telegram_id=?", (telegram_id,))
    c.commit()
    c.close()


def get_all_user_ids() -> List[int]:
    c = _conn()
    rows = c.execute("SELECT telegram_id FROM users").fetchall()
    c.close()
    return [r["telegram_id"] for r in rows]


def get_user_count() -> int:
    c = _conn()
    n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    c.close()
    return n


def get_all_users() -> List[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT telegram_id, username, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_users_with_active_coupons() -> List[int]:
    """Return telegram_ids of users who have active (non-retrieved) coupons AND cookies."""
    c = _conn()
    rows = c.execute("""
        SELECT DISTINCT u.telegram_id
        FROM users u
        JOIN coupons cp ON cp.telegram_id = u.telegram_id
        WHERE cp.status = 'valid'
        AND u.cookies IS NOT NULL
    """).fetchall()
    c.close()
    return [r["telegram_id"] for r in rows]


# ── COUPONS ───────────────────────────────────────────────────────────────────

def add_coupon(telegram_id: int, code: str, category: int) -> bool:
    """Add coupon. Returns False if duplicate."""
    c = _conn()
    try:
        c.execute(
            "INSERT OR IGNORE INTO coupons (telegram_id, code, category, status) VALUES (?,?,?,'valid')",
            (telegram_id, code.upper().strip(), category)
        )
        added = c.total_changes > 0
        c.commit()
        return added
    finally:
        c.close()


def coupon_exists(telegram_id: int, code: str) -> bool:
    c = _conn()
    row = c.execute(
        "SELECT 1 FROM coupons WHERE telegram_id=? AND code=? AND status='valid'",
        (telegram_id, code.upper().strip())
    ).fetchone()
    c.close()
    return row is not None


def get_active_coupons(telegram_id: int) -> List[dict]:
    """All non-retrieved coupons for a user, oldest first."""
    c = _conn()
    rows = c.execute(
        "SELECT * FROM coupons WHERE telegram_id=? AND status='valid' ORDER BY added_at ASC",
        (telegram_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_active_coupons_by_category(telegram_id: int, category: int) -> List[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM coupons WHERE telegram_id=? AND category=? AND status='valid' ORDER BY added_at ASC",
        (telegram_id, category)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_category_counts(telegram_id: int) -> dict:
    """Returns {500: 2, 1000: 0, 2000: 3, 4000: 1}"""
    c = _conn()
    rows = c.execute(
        "SELECT category, COUNT(*) as cnt FROM coupons WHERE telegram_id=? AND status='valid' GROUP BY category",
        (telegram_id,)
    ).fetchall()
    c.close()
    counts = {cat: 0 for cat in CATEGORIES}
    for row in rows:
        counts[row["category"]] = row["cnt"]
    return counts


def retrieve_coupon(telegram_id: int, category: int) -> Optional[str]:
    """
    Get oldest valid coupon of given category.
    Marks it as retrieved. Returns code or None if empty.
    """
    c = _conn()
    try:
        row = c.execute(
            """SELECT id, code FROM coupons
               WHERE telegram_id=? AND category=? AND status='valid'
               ORDER BY added_at ASC LIMIT 1""",
            (telegram_id, category)
        ).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE coupons SET status='retrieved', retrieved_at=datetime('now') WHERE id=?",
            (row["id"],)
        )
        c.commit()
        return row["code"]
    finally:
        c.close()


def get_total_coupon_count() -> int:
    c = _conn()
    n = c.execute("SELECT COUNT(*) FROM coupons WHERE status='valid'").fetchone()[0]
    c.close()
    return n
