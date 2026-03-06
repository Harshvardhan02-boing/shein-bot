"""
bot.py — Shein Voucher Vault Bot
All Telegram handlers and UI live here.
"""

import os
import asyncio
import logging
import time
import re
import random
import datetime
import concurrent.futures
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
GLOBAL_UID = 0  

# 🔴 CRITICAL SCALING UPGRADE: 
# This dedicated thread pool gives the bot 64 lanes of traffic instead of the default 5.
WORKER_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=64)

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────

def default_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Open Menu")]], resize_keyboard=True)

def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🎫 Add Coupon(s)", callback_data="menu_add"),
            InlineKeyboardButton("📤 Retrieve",      callback_data="menu_retrieve"),
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

def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 View Full History (3 Days)", callback_data="menu_history")],
        [InlineKeyboardButton("← Back", callback_data="menu_back")]
    ])

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

def quantity_keyboard(cat: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data=f"retqty_{cat}_1"),
            InlineKeyboardButton("2", callback_data=f"retqty_{cat}_2"),
            InlineKeyboardButton("5", callback_data=f"retqty_{cat}_5"),
        ],
        [
            InlineKeyboardButton("10", callback_data=f"retqty_{cat}_10"),
            InlineKeyboardButton("ALL", callback_data=f"retqty_{cat}_0"),
        ],
        [InlineKeyboardButton("← Back", callback_data="menu_retrieve")],
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
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

MAIN_MENU_TEXT = (
    "🛍 *Laadle Voucher Vault*\n\n"
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
        "Welcome to Laadle Protector Vault|\n\n"
        "Use the 📱 **Open Menu** button at the bottom of your screen to access the bot.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=default_reply_keyboard()
    )
    
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
        await query.edit_message_text(MAIN_MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(is_admin(uid)))
        return

    if data == "menu_help":
        text = (
            "❓ *How to use this bot*\n\n"
            "1️⃣ *Add Coupon* — Select category → paste code(s) → bot bulk checks & protects\n"
            "2️⃣ *Retrieve* — Choose category and quantity to pull out of your vault\n"
            "3️⃣ *Check Coupon* — Verify any coupon codes instantly\n"
            "4️⃣ *My Status* — See your vault stats and history\n\n"
            "📦 *Categories:* ₹500 | ₹1000 | ₹2000 | ₹4000"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        return

    if data == "menu_add":
        await query.edit_message_text("🎫 *Add Coupon(s)*\n\nSelect the coupon category (value):", parse_mode=ParseMode.MARKDOWN, reply_markup=category_keyboard("add"))
        return

    if data == "menu_retrieve":
        loop = asyncio.get_event_loop()
        counts = await loop.run_in_executor(WORKER_POOL, db.get_category_counts, uid)
        
        if sum(counts.values()) == 0:
            await query.edit_message_text("📭 *Your vault is empty*\n\nAdd coupons first.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
            return
        await query.edit_message_text("📤 *Retrieve Coupon*\n\nSelect a category to retrieve from:", parse_mode=ParseMode.MARKDOWN, reply_markup=category_keyboard("retrieve", counts))
        return

    if data == "menu_check":
        await query.edit_message_text(
            "🔍 *Check Coupon*\n\nPaste one or more coupon codes below (separated by spaces or commas).",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_check"
        return

    if data == "menu_status":
        await show_status(query, uid)
        return
        
    if data == "menu_history":
        loop = asyncio.get_event_loop()
        hist = await loop.run_in_executor(WORKER_POOL, db.get_user_history, uid)
        
        active = hist["active"]
        retrieved = hist["retrieved"]
        
        lines = ["📜 *Your Coupon History*"]
        
        lines.append(f"\n🔒 *Currently Protected ({len(active)}):*")
        if active:
            for c in active:
                lines.append(f"  • ₹{c['category']} - `{c['code']}` _(Added: {c['added_at'][:10]})_")
        else:
            lines.append("  _No active coupons._")
            
        lines.append(f"\n📤 *Retrieved (Last 3 Days) ({len(retrieved)}):*")
        if retrieved:
            for c in retrieved:
                lines.append(f"  • ₹{c['category']} - `{c['code']}` _(Pulled: {c['retrieved_at'][:10]})_")
        else:
            lines.append("  _No recent retrievals._")
            
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (truncated)"
            
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        return

    if data == "menu_admin":
        if not is_admin(uid):
            await query.answer("❌ Not authorised.", show_alert=True)
            return
        await query.edit_message_text("👑 *Admin Panel*\n\nWhat would you like to do?", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())
        return

# ── CATEGORY & QUANTITY CALLBACKS ─────────────────────────────────────────────

async def cb_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data 

    action, cat_str = data.split("_", 1)
    category = int(cat_str)

    if action == "add":
        await query.edit_message_text(
            f"🎫 *Add ₹{category} Coupon(s)*\n\n"
            f"Paste your coupon code(s) below.\n"
            f"_(I'll check them all first — only valid coupons get saved)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"]    = "awaiting_add_coupon"
        ctx.user_data["category"] = category
        return

    if action == "retrieve":
        loop = asyncio.get_event_loop()
        counts = await loop.run_in_executor(WORKER_POOL, db.get_category_counts, uid)
        available = counts.get(category, 0)
        
        if available == 0:
            await query.edit_message_text(f"📭 *No ₹{category} coupons available*\n\nAdd some first.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
            return

        await query.edit_message_text(
            f"📦 *Retrieve ₹{category} Coupons*\n\nYou have *{available}* available.\nHow many would you like to pull?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=quantity_keyboard(category)
        )
        return

async def cb_retqty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    _, cat_str, qty_str = query.data.split("_")
    category = int(cat_str)
    qty = int(qty_str)

    loop = asyncio.get_event_loop()
    counts = await loop.run_in_executor(WORKER_POOL, db.get_category_counts, uid)
    available = counts.get(category, 0)

    if available == 0:
        await query.edit_message_text("📭 No coupons available in this category.", reply_markup=back_keyboard())
        return

    limit = qty if (qty > 0 and qty <= available) else available
    codes = await loop.run_in_executor(WORKER_POOL, db.retrieve_multiple_coupons, uid, category, limit)

    remaining = await loop.run_in_executor(WORKER_POOL, db.get_active_coupons, uid)
    if not remaining:
        protector.stop(uid)

    await query.edit_message_text(
        f"✅ *Successfully retrieved {len(codes)} coupon(s)!*\n\nCheck your chat messages below to copy them.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard()
    )

    ist_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    current_time_str = ist_time.strftime("%I:%M %p IST")

    codes_text = "\n".join([f"`{c}`" for c in codes])
    await ctx.bot.send_message(
        chat_id=uid,
        text=(
            f"📤 *Your Retrieved ₹{category} Coupon(s):*\n\n"
            f"{codes_text}\n\n"
            f"🕒 *Retrieved At:* {current_time_str}\n"
            f"⚠️ *IMPORTANT:* *Use after 15 to 20 minutes* (The background protection temporarily locks codes in a virtual cart).\n\n"
            f"🛒 sheinindia.in/cart\n\n"
            f"_{available - len(codes)} ₹{category} coupon(s) remaining in vault._"
        ),
        parse_mode=ParseMode.MARKDOWN
    )

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
            "📢 *Send Announcement*\n\n"
            "Who do you want to message?\n\n"
            "• Type `ALL` to broadcast to everyone.\n"
            "• Or type a specific `User ID` (e.g. 123456789).",
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=back_keyboard()
        )
        ctx.user_data["state"] = "awaiting_announce_target"
        return
        
    if data == "admin_set_cookie":
        await query.edit_message_text("🍪 *Set Global Cookie*\n\nPaste the Shein India Session Cookie below.\nThis will power the entire bot.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        ctx.user_data["state"] = "awaiting_global_cookie"
        return
        
    if data == "admin_cookie_status":
        loop = asyncio.get_event_loop()
        cookies = await loop.run_in_executor(WORKER_POOL, db.get_cookies, GLOBAL_UID)
        status = "✅ Active & Set" if cookies else "❌ Missing / Not Set"
        await query.edit_message_text(f"🌐 *Global Cookie Status:*\n\nStatus: {status}\n\n_If active, all background protectors and user-checkers are functioning._", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())
        return

    if data == "admin_stats":
        loop = asyncio.get_event_loop()
        users       = await loop.run_in_executor(WORKER_POOL, db.get_user_count)
        coupons     = await loop.run_in_executor(WORKER_POOL, db.get_total_voucher_count)
        
        try:
            running = await loop.run_in_executor(WORKER_POOL, db.get_active_protector_count)
        except AttributeError:
            running = "N/A"
            
        all_users   = await loop.run_in_executor(WORKER_POOL, db.get_users_with_coupon_counts)

        user_lines_list = []
        for u in all_users[:30]:
            uname = u['username']
            if not uname or uname.lower() == "none":
                uname = "unknown"
            
            safe_uname = uname.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
            
            user_lines_list.append(f"  • @{safe_uname} <code>({u['telegram_id']})</code>: <b>{u['active_count']}</b> protected")
            
        user_lines = "\n".join(user_lines_list)

        if len(all_users) > 30:
            user_lines += f"\n  <i>...and {len(all_users)-30} more</i>"

        text = (
            f"📊 <b>Live Bot Dashboard</b>\n\n"
            f"👥 Total users: <b>{users}</b>\n"
            f"🎫 Active coupons globally: <b>{coupons}</b>\n"
            f"🔒 Running background loops: <b>{running}</b>\n\n"
            f"👤 <b>User Leaderboard:</b>\n{user_lines}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())
        return

# ── MESSAGE HANDLER ───────────────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    state = ctx.user_data.get("state")
    
    is_photo = bool(update.message.photo)
    text = ""
    if update.message.text:
        text = update.message.text.strip()
    elif update.message.caption:
        text = update.message.caption.strip()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(WORKER_POOL, db.upsert_user, uid, update.effective_user.username or "")

    if text == "📱 Open Menu":
        ctx.user_data.pop("state", None)
        await update.message.reply_text(MAIN_MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(is_admin(uid)))
        return

    # ── TARGETED ANNOUNCEMENTS ────────────────────────────────────────────────
    if state == "awaiting_announce_target" and is_admin(uid):
        ctx.user_data.pop("state", None)
        if not text:
            await update.message.reply_text("⚠️ Please send a valid User ID or 'ALL'.", reply_markup=back_keyboard())
            return
            
        ctx.user_data["announce_target"] = text
        ctx.user_data["state"] = "awaiting_announce_content"
        await update.message.reply_text(
            "📝 Target Set! Now send the **text message** OR an **image with a caption**.", 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=back_keyboard()
        )
        return

    if state == "awaiting_announce_content" and is_admin(uid):
        ctx.user_data.pop("state", None)
        target = ctx.user_data.pop("announce_target", "ALL")

        if target.upper() == "ALL":
            all_ids = await loop.run_in_executor(WORKER_POOL, db.get_all_user_ids)
        else:
            try:
                all_ids = [int(target)]
            except ValueError:
                await update.message.reply_text("❌ Invalid target ID format.", reply_markup=back_keyboard())
                return

        sent = 0
        failed = 0
        
        header = "📢 *Announcement*\n\n"
        signature = "\n\n---Laadle Bot Ke Pitaji---"
        
        if text:
            final_text = f"{header}{text}{signature}"
        else:
            final_text = f"{header}{signature}"
        
        status_msg = await update.message.reply_text(f"📤 Sending to {len(all_ids)} user(s)...")
        
        for user_id in all_ids:
            try:
                if is_photo:
                    photo_id = update.message.photo[-1].file_id
                    await ctx.bot.send_photo(chat_id=user_id, photo=photo_id, caption=final_text, parse_mode=ParseMode.MARKDOWN)
                else:
                    await ctx.bot.send_message(chat_id=user_id, text=final_text, parse_mode=ParseMode.MARKDOWN)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05) 

        await status_msg.edit_text(f"✅ *Announcement sent!*\n\n📨 Delivered: {sent}\n❌ Failed: {failed}", parse_mode=ParseMode.MARKDOWN)
        return

    if is_photo:
        return

    # ── GLOBAL ADMIN COOKIES ──────────────────────────────────────────────────
    if state == "awaiting_global_cookie" and is_admin(uid):
        ctx.user_data.pop("state", None)
        ok, cookie_str, err = validate_cookies(text)
        if not ok:
            await update.message.reply_text(f"❌ *Invalid cookies*\n\n{err}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
            return
            
        await loop.run_in_executor(WORKER_POOL, db.upsert_user, GLOBAL_UID, "SYSTEM_ACCOUNT")
        await loop.run_in_executor(WORKER_POOL, db.set_cookies, GLOBAL_UID, text)
        await update.message.reply_text("✅ *Global Cookies saved successfully!*\n\nThe entire bot and all background protectors are now using this session.", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())
        return

    # ── BULK ADD & CHECK ENGINE ─────────
