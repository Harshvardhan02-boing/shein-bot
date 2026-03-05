"""
db.py — Database layer using Turso (libsql).
Drop-in replacement for local SQLite — same functions, cloud storage.
Env vars required: TURSO_URL and TURSO_TOKEN
"""
import os
import logging
import libsql_experimental as libsql
from typing import Optional, List

logger = logging.getLogger(__name__)

TURSO_URL   = os.environ.get("TURSO_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "")
CATEGORIES  = (500, 1000, 2000, 4000)


def _conn():
    if not TURSO_URL or not TURSO_TOKEN:
        raise RuntimeError("TURSO_URL and TURSO_TOKEN env vars must be set.")
    return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)


def init_db():
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id     INTEGER PRIMARY KEY,
            username        TEXT    DEFAULT '',
            cookies         TEXT,
            protector_on    INTEGER DEFAULT 0,
            created_at      TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS coupons (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     INTEGER NOT NULL,
            code            TEXT    NOT NULL,
            category        INTEGER NOT NULL,
            status          TEXT    DEFAULT 'unknown',
            retrieved       INTEGER DEFAULT 0,
            added_at        TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now')),
            retrieved_at    TEXT,
            UNIQUE(telegram_id, code)
        );
    """)
    conn.commit()
    logger.info("✅ Turso DB ready")


# ── USERS ─────────────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, username: str):
    conn = _conn()
    conn.execute("""
        INSERT INTO users (telegram_id, username) VALUES (?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username
    """, (telegram_id, username or ""))
    conn.commit()


def get_cookies(telegram_id: int) -> Optional[str]:
    conn = _conn()
    row = conn.execute(
        "SELECT cookies FROM users WHERE telegram_id=?", (telegram_id,)
    ).fetchone()
    return row[0] if row else None


def set_cookies(telegram_id: int, cookies: str):
    conn = _conn()
    conn.execute("UPDATE users SET cookies=? WHERE telegram_id=?", (cookies, telegram_id))
    conn.commit()


def set_protector_running(telegram_id: int, running: bool):
    conn = _conn()
    conn.execute("UPDATE users SET protector_on=? WHERE telegram_id=?",
                 (1 if running else 0, telegram_id))
    conn.commit()


def get_users_with_active_protector() -> List[int]:
    conn = _conn()
    rows = conn.execute(
        "SELECT telegram_id FROM users WHERE protector_on=1 AND cookies IS NOT NULL"
    ).fetchall()
    return [r[0] for r in rows]


# ── COUPONS ───────────────────────────────────────────────────────────────────

def add_coupon(telegram_id: int, code: str, category: int, status: str = "unknown") -> bool:
    conn = _conn()
    conn.execute("""
        INSERT OR IGNORE INTO coupons (telegram_id, code, category, status)
        VALUES (?, ?, ?, ?)
    """, (telegram_id, code.upper().strip(), category, status))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM coupons WHERE telegram_id=? AND code=?",
        (telegram_id, code.upper().strip())
    ).fetchone()
    return row is not None


def update_coupon_status(telegram_id: int, code: str, status: str):
    conn = _conn()
    conn.execute("UPDATE coupons SET status=? WHERE telegram_id=? AND code=?",
                 (status, telegram_id, code.upper().strip()))
    conn.commit()


def coupon_exists(telegram_id: int, code: str) -> bool:
    conn = _conn()
    row = conn.execute(
        "SELECT id FROM coupons WHERE telegram_id=? AND code=? AND retrieved=0",
        (telegram_id, code.upper().strip())
    ).fetchone()
    return row is not None


def get_protected_coupons(telegram_id: int) -> List[dict]:
    conn = _conn()
    rows = conn.execute("""
        SELECT id, telegram_id, code, category, status, retrieved, added_at
        FROM coupons WHERE telegram_id=? AND retrieved=0 ORDER BY added_at ASC
    """, (telegram_id,)).fetchall()
    keys = ["id", "telegram_id", "code", "category", "status", "retrieved", "added_at"]
    return [dict(zip(keys, r)) for r in rows]


def get_category_counts(telegram_id: int) -> dict:
    conn = _conn()
    rows = conn.execute("""
        SELECT category, COUNT(*) FROM coupons
        WHERE telegram_id=? AND retrieved=0 GROUP BY category
    """, (telegram_id,)).fetchall()
    counts = {c: 0 for c in CATEGORIES}
    for cat, cnt in rows:
        counts[cat] = cnt
    return counts


def get_status_counts(telegram_id: int) -> dict:
    conn = _conn()
    rows = conn.execute("""
        SELECT status, COUNT(*) FROM coupons
        WHERE telegram_id=? AND retrieved=0 GROUP BY status
    """, (telegram_id,)).fetchall()
    result = {"valid": 0, "invalid": 0, "redeemed": 0, "unknown": 0, "error": 0}
    for status, cnt in rows:
        result[status] = cnt
    return result


def retrieve_coupon(telegram_id: int, category: int) -> Optional[dict]:
    conn = _conn()
    row = conn.execute("""
        SELECT id, code, category FROM coupons
        WHERE telegram_id=? AND category=? AND retrieved=0
        ORDER BY added_at ASC LIMIT 1
    """, (telegram_id, category)).fetchone()
    if not row:
        return None
    conn.execute("""
        UPDATE coupons SET retrieved=1,
        retrieved_at=strftime('%Y-%m-%d %H:%M:%S','now') WHERE id=?
    """, (row[0],))
    conn.commit()
    return {"id": row[0], "code": row[1], "category": row[2]}


def get_all_coupons(telegram_id: int) -> List[dict]:
    conn = _conn()
    rows = conn.execute("""
        SELECT id, telegram_id, code, category, status, retrieved, added_at
        FROM coupons WHERE telegram_id=? AND retrieved=0
        ORDER BY category ASC, added_at ASC
    """, (telegram_id,)).fetchall()
    keys = ["id", "telegram_id", "code", "category", "status", "retrieved", "added_at"]
    return [dict(zip(keys, r)) for r in rows]


def delete_coupon(telegram_id: int, code: str):
    conn = _conn()
    conn.execute("DELETE FROM coupons WHERE telegram_id=? AND code=?",
                 (telegram_id, code.upper().strip()))
    conn.commit()


# ── ADMIN ─────────────────────────────────────────────────────────────────────

def get_all_user_ids() -> List[int]:
    conn = _conn()
    rows = conn.execute("SELECT telegram_id FROM users").fetchall()
    return [r[0] for r in rows]


def get_stats() -> dict:
    conn = _conn()
    users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    coupons = conn.execute("SELECT COUNT(*) FROM coupons WHERE retrieved=0").fetchone()[0]
    active  = conn.execute("SELECT COUNT(*) FROM users WHERE protector_on=1").fetchone()[0]
    rows    = conn.execute(
        "SELECT telegram_id, username, created_at FROM users ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return {
        "users": users,
        "coupons": coupons,
        "active_protectors": active,
        "user_list": [{"telegram_id": r[0], "username": r[1], "created_at": r[2]} for r in rows]
    }
