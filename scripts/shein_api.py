"""
shein_api.py — Raw Shein India HTTP layer
All API calls go through here. Nothing else touches requests directly.
"""

import json
import requests

# ── COOKIE PARSING ────────────────────────────────────────────────────────────

def parse_cookies(raw: str) -> str:
    """
    Accept cookies in any format and return a header-ready string.
    Supported:
      - Raw string:  "aff_bm=abc; usc=def; ..."
      - JSON dict:   {"aff_bm": "abc", "usc": "def"}
      - JSON array:  [{"name": "aff_bm", "value": "abc"}, ...]  (EditThisCookie)
    """
    # 1. Strip whitespace and any accidental quotes from pasting
    raw = raw.strip().strip("'").strip('"')
    
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return "; ".join(f"{k}={v}" for k, v in data.items())
        elif isinstance(data, list):
            parts = []
            for item in data:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    parts.append(f"{item['name']}={item['value']}")
            if parts:
                return "; ".join(parts)
        raise ValueError("Unrecognised JSON cookie format")
    except json.JSONDecodeError:
        # 2. If it's not valid JSON, treat it as a raw string
        # We MUST completely destroy any newlines so the HTTP header doesn't crash
        clean_raw = raw.replace('\n', '').replace('\r', '').strip()
        return clean_raw

def validate_cookies(raw: str) -> tuple[bool, str, str]:
    """
    Try to parse cookies and return (ok, cookie_string, error_message).
    """
    raw = raw.strip()
    if not raw:
        return False, "", "Cookie string is empty."
    try:
        cookie_str = parse_cookies(raw)
        if not cookie_str or "=" not in cookie_str:
            return False, "", "Could not parse cookies — make sure it's valid JSON or a cookie string."
        return True, cookie_str, ""
    except Exception as e:
        return False, "", f"Cookie parse error: {e}"

# ── HEADERS ───────────────────────────────────────────────────────────────────

def get_headers(cookie_string: str) -> dict:
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "origin": "https://www.sheinindia.in",
        "pragma": "no-cache",
        "referer": "https://www.sheinindia.in/cart",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
        "x-tenant-id": "SHEIN",
        "cookie": cookie_string,
    }
    
    # Extract CSRF token from cookies (crucial for Shein security bypass)
    for part in cookie_string.split(";"):
        if "csrfToken=" in part or "csrf_token=" in part:
            headers["x-csrf-token"] = part.split("=")[1].strip()
            
    return headers

# ── API CALLS ─────────────────────────────────────────────────────────────────

def apply_voucher(session: requests.Session, cookie_string: str, code: str) -> tuple:
    """
    POST apply-voucher.
    Returns (status_code, response_dict).
    status_code=None means network/timeout error.
    """
    url = "https://www.sheinindia.in/api/cart/apply-voucher"
    payload = {"voucherId": code, "device": {"client_type": "web"}}
    
    # Force parse the JSON into a flat string so the headers don't crash
    clean_cookie = parse_cookies(cookie_string)
    headers = get_headers(clean_cookie)
    
    print(f"\n🌐 [SHEIN API] Sending request for code: {code}")
    print(f"🌐 [SHEIN API] Using User-Agent: {headers.get('user-agent')}")
    
    try:
        resp = session.post(url, json=payload, headers=headers, timeout=45)
        
        # --- DIAGNOSTIC LOGGING ---
        print(f"🌐 [SHEIN API] HTTP Status Code: {resp.status_code}")
        print(f"🌐 [SHEIN API] Response Body: {resp.text[:300]}") # First 300 chars
        # --------------------------
        
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"errorMessage": "non_json_response"}
            
    except requests.exceptions.Timeout:
        print("🌐 [SHEIN API] ❌ ERROR: Connection Timed Out. Shein might be blocking Railway's IP.")
        return None, {"errorMessage": "timeout"}
    except Exception as e:
        print(f"🌐 [SHEIN API] ❌ ERROR: Network Request Failed: {e}")
        return None, {"errorMessage": str(e)}

def reset_voucher(session: requests.Session, cookie_string: str, code: str):
    """POST reset-voucher — removes coupon from cart. Fire and forget."""
    url = "https://www.sheinindia.in/api/cart/reset-voucher"
    payload = {"voucherId": code, "device": {"client_type": "web"}}
    
    # Force parse here too
    clean_cookie = parse_cookies(cookie_string)
    
    try:
        session.post(url, json=payload, headers=get_headers(clean_cookie), timeout=20)
    except Exception:
        pass

# ── RESPONSE INTERPRETATION ───────────────────────────────────────────────────

# Possible return values from interpret_response:
STATUS_VALID    = "valid"      # ✅ applied successfully
STATUS_REDEEMED = "redeemed"   # 🟡 already used / in use
STATUS_INVALID  = "invalid"    # ❌ not applicable / doesn't exist
STATUS_EXPIRED  = "expired"    # 🔴 cookies expired (401/403 or auth error)
STATUS_ERROR    = "error"      # ⚠️ network / timeout / block

def interpret_response(http_status: int | None, data: dict) -> str:
    """
    Classify an apply-voucher response into one of the STATUS_* constants.
    """
    # Network / timeout errors
    if http_status is None:
        msg = str(data.get("errorMessage", "")).lower()
        if "timeout" in msg:
            return STATUS_ERROR
        return STATUS_ERROR

    # Auth errors → cookies expired
    if http_status in (401, 403):
        return STATUS_EXPIRED

    # No errorMessage = successfully applied ✅
    if "errorMessage" not in data:
        return STATUS_VALID

    err = data["errorMessage"]

    # Non-JSON block response
    if isinstance(err, str):
        low = err.lower()
        if "block" in low or "non_json" in low:
            return STATUS_ERROR
        if "login" in low or "auth" in low or "session" in low:
            return STATUS_EXPIRED
        return STATUS_INVALID

    # Structured error object
    if isinstance(err, dict):
        errors = err.get("errors", [])
        for e in errors:
            msg = e.get("message", "").lower()
            etype = e.get("type", "").lower()

            # Cookie / auth issues
            if any(k in msg for k in ("login", "sign in", "session", "unauthorized", "authentication")):
                return STATUS_EXPIRED
            if any(k in msg for k in ("login", "auth")) and "voucher" not in etype:
                return STATUS_EXPIRED

            # Already redeemed / in use
            if any(k in msg for k in ("already", "redeemed", "in use", "used", "claimed")):
                return STATUS_REDEEMED

            # Not applicable / invalid
            if any(k in msg for k in ("not applicable", "invalid", "expired", "does not exist", "not found", "cannot")):
                return STATUS_INVALID

    return STATUS_INVALID
