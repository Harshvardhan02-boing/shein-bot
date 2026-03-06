"""
db.py — Database layer using Turso (libsql-client).
"""
import os
import logging
import libsql_client
from typing import Optional, List

logger = logging.getLogger(__name__)

TURSO_URL   = os.environ.get("TURSO_URL", "").strip()
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "").strip()
CATEGORIES  = (500, 1000, 2000, 4000)

def _conn():
    if not TURSO_URL or not TURSO_TOKEN:
        raise RuntimeError("TURSO_URL and TURSO_TOKEN env vars must be set.")
    
    safe_url = TURSO_URL
    if safe_url.startswith("libsql://"):
        safe_url = safe_url.replace("libsql://", "https://", 1)
        
    return libsql_client.create_client_sync(url=safe_url, auth_token=TURSO_TOKEN)

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
    """, [telegram_id, username or ""])
    client.close()

def get_cookies(telegram_id: int) -> Optional[str]:
    client = _conn()
    result = client.execute("SELECT cookies FROM users WHERE telegram_id=?", [telegram_id])
    client.close()
    return str(result.rows[0][0]) if result.rows else None

def set_cookies(telegram_id: int, cookies: str):
    client = _conn()
    client.execute("UPDATE users SET cookies=? WHERE telegram_id=?", [cookies, telegram_id])
    client.close()

def set_protector_running(telegram_id: int, running: bool):
    client = _conn()
    client.execute("UPDATE users SET protector_on=? WHERE telegram_id=?", [1 if running else 0, telegram_id])
    client.close()

def get_users_with_active_protector() -> List[int]:
    client = _conn()
    result = client.execute("SELECT telegram_id FROM users WHERE protector_on=1")
    client.close()
    return [int(r[0]) for r in result.rows]

# ── COUPONS ───────────────────────────────────────────────────────────────────

def add_coupon(telegram_id: int, code: str, category: int, status: str = "unknown") -> bool:
    client = _conn()
    client.execute("""
        INSERT INTO coupons (telegram_id, code, category, status, retrieved)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(telegram_id, code) DO UPDATE SET 
            retrieved=0, category=excluded.category, status=excluded.status
    """, [telegram_id, code.upper().strip(), int(category), status])
    
    result = client.execute("SELECT id FROM coupons WHERE telegram_id=? AND code=?", [telegram_id, code.upper().strip()])
    client.close()
    return len(result.rows) > 0

def update_coupon_status(telegram_id: int, code: str, status: str):
    client = _conn()
    client.execute("UPDATE coupons SET status=? WHERE telegram_id=? AND code=?", [status, telegram_id, code.upper().strip()])
    client.close()

def coupon_exists(telegram_id: int, code: str) -> bool:
    client = _conn()
    result = client.execute("SELECT id FROM coupons WHERE telegram_id=? AND code=? AND retrieved=0", [telegram_id, code.upper().strip()])
    client.close()
    return len(result.rows) > 0

def get_protected_coupons(telegram_id: int) -> List[dict]:
    client = _conn()
    result = client.execute("SELECT id, telegram_id, code, category, status, retrieved, added_at FROM coupons WHERE telegram_id=? AND retrieved=0 ORDER BY added_at ASC", [telegram_id])
    keys = ["id", "telegram_id", "code", "category", "status", "retrieved", "added_at"]
    client.close()
    return [dict(zip(keys, r)) for r in result.rows]

def get_category_counts(telegram_id: int) -> dict:
    client = _conn()
    result = client.execute("SELECT category, COUNT(*) FROM coupons WHERE telegram_id=? AND retrieved=0 GROUP BY category", [telegram_id])
    counts = {c: 0 for c in CATEGORIES}
    for row in result.rows:
        try:
            cat = int(row[0]) 
            cnt = int(row[1])
            if cat in counts: counts[cat] = cnt
        except (ValueError, TypeError): continue
    client.close()
    return counts

def retrieve_multiple_coupons(telegram_id: int, category: int, limit: int) -> List[str]:
    client = _conn()
    limit_clause = f"LIMIT {int(limit)}" if limit > 0 else ""
    result = client.execute(f"SELECT id, code FROM coupons WHERE telegram_id=? AND category=? AND retrieved=0 ORDER BY added_at ASC {limit_clause}", [telegram_id, int(category)])
    
    if not result.rows:
        client.close()
        return []
    
    codes = []
    ids = []
    for row in result.rows:
        ids.append(str(int(row[0])))
        codes.append(str(row[1]))
        
    if ids:
        ids_str = ",".join(ids)
        client.execute(f"UPDATE coupons SET retrieved=1, retrieved_at=strftime('%Y-%m-%d %H:%M:%S','now') WHERE id IN ({ids_str})")
        
    client.close()
    return codes

def get_user_history(telegram_id: int) -> dict:
    client = _conn()
    res_active = client.execute("SELECT code, category, added_at FROM coupons WHERE telegram_id=? AND retrieved=0 ORDER BY category ASC, added_at ASC", [telegram_id])
    active = [{"code": str(r[0]), "category": int(r[1]), "added_at": str(r[2])} for r in res_active.rows]
    
    res_retrieved = client.execute("SELECT code, category, retrieved_at FROM coupons WHERE telegram_id=? AND retrieved=1 AND retrieved_at >= datetime('now', '-3 days') ORDER BY retrieved_at DESC", [telegram_id])
    retrieved = [{"code": str(r[0]), "category": int(r[1]), "retrieved_at": str(r[2])} for r in res_retrieved.rows]
    
    client.close()
    return {"active": active, "retrieved": retrieved}

def get_all_coupons(telegram_id: int) -> List[dict]:
    client = _conn()
    result = client.execute("SELECT id, telegram_id, code, category, status, retrieved, added_at FROM coupons WHERE telegram_id=? AND retrieved=0 ORDER BY category ASC, added_at ASC", [telegram_id])
    keys = ["id", "telegram_id", "code", "category", "status", "retrieved", "added_at"]
    client.close()
    return [dict(zip(keys, r)) for r in result.rows]

def delete_coupon(telegram_id: int, code: str):
    client = _conn()
    client.execute("DELETE FROM coupons WHERE telegram_id=? AND code=?", [telegram_id, code.upper().strip()])
    client.close()

# ── ADMIN ─────────────────────────────────────────────────────────────────────

def get_all_user_ids() -> List[int]:
    client = _conn()
    result = client.execute("SELECT telegram_id FROM users")
    client.close()
    return [int(r[0]) for r in result.rows]

def get_users_with_coupon_counts() -> List[dict]:
    client = _conn()
    try:
        result = client.execute("""
            SELECT u.telegram_id, u.username, COUNT(c.id) 
            FROM users u 
            LEFT JOIN coupons c ON u.telegram_id = c.telegram_id AND c.retrieved = 0 
            WHERE u.telegram_id != 0
            GROUP BY u.telegram_id
            ORDER BY COUNT(c.id) DESC
        """)
        users = []
        for r in result.rows:
            try:
                uid = int(r[0])
                uname = str(r[1]) if r[1] else "unknown"
                count = int(float(r[2]))
                users.append({"telegram_id": uid, "username": uname, "active_count": count})
            except Exception:
                continue
        client.close()
        return users
    except Exception as e:
        logger.error(f"Failed to fetch user list: {e}")
        client.close()
        return []

def get_user_count() -> int:
    client = _conn()
    result = client.execute("SELECT COUNT(*) FROM users WHERE telegram_id != 0")
    client.close()
    return int(float(result.rows[0][0])) if result.rows else 0

def get_total_voucher_count() -> int:
    client = _conn()
    result = client.execute("SELECT COUNT(*) FROM coupons WHERE retrieved=0")
    client.close()
    return int(float(result.rows[0][0])) if result.rows else 0

# 🔴 THIS WAS MISSING: The function that counts active loops
def get_active_protector_count() -> int:
    client = _conn()
    result = client.execute("SELECT COUNT(*) FROM users WHERE protector_on=1")
    client.close()
    return int(float(result.rows[0][0])) if result.rows else 0

get_active_coupons = get_all_coupons

def clear_cookies(telegram_id: int):
    set_cookies(telegram_id, None)
