"""
checker.py — Coupon validity checker
Runs synchronously (call via run_in_executor from async bot code).
"""

import requests
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

def check_coupon(cookie_string: str, code: str) -> dict:
    code = code.upper().strip()

    # Removed the >25 character limit as requested.
    if len(code) < 4:
        return {
            "code": code,
            "status": STATUS_INVALID,
            "emoji": "❌",
            "label": "Invalid (too short)",
            "cookies_expired": False,
        }

    session = requests.Session()
    try:
        http_status, data = apply_voucher(session, cookie_string, code)
        status = interpret_response(http_status, data)

        if status not in (STATUS_EXPIRED, STATUS_ERROR):
            reset_voucher(session, cookie_string, code)

        meta = STATUS_META.get(status, STATUS_META[STATUS_INVALID])
        return {
            "code": code,
            "status": status,
            "emoji": meta["emoji"],
            "label": meta["label"],
            "cookies_expired": status == STATUS_EXPIRED,
        }
    finally:
        session.close()
