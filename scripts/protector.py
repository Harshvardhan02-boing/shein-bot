"""
protector.py — Per-user rotating coupon protection loop.
"""

import os
import asyncio
import logging
import random
import httpx
from typing import Dict

import db
from scripts.shein_api import (
    apply_voucher, interpret_response,
    STATUS_EXPIRED, STATUS_ERROR, STATUS_VALID, STATUS_REDEEMED
)

logger = logging.getLogger(__name__)

CYCLE_PAUSE    = 90    # seconds between full rotation cycles
BETWEEN_APPLY  = (4, 7)  # seconds to pause between every single check
MAX_CONSEC_FAILS = 5   # notify + stop after this many consecutive failures

GLOBAL_UID = 0
ADMIN_IDS  = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

_tasks: Dict[int, asyncio.Task] = {}

# 🔴 THE FIX: A Global Lock specifically for the background protector.
# This prevents multiple users from accidentally spamming Shein at the exact same time.
PROTECTOR_SEMAPHORE = None 

async def _notify_admin(bot, text: str):
    """Sends critical protector alerts to all admins."""
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, parse_mode="Markdown")
        except Exception:
            pass

async def _run_loop(telegram_id: int, bot):
    global PROTECTOR_SEMAPHORE
    if PROTECTOR_SEMAPHORE is None:
        # Strictly enforces ONE code check at a time globally for background protection
        PROTECTOR_SEMAPHORE = asyncio.Semaphore(1)

    logger.info(f"🔒 Protector loop started for user {telegram_id}")
    consecutive_fails = 0
    loop = asyncio.get_event_loop()

    while True:
        try:
            cookie_raw = await loop.run_in_executor(None, db.get_cookies, GLOBAL_UID)
            if not cookie_raw:
                await asyncio.sleep(CYCLE_PAUSE)
                continue

            from scripts.shein_api import parse_cookies
            cookie_str = parse_cookies(cookie_raw)

            coupons = await loop.run_in_executor(None, db.get_active_coupons, telegram_id)
            if not coupons:
                await asyncio.sleep(CYCLE_PAUSE)
                continue

            cycle_had_error = False
            
            async with httpx.AsyncClient() as client:
                for coupon in coupons:
                    code = coupon["code"]

                    still_exists = await loop.run_in_executor(None, db.coupon_exists, telegram_id, code)
                    if not still_exists:
                        continue

                    # 🔴 THE FIX: Forces ALL users to wait in a single global line
                    async with PROTECTOR_SEMAPHORE:
                        http_status, data = await apply_voucher(client, cookie_str, code)
                        status = interpret_response(http_status, data)

                        # We sleep INSIDE the lock so no other user can fire a request 
                        # until this human-like delay is finished.
                        wait = random.uniform(*BETWEEN_APPLY)
                        await asyncio.sleep(wait)

                    if status == STATUS_EXPIRED:
                        await loop.run_in_executor(None, db.clear_cookies, GLOBAL_UID)
                        await _notify_admin(bot, "🚨 *CRITICAL PROTECTOR STOP:* The global Shein cookie expired in the background!\n\nAll protection loops are paused. Please set a new cookie in the Admin Panel.")
                        await asyncio.sleep(CYCLE_PAUSE)
                        break  

                    elif status == STATUS_ERROR:
                        consecutive_fails += 1
                        logger.warning(f"⚠️ Protect error for {code} (user {telegram_id}) — fail #{consecutive_fails}")
                        cycle_had_error = True

                        if consecutive_fails >= MAX_CONSEC_FAILS:
                            await _notify_admin(bot, f"⚠️ *Protector Network Issue:* Multiple network errors encountered. Pausing to avoid IP ban.")
                            await asyncio.sleep(CYCLE_PAUSE * 5) 
                            break

                    else:
                        consecutive_fails = 0
                        logger.debug(f"✅ Protected {code} for user {telegram_id} — {status}")

            if not cycle_had_error:
                consecutive_fails = 0

            await asyncio.sleep(CYCLE_PAUSE)

        except asyncio.CancelledError:
            logger.info(f"Protector cancelled for user {telegram_id}")
            await loop.run_in_executor(None, db.set_protector_running, telegram_id, False)
            return
        except Exception as e:
            logger.error(f"Protector loop exception for user {telegram_id}: {e}")
            await asyncio.sleep(30)

    _tasks.pop(telegram_id, None)

def ensure_running(telegram_id: int, bot) -> bool:
    db.set_protector_running(telegram_id, True)
    
    existing = _tasks.get(telegram_id)
    if existing and not existing.done():
        return False
    task = asyncio.create_task(_run_loop(telegram_id, bot))
    _tasks[telegram_id] = task
    return True

def stop(telegram_id: int) -> bool:
    db.set_protector_running(telegram_id, False)
    
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
    loop = asyncio.get_event_loop()
    user_ids = await loop.run_in_executor(None, db.get_users_with_active_protector) 
    count = 0
    for uid in user_ids:
        if not is_running(uid):
            task = asyncio.create_task(_run_loop(uid, bot))
            _tasks[uid] = task
            count += 1
    if count:
        logger.info(f"♻️ Restored {count} protector loop(s) on startup")
