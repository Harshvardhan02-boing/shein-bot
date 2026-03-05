"""
protector.py — Per-user rotating coupon protection loop.

One loop per user. Rotates through ALL their coupons continuously:
  apply code1 → wait → apply code2 → wait → ... → cycle pause → repeat

Adding a coupon: joins next cycle automatically (fetched fresh from DB each cycle).
Removing a coupon: removed from DB, silently skipped next cycle.
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

# One task per user: telegram_id → asyncio.Task
_tasks: Dict[int, asyncio.Task] = {}


async def _run_loop(telegram_id: int, bot):
    """Main protection loop for one user. Runs until cancelled or fatal error."""
    logger.info(f"🔒 Protector loop started for user {telegram_id}")
    consecutive_fails = 0

    while True:
        try:
            # ── Fetch fresh coupon list and cookies every cycle ──────────────
            # Note: Changed to await for Turso compatibility
            cookie_raw = await db.get_cookies(telegram_id)
            if not cookie_raw:
                await _notify(bot, telegram_id,
                    "⚠️ *Protector stopped* — no cookies found.\n"
                    "Set your cookies with the 🍪 Set Cookies button.")
                break

            from scripts.shein_api import parse_cookies
            cookie_str = parse_cookies(cookie_raw)

            # Note: Changed to await for Turso compatibility
            coupons = await db.get_active_coupons(telegram_id)
            if not coupons:
                # No coupons left — pause and check again later
                await asyncio.sleep(CYCLE_PAUSE)
                continue

            # ── Rotate through each coupon ───────────────────────────────────
            cycle_had_error = False
            for coupon in coupons:
                code = coupon["code"]

                # Check if coupon was removed mid-cycle
                if not await db.coupon_exists(telegram_id, code):
                    continue

                session = requests.Session()
                try:
                    http_status, data = await asyncio.get_event_loop().run_in_executor(
                        None, lambda s=session: apply_voucher(s, cookie_str, code)
                    )
                    status = interpret_response(http_status, data)
                finally:
                    session.close()

                if status == STATUS_EXPIRED:
                    # Cookies expired — notify user and stop loop
                    await _notify(bot, telegram_id,
                        "🔴 *Cookies Expired!*\n\n"
                        "Your Shein session has expired. Protection has stopped.\n"
                        "Please update your cookies using 🍪 *Set Cookies*.")
                    await db.clear_cookies(telegram_id)
                    return

                elif status == STATUS_ERROR:
                    consecutive_fails += 1
                    logger.warning(f"⚠️ Protect error for {code} (user {telegram_id}) — fail #{consecutive_fails}")
                    cycle_had_error = True

                    if consecutive_fails >= MAX_CONSEC_FAILS:
                        await _notify(bot, telegram_id,
                            f"⚠️ *Protection interrupted*\n\n"
                            f"Failed {MAX_CONSEC_FAILS} times in a row (network issues?).\n"
                            f"Protection has paused. Use 🔒 *Protector* to restart.")
                        return

                else:
                    consecutive_fails = 0
                    logger.debug(f"✅ Protected {code} for user {telegram_id} — {status}")

                # Small delay between each coupon
                wait = random.uniform(*BETWEEN_APPLY)
                await asyncio.sleep(wait)

            # ── End of cycle ─────────────────────────────────────────────────
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
    logger.info(f"Protector loop ended for user {telegram_id}")


async def _notify(bot, telegram_id: int, text: str):
    try:
        await bot.send_message(telegram_id, text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not notify user {telegram_id}: {e}")


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def ensure_running(telegram_id: int, bot) -> bool:
    """
    Start protect loop for user if not already running.
    Returns True if newly started, False if already running.
    """
    existing = _tasks.get(telegram_id)
    if existing and not existing.done():
        return False  # already running
    task = asyncio.create_task(_run_loop(telegram_id, bot))
    _tasks[telegram_id] = task
    return True

def stop(telegram_id: int) -> bool:
    """Cancel a user's protect loop. Returns True if was running."""
    task = _tasks.get(telegram_id)
    if task and not task.done():
        task.cancel()
        _tasks.pop(telegram_id, None)
        return True
    return False

def is_running(telegram_id: int) -> bool:
    task = _tasks.get(telegram_id)
    return bool(task and not task.done())

def running_count() -> int:
    return sum(1 for t in _tasks.values() if not t.done())

async def restore_all(bot):
    """On bot startup — restart loops for all users who have active coupons."""
    # CHANGED: Added await and updated function name to match your db.py
    users = await db.get_users_with_active_protector() 
    count = 0
    for row in users:
        uid = row["telegram_id"] # Turso returns rows as dicts/objects
        if not is_running(uid):
            task = asyncio.create_task(_run_loop(uid, bot))
            _tasks[uid] = task
            count += 1
    if count:
        logger.info(f"♻️ Restored {count} protector loop(s) on startup")
