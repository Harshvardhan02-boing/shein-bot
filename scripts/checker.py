"""
checker.py — Async Coupon validity checker
"""

import httpx
import asyncio
from scripts.shein_api import (
    apply_voucher, reset_voucher, interpret_response,
    STATUS_VALID, STATUS_REDEEMED, STATUS_INVALID,
    STATUS_EXPIRED, STATUS_ERROR
)

STATUS_META = {
    STATUS_VALID:    {"emoji": "✅", "label": "Valid"},
    STATUS_REDEEMED: {"emoji": "🟡", "label": "Already Redeemed"},
    STATUS_INVALID:  {"emoji": "❌", "label": "Invalid"},
    STATUS_EXPIRED:  {"emoji": "🔴", "label": "Cookies Expired"},
    STATUS_ERROR:    {"emoji": "⚠️",  "label": "Network Error"},
}

# 🔴 NOW FULLY ASYNC
async def check_coupon(cookie_string: str, code: str) -> dict:
    code = code.upper().strip()

    if len(code) < 4:
        return {
            "code": code,
            "status": STATUS_INVALID,
            "emoji": "❌",
            "label": "Invalid (too short)",
            "cookies_expired": False,
        }

    async with httpx.AsyncClient() as client:
        http_status, data = await apply_voucher(client, cookie_string, code)
        status = interpret_response(http_status, data)

        if status not in (STATUS_EXPIRED, STATUS_ERROR):
            # Fire and forget reset
            asyncio.create_task(reset_voucher(client, cookie_string, code))

        meta = STATUS_META.get(status, STATUS_META[STATUS_INVALID])
        return {
            "code": code,
            "status": status,
            "emoji": meta["emoji"],
            "label": meta["label"],
            "cookies_expired": status == STATUS_EXPIRED,
        }
