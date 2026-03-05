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

# Human-readable labels and emojis per status
STATUS_META = {
    STATUS_VALID:    {"emoji": "✅", "label": "Valid"},
    STATUS_REDEEMED: {"emoji": "🟡", "label": "Already Redeemed"},
    STATUS_INVALID:  {"emoji": "❌", "label": "Invalid"},
    STATUS_EXPIRED:  {"emoji": "🔴", "label": "Cookies Expired"},
    STATUS_ERROR:    {"emoji": "⚠️",  "label": "Network Error"},
}

def check_coupon(cookie_string: str, code: str) -> dict:
    """
    Check a single coupon code.

    Returns:
    {
        "code":    "SVH12345",
        "status":  "valid" | "redeemed" | "invalid" | "expired" | "error",
        "emoji":   "✅",
        "label":   "Valid",
        "cookies_expired": True/False,
    }
    """
    code = code.upper().strip()

    # Skip obviously invalid codes (>25 chars or <4 chars)
    if len(code) > 25 or len(code) < 4:
        return {
            "code": code,
            "status": STATUS_INVALID,
            "emoji": "❌",
            "label": "Invalid (bad length)",
            "cookies_expired": False,
        }

    session = requests.Session()
    try:
        http_status, data = apply_voucher(session, cookie_string, code)
        status = interpret_response(http_status, data)

        # Always reset after checking so cart stays clean
        # (except if cookies expired — no point)
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
