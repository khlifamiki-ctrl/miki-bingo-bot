import os
import json
import hmac
import hashlib
import time
import asyncio
import sqlite3
import threading
import random
import requests  # ለ Chapa API ክፍያ ጥሪዎች የተጨመረ
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qsl
from datetime import datetime, timezone, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# TOKEN እና CHAPA_SECRET_KEY ከፕላትፎርሙ Environment Variables ይነበባሉ
TOKEN = os.environ.get("TOKEN")
CHAPA_SECRET_KEY = os.environ.get("CHAPA_SECRET_KEY", "CHASECK_TEST-xxxxxxxxxxxx") # የ Chapa ሴክሬት ቁልፍ
BOT_USERNAME = "miki_bingo_bot" # የቦቱ ተጠቃሚ ስም

if os.path.isdir("/data"):
    DB_FILE = "/data/bingo.db"
else:
    DB_FILE = "bingo.db"

DAILY_BONUS_AMOUNT = 100
DAILY_COOLDOWN_HOURS = 24
REFERRER_REWARD = 100
REFERRED_REWARD = 50

ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "6644747606").split(",")
    if x.strip().isdigit()
}

GAME_COSTS = {
    "dice": 20,
    "bingo": 10,
    "jackpot": 50,
}

DICE_PRIZE = 100
BINGO_PRIZE = 80
JACKPOT_PRIZE = 800

LIVE_ENTRY_FEE = 10        
LIVE_LOBBY_SECONDS = 60    
LIVE_DRAW_SECONDS = 5      
LIVE_MIN_PLAYERS = 2
LIVE_MAX_PLAYERS = 30
LIVE_CARD_POOL = 100       
LIVE_MAX_CARDS = 4         

SEND_MIN_AMOUNT = 10

COOLDOWNS = {
    "spin": 3600,      
    "dice": 1800,      
    "bingo": 600,      
    "jackpot": 1800,   
    "send": 60,        
}

# 1. የቢንጎ ጨዋታው (Mini App) የሚከፈትበት የእርስዎ ትክክለኛ የGitHub Pages ሊንክ
MINIAPP_ORIGIN = os.environ.get("MINIAPP_ORIGIN", "https://github.io")
INITDATA_MAX_AGE = 24 * 3600   

def verify_init_data(init_data):
    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = params.pop("hash", None)
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None
        if time.time() - int(params.get("auth_date", "0")) > INITDATA_MAX_AGE:
            return None
        user = json.loads(params["user"])
        if isinstance(user, dict) and "id" in user:
            return user
    except Exception:
        pass
    return None

def build_me_payload(user):
    user_id = int(user["id"])
    name = user.get("first_name") or "Player"
    return {
        "name": name,
        "balance": get_balance(user_id, name),
        "real_balance": get_real_balance(user_id), # የእውነተኛ ገንዘብ መጠን በድረገጽ ላይ ማሳያ
        "rank": get_rank(user_id),
        "top": [
            {"name": n or "Player", "balance": b} for n, b in get_top_users(10)
        ],
    }

def run_health_server():
    port = int(os.environ.get("PORT", 8080))

    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", MINIAPP_ORIGIN)
            self.send_header("Access-Control-Allow-Headers", "X-Telegram-Init-Data")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Vary", "Origin")

        def _json(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def _user(self):
            return verify_init_data(self.headers.get("X-Telegram-Init-Data", ""))

        def _run_live(self, make_coro):
            try:
                self._json(200, run_on_loop(make_coro))
            except RuntimeError:
                self._json(503, {"error": "starting"})
            except Exception as e:
                print(f"API error: {e}")
                self._json(500, {"error": "server"})

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/me":
                user = self._user()
                if not user:
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, build_me_payload(user))
                return
            if path == "/api/live":
                user = self._user()
                if not user:
                    self._json(401, {"error": "unauthorized"})
                    return
                self._run_live(lambda: api_live_state(user))
                return
            # Chapa Webhook ማረጋገጫ መስመር
            if path == "/webhook/chapa":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Webhook Active")
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Miki Bingo Bot is running")

        def do_POST(self):
            path = self.path.split("?")[0]
            
            # Chapa Webhook በኩል የተሳካ ክፍያን ተቀብሎ አካውንት ላይ መጨመሪያ ስልት
            if path == "/webhook/chapa":
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode()
                try:
                    data = json.loads(body)
                    if data.get('status') == 'success':
                        tx_ref = data.get('tx_ref')
                        if tx_ref and tx_ref.startswith("deposit_"):
                            # ከ tx_ref ላይ የተጠቃሚውን ID መለየት (ምሳሌ: deposit_1234567_timestamp)
                            parts = tx_ref.split("_")
                            user_id = int(parts[1])
                            amount = float(data.get('amount', 0))
                            update_real_balance(user_id, amount)
                            print(f"🎉 Webhook: Added {amount} Birr to User {user_id}")
                except Exception as e:
                    print(f"Webhook error: {e}")
                self.send_response(200)
                self.end_headers()
                return

            try:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
            except Exception:
                pass
            user = self._user()
            if not user:
                self._json(401, {"error": "unauthorized"})
                return
            if path == "/api/live/join":
                self._run_live(lambda: api_live_join(user))
            elif path in ("/api/live/pick", "/api/live/unpick"):
                query = dict(parse_qsl(self.path.partition("?")[2]))
                try:
                    card_no = int(query.get("card", ""))
                except ValueError:
                    self._json(400, {"error": "bad card"})
                    return
                fn = api_live_pick if path == "/api/live/pick" else api_live_unpick
                self._run_live(lambda: fn(user, card_no))
            elif path == "/api/live/claim":
                self._run_live(lambda: api_live_claim(user))
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


# ---------- DATABASE SETUP ----------

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            real_balance REAL DEFAULT 0.0, -- የእውነተኛ ገንዘብ ሂሳብ ማከማቻ በብር
            last_daily_bonus TEXT,
            referred_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cooldowns (
            user_id INTEGER,
            game TEXT,
            last_play TEXT,
            PRIMARY KEY (user_id, game)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS live_round_players (
            user_id INTEGER PRIMARY KEY,
            fee INTEGER
        )
    """)
    conn.commit()

    # የሪል ባላንስ አምድ በሂሳብ መዝገቡ ላይ መኖሩን በድጋሚ ማረጋገጥ
    try:
        cur.execute("ALTER TABLE users ADD COLUMN real_balance REAL DEFAULT 0.0")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    for column_def in ["last_daily_bonus TEXT", "referred_by INTEGER"]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {column_def}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  

    conn.close()

def user_exists(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def create_user(user_id, username, referred_by=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, username, balance, real_balance, referred_by) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, 0, 0.0, referred_by)
    )
    conn.commit()
    conn.close()

def get_balance(user_id, username):
    if not user_exists(user_id):
        create_user(user_id, username)
        return 0
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0]

def get_real_balance(user_id):
    conn = sqlite3.connect(DB_FILE)
Use code with caution.
cur = conn.cursor()
cur.execute("SELECT real_balance FROM users WHERE user_id = ?", (user_id,))
row = cur.fetchone()
conn.close()
return row[0] if row else 0.0
def update_balance(user_id, username, delta):
get_balance(user_id, username)
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
if username:
cur.execute(
"UPDATE users SET balance = balance + ?, username = ? WHERE user_id = ?",
(delta, username, user_id)
)
else:
cur.execute(
"UPDATE users SET balance = balance + ? WHERE user_id = ?",
(delta, user_id)
)
conn.commit()
cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
new_balance = cur.fetchone()[0]
conn.close()
return new_balance
def update_real_balance(user_id, delta):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"UPDATE users SET real_balance = real_balance + ? WHERE user_id = ?",
(delta, user_id)
)
conn.commit()
cur.execute("SELECT real_balance FROM users WHERE user_id = ?", (user_id,))
new_balance = cur.fetchone()[0]
conn.close()
return new_balance
def get_last_daily_bonus(user_id, username):
get_balance(user_id, username)
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("SELECT last_daily_bonus FROM users WHERE user_id = ?", (user_id,))
row = cur.fetchone()
conn.close()
return row[0] if row else None
def set_last_daily_bonus(user_id, timestamp_str):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"UPDATE users SET last_daily_bonus = ? WHERE user_id = ?",
(timestamp_str, user_id)
)
conn.commit()
conn.close()
def get_top_users(limit=10):

conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"SELECT username, balance FROM users ORDER BY balance DESC, created_at ASC LIMIT ?",
(limit,)
)
rows = cur.fetchall()
conn.close()
return rows
def transfer_coins(from_id, to_id, amount):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
(amount, from_id, amount)
)
if cur.rowcount == 0:
conn.close()
return False
cur.execute(
"UPDATE users SET balance = balance + ? WHERE user_id = ?",
(amount, to_id)
)
conn.commit()
conn.close()
return True
def join_live_round(user_id, fee):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
(fee, user_id, fee)
)
if cur.rowcount == 0:
conn.close()
return False
cur.execute(
"INSERT OR REPLACE INTO live_round_players (user_id, fee) VALUES (?, ?)",
(user_id, fee)
)
conn.commit()
conn.close()
return True
def buy_live_card(user_id, fee):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
(fee, user_id, fee)
)
if cur.rowcount == 0:
conn.close()
return False
cur.execute(
"INSERT INTO live_round_players (user_id, fee) VALUES (?, ?) "
"ON CONFLICT(user_id) DO UPDATE SET fee = fee + excluded.fee",
(user_id, fee)
)
conn.commit()
conn.close()
return True
def refund_live_card(user_id, fee):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (fee, user_id))
cur.execute("UPDATE live_round_players SET fee = fee - ? WHERE user_id = ?", (fee, user_id))
cur.execute("DELETE FROM live_round_players WHERE fee <= 0")
conn.commit()
conn.close()
def settle_live_round(winner_id, pot):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (pot, winner_id))
cur.execute("DELETE FROM live_round_players")
conn.commit()
conn.close()
def refund_live_round():
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("""
UPDATE users SET balance = balance +
(SELECT fee FROM live_round_players WHERE live_round_players.user_id = users.user_id)
WHERE user_id IN (SELECT user_id FROM live_round_players)
""")
count = cur.rowcount
cur.execute("DELETE FROM live_round_players")
conn.commit()
conn.close()
return count
def get_stats():
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("SELECT COUNT(), COALESCE(SUM(balance), 0) FROM users")
total_users, total_coins = cur.fetchone()
cur.execute(
"SELECT COUNT() FROM users WHERE created_at >= datetime('now', '-1 day')"
)
new_today = cur.fetchone()[0]
conn.close()
return total_users, total_coins, new_today
def get_user_info(user_id):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"SELECT username, balance, referred_by, created_at FROM users WHERE user_id = ?",
(user_id,)
)
row = cur.fetchone()
if row:
cur.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
referrals = cur.fetchone()[0]
else:
referrals = 0
conn.close()
return row, referrals
def get_rank(user_id):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"SELECT COUNT(*) + 1 FROM users WHERE balance > (SELECT balance FROM users WHERE user_id = ?)",
(user_id,)
)
rank = cur.fetchone()[0]
conn.close()
return rank

---------- COOLDOWN HELPERS ----------

def get_cooldown_remaining(user_id, game):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"SELECT last_play FROM cooldowns WHERE user_id = ? AND game = ?",
(user_id, game)
)
row = cur.fetchone()
conn.close()
if not row:
return 0
last_play = datetime.fromisoformat(row[0])
elapsed = (datetime.now(timezone.utc) - last_play).total_seconds()

remaining = COOLDOWNS[game] - elapsed
return max(0, int(remaining))
def set_cooldown(user_id, game):
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute(
"INSERT OR REPLACE INTO cooldowns (user_id, game, last_play) VALUES (?, ?, ?)",
(user_id, game, datetime.now(timezone.utc).isoformat())
)
conn.commit()
conn.close()
def format_wait(seconds):
hours = seconds // 3600
minutes = (seconds % 3600) // 60
secs = seconds % 60
if hours > 0:
return f"{hours}h {minutes}m"
if minutes > 0:
return f"{minutes}m {secs}s"
return f"{secs}s"
async def blocked_by_cooldown(update, user_id, name, game):
remaining = get_cooldown_remaining(user_id, game)
if remaining > 0:
await update.message.reply_text(
f"⏳ {name}፣ እባክዎ /{game} ከመጫወትዎ በፊት {format_wait(remaining)} ይጠብቁ።"
)
return True
return False
async def not_enough_coins(update, user_id, name, game):
cost = GAME_COSTS[game]
bal = get_balance(user_id, name)
if bal < cost:
await update.message.reply_text(
f"💸 {name}፣ ለመጫወት {cost} ሳንቲም ያስፈልጋል፤ የእርስዎ ቀሪ ሂሳብ {bal} ሳንቲም ነው።\n"
"🎡 ነፃ ሳንቲም ለማግኘት /spin ወይም 🎁 /daily ይጠቀሙ!"
)
return True
return False

---------- KEYBOARD (የገንዘብ ማስገቢያና ማውጫ ቁልፎች በቁልፍ ሰሌዳው ላይ ተካተዋል) ----------

keyboard = [
["/bingo", "/spin", "/dice"],
["/jackpot", "/balance", "/top"],
["/daily", "/invite", "/live"],
["/deposit", "/withdraw", "/id"]
]
reply_markup = ReplyKeyboardMarkup(
keyboard,
resize_keyboard=True
)

---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
is_first_time = not user_exists(user_id)
extra_note = ""
if is_first_time:
referrer_id = None
if context.args and context.args[0].isdigit():
referrer_id = int(context.args[0])
if referrer_id == user_id:
extra_note = "🚫 የራስዎን መጋበዣ ሊንክ መጠቀም አይችሉም።\n\n"
create_user(user_id, name)
elif referrer_id is not None and user_exists(referrer_id):
create_user(user_id, name, referred_by=referrer_id)
update_balance(referrer_id, None, REFERRER_REWARD)
update_balance(user_id, name, REFERRED_REWARD)
extra_note = f"🎉 በጋበዥ ሊንክ ስለገቡ 🎉 +{REFERRED_REWARD} የሳንቲም ስጦታ አግኝተዋል!\n\n"
else:
create_user(user_id, name)
text = (
f"{extra_note}"
"🎉 እንኳን ወደ ሚኪ ቢንጎ (MIKI BINGO) በደህና መጡ! 🎉\n\n"
"🎮 ጨዋታዎችን በመጫወት ያሸንፉ! እውነተኛ ገንዘብ ያስገቡና ያውጡ!\n\n"
"🎡 /spin - ነፃ ዕድል መሞከሪያ\n"
f"🎲 /dice - ዳይስ መጫወቻ ({GAME_COSTS['dice']} ሳንቲም)\n"
f"🎯 /bingo - ቢንጎ መጫወቻ ({GAME_COSTS['bingo']} ሳንቲም)\n"
f"💎 /jackpot - ጃክፖት መጫወቻ ({GAME_COSTS['jackpot']} ሳንቲም)\n"
"💰 /balance - ቀሪ ሂሳብ ማያ\n"
"🎁 /daily - የዕለታዊ ስጦታ ማግኛ\n"
"🏆 /top - ከፍተኛ ተጫዋቾች ሰሌዳ\n"
f"🎱 /live - የቀጥታ ቢንጎ ዙር ({LIVE_ENTRY_FEE} ሳንቲም)\n"
"💵 /deposit - እውነተኛ ብር ማስገቢያ (Chapa)\n"
"🏧 /withdraw - ያሸነፉትን እውነተኛ ብር ማውጫ\n"
"💌 /send - ለወዳጅዎ ሳንቲም መላኪያ\n"
"🆔 /id - የእርስዎን መለያ ቁጥር ማያ\n"
"👥 /invite - ጓደኛ መጋበዣ ሊንክ\n\n"
"👇 ከታች ካሉት ቁልፎች አንዱን ይምረጡ 👇"
)
await update.message.reply_text(text, reply_markup=reply_markup)

---------- CHAPA DEPOSIT & WITHDRAWAL SYSTEMS ----------

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
if len(context.args) != 1 or not context.args[0].isdigit():
await update.message.reply_text("💡 አጠቃቀም፦ /deposit <የብር_መጠን>\nምሳሌ፦ /deposit 100 በመጻፍ 100 ብር ማስገባት ይችላሉ።")
return
amount = int(context.args[0])
if amount < 10:
await update.message.reply_text("🚫 ትንሹ ማስገባት የሚቻለው መጠን 10 ብር ነው።")
return
# Chapa API በመጠቀም የክፍያ ሊንክ ማመንጨት
chapa_url = "chapa.co"
headers = {
"Authorization": f"Bearer {CHAPA_SECRET_KEY}",
"Content-Type": "application/json"
}
# 2. የባክኤንድ የባንክ ስራዎችን የሚያስተናግደው የእርስዎ ትክክለኛ የRailway አድራሻ ዌብሁክ ሊንክ
railway_backend_url = "railway.app"
tx_ref = f"deposit_{user_id}{int(time.time())}"
payload = {
"amount": str(amount),
"currency": "ETB",
"email": f"user{user_id}@mikibingo.com",
"first_name": name,

"last_name": "Player",
"tx_ref": tx_ref,
"callback_url": railway_backend_url,
"customization": {
"title": "Miki Bingo Deposit",
"description": f"Deposit to Miki Bingo account for user {user_id}"
}
}
try:
response = requests.post(chapa_url, json=payload, headers=headers)
res_data = response.json()
if res_data.get("status") == "success":
checkout_url = res_data["data"]["checkout_url"]
inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 በቴሌብር/ባንክ ይክፈሉ", url=checkout_url)]])
await update.message.reply_text(
f"💵 የ {amount} ብር ማስገቢያ ጥያቄዎ ተዘጋጅቷል።\nከታች ያለውን ቁልፍ ተጭነው በቴሌብር፣ በባንክ ወይም በፈለጉት አማራጭ መክፈል ይችላሉ።",
reply_markup=inline_kb
)
else:
await update.message.reply_text("❌ የክፍያ ሊንኩን ማመንጨት አልተቻለም። እባክዎ ቆይተው ይሞክሩ።")
except Exception as e:
print(f"Chapa Error: {e}")
await update.message.reply_text("⚠️ በክፍያ ሲስተሙ ላይ ስህተት አጋጥሟል። እባክዎ የአስተዳዳሪውን እገዛ ይጠይቁ።")
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
if len(context.args) != 2 or not context.args[1].isdigit():
await update.message.reply_text("💡 አጠቃቀም፦ /withdraw <የባንክ_ስም> <የብር_መጠን>\nምሳሌ፦ /withdraw CBE 500 (አስተዳዳሪው አይቶ ያረጋግጥልዎታል)")
return
bank_name = context.args[0]
amount = int(context.args[1])
current_real_bal = get_real_balance(user_id)
if amount > current_real_bal:
await update.message.reply_text(f"💸 በቂ የብር ሂሳብ የለዎትም። የእርስዎ የሪል ሂሳብ መጠን {current_real_bal} ብር ነው።")
return
if amount < 50:
await update.message.reply_text("🚫 ትንሹ ማውጣት የሚቻለው የገንዘብ መጠን 50 ብር ነው።")
return
# ከተጫዋቹ አካውንት ላይ መቀነስ
update_real_balance(user_id, -amount)
# ለአስተዳዳሪዎች የማሳወቂያ መልዕክት መላክ
admin_msg = f"🔔 የገንዘብ ማውጫ ጥያቄ ደርሷል 🔔\n\n👤 ተጠቃሚ ID: {user_id}\n🏦 ባንክ: {bank_name}\n💰 መጠን: {amount} ብር\n\nእባክዎ ክፍያውን እውን ካደረጉ በኋላ ለተጠቃሚው ያሳውቁ።"
for admin_id in ADMIN_IDS:
try:
await context.bot.send_message(chat_id=admin_id, text=admin_msg)
except Exception:
pass
await update.message.reply_text(f"✅ የ {amount} ብር ማውጫ ጥያቄዎ በተሳካ ሁኔታ ለአስተዳዳሪዎች ተልኳል። ባንክዎ ተረጋግጦ በአጭር ጊዜ ውስጥ ገቢ ይደረጋል።")

---------- LEADERBOARD & OTHERS ----------

async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
get_balance(user_id, name)
link = f"t.me{BOT_USERNAME}?start={user_id}"
text = (
"👥 ጓደኛ ይጋብዙ እና ያትርፉ!\n\n"
f"🔗 የእርስዎ መጋበዣ ሊንክ፦\n{link}\n\n"
f"🎁 እርስዎ የሚያገኙት፦ {REFERRER_REWARD} ሳንቲም\n"
f"🎉 የተጋበዘው ወዳጅዎ የሚያገኘው፦ {REFERRED_REWARD} ሳንቲም"
)
await update.message.reply_text(text)
async def bingo(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
get_balance(user_id, name)
if await blocked_by_cooldown(update, user_id, name, "bingo"):
return
if await not_enough_coins(update, user_id, name, "bingo"):
return
set_cooldown(user_id, "bingo")
update_balance(user_id, name, -GAME_COSTS["bingo"])
number = random.randint(1, 100)
if number > 90:
new_balance = update_balance(user_id, name, BINGO_PRIZE)
msg = (
f"🏆 {name} የቢንጎ ቁጥር {number} አግኝተዋል!\n"
f"🎁 የ {BINGO_PRIZE} ሳንቲም አሸንፈዋል! (የተጣራ ትርፍ +{BINGO_PRIZE - GAME_COSTS['bingo']})\n"
f"💰 ቀሪ ሳንቲም: {new_balance}"
)
else:
new_balance = get_balance(user_id, name)
msg = (
f"🎯 {name} ያገኙት ቁጥር {number} ነው\n"
f"❌ አልደረሰዎትም (-{GAME_COSTS['bingo']} ሳንቲም)\n"
f"💰 ቀሪ ሳንቲም: {new_balance}"
)
await update.message.reply_text(msg)
async def spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
get_balance(user_id, name)
if await blocked_by_cooldown(update, user_id, name, "spin"):
return
set_cooldown(user_id, "spin")
reward = random.choice([10, 20, 50, 100])
new_balance = update_balance(user_id, name, reward)
await update.message.reply_text(
f"🎡 {name} የ {reward} ሳንቲም ስጦታ አግኝተዋል!\n💰 ቀሪ ሳንቲም: {new_balance}"
)
async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):

user_id = update.effective_user.id
name = update.effective_user.first_name
get_balance(user_id, name)
if await blocked_by_cooldown(update, user_id, name, "dice"):
return
if await not_enough_coins(update, user_id, name, "dice"):
return
set_cooldown(user_id, "dice")
update_balance(user_id, name, -GAME_COSTS["dice"])
roll = random.randint(1, 6)
if roll == 6:
new_balance = update_balance(user_id, name, DICE_PRIZE)
msg = (
f"🎲 ዳይሱ የደረሰው ቁጥር: {roll}\n"
f"🏆 የ {DICE_PRIZE} ሳንቲም አሸንፈዋል! (የተጣራ ትርፍ +{DICE_PRIZE - GAME_COSTS['dice']})\n"
f"💰 ቀሪ ሳንቲም: {new_balance}"
)
else:
new_balance = get_balance(user_id, name)
msg = (
f"🎲 ዳይሱ የደረሰው ቁጥር: {roll}\n"
f"❌ ድጋሚ ይሞክሩ! (-{GAME_COSTS['dice']} ሳንቲም)\n"
f"💰 ቀሪ ሳንቲም: {new_balance}"
)
await update.message.reply_text(msg)
async def jackpot(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
get_balance(user_id, name)
if await blocked_by_cooldown(update, user_id, name, "jackpot"):
return
if await not_enough_coins(update, user_id, name, "jackpot"):
return
set_cooldown(user_id, "jackpot")
update_balance(user_id, name, -GAME_COSTS["jackpot"])
lucky = random.randint(1, 20)
if lucky == 7:
new_balance = update_balance(user_id, name, JACKPOT_PRIZE)
await update.message.reply_text(
f"💎 ጃክፖት እጣ ደረሰዎት!\n🏆 {name} የ {JACKPOT_PRIZE} ሳንቲም አሸንፈዋል! (የተጣራ ትርፍ +{JACKPOT_PRIZE - GAME_COSTS['jackpot']})\n💰 ቀሪ ሳንቲም: {new_balance}"
)
else:
new_balance = get_balance(user_id, name)
await update.message.reply_text(
f"❌ ለጥቂት አልደረሰዎትም! (-{GAME_COSTS['jackpot']} ሳንቲም)\n"
f"🎰 ድጋሚ ይሞክሩ!\n💰 ቀሪ ሳንቲም: {new_balance}"
)
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
bal = get_balance(user_id, name)
real_bal = get_real_balance(user_id)
await update.message.reply_text(
f"💰 የሂሳብ መግለጫ 💰\n\n"
f"🪙 የጨዋታ ሳንቲም፦ {bal} ሳንቲም\n"
f"💵 እውነተኛ የብር ሂሳብ፦ {real_bal:.2f} ETB"
)

---------- LIVE BINGO ROUND ----------

LIVE = {"round": None}
LIVE_LAST = {"result": None}
APP_STATE = {"loop": None, "bot": None}
BG_TASKS = set()
def spawn(coro):
task = asyncio.create_task(coro)
BG_TASKS.add(task)
task.add_done_callback(BG_TASKS.discard)
return task
CLAIM_KB = InlineKeyboardMarkup(
[[InlineKeyboardButton("🎯 BINGO!", callback_data="bingo_claim")]]
)
def make_card():
cols = [random.sample(range(1 + 15 * c, 16 + 15 * c), 5) for c in range(5)]
card = [[cols[c][r] for c in range(5)] for r in range(5)]
card[2][2] = 0
return card
def card_has_bingo(card, drawn_set):
def ok(v):
return v == 0 or v in drawn_set
for i in range(5):
if all(ok(card[i][j]) for j in range(5)):
return True
if all(ok(card[j][i]) for j in range(5)):
return True
if all(ok(card[i][i]) for i in range(5)):
return True
if all(ok(card[i][4 - i]) for i in range(5)):
return True
return False
def new_round():
return {
"state": "lobby", "players": {}, "drawn": [], "created": time.time(),
"pool": {n: make_card() for n in range(1, LIVE_CARD_POOL + 1)},
"owner": {},
}
def round_pot(rnd):
return LIVE_ENTRY_FEE * len(rnd["owner"])
def player_bingo(player, drawn_set):
return any(card_has_bingo(c, drawn_set) for c in player["cards"].values())
def ball(n):
return f"{'BINGO'[(n - 1) // 15]}-{n}"
def render_card(card, drawn_set):
lines = [" ".join(f"{letter:^4}" for letter in "BINGO")]
for row in card:
cells = []
for v in row:
if v == 0:
cells.append("[**]")
elif v in drawn_set:
cells.append(f"[{v:2}]")
else:
cells.append(f" {v:2} ")
lines.append(" ".join(cells))
return "\n".join(lines)
def render_board(rnd, player):
drawn = rnd["drawn"]
pot = round_pot(rnd)
text = (
"🎱 የቀጥታ ቢንጎ (LIVE BINGO)\n"
f"💰 ጠቅላላ ሽልማት (Pot): {pot} ሳንቲም · 👥 {len(rnd['players'])} ተጫዋቾች\n"
)
if drawn:
text += f"🔢 የወጣው ቁጥር: {ball(drawn[-1])} ({len(drawn)}/75)\n"

before = ", ".join(ball(x) for x in drawn[-6:-1][::-1])
if before:
text += f"ከዚህ በፊት የወጡ: {before}\n"
else:
left = max(0, int(LIVE_LOBBY_SECONDS - (time.time() - rnd["created"])))
text += f"⏳ ጨዋታው ለመጀመር የቀረው ጊዜ ~{left}ሰከንድ (ቢያንስ {LIVE_MIN_PLAYERS}+ ተጫዋች ያስፈልጋል)\n"
nos = sorted(player["cards"])
text += f"\n🎟 ካርድ ቁጥር #{nos[0]}\n" + render_card(player["cards"][nos[0]], set(drawn)) + "\n"
if len(nos) > 1:
text += f"➕ +{len(nos) - 1} ተጨማሪ ካርዶች በ Mini App ላይ ማየት ይችላሉ\n"
text += "ሙሉ ረድፍ፣ አምድ ወይም ሰያፍ ከሞላ ፈጥነው BINGO ይጫኑ! 👇"
return text
async def refresh_boards(bot, rnd):
async def one(player):
if not player.get("message_id"):
return
try:
await bot.edit_message_text(
chat_id=player["chat_id"],
message_id=player["message_id"],
text=render_board(rnd, player),
parse_mode="HTML",
reply_markup=CLAIM_KB,
)
except Exception:
pass
await asyncio.gather(*(one(p) for p in list(rnd["players"].values())))
async def broadcast(bot, rnd, text):
async def one(player):
try:
await bot.send_message(chat_id=player["chat_id"], text=text)
except Exception:
pass
await asyncio.gather(*(one(p) for p in list(rnd["players"].values())))
def claim_win(rnd, winner_id):
if rnd["state"] != "running":
return 0
rnd["state"] = "finished"
pot = round_pot(rnd)
settle_live_round(winner_id, pot)
if LIVE["round"] is rnd:
LIVE["round"] = None
LIVE_LAST["result"] = {
"kind": "win", "winner": rnd["players"][winner_id]["name"],
"pot": pot, "at": time.time(),
}
return pot
async def end_round_refund(bot, rnd, reason):
if rnd["state"] == "finished":
return
rnd["state"] = "finished"
refund_live_round()
if LIVE["round"] is rnd:
LIVE["round"] = None
code = "few" if "Not enough" in reason else ("nowin" if "All 75" in reason else "error")
LIVE_LAST["result"] = {"kind": "refund", "code": code, "at": time.time()}
await broadcast(bot, rnd, f"{reason}\n💰 የገቡበት ሳንቲም በሙሉ ወደ ሂሳብዎ ተመልሷል።")
async def run_round(bot, rnd):
try:
await asyncio.sleep(LIVE_LOBBY_SECONDS)
if len(rnd["players"]) < LIVE_MIN_PLAYERS:
await end_round_refund(bot, rnd, "😕 በቂ ተጫዋች ስላልገባ ይህ ዙር ተሰርዟል።")
return
rnd["state"] = "running"
numbers = list(range(1, 76))
random.shuffle(numbers)
for n in numbers:
if rnd["state"] != "running":
return
rnd["drawn"].append(n)
await refresh_boards(bot, rnd)
await asyncio.sleep(LIVE_DRAW_SECONDS)
if rnd["state"] == "running":
await end_round_refund(bot, rnd, "🎱 ሁሉም 75 ቁጥሮች ወጥተው ማንም ያሸነፈ የለም።")
except Exception as e:
print(f"Live round error: {e}")
await end_round_refund(bot, rnd, "⚠️ ቴክኒካዊ ችግር ስላጋጠመ ጨዋታው ተሰርዟል።")
def try_pick(user_id, name, chat_id, bot, card_no):
rnd = LIVE["round"]
if rnd and rnd["state"] == "running":
return "running", rnd
if not (1 <= card_no <= LIVE_CARD_POOL):
return "invalid", rnd
player = None
if rnd:
if card_no in rnd["owner"]:
return "taken", rnd
player = rnd["players"].get(user_id)
if player and len(player["cards"]) >= LIVE_MAX_CARDS:
return "limit", rnd
if not player and len(rnd["players"]) >= LIVE_MAX_PLAYERS:
return "full", rnd
if not buy_live_card(user_id, LIVE_ENTRY_FEE):
return "no_coins", rnd
if rnd is None:
rnd = new_round()
LIVE["round"] = rnd
rnd["task"] = asyncio.create_task(run_round(bot, rnd))
player = rnd["players"].get(user_id)
if not player:
player = {
"name": name, "chat_id": chat_id, "message_id": None,
"cards": {}, "locked_at": -1,
}
rnd["players"][user_id] = player
player["cards"][card_no] = rnd["pool"][card_no]
rnd["owner"][card_no] = user_id
return "picked", rnd
def try_unpick(user_id, card_no):
rnd = LIVE["round"]
if not rnd:
return "no_round"
if rnd["state"] != "lobby":
return "running"
player = rnd["players"].get(user_id)
if not player or card_no not in player["cards"]:
return "not_yours"
refund_live_card(user_id, LIVE_ENTRY_FEE)
del player["cards"][card_no]
del rnd["owner"][card_no]
if not player["cards"]:
del rnd["players"][user_id]
if not rnd["players"]:

rnd["state"] = "finished"
LIVE["round"] = None
task = rnd.get("task")
if task:
task.cancel()
return "removed"
def try_join(user_id, name, chat_id, bot):
rnd = LIVE["round"]
if rnd and rnd["state"] == "running":
return "running", rnd, None
if rnd and user_id in rnd["players"]:
return "already", rnd, rnd["players"][user_id]
free = [n for n in range(1, LIVE_CARD_POOL + 1) if not rnd or n not in rnd["owner"]]
if not free:
return "full", rnd, None
status, rnd = try_pick(user_id, name, chat_id, bot, random.choice(free))
if status == "picked":
return "joined", rnd, rnd["players"][user_id]
return status, rnd, None
async def announce_win(bot, rnd, winner_name, pot):
await broadcast(
bot, rnd,
f"🏆 ቢንጎ! {winner_name} የ {pot} ሳንቲም ሙሉ ድስት በላ!\n"
"ለመጫወት ድጋሚ /live ይላኩ!"
)
async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
chat_id = update.effective_chat.id
get_balance(user_id, name)
status, rnd, player = try_join(user_id, name, chat_id, context.bot)
if status == "running":
await update.message.reply_text(
f"🎱 ጨዋታ እየተካሄደ ነው ({len(rnd['drawn'])}/75 ቁጥሮች ወጥተዋል)።\nቀጣዩን ዙር ለመቀላቀል ከጥቂት ደቂቃዎች በኋላ ድጋሚ /live ይላኩ።"
)
elif status == "already":
await update.message.reply_text("✅ በዚህ ዙር ውስጥ አስቀድመው ገብተዋል!")
elif status == "full":
await update.message.reply_text("🚫 ይህ ዙር ሞልቷል። እባክዎ ቀጣዩን ይሞክሩ!")
elif status == "no_coins":
bal = get_balance(user_id, name)
await update.message.reply_text(
f"💸 ለመግባት {LIVE_ENTRY_FEE} ሳንቲም ያስፈልጋል፤ የእርስዎ ቀሪ ሂሳብ {bal} ሳንቲም ነው።\n🎡 በ /spin ወይም 🎁 /daily ነፃ ሳንቲም ያግኙ!"
)
elif status == "joined":
msg = await update.message.reply_text(
render_board(rnd, player), parse_mode="HTML", reply_markup=CLAIM_KB
)
player["message_id"] = msg.message_id
else:
await update.message.reply_text("🚫 መግባት አልተቻለም። ድጋሚ ይሞክሩ!")
async def live_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
user_id = query.from_user.id
rnd = LIVE["round"]
if not rnd or user_id not in rnd["players"]:
await query.answer("ይህ ዙር አልቋል። ድጋሚ ለመጫወት /live ይላኩ!", show_alert=True)
return
if rnd["state"] != "running":
await query.answer("ጨዋታው ገና አልተጀመረም።", show_alert=False)
return
player = rnd["players"][user_id]
drawn = rnd["drawn"]
if player["locked_at"] == len(drawn):
await query.answer("ቀጣዩ ቁጥር እስኪወጣ ድረስ በትንሹ ይጠብቁ።")
return
if player_bingo(player, set(drawn)):
pot = claim_win(rnd, user_id)
if pot:
await query.answer(f"🎉 ቢንጎ! የ {pot} ሳንቲም አሸንፈዋል!", show_alert=True)
await announce_win(context.bot, rnd, player["name"], pot)
else:
await query.answer("ቀድመውዎት የነኩ ተጫዋቾች አሉ!", show_alert=True)
else:
player["locked_at"] = len(drawn)
await query.answer("❌ ካርድዎ ገና አልሞላም! መከታተልዎን ይቀጥሉ ሰሌዳውን ይከታተሉ።", show_alert=True)

---------- LIVE BINGO FOR THE MINI APP (web API) ----------

def live_state_for(user_id):
rnd = LIVE["round"]
out = {
"entry_fee": LIVE_ENTRY_FEE,
"min_players": LIVE_MIN_PLAYERS,
"max_players": LIVE_MAX_PLAYERS,
"pool_size": LIVE_CARD_POOL,
"max_cards": LIVE_MAX_CARDS,
"state": "none",
"last": None,
"taken": [],
"mine": [],
}
last = LIVE_LAST["result"]
if last and time.time() - last["at"] < 120:
out["last"] = dict(last, ago=int(time.time() - last["at"]))
if not rnd:
return out
out["state"] = rnd["state"]
out["players"] = len(rnd["players"])
out["cards_total"] = len(rnd["owner"])
out["pot"] = round_pot(rnd)
out["drawn"] = list(rnd["drawn"])
if rnd["state"] == "lobby":
out["starts_in"] = max(0, int(LIVE_LOBBY_SECONDS - (time.time() - rnd["created"])))
out["taken"] = sorted(n for n, u in rnd["owner"].items() if u != user_id)
player = rnd["players"].get(user_id)
if player:
drawn_set = set(rnd["drawn"])
cards = [
{"no": n, "card": c, "has_bingo": card_has_bingo(c, drawn_set)}
for n, c in sorted(player["cards"].items())
]
out["mine"] = [c["no"] for c in cards]

out["you"] = {"cards": cards, "has_bingo": any(c["has_bingo"] for c in cards)}
return out
async def api_live_state(user):
return live_state_for(int(user["id"]))
async def api_live_join(user):
user_id = int(user["id"])
name = user.get("first_name") or "Player"
get_balance(user_id, name)
status, rnd, player = try_join(user_id, name, user_id, APP_STATE["bot"])
out = live_state_for(user_id)
out["status"] = status
out["balance"] = get_balance(user_id, name)
return out
async def api_live_pick(user, card_no):
user_id = int(user["id"])
name = user.get("first_name") or "Player"
get_balance(user_id, name)
status, rnd = try_pick(user_id, name, user_id, APP_STATE["bot"], card_no)
out = live_state_for(user_id)
out["status"] = status
out["balance"] = get_balance(user_id, name)
return out
async def api_live_unpick(user, card_no):
user_id = int(user["id"])
status = try_unpick(user_id, card_no)
out = live_state_for(user_id)
out["status"] = status
out["balance"] = get_balance(user_id, user.get("first_name") or "Player")
return out
async def api_live_claim(user):
user_id = int(user["id"])
rnd = LIVE["round"]
if not rnd:
last = LIVE_LAST["result"]
if last and last["kind"] == "win" and time.time() - last["at"] < 30:
return {"result": "too_late"}
return {"result": "not_in_round"}
if user_id not in rnd["players"]:
return {"result": "not_in_round"}
if rnd["state"] != "running":
return {"result": "not_started"}
player = rnd["players"][user_id]
drawn = rnd["drawn"]
if player["locked_at"] == len(drawn):
return {"result": "wait"}
if not player_bingo(player, set(drawn)):
player["locked_at"] = len(drawn)
return {"result": "not_bingo"}
pot = claim_win(rnd, user_id)
if not pot:
return {"result": "too_late"}
spawn(announce_win(APP_STATE["bot"], rnd, player["name"], pot))
return {"result": "win", "pot": pot, "balance": get_balance(user_id, player["name"])}
def run_on_loop(make_coro, timeout=10):
loop = APP_STATE["loop"]
if loop is None:
raise RuntimeError("bot is still starting")
return asyncio.run_coroutine_threadsafe(make_coro(), loop).result(timeout=timeout)
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
get_balance(user_id, name)
await update.message.reply_text(
f"🆔 የእርስዎ ID ቁጥር፦ {user_id}\n\n"
"ይህንን ቁጥር ለጓደኛዎ በመላክ ሳንቲም እንዲያስተላልፉልዎት ማድረግ ይችላሉ፦\n"
f"/send {user_id} <የሳንቲም_ብዛት>"
)
async def send(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
get_balance(user_id, name)
parsed = parse_target_and_amount(context)
if not parsed:
await update.message.reply_text(
"💡 አጠቃቀም፦ /send <ተቀባይ_ID> <የሳንቲም_ብዛት>\n"
"ተቀባዩ የእሱን ID ለማወቅ /id መላክ አለበት።"
)
return
target_id, amount = parsed
if target_id == user_id:
await update.message.reply_text("🚫 ለራስዎ ማስተላለፍ አይችሉም።")
return
if amount < SEND_MIN_AMOUNT:
await update.message.reply_text(f"🚫 ትንሹ መጠን {SEND_MIN_AMOUNT} ሳንቲም ነው።")
return
if not user_exists(target_id):
await update.message.reply_text("❌ ተጠቃሚው አልተገኘም። ተቀባዩ መጀመሪያ ቦቱን መክፈት (/start) አለበት።")
return
if await blocked_by_cooldown(update, user_id, name, "send"):
return
if not transfer_coins(user_id, target_id, amount):
bal = get_balance(user_id, name)
await update.message.reply_text(
f"💸 በቂ ሳንቲም የለዎትም። የእርስዎ ቀሪ ሂሳብ {bal} ሳንቲም ነው።"
)
return
set_cooldown(user_id, "send")
my_balance = get_balance(user_id, name)
await update.message.reply_text(
f"✅ የ {amount} ሳንቲም ወደ {target_id} በተሳካ ሁኔታ ተላልፏል።\n💰 ቀሪ ሳንቲም: {my_balance}"
)
try:
await context.bot.send_message(
chat_id=target_id,
text=f"🎁 {name} የ {amount} ሳንቲም በስጦታ አበርክቶልዎታል!\n💰 ቀሪ ሳንቲም: {get_balance(target_id, None)}"
)
except Exception:
pass
async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name

my_balance = get_balance(user_id, name)
rows = get_top_users(10)
medals = ["🥇", "🥈", "🥉"]
lines = ["🏆 ከፍተኛ ውጤት ያስመዘገቡ 10 ተጫዋቾች\n"]
for i, (username, bal) in enumerate(rows):
prefix = medals[i] if i < 3 else f"{i + 1}."
lines.append(f"{prefix} {username or 'Player'} - {bal} ሳንቲም")
lines.append(f"\n📍 የእርስዎ ደረጃ፦ #{get_rank(user_id)} ({my_balance} ሳንቲም)")
await update.message.reply_text("\n".join(lines))

---------- ADMIN COMMANDS ----------

def is_admin(update: Update):
return update.effective_user.id in ADMIN_IDS
def parse_target_and_amount(context):
if len(context.args) != 2:
return None
if not context.args[0].isdigit() or not context.args[1].isdigit():
return None
amount = int(context.args[1])
if amount <= 0:
return None
return int(context.args[0]), amount
async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
if not is_admin(update):
return
parsed = parse_target_and_amount(context)
if not parsed:
await update.message.reply_text("Usage: /give <user_id> ")
return
target_id, amount = parsed
if not user_exists(target_id):
await update.message.reply_text("❌ User not found.")
return
new_balance = update_balance(target_id, None, amount)
await update.message.reply_text(
f"✅ Gave {amount} coins to {target_id}.\n💰 New balance: {new_balance}"
)
async def take(update: Update, context: ContextTypes.DEFAULT_TYPE):
if not is_admin(update):
return
parsed = parse_target_and_amount(context)
if not parsed:
await update.message.reply_text("Usage: /take <user_id> ")
return
target_id, amount = parsed
if not user_exists(target_id):
await update.message.reply_text("❌ User not found.")
return
current = get_balance(target_id, None)
amount = min(amount, current)
new_balance = update_balance(target_id, None, -amount)
await update.message.reply_text(
f"✅ Took {amount} coins from {target_id}.\n💰 New balance: {new_balance}"
)
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
if not is_admin(update):
return
total_users, total_coins, new_today = get_stats()
await update.message.reply_text(
"📊 BOT STATS\n\n"
f"👥 Total users: {total_users}\n"
f"🆕 New in last 24h: {new_today}\n"
f"💰 Total coins: {total_coins}"
)
async def userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
if not is_admin(update):
return
if len(context.args) != 1 or not context.args[0].isdigit():
await update.message.reply_text("Usage: /user <user_id>")
return
target_id = int(context.args[0])
row, referrals = get_user_info(target_id)
if not row:
await update.message.reply_text("❌ User not found.")
return
username, bal, referred_by, created_at = row
real_bal = get_real_balance(target_id)
await update.message.reply_text(
"👤 USER INFO\n\n"
f"🆔 ID: {target_id}\n"
f"📛 Name: {username}\n"
f"🪙 Coins Balance: {bal}\n"
f"💵 Real Cash Balance: {real_bal:.2f} ETB\n"
f"👥 Invited friends: {referrals}\n"
f"🔗 Referred by: {referred_by or '-'}\n"
f"📅 Joined: {created_at}"
)
async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
name = update.effective_user.first_name
now = datetime.now(timezone.utc)
last_claim_str = get_last_daily_bonus(user_id, name)
if last_claim_str:
last_claim = datetime.fromisoformat(last_claim_str)
elapsed = now - last_claim
cooldown = timedelta(hours=DAILY_COOLDOWN_HOURS)
if elapsed < cooldown:
remaining = cooldown - elapsed
hours = int(remaining.total_seconds() // 3600)
minutes = int((remaining.total_seconds() % 3600) // 60)
await update.message.reply_text(
f"⏳ {name}፣ የዛሬውን ስጦታዎን አስቀድመው ወስደዋል!\nእባክዎ ከ {hours}ሰዓት ከ {minutes}ደቂቃ በኋላ ይመለሱ።"
)
return
new_balance = update_balance(user_id, name, DAILY_BONUS_AMOUNT)
set_last_daily_bonus(user_id, now.isoformat())
await update.message.reply
_text(
f"🎁 {name}፣ የዕለታዊ ስጦታዎን ወስደዋል!\n"
f"💰 +{DAILY_BONUS_AMOUNT} ሳንቲም ገቢ ሆኗል\n"
f"💰 ጠቅላላ ሳንቲም: {new_balance}\n\n"
f"⏰ ለቀጣዩ ስጦታ ከ 24 ሰዓታት በኋላ ይመለሱ!"
)

---------- MAIN ----------

if not TOKEN:
raise RuntimeError("TOKEN variable is missing. Add it in Railway > Variables.")
init_db()
_refunded = refund_live_round()
if _refunded:
print(f"Refunded {_refunded} players from an interrupted live round")
threading.Thread(target=run_health_server, daemon=True).start()
async def on_startup(application):
APP_STATE["loop"] = asyncio.get_running_loop()
APP_STATE["bot"] = application.bot
app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("bingo", bingo))
app.add_handler(CommandHandler("spin", spin))
app.add_handler(CommandHandler("dice", dice))
app.add_handler(CommandHandler("jackpot", jackpot))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("daily", daily))
app.add_handler(CommandHandler("invite", invite))
app.add_handler(CommandHandler("top", top))
app.add_handler(CommandHandler("send", send))
app.add_handler(CommandHandler("id", myid))
app.add_handler(CommandHandler("live", live))
app.add_handler(CommandHandler("deposit", deposit))
app.add_handler(CommandHandler("withdraw", withdraw))
app.add_handler(CallbackQueryHandler(live_claim, pattern="^bingo_claim$"))
app.add_handler(CommandHandler("give", give))
app.add_handler(CommandHandler("take", take))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("user", userinfo))
print(f"Miki Bingo Bot is running. Database: {DB_FILE}")
app.run_polling()
