import os
import re
import io
import logging
import asyncio
import random
import secrets
from datetime import datetime, timedelta
import aiosqlite
from telethon import TelegramClient, events, functions, types, Button

# --- CONFIGURATION & LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("PomPomBot")

# ==========================================
#         PRODUCTION CONFIGURATION
# ==========================================
API_ID = 35485985              
API_HASH = '5441c09a9c8bf58374e1f8f227b95794'     
BOT_TOKEN = '8989447030:AAE1iJt-9H8fRWWAqyfvral4Ny6jdD2pQpE'   

# UNLIMITED ADMINS SUPPORT
ADMIN_IDS = [
    7952327997,  # Primary Admin
]                 

REQUIRED_CHANNELS = [
    {"id": -1003985304953, "link": "https://t.me/yagamicorporation"},
    {"id": -1001782407376, "link": "https://t.me/+Q7PnaxCClc02ODRl"},
    {"id": -1003098095383 , "link": "https://t.me/whitelroom"},
    {"id": -1003945131867 , "link": "https://t.me/+YvXUYagQi6BkNzY9"}
]       

# UPDATED TO NEW DATABASE CHANNEL LINK
POMPOM_CHANNEL_ID = 'https://t.me/hxhyhxbhxhdyfjvkcutevudsuxhxyxy'

# PUBLIC LOG GROUP USERNAME FOR RELIABLE ROUTING
LOGS_CHAT_PUBLIC = "gopaljikalunnnahihai"  

client = None  # Instantiated later inside the active event loop

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "pompom.db")

# Memory state storage to track user actions and maintenance status
USER_STATES = {}
SYSTEM_MAINTENANCE = False  # Global boolean flag for maintenance toggles

# FIXED LIST OF 10 MATH CAPTCHAS
MATH_CAPTCHAS = [
    {"question": "5 + 5", "answer": "10"},
    {"question": "3 + 7", "answer": "10"},
    {"question": "12 - 4", "answer": "8"},
    {"question": "6 + 3", "answer": "9"},
    {"question": "4 + 4", "answer": "8"},
    {"question": "9 - 2", "answer": "7"},
    {"question": "8 + 5", "answer": "13"},
    {"question": "15 - 5", "answer": "10"},
    {"question": "7 + 7", "answer": "14"},
    {"question": "10 - 4", "answer": "6"}
]

def format_size(bytes_size):
    if not bytes_size: return "Unknown Size"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

async def dispatch_system_log(caption_text: str, media_file=None):
    try:
        target_peer = await client.get_input_entity(LOGS_CHAT_PUBLIC)
        if media_file:
            await client.send_file(target_peer, file=media_file, caption=caption_text, parse_mode='markdown')
        else:
            await client.send_message(target_peer, caption_text, parse_mode='markdown')
    except Exception as e:
        logger.error(f"❌🛠 [Log System] Failed forwarding broadcast packet: {e}")

async def auto_delete_media_worker(chat_id, target_msg_id):
    """
    Waits exactly 2 minutes (120 seconds) and automatically deletes the media.
    """
    await asyncio.sleep(120)
    try:
        await client.delete_messages(chat_id, target_msg_id)
        await client.send_message(
            chat_id,
            "🗑❌ **Video Deleted Automatically!**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔒 To strictly maintain security guidelines, requested video streams are wiped automatically after 2 minutes."
        )
    except Exception as e:
        logger.error(f"🗑❌ [Auto-Delete Core] Failed to wipe message {target_msg_id} in chat {chat_id}: {e}")

# ==========================================
#         DATABASE CONTROLLER LAYER
# ==========================================
class DatabaseManager:
    @staticmethod
    async def initialize():
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT,
                    plan TEXT DEFAULT 'Free',
                    points INTEGER DEFAULT 10,
                    referral_count INTEGER DEFAULT 0,
                    referred_by TEXT,
                    banned INTEGER DEFAULT 0,
                    last_reward_time TEXT,
                    premium_expiry TEXT DEFAULT 'Never'
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    plan_name TEXT,
                    price TEXT,
                    status TEXT DEFAULT 'Pending',
                    timestamp TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pompoms (
                    msg_id INTEGER PRIMARY KEY,
                    file_name TEXT,
                    file_size INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS coupons (
                    coupon_code TEXT PRIMARY KEY,
                    points_value INTEGER,
                    is_used INTEGER DEFAULT 0,
                    created_by TEXT,
                    timestamp TEXT
                )
            """)
            await db.commit()
            logger.info("⚡ Database structures verified and online.")

    @staticmethod
    async def register_user(user_id: str, username: str, referrer_id: str = None):
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT user_id, banned FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return "BANNED" if row['banned'] == 1 else False
            
            # FEATURE INTEGRATED: Default starting points configuration shifted from 5 to 10
            await db.execute("""
                INSERT INTO users (user_id, username, plan, points, referral_count, referred_by, banned, last_reward_time, premium_expiry)
                VALUES (?, ?, 'Free', 10, 0, ?, 0, NULL, 'Never')
            """, (user_id, username, referrer_id))
            if referrer_id:
                await db.execute("UPDATE users SET points = points + 3, referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
            await db.commit()
            return True

    @staticmethod
    async def get_user(user_id: str):
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone()

    @staticmethod
    async def deduct_point(user_id: str):
        if int(user_id) in ADMIN_IDS:
            return "Unlimited"
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET points = MAX(0, points - 1) WHERE user_id = ?", (user_id,))
            await db.commit()
            async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def remove_dead_video(msg_id: int):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("DELETE FROM pompoms WHERE msg_id = ?", (msg_id,))
            await db.commit()

    @staticmethod
    async def add_points(user_id: str, points_to_add: int, timestamp_str: str = None):
        async with aiosqlite.connect(DB_FILE) as db:
            if timestamp_str:
                await db.execute("UPDATE users SET points = points + ?, last_reward_time = ? WHERE user_id = ?", (points_to_add, timestamp_str, user_id))
            else:
                await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points_to_add, user_id))
            await db.commit()

    @staticmethod
    async def log_payment_attempt(user_id: str, plan_name: str, price: str):
        async with aiosqlite.connect(DB_FILE) as db:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute("INSERT INTO payments (user_id, plan_name, price, status, timestamp) VALUES (?, ?, ?, 'Pending', ?)", (user_id, plan_name, price, now_str))
            await db.commit()

    @staticmethod
    async def update_premium_plan(user_id: str, plan_name: str):
        async with aiosqlite.connect(DB_FILE) as db:
            expiry_str = str((datetime.now() + timedelta(days=30)).date())
            await db.execute("UPDATE users SET plan = ?, premium_expiry = ? WHERE user_id = ?", (plan_name, expiry_str, user_id))
            await db.commit()

    @staticmethod
    async def cache_pompom_video(msg_id: int, name: str, size: int):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                INSERT OR REPLACE INTO pompoms (msg_id, file_name, file_size)
                VALUES (?, ?, ?)
            """, (msg_id, name, size))
            await db.commit()

    @staticmethod
    async def get_all_indexed_video_ids():
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT msg_id FROM pompoms") as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    @staticmethod
    async def get_system_stats():
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as c1: total_users = (await c1.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM pompoms") as c2: total_pompoms = (await c2.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE banned = 1") as c3: banned_users = (await c3.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM payments WHERE status = 'Pending'") as c4: pending_payments = (await c4.fetchone())[0]
            return total_users, total_pompoms, banned_users, pending_payments

    @staticmethod
    async def set_user_ban_status(user_id: str, status: int):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET banned = ? WHERE user_id = ?", (status, user_id))
            await db.commit()

    @staticmethod
    async def get_all_active_user_ids():
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT user_id FROM users WHERE banned = 0") as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    @staticmethod
    async def create_coupon(code: str, points: int, creator: str):
        async with aiosqlite.connect(DB_FILE) as db:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                "INSERT INTO coupons (coupon_code, points_value, is_used, created_by, timestamp) VALUES (?, ?, 0, ?, ?)",
                (code, points, creator, now_str)
            )
            await db.commit()

    @staticmethod
    async def use_coupon(code: str, user_id: str):
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM coupons WHERE coupon_code = ? AND is_used = 0", (code,)) as cursor:
                coupon = await cursor.fetchone()
                if not coupon:
                    return None
                
                points_to_credit = coupon['points_value']
                await db.execute("UPDATE coupons SET is_used = 1 WHERE coupon_code = ?", (code,))
                await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (user_id, points_to_credit))
                await db.commit()
                return points_to_credit

async def check_membership(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True  
    for ch in REQUIRED_CHANNELS:
        try:
            await client(functions.channels.GetParticipantRequest(channel=ch['id'], participant=user_id))
        except Exception:
            return False
    return True

# ==========================================
#         HUMANIZED ROUTER FUNCTIONS
# ==========================================
@client.on(events.NewMessage(pattern='/start'))
async def on_start_command(event):
    user_id = str(event.sender_id)
    
    if SYSTEM_MAINTENANCE and int(user_id) not in ADMIN_IDS:
        await event.reply(
            "⚠️🛠 **SYSTEM UNDER MAINTENANCE**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚙️ The core platform is currently undergoing necessary server optimizations.\n\n"
            "📢 **Notice:** All processes are locked. We will be back online shortly!"
        )
        return

    username = event.sender.username or "No Username"
    payload = event.message.message.split(' ')
    referrer_id = payload[1] if len(payload) > 1 and payload[1].isdigit() else None
    if referrer_id == user_id: referrer_id = None
        
    # STEP 1: Strict Channel Membership Check First
    is_joined = await check_membership(event.sender_id)
    if not is_joined:
        channel_buttons = [[Button.url(f"📢 Join Channel {i+1}", ch['link'])] for i, ch in enumerate(REQUIRED_CHANNELS)]
        channel_buttons.append([Button.url("🔄 Check Again", f"https://t.me/{(await client.get_me()).username}?start={referrer_id if referrer_id else ''}")])
        await event.reply(
            "🛑❌ **Verification Required:** You must join our official channels first before running this script!",
            buttons=channel_buttons
        )
        return

    # If already a verified database member, bypass captcha restrictions completely
    existing_user = await DatabaseManager.get_user(user_id)
    if existing_user:
        if existing_user['banned'] == 1:
            await event.reply("🚫 **Access Denied:** Your account has been suspended by an administrator.")
            return
        await send_humanized_dashboard(event.chat_id, user_id)
        return

    # STEP 2: Separate Verification Request with Clean Tracking Node
    USER_STATES[user_id] = {
        "state": "TRIGGER_CAPTCHA_PROMPT",
        "referrer_id": referrer_id,
        "username": username
    }

    await event.reply(
        "✨🎬 **Welcome to Studio Media Engine!**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "To securely verify your authorization token and complete structural setups, tap below:\n\n"
        "🤖 **Click the button below to generate your Math Captcha Puzzle:**",
        buttons=[[Button.inline("🔑 Click to Verify", f"prompt_captcha_{user_id}")]]
    )

async def send_humanized_dashboard(chat_id, user_id, message_id=None):
    user_data = await DatabaseManager.get_user(user_id)
    if not user_data:
        await DatabaseManager.register_user(user_id, "User")
        user_data = await DatabaseManager.get_user(user_id)

    is_admin = int(user_id) in ADMIN_IDS
    points = "Unlimited" if is_admin else user_data['points']
    plan = "System Administrator" if is_admin else user_data['plan']

    welcome_text = (
        f"👑 **STUDIO MEDIA ENGINE**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 **Welcome back, {user_data['username']}!**\n\n"
        f"📊 **Account Overview:**\n"
        f"💎 **Current Plan:** `{plan}`\n"
        f"🪙 **Search Balance:** `{points} Credits`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎮 **Use the menu buttons below to interact with the system:**"
    )

    keyboard_buttons = [
        [Button.text("🎬 Get Video", resize=True), Button.text("🔗 Refer Link", resize=True)],
        [Button.text("💎 Buy Premium", resize=True), Button.text("🎫 Redeem Coupon", resize=True)],
        [Button.text("👤 Profile", resize=True), Button.text("📖 How to Use", resize=True)]
    ]
    
    if message_id:
        try: await client.delete_messages(chat_id, message_id)
        except Exception: pass
            
    await client.send_message(chat_id, welcome_text, buttons=keyboard_buttons, parse_mode='markdown')

# ==========================================
#         INTERACTIVE BUTTON CALLBACKS
# ==========================================
@client.on(events.CallbackQuery)
async def on_ui_interaction(event):
    action = event.data
    user_id = str(event.sender_id)
    
    if SYSTEM_MAINTENANCE and int(user_id) not in ADMIN_IDS:
        if hasattr(event, 'answer'):
            await event.answer("⚠️ System is undergoing server-side maintenance updates.", alert=True)
        return

    # Handle separate interactive math generation step
    if action.startswith(b'prompt_captcha_'):
        target_uid = action.decode('utf-8').split('_')[2]
        if user_id != target_uid:
            await event.answer("⚠️ This verification prompt belongs to another active terminal node.", alert=True)
            return

        state_data = USER_STATES.get(user_id)
        if isinstance(state_data, dict) and state_data.get("state") == "TRIGGER_CAPTCHA_PROMPT":
            chosen_captcha = random.choice(MATH_CAPTCHAS)
            
            USER_STATES[user_id]["state"] = "AWAITING_REFERRAL_CAPTCHA"
            USER_STATES[user_id]["ans"] = chosen_captcha["answer"]
            
            await event.edit(
                f"🛡️ **SECURITY HUMAN CAPTCHA**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Solve this simple math expression to verify your session details:\n\n"
                f"🧩 **{chosen_captcha['question']} = ?**\n\n"
                f"✍️ Please type the single numerical result directly in text chat below:"
            )
        else:
            await event.answer("⏳ Session expired or already processed. Send /start again.", alert=True)
        return

    user_data = await DatabaseManager.get_user(user_id)
    is_admin = int(user_id) in ADMIN_IDS
    
    if not user_data: return
    if user_data['banned'] == 1 and not is_admin: return

    if action == b'show_profile':
        points_display = "Unlimited" if is_admin else f"{user_data['points']} pts"
        plan_display = "System Administrator" if is_admin else user_data['plan']
        profile_text = (
            f"👤 **YOUR SYSTEM PROFILE**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"👑 **Account Status:** `{plan_display}`\n"
            f"🪙 **Available Points:** `{points_display}`\n"
            f"👥 **Total Referrals:** `{user_data['referral_count']} users`"
        )
        await client.send_message(event.chat_id, profile_text, parse_mode='markdown')

    elif action == b'premium_store':
        # FEATURE INTEGRATED: Catalog modified to include Platinum Pass Tier criteria
        store_text = (
            f"🏪 **OFFICIAL PREMIUM STORE**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🥈 **Silver Premium Pass**\n"
            f"💰 Price: ₹30 UPI / 40 Stars\n"
            f"📈 Allowance: `15 Points daily` for a month\n\n"
            f"🥇 **Gold Premium Pass**\n"
            f"💰 Price: ₹50 UPI / 60 Stars\n"
            f"📈 Allowance: `25 Points daily` to search\n\n"
            f"💎 **Platinum Ultimate Pass**\n"
            f"💰 Price: ₹299 UPI / 599 Stars\n"
            f"📈 Allowance: `1000 Searches daily` for 30 days\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 **Select your preferred payment gateway below:**"
        )
        buttons = [
            [Button.inline("🥈 Silver Tier (₹30)", b"buy_Silver_30"), Button.inline("⭐ Silver (40 ⭐)", b"stars_Silver_40")],
            [Button.inline("🥇 Gold Tier (₹50)", b"buy_Gold_50"), Button.inline("⭐ Gold (60 ⭐)", b"stars_Gold_60")],
            [Button.inline("💎 Platinum Tier (₹299)", b"buy_Platinum_299"), Button.inline("⭐ Platinum (599 ⭐)", b"stars_Platinum_599")]
        ]
        await client.send_message(event.chat_id, store_text, buttons=buttons, parse_mode='markdown')

    elif action.startswith(b'stars_'):
        _, tier, star_count = action.decode('utf-8').split('_')
        stars_instruction_text = (
            f"⭐ **PREMIUM VIA TELEGRAM STARS**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 **Selected Tier:** `{tier} Pass` ({star_count} Stars)\n\n"
            f"🛠️ **How to pay:**\n"
            f"1️⃣ Go to **@BMWM4Z** and send him the required stars.\n"
            f"2️⃣ Take a proper screenshot of your successful transaction.\n"
            f"3️⃣ 📥 **Reply directly to this bot** with your payment proof photo!\n\n"
            f"⚡ *Your account configuration will be upgraded immediately after manual desk verification.*"
        )
        await client.send_message(event.chat_id, stars_instruction_text, parse_mode='markdown')

    elif action.startswith(b'buy_'):
        _, tier, cost = action.decode('utf-8').split('_')
        upi_id = "8368680967@fam"
        link = f"upi://pay?pa={upi_id}&pn=PremiumMediaHub&am={cost}&cu=INR&tn=Pay_{tier}_{user_id}"
        
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(link)
            qr.make(fit=True)
            bio = io.BytesIO()
            qr.make_image(fill_color="black", back_color="white").save(bio, format="PNG")
            bio.seek(0)
            bio.name = "pay_qr.png"
            
            await DatabaseManager.log_payment_attempt(user_id, tier, cost)
            await client.send_file(
                event.chat_id, file=bio,
                caption=f"🔒 **SECURE UPI GATEWAY**\n\n"
                        f"💎 **Selected Plan:** `{tier} Pass` (₹{cost} INR)\n\n"
                        f"📸 Scan using any payment application (GPay, PhonePe, Paytm).\n"
                        f"⚠️ **Note:** Please **REPLY** directly to this message with your confirmation screenshot proof."
            )
        except ModuleNotFoundError:
            await event.reply(f"Please process payment coordinates manually to this address:\n\n`{link}`")

    elif action == b'lucky_video_roll':
        is_sub = await check_membership(event.sender_id)
        if not is_sub:
            channel_buttons = [[Button.url(f"📢 Join Channel {i+1}", ch['link'])] for i, ch in enumerate(REQUIRED_CHANNELS)]
            await client.send_message(
                event.chat_id, 
                "🛑❌ **Verification Required:** You must join our official partner channels to unlock media requests!",
                buttons=channel_buttons
            )
            return

        if user_data['points'] < 1 and not is_admin:
            await client.send_message(event.chat_id, "🪙❌ **Empty Balance:** You do not have enough search credits. Invite friends or unlock premium packages.")
            return

        video_pool = await DatabaseManager.get_all_indexed_video_ids()
        if not video_pool:
            await client.send_message(event.chat_id, "📂❌ **Database Empty:** No valid media contents found inside registers.")
            return
        
        try:
            channel_entity = await client.get_entity(POMPOM_CHANNEL_ID)
        except Exception as e:
            logger.error(f"Media core database mismatch: {e}")
            await client.send_message(event.chat_id, "⚙️❌ **Configuration Error:** Routing error on verification tables.")
            return

        random.shuffle(video_pool)
        success = False

        for picked_msg_id in video_pool:
            try:
                source_msg = await client.get_messages(channel_entity, ids=picked_msg_id)
                if source_msg and source_msg.media:
                    media_msg = await client.send_file(
                        event.chat_id, 
                        file=source_msg.media, 
                        caption=f"🎬 **YOUR VIDEO READY**\n"
                                f"🔑 **Asset Database ID:** `{picked_msg_id}`\n"
                                f"🗑️ **Notice:** This specific video object will self-destruct in exactly **2 minutes** due to system server limits!"
                    )
                    
                    # TRIGGER AUTO-DELETION PIPELINE (2 MINUTES)
                    asyncio.create_task(auto_delete_media_worker(event.chat_id, media_msg.id))
                    
                    remaining_balance = await DatabaseManager.deduct_point(user_id)
                    await client.send_message(
                        event.chat_id, 
                        f"💳 **Account Updated:** `1 Credit Point` deducted.\n"
                        f"🪙 **Total Points Remaining:** `{remaining_balance}` points.",
                        parse_mode='markdown'
                    )
                    success = True
                    break
                else:
                    await DatabaseManager.remove_dead_video(picked_msg_id)
            except Exception:
                await DatabaseManager.remove_dead_video(picked_msg_id)
                continue

        if not success:
            await client.send_message(event.chat_id, "❌🛠 **System Error:** Failed compiling media payload elements.")

# ==========================================
#     NATIVE TEXT KEYBOARD NAVIGATION ROUTER
# ==========================================
@client.on(events.NewMessage)
async def handle_text_menu_navigation(event):
    if not event.text: return
    text = event.text.strip()
    user_id = str(event.sender_id)
    
    if text.startswith('/'): return

    if SYSTEM_MAINTENANCE and int(user_id) not in ADMIN_IDS:
        MENU_NAV_TRIGGERS = ["🎬 Get Video", "🔗 Refer Link", "💎 Buy Premium", "🎫 Redeem Coupon", "👤 Profile", "📖 How to Use"]
        if text in MENU_NAV_TRIGGERS:
            await event.reply(
                "⚠️🛠 **SYSTEM UNDER MAINTENANCE**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚙️ The core platform is currently undergoing necessary server optimizations.\n\n"
                "📢 **Notice:** All processes are locked. We will be back online shortly!"
            )
            return

    if text == "🎬 Get Video":
        event.data = b'lucky_video_roll'
        await on_ui_interaction(event)
    elif text == "🔗 Refer Link":
        bot_user = await client.get_me()
        refer_link = f"https://t.me/{bot_user.username}?start={user_id}"
        share_text = (
            f"🤝 **YOUR UNIQUE REFERRAL LINK**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 Share this promotional link with friends! When they verify and start the engine, you instantly gain `+3 Points` credited directly to your account.\n\n"
            f"🚀 `{refer_link}`"
        )
        await event.reply(share_text, parse_mode='markdown')
    elif text == "💎 Buy Premium":
        event.data = b'premium_store'
        await on_ui_interaction(event)
    elif text == "👤 Profile":
        event.data = b'show_profile'
        await on_ui_interaction(event)
    elif text == "🎫 Redeem Coupon":
        USER_STATES[user_id] = "AWAITING_COUPON"
        await event.reply(
            "🎫 **COUPON REDEMPTION SYSTEM**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✍️ Please **type or paste** your system coupon code below now:\n\n"
            "⚠️ **Notice:** Promotional keys are completely case-sensitive and expire immediately after the first usage."
        )
    elif text == "📖 How to Use":
        # FEATURE INTEGRATED: Documentation update reflecting 10 Welcome Points and Platinum tier options
        guide_text = (
            "📖 **BOT INSTRUCTION MANUAL**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Follow these standard rules for clean operation execution:\n\n"
            "1️⃣ **Unlocking Media (🎬 Get Video)**\n"
            "🔹 Tap the 'Get Video' selector menu.\n"
            "🔹 Every action consumes exactly `1 Credit Point` from your dashboard.\n"
            "🔹 Ensure you do not leave required sponsor channels!\n"
            "🔹 🗑️ **Auto Wiping:** Every video drops out of chat history exactly **2 minutes** post reception.\n\n"
            "2️⃣ **Earning Free Points (🔗 Refer Link)**\n"
            "🔹 New profiles automatically generate a **10 Point Welcome Bonus** upon verification.\n"
            "🔹 Generate your tracking link using 'Refer Link'.\n"
            "🔹 Gain `+3 Points` the instant your friend completes captcha verifications.\n\n"
            "3️⃣ **Going Premium (💎 Buy Premium)**\n"
            "🔹 Acquire continuous daily balance options using Telegram Stars (40 / 60 / 599 Stars via direct admin verification to @BMWM4Z) or UPI."
        )
        await event.reply(guide_text, parse_mode='markdown')

# ==========================================
#  STATE MACHINE & TRANSACTION PROOF LISTENER
# ==========================================
@client.on(events.NewMessage)
async def process_incoming_messages(event):
    if not event.text and not event.photo: return
    user_id = str(event.sender_id)
    
    MENU_BUTTONS = ["🎬 Get Video", "🔗 Refer Link", "💎 Buy Premium", "🎫 Redeem Coupon", "👤 Profile", "📖 How to Use"]
    if event.text and event.text.strip() in MENU_BUTTONS:
        return 

    if user_id in USER_STATES:
        current_state = USER_STATES[user_id]
        
        if SYSTEM_MAINTENANCE and int(user_id) not in ADMIN_IDS:
            USER_STATES.pop(user_id, None)
            await event.reply("⚠️🛠 Operation canceled: The platform has switched to maintenance mode.")
            return

        # STEP 3: SOLVING THE SPECIFIC MATH CAPTCHA LIST SELECTIONS
        if isinstance(current_state, dict) and current_state.get("state") == "AWAITING_REFERRAL_CAPTCHA":
            user_ans = event.text.strip() if event.text else ""
            if user_ans == current_state["ans"]:
                referrer_id = current_state["referrer_id"]
                username = current_state["username"]
                USER_STATES.pop(user_id, None)  # Wipe matching parameters instantly
                
                # Double check channel requirement matches before appending database parameters
                is_sub = await check_membership(event.sender_id)
                if not is_sub:
                    await event.reply("🛑 **Verification Failed:** It looks like you unjoined our partner channels while typing the captcha. Run /start again.")
                    return

                reg = await DatabaseManager.register_user(user_id, username, referrer_id)
                if reg == "BANNED":
                    await event.reply("🚫 **Access Denied:** Your account has been suspended by an administrator.")
                    return
                
                await event.reply("✅ **Verification Successful!** Full bot authority granted.")
                
                if reg and referrer_id:
                    try: await client.send_message(int(referrer_id), f"🎉 **Referral Success!** A new user joined via your link. `+3 Points` credited successfully!")
                    except Exception: pass

                    referrer_data = await DatabaseManager.get_user(referrer_id)
                    ref_username = referrer_data['username'] if referrer_data else "Unknown"
                    
                    ref_log_msg = (
                        f"🔔 **REFERRAL ALERT (JOINED & MATH VERIFIED)**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 **Invited User:** {event.sender.first_name or 'User'} (@{username}) [`{user_id}`]\n"
                        f"🤝 **Invited By:** @{ref_username} [`{referrer_id}`]\n"
                        f"🎁 **Reward status:** `+3 Points` added to inviter balance."
                    )
                    await dispatch_system_log(ref_log_msg)
                
                # Render active app panel metrics
                await send_humanized_dashboard(event.chat_id, user_id)
            else:
                # Select a brand new item out of the 10 math queries on failure
                chosen_captcha = random.choice(MATH_CAPTCHAS)
                USER_STATES[user_id]["ans"] = chosen_captcha["answer"]
                await event.reply(
                    f"❌ **Incorrect Math Result!** Let's try another string setup:\n\n"
                    f"🧩 **{chosen_captcha['question']} = ?**\n\n"
                    f"✍️ **Type the single number value below:**"
                )
            return

        if current_state == "AWAITING_COUPON" and event.text:
            coupon_code = event.text.strip()
            USER_STATES.pop(user_id, None)  
            
            awarded_points = await DatabaseManager.use_coupon(coupon_code, user_id)
            if awarded_points:
                await event.reply(
                    f"🎉 **COUPON REDEEMED SUCCESSFULLY**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔑 Security Code `{coupon_code}` verified and cleared.\n"
                    f"🪙 Credited `+{awarded_points} Balance Points` into your wallet database!"
                )
                
                coupon_use_log = (
                    f"🎫 **PROMO VOUCHER REDEEMED SYSTEM LOG**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **User:** @{event.sender.username or 'User'} (`{user_id}`)\n"
                    f"🎫 **Voucher Code:** `{coupon_code}`\n"
                    f"📈 **Value Delta:** `+{awarded_points} Points` added."
                )
                await dispatch_system_log(coupon_use_log)
            else:
                await event.reply(
                    "❌ **INVALID REDEMPTION KEY**\n\n"
                    "This coupon string is invalid, fake, or has already been consumed by another terminal node."
                )
            return

    if int(user_id) in ADMIN_IDS: return  
    user_data = await DatabaseManager.get_user(user_id)
    if user_data and user_data['banned'] == 1: return
    
    if event.message.photo:
        if SYSTEM_MAINTENANCE:
            await event.reply("⚠️🛠 **System Maintenance Active:** Transaction proof handling processes are locked currently.")
            return
            
        for admin_id in ADMIN_IDS:
            try:
                await client.send_message(
                    admin_id, 
                    f"📸 **INCOMING PREMIUM TRANSACTION PROOF**\n\n"
                    f"🆔 **Sender ID:** `{user_id}`\n"
                    f"👤 **Username:** @{event.sender.username or 'N/A'}\n"
                    f"🛠️ **Action Required:** Verify the uploaded invoice file below:",
                    file=event.message.photo,
                    # FEATURE INTEGRATED: Dynamic tracking added to register Platinum pass approvals directly via manual validation matrix
                    buttons=[
                        [Button.inline("🥈 Silver Pass", f"adm_v_Silver_{user_id}"), Button.inline("🥇 Gold Pass", f"adm_v_Gold_{user_id}")],
                        [Button.inline("💎 Platinum Pass", f"adm_v_Platinum_{user_id}")],
                        [Button.inline("❌ Reject Documentation", f"adm_v_Reject_{user_id}")]
                    ]
                )
            except Exception as e:
                logger.error(f"Failed to deliver receipt to admin desk `{admin_id}`: {e}")
                
        await event.reply("🚀 **Transmission Received:** Your invoice asset file was routed to the administrative operations terminal for priority processing.")

# ==========================================
#      NATIVE STARS PRE-CHECKOUT HANDLERS
# ==========================================
@client.on(events.Raw(types.UpdateBotPrecheckoutQuery))
async def handle_stars_precheckout(event):
    await client(functions.messages.SetBotPrecheckoutResultsRequest(
        query_id=event.query_id, success=True
    ))

@client.on(events.Raw(types.UpdateNewMessage))
async def handle_successful_star_payment(event):
    if hasattr(event, 'message') and isinstance(event.message, types.Message) and event.message.action:
        if isinstance(event.message.action, types.MessageActionPaymentSentMe):
            payment_action = event.message.action
            payload = payment_action.payload.decode('utf-8')
            
            if payload.startswith("starpay_"):
                _, tier, uid = payload.split('_')
                await DatabaseManager.update_premium_plan(uid, tier)
                try:
                    await client.send_message(int(uid), f"💎 **PREMIUM UPGRADE COMPLETE**\n\nYour account infrastructure configurations scaled successfully to **{tier} Pass**!")
                except Exception: pass
                
                for admin_id in ADMIN_IDS:
                    try: await client.send_message(admin_id, f"⭐ `[STARS TRANSACTION LOG]`\n👤 **User ID:** `{uid}`\n👑 **Tier Activated:** `{tier}`")
                    except Exception: pass
                
                star_log = (
                    f"⭐ **AUTOMATED STAR TRANSACTION CLEARED**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 **Target ID:** `{uid}`\n"
                    f"💎 **Plan Unlocked:** `{tier} Pass` via checkout routing protocols."
                )
                await dispatch_system_log(star_log)

# ==========================================
#         ADMIN ACTIONS PROCESSOR
# ==========================================
@client.on(events.CallbackQuery(pattern=b'adm_v_'))
async def handle_admin_verdict(event):
    if event.sender_id not in ADMIN_IDS: 
        await event.answer("🚫 Permission Denied: Admin verification credentials required.", alert=True)
        return
        
    _, _, choice, target_uid = event.data.decode('utf-8').split('_')
    
    if choice != "Reject":
        await DatabaseManager.update_premium_plan(target_uid, choice)
        try: 
            await client.send_message(
                int(target_uid), 
                f"💎 **Premium Activated!** Your transaction documentation was verified by our admin desk.\n"
                f"Your profile database has been scaled to the premium **{choice} Pass**! Access features now."
            )
        except Exception: pass
        await event.edit(f"✅ Approved user database node `{target_uid}` for premium `{choice}` Pass.")
        
        try:
            target_entity = await client.get_entity(int(target_uid))
            first_name = target_entity.first_name or 'User'
            username_field = f"@{target_entity.username}" if target_entity.username else "N/A"
        except Exception:
            first_name = "User"
            username_field = "N/A"

        premium_log = (
            f"🛠️ **MANUAL SYSTEM UPGRADE EXECUTED**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Account User:** {first_name} ({username_field})\n"
            f"🆔 **User ID:** `{target_uid}`\n"
            f"👑 **Assigned Profile:** `{choice} Pass` (Manual Admin Approval)"
        )
        await dispatch_system_log(premium_log)
    else:
        try: 
            await client.send_message(int(target_uid), "❌ **Transaction Invoice Dropped:** The invoice screenshot document submitted was rejected upon administrative checking logs.")
        except Exception: pass
        await event.edit(f"❌ Rejected and discarded document upload packet filed by user link `{target_uid}`.")

# --- TERMINAL OVERRIDE CONTROL INTERFACE ---
@client.on(events.NewMessage(pattern=r'/adminGC'))
async def admin_central_terminal_cmd(event):
    global SYSTEM_MAINTENANCE
    if event.sender_id not in ADMIN_IDS: return
    raw_args = event.text.split(" ")
    u_count, m_count, b_count, p_count = await DatabaseManager.get_system_stats()
    
    current_m_status = "⚠️ ACTIVE (Users Locked)" if SYSTEM_MAINTENANCE else "✅ OFFLINE (Standard Operation)"

    stats_panel = (
        f"👑 **ADMIN OVERRIDE DASHBOARD**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ **Maintenance Mode Status:** `{current_m_status}`\n"
        f"👥 **Total Users registered:** `{u_count}`\n"
        f"🎬 **Indexed Media Elements:** `{m_count}`\n"
        f"🚫 **Blacklisted System IDs:** `{b_count}`\n"
        f"💳 **Pending Payment Orders:** `{p_count}`\n\n"
        f"🛠️ `/adminGC maintenance` (Toggle lock switches)\n"
        f"🚫 `/adminGC ban <id>`\n"
        f"🔓 `/adminGC unban <id>`\n"
        f"🪙 `/adminGC addpoints <id> <amount>`\n"
        f"🎫 `/coupon <count> <points_per_coupon>`\n"
        f"📢 `/adminGC broadcast <your message>`\n"
        f"📦 `/exportdb` (Backup full pompom.db file)"
    )
    
    if len(raw_args) == 1:
        await event.reply(stats_panel, parse_mode='markdown')
        return
        
    sub_command = raw_args[1].lower()
    
    if sub_command == "maintenance":
        SYSTEM_MAINTENANCE = not SYSTEM_MAINTENANCE
        status_text = "ENABLED (Users Locked out)" if SYSTEM_MAINTENANCE else "DISABLED (Standard Operation online)"
        await event.reply(f"⚙️🛠 **System Maintenance Update:** Switch toggled successfully. Currently **{status_text}**.")
        
        m_log = (
            f"⚙️🛠 **SYSTEM STATUS FLIP NOTICE**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛠️ **Action Flag:** Maintenance parameter configuration changed.\n"
            f"📊 **Current Matrix State:** Bot status is now set to `{status_text}`."
        )
        await dispatch_system_log(m_log)

    elif sub_command == "ban" and len(raw_args) > 2:
        target = raw_args[2]
        await DatabaseManager.set_user_ban_status(target, 1)
        await event.reply(f"🚫 **Action Verified:** User ID `{target}` is now blacklisted globally.")
        
    elif sub_command == "unban" and len(raw_args) > 2:
        target = raw_args[2]
        await DatabaseManager.set_user_ban_status(target, 0)
        await event.reply(f"🔓 **Action Verified:** User ID `{target}` access clearances restored.")

    elif sub_command == "addpoints" and len(raw_args) > 3:
        target = raw_args[2]
        amount = int(raw_args[3])
        await DatabaseManager.add_points(target, amount)
        await event.reply(f"🪙 **Action Verified:** Deposited `+{amount}` points directly to user row ID `{target}`.")
        
    elif sub_command == "broadcast" and len(raw_args) > 2:
        broadcast_msg = event.text.split("broadcast ", 1)[1]
        user_list = await DatabaseManager.get_all_active_user_ids()
        prog = await event.reply(f"🚀 Packaging network transmission to {len(user_list)} data points...")
        
        sent = 0
        for uid in user_list:
            try:
                await client.send_message(int(uid), f"📢 **GLOBAL SYSTEM ANNOUNCEMENT**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{broadcast_msg}")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception: pass
        await prog.edit(f"🚀 **Transmission Terminated:** Deliveries dispatched to `{sent}` terminal ports successfully.")

# --- DIRECT BACKUP MANAGEMENT SYSTEM ---
@client.on(events.NewMessage(pattern=r'/exportdb'))
async def export_database_handler(event):
    if event.sender_id not in ADMIN_IDS: return
    if not os.path.exists(DB_FILE):
        await event.reply("📂❌ **Error:** No existing database file found on the disk.")
        return
        
    try:
        await event.reply("⏳ *Extracting structural database assets...*")
        await client.send_file(
            event.chat_id,
            file=DB_FILE,
            caption=f"📦 **POMPOM BACKUP SECURE NODE**\n\n🗓️ **Generated On:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n⚠️ Keep this file safe to recover assets in case of a server reset."
        )
    except Exception as e:
        await event.reply(f"❌ **Export Failed:** {e}")

@client.on(events.NewMessage(pattern=r'/importdb'))
async def import_database_handler(event):
    if event.sender_id not in ADMIN_IDS: return
    
    # Verify if the admin replied directly to a valid document file
    if not event.is_reply:
        await event.reply("⚠️ **Usage:** Reply to a valid `.db` file with `/importdb` to swap configurations.")
        return
        
    replied_msg = await event.get_reply_message()
    if not replied_msg or not replied_msg.document:
        await event.reply("❌ **Error:** The target message does not contain a valid backup document container.")
        return

    status_msg = await event.reply("⏳ *Stopping queries and overwriting live database storage layer...*")
    try:
        # Download the new database over the old path
        await client.download_media(replied_msg.document, file=DB_FILE)
        
        # Flush connection tables by reinitializing structures
        await DatabaseManager.initialize()
        u, m, b, p = await DatabaseManager.get_system_stats()
        
        await status_msg.edit(
            f"✅ **DATABASE SWAPPED SUCCESSFULLY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Users Restored: `{u}`\n"
            f"🎬 Videos Synced: `{m}`\n"
            f"🚫 Suspended Profiles: `{b}`\n\n"
            f"🚀 *All systems synchronized and fully live!*"
        )
    except Exception as e:
        await status_msg.edit(f"❌ **Import Failed:** Engine failed critical recovery: {e}")

# --- COUPON CREATOR ---
@client.on(events.NewMessage(pattern=r'/coupon'))
async def admin_coupon_generator_handler(event):
    if event.sender_id not in ADMIN_IDS: return
    raw_args = event.text.split(" ")
    
    if len(raw_args) < 3:
        await event.reply("🎫⚙️ **Usage Guide:** `/coupon <count> <points>`\nExample: `/coupon 5 20` (Generates 5 separate codes worth 20 points each)")
        return
        
    try:
        count = int(raw_args[1])
        points = int(raw_args[2])
    except ValueError:
        await event.reply("❌ **Processing Error:** Script arguments must be valid integers.")
        return

    output_buffer = []
    for _ in range(count):
        secret_key = f"POMPOM-{secrets.token_hex(6).upper()}"
        await DatabaseManager.create_coupon(secret_key, points, str(event.sender_id))
        output_buffer.append(f"`{secret_key}`")
        
    coupon_list_str = "\n".join(output_buffer)
    await event.reply(
        f"🎫🎁 **{count} NEW CAMPAIGN COUPONS COMMITTED**\n"
        f"🪙 **Reward Value:** `{points} Points` each\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{coupon_list_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ *Voucher Parameters:* Codes are completely single-use and disappear instantly upon processing validation."
    )
    
    coupon_gen_log = (
        f"🎫⚙️ **NEW SYSTEM PROMO KEYS COMPILED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 **Total Volume Generated:** `{count}` coupons initialized\n"
        f"📈 **Unit Value Delta:** `{points} Balance Points` per voucher code\n"
        f"🆔 **Generated By Admin ID:** `{event.sender_id}`\n"
        f"⏳ **Status:** Awaiting operational processing loops."
    )
    await dispatch_system_log(coupon_gen_log)

# --- AUTOMATIC MEDIA INTERCEPT INDEXER ---
@client.on(events.NewMessage)
async def admin_manual_forward_indexer(event):
    if event.sender_id not in ADMIN_IDS: return
    if event.message.fwd_from and event.message.file:
        channel_post_id = event.message.fwd_from.channel_post or event.message.fwd_from.saved_from_msg_id or event.message.id
        file_attr = event.message.file
        raw_name = file_attr.name or f"VideoAsset_{channel_post_id}"
        bytes_measure = file_attr.size or 0
        
        await DatabaseManager.cache_pompom_video(msg_id=channel_post_id, name=raw_name, size=bytes_measure)
        await event.reply(f"🎬 **MEDIA ASSET SYSTEM INDEXED**\n🔑 **DB Record Key:** `{channel_post_id}`\n📜 **Assigned Name:** `{raw_name}`\n📊 **File Size Matrix:** `{format_size(bytes_measure)}`")
# ==========================================
#         MAIN SYSTEM INITIALIZER
# ==========================================
async def main():
    global client
    
    # 1. Instantiate the client safely inside the running 3.14 event loop
    client = TelegramClient('pompom_core_session', API_ID, API_HASH)
    
    # 2. Register your event listeners dynamically to the newly built client
    client.add_event_handler(on_start_command, events.NewMessage(pattern='/start'))
    client.add_event_handler(on_ui_interaction, events.CallbackQuery)
    client.add_event_handler(handle_text_menu_navigation, events.NewMessage)
    client.add_event_handler(process_incoming_messages, events.NewMessage)
    client.add_event_handler(admin_central_terminal_cmd, events.NewMessage(pattern=r'/adminGC'))
    client.add_event_handler(export_database_handler, events.NewMessage(pattern=r'/exportdb'))
    client.add_event_handler(import_database_handler, events.NewMessage(pattern=r'/importdb'))
    client.add_event_handler(admin_coupon_generator_handler, events.NewMessage(pattern=r'/coupon'))
    client.add_event_handler(admin_manual_forward_indexer, events.NewMessage)
    
    # 3. Start the engine credentials 
    await client.start(bot_token=BOT_TOKEN)
    await DatabaseManager.initialize()
    logger.info("🤖 Complete professional communication suite is live and listening on Python 3.14!")
    
    # 4. Hand off execution loop control
    await client.run_until_disconnected()

if __name__ == '__main__':
    # Safely creates and drops the runtime event loop infrastructure
    asyncio.run(main())

