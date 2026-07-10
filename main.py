import os
import re
import json
import io
import logging
import asyncio
import random
from datetime import datetime, timedelta
import aiosqlite
import telethon
from telethon import TelegramClient, events, functions, types, Button

# --- 🔍 FUZZY MATCHING LOGIC 🔍 ---
def clean_string(text):
    text = re.sub(r'[\._\-]', ' ', text.lower())
    return re.sub(r'\s+', ' ', text).strip()

def calculate_similarity(s1, s2):
    s1, s2 = clean_string(s1), clean_string(s2)
    if not s1 or not s2: return 0.0
    if s1 in s2 or s2 in s1: return 0.85
    
    words1, words2 = s1.split(), s2.split()
    matches = sum(1 for w in words1 if any(w in target or target in w for target in words2))
    return matches / max(len(words1), len(words2))

def format_size(bytes_size):
    if not bytes_size: return "Unknown Size"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

# --- 📝 CONFIGURATION LOGING LAYER 📝 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_runtime.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("MovieAdvancedBot")

# ==========================================
#         ⚙️ HARDCODED PRODUCTION VALUES
# ==========================================
API_ID = 35485985              
API_HASH = '5441c09a9c8bf58374e1f8f227b95794'     
BOT_TOKEN = '8791980160:AAGU4JwkQXL1dxgRqVUxgeARJROwLfL19g4'   
ADMIN_ID = 7952327997                 

REQUIRED_CHANNELS = [
    {"id": -1003985304953, "link": "https://t.me/yagamicorporation"},
]       
CHANNEL_LINK = "https://t.me/yagamicorporation"
MOVIE_CHANNEL_ID = -1002107962104        

client = TelegramClient('movie_advanced_session', API_ID, API_HASH)

# 📂 ABSOLUTE PATH FIX: Guarantees your database is read correctly instead of blank creation
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "bot_production.db")

# 🧠 Pagination Dynamic Cache Mapping (Key: user_id)
PAGINATION_CACHE = {}

# ==========================================
#         🗄️ DATABASE SYSTEM ABSTRACTS
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
                    searches_today INTEGER DEFAULT 0,
                    max_limit INTEGER DEFAULT 5,
                    referral_count INTEGER DEFAULT 0,
                    referred_by TEXT,
                    last_reset_date TEXT,
                    banned INTEGER DEFAULT 0,
                    last_reward_time TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS movies (
                    msg_id INTEGER PRIMARY KEY,
                    file_name TEXT,
                    caption TEXT,
                    search_vector TEXT,
                    file_size INTEGER
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
            await db.commit()

            async with db.execute("PRAGMA table_info(users)") as cursor:
                user_columns = [row[1] for row in await cursor.fetchall()]
                if 'premium_expiry' not in user_columns:
                    await db.execute("ALTER TABLE users ADD COLUMN premium_expiry TEXT DEFAULT 'Never'")
                if 'last_reward_time' not in user_columns:
                    await db.execute("ALTER TABLE users ADD COLUMN last_reward_time TEXT")
                await db.commit()

            async with db.execute("PRAGMA table_info(movies)") as cursor:
                movie_columns = [row[1] for row in await cursor.fetchall()]
                if 'search_count' not in movie_columns:
                    await db.execute("ALTER TABLE movies ADD COLUMN search_count INTEGER DEFAULT 0")
                    await db.commit()

            await db.execute("CREATE INDEX IF NOT EXISTS idx_movies_vector ON movies(search_vector);")
            await db.commit()
            logger.info("⚡ SQLite Persistent Engine Online and Absolute Paths Mounted.")

    @staticmethod
    async def check_and_dump_movies_terminal():
        print("\n=========================================================")
        print("🔍 DIAGNOSTIC: CHECKING 'MOVIES' TABLE IN DATABASE...")
        print("=========================================================")
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("PRAGMA table_info(movies)") as cursor:
                    cols = await cursor.fetchall()
                    print(f"📋 Table Columns Found: {[c['name'] for c in cols]}")
                
                async with db.execute("SELECT COUNT(*) FROM movies") as cursor:
                    total_count = (await cursor.fetchone())[0]
                    print(f"📊 Total Movies Found in Database: {total_count} records")
                
                if total_count == 0:
                    print("⚠️ WARNING: The table is empty! Check your database location or file name.")
                else:
                    print("✅ Printing a snapshot verification row:")
                    async with db.execute("SELECT msg_id, file_name, search_vector FROM movies LIMIT 1") as cursor:
                        row = await cursor.fetchone()
                        if row:
                            print(f"  [FOUND UNIQUE DATA] ID: {row['msg_id']} | Title: {row['file_name']}")
        except Exception as e:
            print(f"❌ DATABASE CHECK ERROR: {str(e)}")
        print("=========================================================\n")

    @staticmethod
    async def register_user(user_id: str, username: str, referrer_id: str = None):
        async with aiosqlite.connect(DB_FILE) as db:
            today = str(datetime.now().date())
            async with db.execute("SELECT user_id, banned FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    if row[1] == 1: return "BANNED"
                    await DatabaseManager.verify_daily_reset(user_id)
                    return False
            
            await db.execute("""
                INSERT INTO users (user_id, username, plan, searches_today, max_limit, referral_count, referred_by, last_reset_date, banned, premium_expiry)
                VALUES (?, ?, 'Free', 0, 5, 0, ?, ?, 0, 'Never')
            """, (user_id, username, referrer_id, today))
            if referrer_id:
                await db.execute("UPDATE users SET max_limit = max_limit + 5, referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
            await db.commit()
            return True

    @staticmethod
    async def get_user(user_id: str):
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    await DatabaseManager.verify_daily_reset(user_id)
                    await DatabaseManager.check_premium_expiry(user_id)
                    async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as fresh_cursor:
                        return await fresh_cursor.fetchone()
                return None

    @staticmethod
    async def verify_daily_reset(user_id: str):
        today = str(datetime.now().date())
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT last_reset_date, plan FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0] != today:
                    base_limit = 5
                    if row[1] == 'Silver': base_limit = 30
                    elif row[1] == 'Gold': base_limit = 60
                    await db.execute("UPDATE users SET searches_today = 0, last_reset_date = ?, max_limit = MAX(max_limit, ?) WHERE user_id = ?", (today, base_limit, user_id))
                    await db.commit()

    @staticmethod
    async def check_premium_expiry(user_id: str):
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT plan, premium_expiry FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0] != 'Free' and row[1] != 'Never':
                    try:
                        expiry_date = datetime.strptime(row[1], "%Y-%m-%d").date()
                        if datetime.now().date() > expiry_date:
                            await db.execute("UPDATE users SET plan = 'Free', max_limit = 5, premium_expiry = 'Never' WHERE user_id = ?", (user_id,))
                            await db.commit()
                    except Exception as e:
                        logger.error(f"Expiry date reading error: {e}")

    @staticmethod
    async def increment_search(user_id: str):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET searches_today = searches_today + 1 WHERE user_id = ?", (user_id,))
            await db.commit()

    @staticmethod
    async def update_premium_plan(user_id: str, plan_name: str, allocated_limit: int, duration_days: int = 30):
        async with aiosqlite.connect(DB_FILE) as db:
            expiry_str = 'Never' if plan_name == 'Free' else str((datetime.now() + timedelta(days=duration_days)).date())
            await db.execute("UPDATE users SET plan = ?, max_limit = MAX(max_limit, ?), premium_expiry = ? WHERE user_id = ?", (plan_name, allocated_limit, expiry_str, user_id))
            await db.commit()

    @staticmethod
    async def update_user_reward(user_id: str, added_quota: int, timestamp_str: str):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET max_limit = max_limit + ?, last_reward_time = ? WHERE user_id = ?", (added_quota, timestamp_str, user_id))
            await db.commit()

    @staticmethod
    async def log_payment_attempt(user_id: str, plan_name: str, price: str):
        async with aiosqlite.connect(DB_FILE) as db:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute("INSERT INTO payments (user_id, plan_name, price, status, timestamp) VALUES (?, ?, ?, 'Pending', ?)", (user_id, plan_name, price, now_str))
            await db.commit()

    @staticmethod
    async def update_payment_status(user_id: str, plan_name: str, status: str):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE payments SET status = ? WHERE user_id = ? AND plan_name = ? AND status = 'Pending'", (status, user_id, plan_name))
            await db.commit()

    @staticmethod
    async def cache_movie(msg_id: int, name: str, caption: str, size: int):
        async with aiosqlite.connect(DB_FILE) as db:
            vector = f"{name} {caption}".lower().strip()
            await db.execute("""
                INSERT OR REPLACE INTO movies (msg_id, file_name, caption, search_vector, file_size, search_count)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT search_count FROM movies WHERE msg_id = ?), 0))
            """, (msg_id, name, caption, vector, size, msg_id))
            await db.commit()

    @staticmethod
    async def query_movie_catalog(query_string: str):
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            clean_input = clean_string(query_string)
            tokens = [t for t in clean_input.split() if len(t) > 1]
            if not tokens: tokens = [clean_input]

            sql_conditions = []
            sql_parameters = []
            for token in tokens[:3]:
                sql_conditions.append("(file_name LIKE ? OR search_vector LIKE ?)")
                sql_parameters.extend([f"%{token}%", f"%{token}%"])
            
            where_clause = " OR ".join(sql_conditions)
            query = f"SELECT * FROM movies WHERE {where_clause} LIMIT 500"
            
            async with db.execute(query, sql_parameters) as cursor:
                filtered_subset = await cursor.fetchall()
            
            scored_matches = []
            for item in filtered_subset:
                score_name = calculate_similarity(query_string, item['file_name'])
                score_vector = calculate_similarity(query_string, item['search_vector'] or "")
                best_score = max(score_name, score_vector)
                if best_score >= 0.35:
                    scored_matches.append((best_score, item))
            
            scored_matches.sort(key=lambda x: x[0], reverse=True)
            return [element[1] for element in scored_matches]

    @staticmethod
    async def get_trending_movies():
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM movies WHERE search_count > 0 ORDER BY search_count DESC LIMIT 5") as cursor:
                return await cursor.fetchall()

    @staticmethod
    async def increment_movie_download(msg_id: int):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE movies SET search_count = search_count + 1 WHERE msg_id = ?", (msg_id,))
            await db.commit()

    @staticmethod
    async def get_system_stats():
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as c1: total_users = (await c1.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM movies") as c2: total_movies = (await c2.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE banned = 1") as c3: banned_users = (await c3.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM payments WHERE status = 'Pending'") as c4: pending_payments = (await c4.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE plan != 'Free'") as c5: active_premiums = (await c5.fetchone())[0]
            return total_users, total_movies, banned_users, pending_payments, active_premiums

    @staticmethod
    async def get_top_referrers():
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT username, user_id, referral_count FROM users ORDER BY referral_count DESC LIMIT 5") as cursor:
                return await cursor.fetchall()

    @staticmethod
    async def set_user_ban_status(user_id: str, status: int):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET banned = ? WHERE user_id = ?", (status, user_id))
            await db.commit()

    @staticmethod
    async def reset_all_daily_quotas():
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET searches_today = 0")
            await db.commit()

    @staticmethod
    async def get_all_user_ids():
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT user_id FROM users WHERE banned = 0") as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

async def check_membership(user_id: int) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            await client(functions.channels.GetParticipantRequest(channel=ch['id'], participant=user_id))
        except Exception:
            return False
    return True

# ==========================================
#         🤖 TELEGRAM SYSTEM EVENTS
# ==========================================
@client.on(events.NewMessage(pattern='/start'))
async def on_start_command(event):
    user_id = str(event.sender_id)
    username = event.sender.username or "Anonymous"
    payload = event.message.message.split(' ')
    referrer_id = payload[1] if len(payload) > 1 and payload[1].isdigit() else None
    if referrer_id == user_id: referrer_id = None
        
    registration_status = await DatabaseManager.register_user(user_id, username, referrer_id)
    if registration_status == "BANNED":
        await event.reply("⚠️ *Your access privilege has been revoked by administration rules.*")
        return
    
    if registration_status and referrer_id:
        try: await client.send_message(int(referrer_id), f"🎉 🔔 *Referral Alert!*\n\nAn authorized user joined via your link.\n➕5 Daily Requests applied!")
        except Exception: pass

    await send_advanced_dashboard(event.chat_id, user_id)

async def send_advanced_dashboard(chat_id, user_id, message_id=None):
    user_data = await DatabaseManager.get_user(user_id)
    if not user_data:
        await DatabaseManager.register_user(user_id, "User")
        user_data = await DatabaseManager.get_user(user_id)

    welcome_styled = (
        f"🎬 🎪 *WELCOME TO THE MOVIE ENGINE HUB v2.6* 🎪\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 *Status Profile*: `{user_data['plan']}` Class Tier\n"
        f"📊 *Usage Counters*: `{user_data['searches_today']}` / `{user_data['max_limit']}` Daily Tokens\n"
        f"⏳ *Expiry Monitor*: `{user_data['premium_expiry']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✍️ Drop cinematic titles into the chat window below."
    )

    keyboard = [
        [Button.inline("🔍 Discover Movies & Series Hub", b"discover_hub")],
        [Button.inline("👤 Profile & Plan", b"account_status"), Button.inline("🎁 Claim Token", b"daily_reward")],
        [Button.inline("📈 Stats & Trends", b"stats_panel"), Button.inline("🏆 Leaderboard", b"leaderboard_view")],
        [Button.inline("💎 Upgrade Premium (UPI / Stars)", b"premium_menu")]
    ]
    
    if message_id:
        try: await client.edit_message(chat_id, message_id, welcome_styled, buttons=keyboard, parse_mode='markdown')
        except Exception: await client.send_message(chat_id, welcome_styled, buttons=keyboard, parse_mode='markdown')
    else:
        await client.send_message(chat_id, welcome_styled, buttons=keyboard, parse_mode='markdown')

@client.on(events.NewMessage(pattern='/menu'))
async def on_menu_command(event):
    await on_start_command(event)

# ==========================================
#         🎛️ CALLBACK ROUTING SUBSYSTEM
# ==========================================
@client.on(events.CallbackQuery)
async def on_interactive_callback(event):
    action = event.data
    user_id = str(event.sender_id)
    user_data = await DatabaseManager.get_user(user_id)
    
    if not user_data:
        await DatabaseManager.register_user(user_id, event.sender.username or "User")
        user_data = await DatabaseManager.get_user(user_id)
    
    if user_data and user_data['banned'] == 1:
        await event.answer("⚠️ Access suspended.", alert=True)
        return
        
    bot_identity = await client.get_me()

    if action == b'discover_hub':
        await event.answer("💡 Just type the movie name directly into the chat box!", alert=True)

    elif action == b'daily_reward':
        now = datetime.now()
        can_claim = True
        
        if user_data['last_reward_time']:
            try:
                last_claim_dt = datetime.strptime(user_data['last_reward_time'], "%Y-%m-%d %H:%M:%S")
                if now - last_claim_dt < timedelta(hours=24):
                    can_claim = False
                    remaining_time = timedelta(hours=24) - (now - last_claim_dt)
                    hours, remainder = divmod(remaining_time.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    await event.answer(f"🔒 Reward locked! Try again in {hours}h {minutes}m.", alert=True)
            except Exception: pass
            
        if can_claim:
            lucky_bonus = random.randint(1, 20)
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            await DatabaseManager.update_user_reward(user_id, lucky_bonus, now_str)
            await event.answer(f"🎁 Random Gift Mystery Box Unlocked!\nYou received +{lucky_bonus} Supplementary Search Quota limits!", alert=True)

    elif action == b'account_status':
        stats_layout = (
            f"👤 *YOUR ACCOUNT PRIVILEGE PROFILE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 **User ID**: `{user_id}`\n"
            f"🏅 **System Rank**: *{user_data['plan']} User Class*\n"
            f"📊 **Daily Usage Counter**: `{user_data['searches_today']}` / `{user_data['max_limit']}` Allocations\n"
            f"🤝 **Network Referrals**: `{user_data['referral_count']}` Verified Joins\n"
            f"⏳ **Subscription Cycle Expiry**: `{user_data['premium_expiry']}`"
        )
        await event.edit(stats_layout, buttons=[[Button.inline("🔙 Return to Dashboard", b"back_to_root")]], parse_mode='markdown')

    elif action == b'stats_panel':
        ref_link = f"https://t.me/{bot_identity.username}?start={user_id}"
        trending_list = await DatabaseManager.get_trending_movies()
        trends_text = "🔥 *CURRENT TOP SEARCH TRENDS*:\n"
        if trending_list:
            for idx, tr in enumerate(trending_list, 1):
                trends_text += f" `{idx}`. {tr['file_name']} (Dispatched `{tr['search_count']}` times)\n"
        else:
            trends_text += " _No metrics cached for today's logs yet._\n"

        stats_layout = (
            f"🚀 **NETWORK GROWTH METRICS & SYSTEM PARAMETERS**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 **Your Referrals**: `{user_data['referral_count']}` Users Joined\n"
            f"🔗 **Your Affiliate Link Track Vector**:\n`{ref_link}`\n\n" + trends_text
        )
        await event.edit(stats_layout, buttons=[[Button.inline("🔙 Return to Dashboard", b"back_to_root")]], parse_mode='markdown')

    elif action == b'leaderboard_view':
        top_referrers = await DatabaseManager.get_top_referrers()
        board_text = f"🏆 *GLOBAL SYSTEM REFERRERS LEADERBOARD*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for rank, ref in enumerate(top_referrers, 1):
            board_text += f" ⭐ *Position [{rank}]* 🏅 @{ref['username'] or 'None'} with `{ref['referral_count']}` active nodes\n"
        await event.edit(board_text, buttons=[[Button.inline("🔙 Return to Dashboard", b"back_to_root")]], parse_mode='markdown')

    elif action == b'premium_menu':
        upgrade_layout = (
            f"💎 *PREMIUM PLAN ENGINE CONFIGURATIONS*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🥈 *SILVER PASS TIERS* - 30 limits/24h (₹29 / 50 Stars)\n"
            f"🥇 *GOLD PASS TIERS* - 60 limits/24h (₹49 / 100 Stars)\n"
        )
        buttons = [
            [Button.inline("🥈 Silver (₹29 UPI)", b"pay_Silver_29"), Button.inline("⭐ Silver (50 Stars)", b"stars_Silver_50")],
            [Button.inline("🥇 Gold (₹49 UPI)", b"pay_Gold_49"), Button.inline("⭐ Gold (100 Stars)", b"stars_Gold_100")],
            [Button.inline("🔙 Return to Dashboard", b"back_to_root")]
        ]
        await event.edit(upgrade_layout, buttons=buttons, parse_mode='markdown')

    # 🌟 REDIRECT TO USER FOR STARS PAYMENT 🌟
    elif action.startswith(b'stars_'):
        _, tier_name, price_stars = action.decode('utf-8').split('_')
        
        redirect_layout = (
            f"⭐️ *MANUAL TELEGRAM STARS PAYMENT* ⭐️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 **Selected Plan**: `{tier_name} Premium Pass` (30 Days)\n"
            f"💰 **Price**: `{price_stars} Telegram Stars`\n\n"
            f"👉 Please click the button below to message **@Gopalji_chouney** directly to send the Stars payment. "
            f"Once you complete the transfer, send them your payment proof to get activated manually! 🎉"
        )
        
        buttons = [
            [Button.url("💬 Send Stars to Admin", "https://t.me/Gopalji_chouney")],
            [Button.inline("🔙 Back to Premium Menu", b"premium_menu")]
        ]
        await event.edit(redirect_layout, buttons=buttons, parse_mode='markdown')

    elif action.startswith(b'pay_'):
        _, plan_name, price_val = action.decode('utf-8').split('_')
        business_upi = "8368680967@fam"  
        upi_payload = f"upi://pay?pa={business_upi}&pn=MovieSystem&am={price_val}&cu=INR&tn=Pay_{plan_name}_{user_id}"
        
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(upi_payload)
            qr.make(fit=True)
            
            byte_io = io.BytesIO()
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img.save(byte_io, format="PNG")
            byte_io.seek(0)
            byte_io.name = "payment_qr.png"
            
            await DatabaseManager.log_payment_attempt(user_id, plan_name, price_val)
            await event.delete()
            
            await client.send_file(
                event.chat_id, 
                file=byte_io,
                force_document=False,
                caption=f"📲 *DYNAMIC UPI TRANSFER INTERFACE*\n"
                        f"💳 **Target Pass**: `{plan_name} Account Tier` - `₹{price_val} INR` \n\n"
                        f"🤳 Scan this QR code using FamPay, PhonePe, or GPay. When complete, REPLY to this image with your payment screenshot verification!",
                buttons=[[Button.inline("❌ Drop Order & Return", b"premium_menu")]]
            )
        except ModuleNotFoundError:
            await event.reply(
                "⚠️ **UPI QR Code system module missing.**\n"
                f"Alternative direct copy string payload:\n`{upi_payload}`"
            )

    elif action.startswith(b'get_file_'):
        msg_id_target = int(action.decode('utf-8').split('_')[2])
        if user_data['searches_today'] >= user_data['max_limit']:
            await event.answer("⚠️ Search allocation quota hit. Invite users or upgrade tiers!", alert=True)
            return
            
        try:
            await client.forward_messages(event.chat_id, msg_id_target, MOVIE_CHANNEL_ID)
            await DatabaseManager.increment_search(user_id)
            await DatabaseManager.increment_movie_download(msg_id_target)
            await event.answer("📦 Movie dispatch sequence complete!", alert=False)
        except Exception:
            await event.answer("❌ File mapping dispatch pointer failure exception.", alert=True)

    # 🔄 PAGINATION INTERACTIVE CONTROLS (Next/Previous Page Engine)
    elif action.startswith(b'page_'):
        _, target_page_str = action.decode('utf-8').split('_')
        target_page = int(target_page_str)
        
        if user_id in PAGINATION_CACHE:
            cached = PAGINATION_CACHE[user_id]
            cached['current_page'] = target_page
            
            await RenderPaginationView(event, cached['query'], cached['matches'], target_page)
        else:
            await event.answer("⏳ Search session expired. Please type the movie title again.", alert=True)

    elif action.startswith(b'adm_app_') or action.startswith(b'adm_dec_'):
        parsed_action = action.decode('utf-8').split('_')
        resolution, target_uid, assigned_tier = parsed_action[1], parsed_action[2], parsed_action[3]
        allocated_quota = 30 if assigned_tier == "Silver" else 60
        
        if resolution == "app":
            await DatabaseManager.update_premium_plan(target_uid, assigned_tier, allocated_quota, 30)
            await DatabaseManager.update_payment_status(target_uid, assigned_tier, "Approved")
            try: await client.send_message(int(target_uid), f"✅ *PAYMENT VALIDATION APPROVED!*\nYour profile limits have been extended to **{assigned_tier} Pass**.")
            except Exception: pass
            await event.edit(f"🟢 **RESOLVED**: Upgraded User `{target_uid}` to `{assigned_tier}`.")
        else:
            await DatabaseManager.update_payment_status(target_uid, assigned_tier, "Declined")
            try: await client.send_message(int(target_uid), "🔴 *PAYMENT RECEIVED EXCEPTION AUDIT REJECTED*")
            except Exception: pass
            await event.edit(f"🔴 **DECLINED**: Receipt pipeline closed for User `{target_uid}`.")

    elif action == b'back_to_root':
        await send_advanced_dashboard(event.chat_id, user_id, event.message_id)

    elif action == b'verify_subscription':
        is_subscribed = await check_membership(event.sender_id)
        if is_subscribed:
            await event.answer("✅ Subscriptions Verified!", alert=True)
            await send_advanced_dashboard(event.chat_id, user_id, event.message_id)
        else:
            await event.answer("❌ Verification Failed. Please join the channels.", alert=True)

# ==========================================
#         📊 PAGINATION VIEW RENDERING ENGINE
# ==========================================
async def RenderPaginationView(event, query_text, matches, page=1):
    items_per_page = 10
    total_matches = len(matches)
    total_pages = (total_matches + items_per_page - 1) // items_per_page
    
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    
    start_index = (page - 1) * items_per_page
    end_index = start_index + items_per_page
    page_items = matches[start_index:end_index]
    
    catalog_response_text = (
        f"📂 *SEARCH INDEX CATALOG MATRIX*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 **Query Input**: `{query_text}`\n"
        f"📊 **Results Found**: `{total_matches} entries` | **Page**: `{page}/{total_pages}`\n"
        f"💡 *Instructions*: Click on any file block to forward data streams directly."
    )
    
    file_delivery_buttons = []
    for record in page_items:
        f_size = format_size(record['file_size'])
        label = f"🎬 {record['file_name']} [{f_size}]"
        file_delivery_buttons.append([Button.inline(label, f"get_file_{record['msg_id']}".encode('utf-8'))])
        
    nav_row = []
    if page > 1:
        nav_row.append(Button.inline("⬅️ Prev", f"page_{page-1}".encode('utf-8')))
    if page < total_pages:
        nav_row.append(Button.inline("Next ➡️", f"page_{page+1}".encode('utf-8')))
        
    if nav_row:
        file_delivery_buttons.append(nav_row)
        
    try:
        if isinstance(event, events.CallbackQuery.Event):
            await event.edit(catalog_response_text, buttons=file_delivery_buttons, parse_mode='markdown')
        else:
            await client.send_message(event.chat_id, catalog_response_text, buttons=file_delivery_buttons, parse_mode='markdown')
    except (telethon.errors.rpcerrorlist.MessageIdInvalidError, Exception):
        try:
            await client.send_message(event.chat_id, catalog_response_text, buttons=file_delivery_buttons, parse_mode='markdown')
        except Exception:
            pass

# ==========================================
#         🎯 CORE SEARCH ROUTER HANDLER
# ==========================================
@client.on(events.NewMessage)
async def core_search_router(event):
    if event.text.startswith('/'): return

    user_id = str(event.sender_id)
    user_data = await DatabaseManager.get_user(user_id)
    if user_data and user_data['banned'] == 1: return

    if event.message.photo:
        await client.send_message(
            ADMIN_ID, f"📥 *INBOUND FINANCIAL TRANSACTION CLAIM RECEIPT*\n\n👤 **User ID**: `{user_id}`",
            file=event.message.photo,
            buttons=[
                [Button.inline("🥈 Validate Silver Upgrade", f"adm_app_{user_id}_Silver"), Button.inline("🥇 Validate Gold Upgrade", f"adm_app_{user_id}_Gold")],
                [Button.inline("❌ Drop Transaction Claims", f"adm_dec_{user_id}_None")]
            ]
        )
        await event.reply("📨 *Receipt forwarded to processing hub pipelines.* System admins will verify your receipt picture shortly!")
        return

    is_subscribed = await check_membership(event.sender_id)
    if not is_subscribed:
        lockout_text = "⚠️ *SUBSCRIPTION REQUIRED*\nPlease join all our channels to access content:"
        verification_buttons = [[Button.url(f"📢 Join Channel", ch['link'])] for ch in REQUIRED_CHANNELS]
        verification_buttons.append([Button.inline("🔄 Synchronize Status", b"verify_subscription")])
        await event.reply(lockout_text, buttons=verification_buttons, parse_mode='markdown')
        return

    if user_data['searches_today'] >= user_data['max_limit']:
        exhausted_text = f"🚨 *DAILY LIMIT EXHAUSTED!*\nYour limits are currently saturated at (`{user_data['searches_today']}/{user_data['max_limit']}`)."
        await event.reply(exhausted_text, buttons=[[Button.inline("💎 Open Premium Account Upgrades", b"premium_menu")]], parse_mode='markdown')
        return

    user_query = event.text.strip()
    if len(user_query) < 2:
        await event.reply("⚠️ *Query context parameters too short.* Input explicit terms.")
        return

    progress_ticker = await event.respond("⚡ _Parsing database index records with fast indexing autocorrect..._")
    matches = await DatabaseManager.query_movie_catalog(user_query)

    if not matches:
        await progress_ticker.edit("❌ *No file matches found matching your metrics.* Try alternative title variations.")
        return

    PAGINATION_CACHE[user_id] = {
        "query": user_query,
        "matches": matches,
        "current_page": 1
    }

    await progress_ticker.delete()
    await RenderPaginationView(event, user_query, matches, page=1)

# ==========================================
#     👑 ADMIN TERMINAL COMMANDS LAYER
# ==========================================
@client.on(events.NewMessage(pattern='/adminGC'))
async def admin_central_terminal_cmd(event):
    if event.sender_id != ADMIN_ID: return
    raw_args = event.text.split(" ")
    u_count, m_count, b_count, p_count, prem_count = await DatabaseManager.get_system_stats()
    
    stats_panel = (
        f"👑 *CENTRAL TELEGRAM SYSTEM EXECUTIVE DESK* (`/adminGC`)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ **CORE DATABASE PROFILE METRICS**\n"
        f"👥 Total Logged Profiles       : `{u_count}` users\n"
        f"💎 Active Paid Subscribers     : `{prem_count}` accounts\n"
        f"📂 Indexed Storage Pointers     : `{m_count}` documents\n"
        f"🚫 Blocked Connections          : `{b_count}` profiles\n"
        f"⏳ Pending Invoices             : `{p_count}` claims\n\n"
        f"🛠️ **ADMIN ACTIONS INTERFACE CONSOLE**\n"
        f"🔹 `/adminGC ban <user_id>`\n"
        f"🔹 `/adminGC unban <user_id>`\n"
        f"🔹 `/adminGC addquota <user_id> <amount>`\n"
        f"🔹 `/adminGC reset`\n"
        f"🔹 `/adminGC broadcast <message>`"
    )
    
    if len(raw_args) == 1:
        await event.reply(stats_panel, parse_mode='markdown')
        return
        
    sub_command = raw_args[1].lower()
    
    if sub_command == "ban" and len(raw_args) > 2:
        target = raw_args[2]
        await DatabaseManager.set_user_ban_status(target, 1)
        await event.reply(f"🚫 User `{target}` banned.")
        
    elif sub_command == "unban" and len(raw_args) > 2:
        target = raw_args[2]
        await DatabaseManager.set_user_ban_status(target, 0)
        await event.reply(f"🟢 User `{target}` unbanned.")

    elif sub_command == "addquota" and len(raw_args) > 3:
        target = raw_args[2]
        amount = int(raw_args[3])
        current_data = await DatabaseManager.get_user(target)
        if current_data:
            new_lim = current_data['max_limit'] + amount
            await DatabaseManager.update_premium_plan(target, current_data['plan'], new_lim, 30)
            await event.reply(f"⚡ Added `+{amount}` quota limits to user `{target}`.")
            
    elif sub_command == "reset":
        await DatabaseManager.reset_all_daily_quotas()
        await event.reply("🔄 Global searches reset successfully.")
        
    elif sub_command == "broadcast" and len(raw_args) > 2:
        broadcast_msg = event.text.split("broadcast ", 1)[1]
        user_list = await DatabaseManager.get_all_user_ids()
        status_update = await event.reply(f"📢 Sending broadcast message to {len(user_list)} endpoints...")
        
        sent_success = 0
        for individual_id in user_list:
            try:
                await client.send_message(int(individual_id), f"📢 *IMPORTANT ANNOUNCEMENT*\n\n{broadcast_msg}")
                sent_success += 1
                await asyncio.sleep(0.05)
            except Exception: pass
        await status_update.edit(f"✅ Broadcast finished. Sent completely to `{sent_success}` users.")

@client.on(events.NewMessage)
async def admin_manual_forward_indexer(event):
    if event.sender_id != ADMIN_ID: return
    if event.message.fwd_from and event.message.file:
        channel_post_id = event.message.fwd_from.channel_post or event.message.fwd_from.saved_from_msg_id or event.message.id
        file_attr = event.message.file
        raw_name = file_attr.name or "Unnamed FileAsset"
        caption_context = event.message.message or ""
        bytes_measure = file_attr.size or 0
        
        await DatabaseManager.cache_movie(msg_id=channel_post_id, name=raw_name, caption=caption_context, size=bytes_measure)
        await event.reply(f"📥 Indexed successfully with Channel ID ({channel_post_id}): {raw_name}")

# ==========================================
#         🚀 SYSTEM RUN INITIALIZER ENTRY
# ==========================================
async def main_environment_bootstrap():
    await client.start(bot_token=BOT_TOKEN)
    await DatabaseManager.initialize()
    await DatabaseManager.check_and_dump_movies_terminal()
    logger.info("⚙️ System Bootstrap Initialization Stage Complete. Bot Service is operational.")

if __name__ == '__main__':
    print("---------------------------------------------------------")
    print("🚀 Running Advanced Architecture Split Channel Movie Bot System...")
    print("---------------------------------------------------------")
    
    # 🔧 FIX: Explicitly create and allocate a dedicated event loop instance for the startup thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main_environment_bootstrap())
        client.run_until_disconnected()
    except KeyboardInterrupt:
        print("🛑 System runtime disconnected manually.")
