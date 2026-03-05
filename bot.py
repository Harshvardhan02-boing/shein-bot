"""
bot.py — Shein Voucher Vault Bot
All Telegram handlers and UI live here.
"""

import os
import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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
GLOBAL_UID = 0  # Dummy ID used to store the master admin cookie

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────

def default_reply_keyboard() -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard for easy access."""
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Open Menu")]], resize_keyboard=True)

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
            InlineKeyboardButton("❓ Help",          callback_data="menu_help"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="menu_admin")])
    return InlineKeyboardMarkup(rows)

def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data="menu_back")]])

def category_keyboard(prefix: str, counts: dict = None) -> InlineKeyboardMarkup:
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
        [
            InlineKeyboardButton("🍪 Set Global Cookie", callback_data="admin_set_cookie"),
            InlineKeyboardButton("🌐 Cookie Status", callback_data="admin_cookie_status"),
        ],
        [InlineKeyboardButton("← Back", callback_data="menu_back")],
    ])

# ── HELPERS ───────────────────────────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def progress_bar(done: int, total: int, width: int = 10) -> str:
    filled = int(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)

async def notify_admins(bot, text: str):
    """Sends critical alerts directly to all admins."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

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
    
    # Send the persistent reply keyboard first
    await update.message.reply_text(
        "Welcome to the Shein Voucher Vault! 🛍️\n\n"
        "Use the 📱 **Open Menu** button at the bottom of your screen to access the bot.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=default_reply_keyboard()
    )
    
    # Then send the actual inline menu
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

    if data == "menu_back":
        await query.edit_message_text(
            MAIN_MENU_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(is_admin(uid))
        )
        return

    if data == "menu_help":
        text = (
            "❓ *How to use this bot*\n\n"
            "1️⃣ *Add Coupon* — Select category → paste code → bot checks & protects it\n"
            "2️⃣ *Retrieve* — Get your oldest coupon from a category when ready to use\n"
            "3️⃣ *Check Coupon* — Verify any coupon code instantly\n"
            "4️⃣ *My Status* — See your vault stats\n\n"
            "📦 *Categories:* ₹500 | ₹1000 | ₹2000 | ₹4000"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        return

    if data == "menu_add":
        await query.edit_message_text(
            "🎫 *Add Coupon*\n\nSelect the coupon category (value):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=category_keyboard("add")
        )
        return

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

    if data == "menu_check":
        await query.edit_message_text(
            "🔍 *Check Coupon*\n\n"
            "Paste one or more coupon codes below (one per line):\n\n"
            "_Max 25 characters per code. Codes over 25 chars are skipped._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_check"
        return

    if data == "menu_status":
        await show_status(query, uid)
        return

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
    data = query.data 

    action, cat_str = data.split("_", 1)
    category = int(cat_str)

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

    if action == "retrieve":
        counts = db.get_category_counts(uid)
        available = counts.get(category, 0)
        if available == 0:
            await query.edit_message_text(
                f"📭 *No ₹{category} coupons available*\n\nAdd some first using 🎫 Add Coupon.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        code = db.retrieve_coupon(uid, category)
        if not code:
            await query.edit_message_text(f"📭 No ₹{category} coupons left.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
            return

        remaining = db.get_active_coupons(uid)
        if not remaining:
            protector.stop(uid)

        await query.edit_message_text(
            f"📤 *Here's your ₹{category} coupon:*\n\n"
            f"`{code['code']}`\n\n"
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
            "📢 *Broadcast Message*\n\nType your announcement below.\nIt will be sent to all users:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_announce"
        return
        
    if data == "admin_set_cookie":
        await query.edit_message_text(
            "🍪 *Set Global Cookie*\n\nPaste the Shein India Session Cookie below.\nThis will power the entire bot.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_global_cookie"
        return
        
    if data == "admin_cookie_status":
        cookies = db.get_cookies(GLOBAL_UID)
        status = "✅ Active & Set" if cookies else "❌ Missing / Not Set"
        await query.edit_message_text(
            f"🌐 *Global Cookie Status:*\n\nStatus: {status}\n\n_If active, all background protectors and user-checkers are functioning._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_keyboard()
        )
        return

    if data == "admin_stats":
        users       = db.get_user_count()
        coupons     = db.get_total_voucher_count()
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
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())
        return

# ── MESSAGE HANDLER ───────────────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    text  = update.message.text.strip() if update.message.text else ""
    state = ctx.user_data.get("state")

    db.upsert_user(uid, update.effective_user.username or "")

    # Reply Keyboard Handler
    if text == "📱 Open Menu":
        ctx.user_data.pop("state", None)
        await update.message.reply_text(
            MAIN_MENU_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(is_admin(uid))
        )
        return

    # ── GLOBAL ADMIN COOKIES ──────────────────────────────────────────────────
    if state == "awaiting_global_cookie" and is_admin(uid):
        ctx.user_data.pop("state", None)
        ok, cookie_str, err = validate_cookies(text)
        if not ok:
            await update.message.reply_text(
                f"❌ *Invalid cookies*\n\n{err}\n\nPlease try again.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return
            
        db.upsert_user(GLOBAL_UID, "SYSTEM_ACCOUNT")
        db.set_cookies(GLOBAL_UID, text) 
        await update.message.reply_text(
            "✅ *Global Cookies saved successfully!*\n\nThe entire bot and all background protectors are now using this session.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_keyboard()
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

        if len(code) > 25:
            await update.message.reply_text(f"❌ *Code too long* ({len(code)} chars)\n\nCoupon codes should be 25 characters or less.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
            return
        if len(code) < 4:
            await update.message.reply_text(f"❌ *Code too short* ({len(code)} chars)", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
            return

        if db.coupon_exists(uid, code):
            await update.message.reply_text(f"⚠️ `{code}` is already in your vault.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
            return

        cookie_raw = db.get_cookies(GLOBAL_UID)
        if not cookie_raw:
            await update.message.reply_text("❌ *System Maintenance*\n\nThe global system cookie is missing. Please ask the Admin to set it.", parse_mode=ParseMode.MARKDOWN)
            return
            
        cookie_str = parse_cookies(cookie_raw)

        msg = await update.message.reply_text(f"🔍 *Checking coupon...*\n\n`{code}`", parse_mode=ParseMode.MARKDOWN)

        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, check_coupon, cookie_str, code)

        if result["cookies_expired"]:
            db.clear_cookies(GLOBAL_UID)
            await notify_admins(ctx.bot, "🚨 *CRITICAL:* The global Shein cookie expired during a user check! Please set a new one in the Admin panel.")
            await msg.edit_text(
                "🔴 *System Maintenance*\n\nThe backend session expired. The admin has been notified.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        if result["status"] == STATUS_VALID:
            db.add_coupon(uid, code, category)
            started = protector.ensure_running(uid, ctx.bot)
            protect_msg = "🔒 Protection loop started!" if started else "🔒 Added to protection loop!"
            await msg.edit_text(
                f"✅ *Coupon Added!*\n\nCode: `{code}`\nCategory: ₹{category}\n\n{protect_msg}\nYour coupon is now being protected automatically.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard(is_admin(uid))
            )
        else:
            await msg.edit_text(
                f"{result['emoji']} *Coupon Not Saved*\n\nCode: `{code}`\nStatus: *{result['label']}*\n\nOnly valid coupons are saved to your vault.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
        return

    # ── CHECK COUPON(S) ───────────────────────────────────────────────────────
    if state == "awaiting_check":
        ctx.user_data.pop("state", None)
        import re
        
        # Split by spaces, commas, or newlines
        raw_splits = re.split(r"[\s,\n]+", text)
        
        # Strict filter: Only keep strings that look like actual coupons (alphanumeric).
        # This completely blocks accidental cookie strings (which contain = ; { } ")
        raw_codes = []
        for c in raw_splits:
            c = c.upper().strip()
            # If it's empty, or contains weird cookie characters, skip it
            if not c or "=" in c or ";" in c or "{" in c or '"' in c:
                continue
            raw_codes.append(c)

        if not raw_codes:
            await update.message.reply_text("⚠️ No valid codes found. Please make sure you didn't paste a cookie by accident.", reply_markup=back_keyboard())
            return

        # Hard cap to prevent spamming the API and getting banned
        if len(raw_codes) > 20:
            await update.message.reply_text("⚠️ *Too many codes!*\n\nChecking maximum of 20 codes to prevent server bans.", parse_mode=ParseMode.MARKDOWN)
            raw_codes = raw_codes[:20]

        cookie_raw = db.get_cookies(GLOBAL_UID)
        if not cookie_raw:
            await update.message.reply_text("❌ *System Maintenance*\n\nThe global system cookie is missing. Please ask the Admin to set it.", parse_mode=ParseMode.MARKDOWN)
            return

        cookie_str = parse_cookies(cookie_raw)
        total      = len(raw_codes)
        start_time = time.time()

        msg = await update.message.reply_text(
            f"🔍 *Checking {total} coupon(s)...*\n\n[{'░' * 10}] 0/{total}\n\n✅ 0  ❌ 0  🟡 0",
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

            bar = progress_bar(i, total)
            try:
                await msg.edit_text(
                    f"🔍 *Checking coupon {i}/{total}...*\n\n▸ `{code}`\n[{bar}] {i}/{total}\n\n✅ {valid_ct}  ❌ {invalid_ct}  🟡 {redeemed_ct}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            await asyncio.sleep(0.5)

        if expired:
            db.clear_cookies(GLOBAL_UID)
            await notify_admins(ctx.bot, "🚨 *CRITICAL:* The global Shein cookie expired during a mass-check! Please set a new one in the Admin panel.")
            await msg.edit_text(
                "🔴 *System Maintenance*\n\nThe backend session expired. The admin has been notified.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        elapsed = int(time.time() - start_time)
        lines   = [f"✅ *Check Complete* — {total} coupon(s)\n", f"[{'█' * 10}] {total}/{total}\n"]

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

        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        return

    # ── ANNOUNCE (admin) ──────────────────────────────────────────────────────
    if state == "awaiting_announce":
        ctx.user_data.pop("state", None)
        if not is_admin(uid):
            return

        all_ids   = db.get_all_user_ids()
        sent      = 0
        failed    = 0
        broadcast = f"📢 *Announcement*\n\n{text}\n\n— _Shein Vault Bot Admin_"
        status_msg = await update.message.reply_text(f"📤 Sending to {len(all_ids)} users...")
        for user_id in all_ids:
            try:
                await ctx.bot.send_message(user_id, broadcast, parse_mode=ParseMode.MARKDOWN)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05) 

        await status_msg.edit_text(
            f"✅ *Announcement sent!*\n\n📨 Delivered: {sent}\n❌ Failed: {failed} (blocked/deleted)",
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
    counts    = db.get_category_counts(uid)
    total     = sum(counts.values())
    running   = protector.is_running(uid)

    protect_status = "▶️ Running" if running else "⏸ Stopped"

    lines = [
        "📊 *Your Vault Status*\n",
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
    db.upsert_user(GLOBAL_UID, "SYSTEM_GLOBAL")
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
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern=r"^menu_"))
    app.add_handler(CallbackQueryHandler(cb_category, pattern=r"^(add|retrieve)_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Starting Shein Vault Bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
