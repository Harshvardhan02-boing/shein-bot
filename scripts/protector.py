"""
protector.py — Per-user rotating coupon protection loop.
"""

import asyncio
import logging
import random
import requests
from typing import Dict

import db
from scripts.shein_api import (
    apply_voucher, interpret_response,
    STATUS_EXPIRED, STATUS_ERROR, STATUS_VALID, STATUS_REDEEMED
)

logger = logging.getLogger(__name__)

CYCLE_PAUSE    = 90    # seconds between full rotation cycles
BETWEEN_APPLY  = (3, 6)  # random seconds between each coupon in a cycle
MAX_CONSEC_FAILS = 5   # notify + stop after this many consecutive failures

_tasks: Dict[int, asyncio.Task] = {}


async def _run_loop(telegram_id: int, bot):
    """Main protection loop for one user. Runs until cancelled or fatal error."""
    logger.info(f"🔒 Protector loop started for user {telegram_id}")
    consecutive_fails = 0

    while True:
        try:
            # REMOVED 'await' because your db functions return a list directly
            cookie_raw = db.get_cookies(telegram_id)
            if not cookie_raw:
                await _notify(bot, telegram_id,
                    "⚠️ *Protector stopped* — no cookies found.\n"
                    "Set your cookies with the 🍪 Set Cookies button.")
                break

            from scripts.shein_api import parse_cookies
            cookie_str = parse_cookies(cookie_raw)

            # REMOVED 'await'
            coupons = db.get_active_coupons(telegram_id)
            if not coupons:
                await asyncio.sleep(CYCLE_PAUSE)
                continue

            cycle_had_error = False
            for coupon in coupons:
                code = coupon["code"]

                # REMOVED 'await'
                if not db.coupon_exists(telegram_id, code):
                    continue

                session = requests.Session()
                try:
                    # Keep this as an executor since apply_voucher is likely synchronous
                    http_status, data = await asyncio.get_event_loop().run_in_executor(
                        None, lambda s=session: apply_voucher(s, cookie_str, code)
                    )
                    status = interpret_response(http_status, data)
                finally:
                    session.close()

                if status == STATUS_EXPIRED:
                    await _notify(bot, telegram_id,
                        "🔴 *Cookies Expired!*\n\n"
                        "Your Shein session has expired. Protection has stopped.\n"
                        "Please update your cookies using 🍪 *Set Cookies*.")
                    db.clear_cookies(telegram_id)
                    return

                elif status == STATUS_ERROR:
                    consecutive_fails += 1
                    logger.warning(f"⚠️ Protect error for {code} (user {telegram_id}) — fail #{consecutive_fails}")
                    cycle_had_error = True

                    if consecutive_fails >= MAX_CONSEC_FAILS:
                        await _notify(bot, telegram_id,
                            f"⚠️ *Protection interrupted*\n\n"
                            f"Failed {MAX_CONSEC_FAILS} times in a row. Protection has paused.")
                        return

                else:
                    consecutive_fails = 0
                    logger.debug(f"✅ Protected {code} for user {telegram_id} — {status}")

                wait = random.uniform(*BETWEEN_APPLY)
                await asyncio.sleep(wait)

            if not cycle_had_error:
                consecutive_fails = 0

            await asyncio.sleep(CYCLE_PAUSE)

        except asyncio.CancelledError:
            logger.info(f"Protector cancelled for user {telegram_id}")
            return
        except Exception as e:
            logger.error(f"Protector loop exception for user {telegram_id}: {e}")
            await asyncio.sleep(30)

    _tasks.pop(telegram_id, None)


async def _notify(bot, telegram_id: int, text: str):
    try:
        await bot.send_message(telegram_id, text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not notify user {telegram_id}: {e}")


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def ensure_running(telegram_id: int, bot) -> bool:
    existing = _tasks.get(telegram_id)
    if existing and not existing.done():
        return False
    task = asyncio.create_task(_run_loop(telegram_id, bot))
    _tasks[telegram_id] = task
    return True

def stop(telegram_id: int) -> bool:
    task = _tasks.get(telegram_id)
    if task and not task.done():
        task.cancel()
        _tasks.pop(telegram_id, None)
        return True
    return False

def is_running(telegram_id: int) -> bool:
    task = _tasks.get(telegram_id)
    return bool(task and not task.done())

async def restore_all(bot):
    """On bot startup — restart loops for all users who have active coupons."""
    # REMOVED 'await' and fixed function name to match your DB recommendation
    users = db.get_users_with_active_protector() 
    
    count = 0
    # Turso/LibSQL returns rows that can be used as dicts or objects
    for row in users:
        # Check if row is a dictionary or an object with an ID attribute
        uid = row["telegram_id"] if isinstance(row, dict) else row[0]
        if not is_running(uid):
            task = asyncio.create_task(_run_loop(uid, bot))
            _tasks[uid] = task
            count += 1
    if count:
        logger.info(f"♻️ Restored {count} protector loop(s) on startup")
