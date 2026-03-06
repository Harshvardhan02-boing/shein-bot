"""
shein_api.py — Asynchronous Shein India HTTP layer using httpx
"""

import json
import httpx
import random
import asyncio

def parse_cookies(raw: str) -> str:
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
        clean_raw = raw.replace('\n', '').replace('\r', '').strip()
        return clean_raw

def validate_cookies(raw: str) -> tuple[bool, str, str]:
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
    
    for part in cookie_string.split(";"):
        if "csrfToken=" in part or "csrf_token=" in part:
            headers["x-csrf-token"] = part.split("=")[1].strip()
            
    return headers

async def apply_voucher(client: httpx.AsyncClient, cookie_string: str, code: str) -> tuple:
    url = "https://www.sheinindia.in/api/cart/apply-voucher"
    payload = {"voucherId": code, "device": {"client_type": "web"}}
    
    clean_cookie = parse_cookies(cookie_string)
    headers = get_headers(clean_cookie)
    
    await asyncio.sleep(random.uniform(0.1, 0.4))
    
    print(f"\n🌐 [SHEIN API] Sending request for code: {code}")
    
    try:
        # 🔴 FIXED: Timeout reduced to 12 seconds so the bot escapes network freezes
        resp = await client.post(url, json=payload, headers=headers, timeout=12.0)
        print(f"🌐 [SHEIN API] HTTP Status Code: {resp.status_code}")
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"errorMessage": "non_json_response"}
            
    except httpx.TimeoutException:
        print("🌐 [SHEIN API] ❌ ERROR: Connection Timed Out.")
        return None, {"errorMessage": "timeout"}
    except Exception as e:
        print(f"🌐 [SHEIN API] ❌ ERROR: Network Request Failed: {e}")
        return None, {"errorMessage": str(e)}

async def reset_voucher(client: httpx.AsyncClient, cookie_string: str, code: str):
    url = "https://www.sheinindia.in/api/cart/reset-voucher"
    payload = {"voucherId": code, "device": {"client_type": "web"}}
    clean_cookie = parse_cookies(cookie_string)
    try:
        # 🔴 FIXED: Timeout reduced to 8 seconds
        await client.post(url, json=payload, headers=get_headers(clean_cookie), timeout=8.0)
    except Exception:
        pass

STATUS_VALID    = "valid"
STATUS_REDEEMED = "redeemed"
STATUS_INVALID  = "invalid"
STATUS_EXPIRED  = "expired"
STATUS_ERROR    = "error"

def interpret_response(http_status: int | None, data: dict) -> str:
    if http_status is None:
        msg = str(data.get("errorMessage", "")).lower()
        if "timeout" in msg:
            return STATUS_ERROR
        return STATUS_ERROR

    if http_status in (401, 403):
        return STATUS_EXPIRED

    if "errorMessage" not in data:
        return STATUS_VALID

    err = data["errorMessage"]

    if isinstance(err, str):
        low = err.lower()
        if "block" in low or "non_json" in low:
            return STATUS_ERROR
        if "login" in low or "auth" in low or "session" in low:
            return STATUS_EXPIRED
        return STATUS_INVALID

    if isinstance(err, dict):
        errors = err.get("errors", [])
        for e in errors:
            msg = e.get("message", "").lower()
            etype = e.get("type", "").lower()

            if any(k in msg for k in ("login", "sign in", "session", "unauthorized", "authentication")):
                return STATUS_EXPIRED
            if any(k in msg for k in ("login", "auth")) and "voucher" not in etype:
                return STATUS_EXPIRED
            if any(k in msg for k in ("already", "redeemed", "in use", "used", "claimed")):
                return STATUS_REDEEMED
            if any(k in msg for k in ("not applicable", "invalid", "expired", "does not exist", "not found", "cannot")):
                return STATUS_INVALID

    return STATUS_INVALID
