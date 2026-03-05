"""
bot.py — Shein Voucher Vault Bot
All Telegram handlers and UI live here.
"""

import os
import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

import db
import scripts.protector as protector
from scripts.checker import check_coupon, STATUS_VALID, STATUS_EXPIRED
from scripts.shein_api import validate_cookies, parse_cookies

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.environ["BOT_TOKEN"]
ADMIN_IDS  = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
CATEGORIES = [500, 1000, 2000, 4000]

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────

def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🎫 Add Coupon",    callback_data="menu_add"),
            InlineKeyboardButton("📤 Retrieve",       callback_data="menu_retrieve"),
        ],
        [
            InlineKeyboardButton("🔍 Check Coupon",  callback_data="menu_check"),
            InlineKeyboardButton("📊 My Status",     callback_data="menu_status"),
        ],
        [
            InlineKeyboardButton("🍪 Set Cookies",   callback_data="menu_cookies"),
            InlineKeyboardButton("❓ Help",           callback_data="menu_help"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="menu_admin")])
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data="menu_back")]])


def category_keyboard(prefix: str, counts: dict = None) -> InlineKeyboardMarkup:
    """4 category buttons. If counts provided, shows (n) next to each."""
    def label(cat):
        n = counts.get(cat, 0) if counts else None
        return f"₹{cat} ({n})" if n is not None else f"₹{cat}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(label(500),  callback_data=f"{prefix}_500"),
            InlineKeyboardButton(label(1000), callback_data=f"{prefix}_1000"),
        ],
        [
            InlineKeyboardButton(label(2000), callback_data=f"{prefix}_2000"),
            InlineKeyboardButton(label(4000), callback_data=f"{prefix}_4000"),
        ],
        [InlineKeyboardButton("← Back", callback_data="menu_back")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Announce",   callback_data="admin_announce"),
            InlineKeyboardButton("📊 User Stats", callback_data="admin_stats"),
        ],
        [InlineKeyboardButton("← Back", callback_data="menu_back")],
    ])

# ── HELPERS ───────────────────────────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def progress_bar(done: int, total: int, width: int = 10) -> str:
    filled = int(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)

MAIN_MENU_TEXT = (
    "🛍 *Shein Voucher Vault*\n\n"
    "Your personal coupon protection system.\n"
    "Add coupons → auto-protected 24/7\n"
    "Retrieve when you need to use one.\n\n"
    "Choose an option below:"
)

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username or user.first_name or "")
    await update.message.reply_text(
        MAIN_MENU_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(is_admin(user.id))
    )

# ── MAIN MENU CALLBACK ────────────────────────────────────────────────────────

async def cb_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = query.from_user.id
    data  = query.data

    # ── Back → main menu ──────────────────────────────────────────────────────
    if data == "menu_back":
        await query.edit_message_text(
            MAIN_MENU_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(is_admin(uid))
        )
        return

    # ── Help ──────────────────────────────────────────────────────────────────
    if data == "menu_help":
        text = (
            "❓ *How to use this bot*\n\n"
            "1️⃣ *Set Cookies* — Paste your Shein India login cookies\n"
            "2️⃣ *Add Coupon* — Select category → paste code → bot checks & protects it\n"
            "3️⃣ *Retrieve* — Get your oldest coupon from a category when ready to use\n"
            "4️⃣ *Check Coupon* — Verify any coupon code instantly\n"
            "5️⃣ *My Status* — See your vault stats\n\n"
            "🍪 *Getting Cookies:*\n"
            "Open sheinindia.in → Login → DevTools (F12)\n"
            "→ Application → Cookies → Export as JSON\n\n"
            "📦 *Categories:* ₹500 | ₹1000 | ₹2000 | ₹4000"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=back_keyboard())
        return

    # ── Set Cookies ───────────────────────────────────────────────────────────
    if data == "menu_cookies":
        await query.edit_message_text(
            "🍪 *Set Your Cookies*\n\n"
            "Paste your Shein India cookies below.\n\n"
            "Accepted formats:\n"
            "• JSON dict: `{\"aff_bm\": \"abc\", ...}`\n"
            "• JSON array (EditThisCookie): `[{\"name\":\"aff_bm\",\"value\":\"abc\"}, ...]`\n"
            "• Raw string: `aff_bm=abc; usc=def; ...`\n\n"
            "📋 Just paste them now:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_cookies"
        return

    # ── Add Coupon ────────────────────────────────────────────────────────────
    if data == "menu_add":
        cookies = db.get_cookies(uid)
        if not cookies:
            await query.edit_message_text(
                "❌ *No cookies set!*\n\nPlease set your cookies first using 🍪 Set Cookies.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return
        await query.edit_message_text(
            "🎫 *Add Coupon*\n\nSelect the coupon category (value):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=category_keyboard("add")
        )
        return

    # ── Retrieve ──────────────────────────────────────────────────────────────
    if data == "menu_retrieve":
        counts = db.get_category_counts(uid)
        total  = sum(counts.values())
        if total == 0:
            await query.edit_message_text(
                "📭 *Your vault is empty*\n\nAdd coupons first using 🎫 Add Coupon.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return
        await query.edit_message_text(
            "📤 *Retrieve Coupon*\n\nSelect a category to retrieve from:\n_(shows count available)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=category_keyboard("retrieve", counts)
        )
        return

    # ── Check Coupon ──────────────────────────────────────────────────────────
    if data == "menu_check":
        cookies = db.get_cookies(uid)
        if not cookies:
            await query.edit_message_text(
                "❌ *No cookies set!*\n\nPlease set your cookies first using 🍪 Set Cookies.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return
        await query.edit_message_text(
            "🔍 *Check Coupon*\n\n"
            "Paste one or more coupon codes below (one per line):\n\n"
            "_Max 25 characters per code. Codes over 25 chars are skipped._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_check"
        return

    # ── My Status ─────────────────────────────────────────────────────────────
    if data == "menu_status":
        await show_status(query, uid)
        return

    # ── Admin ─────────────────────────────────────────────────────────────────
    if data == "menu_admin":
        if not is_admin(uid):
            await query.answer("❌ Not authorised.", show_alert=True)
            return
        await query.edit_message_text(
            "👑 *Admin Panel*\n\nWhat would you like to do?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_keyboard()
        )
        return

# ── CATEGORY CALLBACKS ────────────────────────────────────────────────────────

async def cb_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data  # e.g. "add_500" or "retrieve_1000"

    action, cat_str = data.split("_", 1)
    category = int(cat_str)

    # ── ADD ───────────────────────────────────────────────────────────────────
    if action == "add":
        await query.edit_message_text(
            f"🎫 *Add ₹{category} Coupon*\n\n"
            f"Paste your coupon code below:\n"
            f"_(I'll check it first — only valid coupons get saved)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"]    = "awaiting_add_coupon"
        ctx.user_data["category"] = category
        return

    # ── RETRIEVE ──────────────────────────────────────────────────────────────
    if action == "retrieve":
        counts = db.get_category_counts(uid)
        available = counts.get(category, 0)
        if available == 0:
            await query.edit_message_text(
                f"📭 *No ₹{category} coupons available*\n\n"
                f"Add some first using 🎫 Add Coupon.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        code = db.retrieve_coupon(uid, category)
        if not code:
            await query.edit_message_text(
                f"📭 No ₹{category} coupons left.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        # Check if protector loop should continue (might still have other coupons)
        remaining = db.get_active_coupons(uid)
        if not remaining:
            protector.stop(uid)

        await query.edit_message_text(
            f"📤 *Here's your ₹{category} coupon:*\n\n"
            f"`{code}`\n\n"
            f"⚡ Apply it on Shein *quickly* before the session expires!\n"
            f"🛒 sheinindia.in/cart → Enter coupon code\n\n"
            f"_{available - 1} ₹{category} coupon(s) remaining in vault_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        return

# ── ADMIN CALLBACKS ───────────────────────────────────────────────────────────

async def cb_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    if not is_admin(uid):
        await query.answer("❌ Not authorised.", show_alert=True)
        return

    if data == "admin_announce":
        await query.edit_message_text(
            "📢 *Broadcast Message*\n\n"
            "Type your announcement below.\n"
            "It will be sent to all users:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_announce"
        return

    if data == "admin_stats":
        users       = db.get_user_count()
        coupons     = db.get_total_coupon_count()
        running     = protector.running_count()
        all_users   = db.get_all_users()

        user_lines = "\n".join(
            f"  • @{u['username'] or 'unknown'} `({u['telegram_id']})`"
            for u in all_users[:30]
        )
        if len(all_users) > 30:
            user_lines += f"\n  _...and {len(all_users)-30} more_"

        text = (
            f"📊 *Bot Statistics*\n\n"
            f"👥 Total users: *{users}*\n"
            f"🎫 Active coupons: *{coupons}*\n"
            f"🔒 Running protectors: *{running}*\n\n"
            f"*All Users:*\n{user_lines}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=admin_keyboard())
        return

# ── MESSAGE HANDLER ───────────────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    text  = update.message.text.strip() if update.message.text else ""
    state = ctx.user_data.get("state")

    db.upsert_user(uid, update.effective_user.username or "")

    # ── COOKIES ───────────────────────────────────────────────────────────────
    if state == "awaiting_cookies":
        ctx.user_data.pop("state", None)
        ok, cookie_str, err = validate_cookies(text)
        if not ok:
            await update.message.reply_text(
                f"❌ *Invalid cookies*\n\n{err}\n\nPlease try again.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return
        db.set_cookies(uid, text)  # store raw (we parse on use)
        await update.message.reply_text(
            "✅ *Cookies saved successfully!*\n\n"
            "Your Shein session is now active.\n"
            "You can now add and check coupons.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(is_admin(uid))
        )
        return

    # ── ADD COUPON ────────────────────────────────────────────────────────────
    if state == "awaiting_add_coupon":
        ctx.user_data.pop("state", None)
        category = ctx.user_data.pop("category", None)
        if not category:
            await update.message.reply_text("Something went wrong. Please try again.")
            return

        code = text.upper().strip()

        # Length validation
        if len(code) > 25:
            await update.message.reply_text(
                f"❌ *Code too long* ({len(code)} chars)\n\n"
                f"Coupon codes should be 25 characters or less.\n"
                f"Please check and try again.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return
        if len(code) < 4:
            await update.message.reply_text(
                f"❌ *Code too short* ({len(code)} chars)\n\nPlease check and try again.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        # Check duplicate before API call
        if db.coupon_exists(uid, code):
            await update.message.reply_text(
                f"⚠️ `{code}` is already in your vault.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        # Checking animation
        msg = await update.message.reply_text(
            f"🔍 *Checking coupon...*\n\n`{code}`",
            parse_mode=ParseMode.MARKDOWN
        )

        cookie_raw = db.get_cookies(uid)
        cookie_str = parse_cookies(cookie_raw)

        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, check_coupon, cookie_str, code)

        if result["cookies_expired"]:
            db.clear_cookies(uid)
            await msg.edit_text(
                "🔴 *Cookies Expired*\n\n"
                "Your Shein session has expired.\n"
                "Please update your cookies using 🍪 Set Cookies.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        if result["status"] == STATUS_VALID:
            db.add_coupon(uid, code, category)
            started = protector.ensure_running(uid, ctx.bot)
            protect_msg = "🔒 Protection loop started!" if started else "🔒 Added to protection loop!"
            await msg.edit_text(
                f"✅ *Coupon Added!*\n\n"
                f"Code: `{code}`\n"
                f"Category: ₹{category}\n\n"
                f"{protect_msg}\n"
                f"Your coupon is now being protected automatically.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard(is_admin(uid))
            )
        else:
            await msg.edit_text(
                f"{result['emoji']} *Coupon Not Saved*\n\n"
                f"Code: `{code}`\n"
                f"Status: *{result['label']}*\n\n"
                f"Only valid coupons are saved to your vault.\n"
                f"Please check the coupon and try again.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
        return

    # ── CHECK COUPON(S) ───────────────────────────────────────────────────────
    if state == "awaiting_check":
        ctx.user_data.pop("state", None)
        import re
        raw_codes = [c.upper().strip() for c in re.split(r"[\s,\n]+", text) if c.strip()]

        if not raw_codes:
            await update.message.reply_text(
                "⚠️ No codes found. Please paste coupon codes and try again.",
                reply_markup=back_keyboard()
            )
            return

        cookie_raw = db.get_cookies(uid)
        cookie_str = parse_cookies(cookie_raw)
        total      = len(raw_codes)
        start_time = time.time()

        # Initial message
        msg = await update.message.reply_text(
            f"🔍 *Checking {total} coupon(s)...*\n\n"
            f"[{'░' * 10}] 0/{total}\n\n"
            f"✅ 0  ❌ 0  🟡 0",
            parse_mode=ParseMode.MARKDOWN
        )

        results   = []
        valid_ct  = 0
        invalid_ct = 0
        redeemed_ct = 0
        loop      = asyncio.get_event_loop()
        expired   = False

        for i, code in enumerate(raw_codes, 1):
            result = await loop.run_in_executor(None, check_coupon, cookie_str, code)
            results.append(result)

            if result["cookies_expired"]:
                expired = True
                break

            if result["status"] == "valid":
                valid_ct += 1
            elif result["status"] == "redeemed":
                redeemed_ct += 1
            else:
                invalid_ct += 1

            # Update progress every coupon
            bar = progress_bar(i, total)
            try:
                await msg.edit_text(
                    f"🔍 *Checking coupon {i}/{total}...*\n\n"
                    f"▸ `{code}`\n"
                    f"[{bar}] {i}/{total}\n\n"
                    f"✅ {valid_ct}  ❌ {invalid_ct}  🟡 {redeemed_ct}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass

            await asyncio.sleep(0.5)

        if expired:
            db.clear_cookies(uid)
            await msg.edit_text(
                "🔴 *Cookies Expired*\n\n"
                "Your session expired mid-check.\n"
                "Please update your cookies using 🍪 Set Cookies.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        # Build final results message
        elapsed = int(time.time() - start_time)
        lines   = [
            f"✅ *Check Complete* — {total} coupon(s)\n",
            f"[{'█' * 10}] {total}/{total}\n",
        ]

        if valid_ct:
            lines.append(f"✅ *Valid ({valid_ct}):*")
            for r in results:
                if r["status"] == "valid":
                    lines.append(f"  ✅ `{r['code']}`")

        if redeemed_ct:
            lines.append(f"\n🟡 *Already Redeemed ({redeemed_ct}):*")
            for r in results:
                if r["status"] == "redeemed":
                    lines.append(f"  🟡 `{r['code']}`")

        if invalid_ct:
            lines.append(f"\n❌ *Invalid ({invalid_ct}):*")
            for r in results:
                if r["status"] == "invalid":
                    lines.append(f"  ❌ `{r['code']}`")

        error_ct = sum(1 for r in results if r["status"] == "error")
        if error_ct:
            lines.append(f"\n⚠️ *Errors ({error_ct}) — check network:*")
            for r in results:
                if r["status"] == "error":
                    lines.append(f"  ⚠️ `{r['code']}`")

        lines.append(f"\n⏱ Checked in {elapsed}s")

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        return

    # ── ANNOUNCE (admin) ──────────────────────────────────────────────────────
    if state == "awaiting_announce":
        ctx.user_data.pop("state", None)
        if not is_admin(uid):
            return

        all_ids   = db.get_all_user_ids()
        sent      = 0
        failed    = 0
        broadcast = (
            f"📢 *Announcement*\n\n{text}\n\n"
            f"— _Shein Vault Bot Admin_"
        )
        status_msg = await update.message.reply_text(
            f"📤 Sending to {len(all_ids)} users..."
        )
        for user_id in all_ids:
            try:
                await ctx.bot.send_message(user_id, broadcast, parse_mode=ParseMode.MARKDOWN)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)  # Telegram rate limit

        await status_msg.edit_text(
            f"✅ *Announcement sent!*\n\n"
            f"📨 Delivered: {sent}\n"
            f"❌ Failed: {failed} (blocked/deleted)",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Unknown ───────────────────────────────────────────────────────────────
    await update.message.reply_text(
        "Use the menu buttons to navigate. Type /start to open the main menu.",
        reply_markup=main_menu_keyboard(is_admin(uid))
    )

# ── STATUS HELPER ─────────────────────────────────────────────────────────────

async def show_status(query, uid: int):
    cookies   = db.get_cookies(uid)
    counts    = db.get_category_counts(uid)
    total     = sum(counts.values())
    running   = protector.is_running(uid)

    cookie_status = "✅ Active" if cookies else "❌ Not set"
    protect_status = "▶️ Running" if running else "⏸ Stopped"

    lines = [
        "📊 *Your Vault Status*\n",
        f"🍪 Cookies: {cookie_status}",
        f"🔒 Protection: {protect_status}\n",
        "💰 *Your Coupons:*",
    ]
    for cat in [500, 1000, 2000, 4000]:
        n = counts[cat]
        icon = "🟢" if n > 0 else "⚫"
        lines.append(f"  {icon} ₹{cat}  →  {n} protected")

    lines.append(f"\n🎫 Total: *{total}* coupon(s) protected")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard()
    )

# ── STARTUP ───────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    db.init_db()
    await protector.restore_all(app.bot)
    logger.info("🤖 Bot is live!")

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))

    # Main menu + back
    app.add_handler(CallbackQueryHandler(cb_main_menu,
        pattern=r"^menu_"))

    # Category buttons (add_500, retrieve_1000 etc.)
    app.add_handler(CallbackQueryHandler(cb_category,
        pattern=r"^(add|retrieve)_\d+$"))

    # Admin buttons
    app.add_handler(CallbackQueryHandler(cb_admin,
        pattern=r"^admin_"))

    # All text messages (state machine)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message
    ))

    logger.info("🚀 Starting Shein Vault Bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
