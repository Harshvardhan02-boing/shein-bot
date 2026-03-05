"""
db.py — Database layer using Turso (libsql-client).
"""
import os
import logging
import libsql_client
from typing import Optional, List

logger = logging.getLogger(__name__)

TURSO_URL   = os.environ.get("TURSO_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "")
CATEGORIES  = (500, 1000, 2000, 4000)

def _conn():
    if not TURSO_URL or not TURSO_TOKEN:
        raise RuntimeError("TURSO_URL and TURSO_TOKEN env vars must be set.")
    # FIXED: auth_token instead of authToken
    return libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)

def init_db():
    client = _conn()
    client.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id     INTEGER PRIMARY KEY,
            username        TEXT    DEFAULT '',
            cookies         TEXT,
            protector_on    INTEGER DEFAULT 0,
            created_at      TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
        )
    """)
    client.execute("""
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
        )
    """)
    client.close()
    logger.info("✅ Turso DB ready")

# ── USERS ─────────────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, username: str):
    client = _conn()
    client.execute("""
        INSERT INTO users (telegram_id, username) VALUES (?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username
    """, (telegram_id, username or ""))
    client.close()

def get_cookies(telegram_id: int) -> Optional[str]:
    client = _conn()
    result = client.execute("SELECT cookies FROM users WHERE telegram_id=?", (telegram_id,))
    client.close()
    return result.rows[0][0] if result.rows else None

def set_cookies(telegram_id: int, cookies: str):
    client = _conn()
    client.execute("UPDATE users SET cookies=? WHERE telegram_id=?", (cookies, telegram_id))
    client.close()

def set_protector_running(telegram_id: int, running: bool):
    client = _conn()
    client.execute("UPDATE users SET protector_on=? WHERE telegram_id=?", (1 if running else 0, telegram_id))
    client.close()

def get_users_with_active_protector() -> List[int]:
    client = _conn()
    result = client.execute("SELECT telegram_id FROM users WHERE protector_on=1 AND cookies IS NOT NULL")
    client.close()
    return [r[0] for r in result.rows]

# ── COUPONS ───────────────────────────────────────────────────────────────────

def add_coupon(telegram_id: int, code: str, category: int, status: str = "unknown") -> bool:
    client = _conn()
    client.execute("""
        INSERT OR IGNORE INTO coupons (telegram_id, code, category, status)
        VALUES (?, ?, ?, ?)
    """, (telegram_id, code.upper().strip(), category, status))
    result = client.execute("SELECT id FROM coupons WHERE telegram_id=? AND code=?", (telegram_id, code.upper().strip()))
    client.close()
    return len(result.rows) > 0

def update_coupon_status(telegram_id: int, code: str, status: str):
    client = _conn()
    client.execute("UPDATE coupons SET status=? WHERE telegram_id=? AND code=?", (status, telegram_id, code.upper().strip()))
    client.close()

def coupon_exists(telegram_id: int, code: str) -> bool:
    client = _conn()
    result = client.execute("SELECT id FROM coupons WHERE telegram_id=? AND code=? AND retrieved=0", (telegram_id, code.upper().strip()))
    client.close()
    return len(result.rows) > 0

def get_protected_coupons(telegram_id: int) -> List[dict]:
    client = _conn()
    result = client.execute("SELECT id, telegram_id, code, category, status, retrieved, added_at FROM coupons WHERE telegram_id=? AND retrieved=0 ORDER BY added_at ASC", (telegram_id,))
    keys = ["id", "telegram_id", "code", "category", "status", "retrieved", "added_at"]
    client.close()
    return [dict(zip(keys, r)) for r in result.rows]

def get_category_counts(telegram_id: int) -> dict:
    client = _conn()
    result = client.execute("SELECT category, COUNT(*) FROM coupons WHERE telegram_id=? AND retrieved=0 GROUP BY category", (telegram_id,))
    counts = {c: 0 for c in CATEGORIES}
    for cat, cnt in result.rows:
        counts[cat] = cnt
    client.close()
    return counts

def get_status_counts(telegram_id: int) -> dict:
    client = _conn()
    result = client.execute("SELECT status, COUNT(*) FROM coupons WHERE telegram_id=? AND retrieved=0 GROUP BY status", (telegram_id,))
    status_dict = {"valid": 0, "invalid": 0, "redeemed": 0, "unknown": 0, "error": 0}
    for status, cnt in result.rows:
        status_dict[status] = cnt
    client.close()
    return status_dict

def retrieve_coupon(telegram_id: int, category: int) -> Optional[dict]:
    client = _conn()
    result = client.execute("SELECT id, code, category FROM coupons WHERE telegram_id=? AND category=? AND retrieved=0 ORDER BY added_at ASC LIMIT 1", (telegram_id, category))
    if not result.rows:
        client.close()
        return None
    row = result.rows[0]
    client.execute("UPDATE coupons SET retrieved=1, retrieved_at=strftime('%Y-%m-%d %H:%M:%S','now') WHERE id=?", (row[0],))
    client.close()
    return {"id": row[0], "code": row[1], "category": row[2]}

def get_all_coupons(telegram_id: int) -> List[dict]:
    client = _conn()
    result = client.execute("SELECT id, telegram_id, code, category, status, retrieved, added_at FROM coupons WHERE telegram_id=? AND retrieved=0 ORDER BY category ASC, added_at ASC", (telegram_id,))
    keys = ["id", "telegram_id", "code", "category", "status", "retrieved", "added_at"]
    client.close()
    return [dict(zip(keys, r)) for r in result.rows]

def delete_coupon(telegram_id: int, code: str):
    client = _conn()
    client.execute("DELETE FROM coupons WHERE telegram_id=? AND code=?", (telegram_id, code.upper().strip()))
    client.close()

# ── ADMIN ─────────────────────────────────────────────────────────────────────

def get_all_user_ids() -> List[int]:
    client = _conn()
    result = client.execute("SELECT telegram_id FROM users")
    client.close()
    return [r[0] for r in result.rows]

def get_stats() -> dict:
    client = _conn()
    users = client.execute("SELECT COUNT(*) FROM users").rows[0][0]
    coupons = client.execute("SELECT COUNT(*) FROM coupons WHERE retrieved=0").rows[0][0]
    active = client.execute("SELECT COUNT(*) FROM users WHERE protector_on=1").rows[0][0]
    result = client.execute("SELECT telegram_id, username, created_at FROM users ORDER BY created_at DESC LIMIT 50")
    client.close()
    return {
        "users": users,
        "coupons": coupons,
        "active_protectors": active,
        "user_list": [{"telegram_id": r[0], "username": r[1], "created_at": r[2]} for r in result.rows]
    }

def get_user_count() -> int:
    client = _conn()
    result = client.execute("SELECT COUNT(*) FROM users")
    client.close()
    return result.rows[0][0] if result.rows else 0

def get_total_voucher_count() -> int:
    client = _conn()
    result = client.execute("SELECT COUNT(*) FROM coupons")
    client.close()
    return result.rows[0][0] if result.rows else 0

def get_active_protector_count() -> int:
    client = _conn()
    result = client.execute("SELECT COUNT(*) FROM users WHERE protector_on=1")
    client.close()
    return result.rows[0][0] if result.rows else 0

# --- ALIAS FIXES FOR PROTECTOR.PY ---
get_active_coupons = get_all_coupons

def clear_cookies(telegram_id: int):
    set_cookies(telegram_id, None)
