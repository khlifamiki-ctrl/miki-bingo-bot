import os
import sqlite3
import threading
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# TOKEN is read from the platform's environment Variables (never hardcode it)
TOKEN = os.environ.get("TOKEN")
BOT_USERNAME = "miki_bingo_bot"

# Use the Railway Volume (/data) if it exists, otherwise a local file (no crash)
if os.path.isdir("/data"):
    DB_FILE = "/data/bingo.db"
else:
    DB_FILE = "bingo.db"

DAILY_BONUS_AMOUNT = 100
DAILY_COOLDOWN_HOURS = 24
REFERRER_REWARD = 100
REFERRED_REWARD = 50

# Cooldowns in seconds for each game (change these numbers as you like)
COOLDOWNS = {
    "spin": 3600,      # 1 hour
    "dice": 1800,      # 30 minutes
    "bingo": 600,      # 10 minutes
    "jackpot": 1800,   # 30 minutes
}


# ---------- TINY HEALTH CHECK SERVER ----------

def run_health_server():
    port = int(os.environ.get("PORT", 8080))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Miki Bingo Bot is running")

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), Handler)
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
    conn.commit()

    for column_def in ["last_daily_bonus TEXT", "referred_by INTEGER"]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {column_def}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

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
        "INSERT INTO users (user_id, username, balance, referred_by) VALUES (?, ?, ?, ?)",
        (user_id, username, 0, referred_by)
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
        # No name given (e.g. referrer reward): keep the existing username
        cur.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (delta, user_id)
        )
    conn.commit()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
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


# ---------- COOLDOWN HELPERS ----------

def get_cooldown_remaining(user_id, game):
    """Returns seconds left before the user can play again (0 = can play)."""
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
    """Returns True (and tells the user) if the game is still on cooldown."""
    remaining = get_cooldown_remaining(user_id, game)
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ {name}, please wait {format_wait(remaining)} before playing /{game} again."
        )
        return True
    return False


# ---------- KEYBOARD ----------

keyboard = [
    ["/bingo", "/spin", "/dice"],
    ["/jackpot", "/balance"],
    ["/daily", "/invite"]
]

reply_markup = ReplyKeyboardMarkup(
    keyboard,
    resize_keyboard=True
)


# ---------- HANDLERS ----------

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
            extra_note = "🚫 You can't use your own referral link.\n\n"
            create_user(user_id, name)
        elif referrer_id is not None and user_exists(referrer_id):
            create_user(user_id, name, referred_by=referrer_id)
            update_balance(referrer_id, None, REFERRER_REWARD)
            update_balance(user_id, name, REFERRED_REWARD)
            extra_note = f"🎉 You joined via a referral! +{REFERRED_REWARD} coins bonus!\n\n"
        else:
            create_user(user_id, name)

    text = (
        f"{extra_note}"
        "🎉 WELCOME TO WIN BIRR BINGO! 🎉\n\n"
        "🎮 Play games and win coins!\n\n"
        "🎡 /spin - Spin wheel\n"
        "🎲 /dice - Roll dice\n"
        "🎯 /bingo - Play bingo\n"
        "💰 /balance - Check coins\n"
        "🎁 /daily - Daily bonus\n"
        "👥 /invite - Invite friends\n\n"
        "👇 Choose a button below 👇"
    )

    await update.message.reply_text(text, reply_markup=reply_markup)


async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    get_balance(user_id, name)

    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        "👥 Invite Friends & Earn!\n\n"
        f"🔗 Your referral link:\n{link}\n\n"
        f"🎁 You earn: {REFERRER_REWARD} coins\n"
        f"🎉 Your friend earns: {REFERRED_REWARD} coins"
    )
    await update.message.reply_text(text)


async def bingo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    get_balance(user_id, name)

    if await blocked_by_cooldown(update, user_id, name, "bingo"):
        return
    set_cooldown(user_id, "bingo")

    number = random.randint(1, 100)

    if number > 90:
        new_balance = update_balance(user_id, name, 100)
        msg = (
            f"🏆 {name} got bingo number {number}!\n"
            f"🎁 You won 100 coins!\n"
            f"💰 Balance: {new_balance}"
        )
    else:
        msg = f"🎯 {name} got bingo number {number}"

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
        f"🎡 {name} won {reward} coins!\n💰 Balance: {new_balance}"
    )


async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    get_balance(user_id, name)

    if await blocked_by_cooldown(update, user_id, name, "dice"):
        return
    set_cooldown(user_id, "dice")

    roll = random.randint(1, 6)

    if roll == 6:
        new_balance = update_balance(user_id, name, 200)
        msg = (
            f"🎲 Dice rolled: {roll}\n"
            f"🏆 You won 200 coins!\n"
            f"💰 Balance: {new_balance}"
        )
    else:
        msg = (
            f"🎲 Dice rolled: {roll}\n"
            "❌ Try again!"
        )

    await update.message.reply_text(msg)


async def jackpot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    get_balance(user_id, name)

    if await blocked_by_cooldown(update, user_id, name, "jackpot"):
        return
    set_cooldown(user_id, "jackpot")

    lucky = random.randint(1, 20)

    if lucky == 7:
        new_balance = update_balance(user_id, name, 1000)
        await update.message.reply_text(
            f"💎 JACKPOT!\n🏆 {name} won 1000 coins!\n💰 Balance: {new_balance}"
        )
    else:
        await update.message.reply_text(
            "❌ No jackpot!\n🎰 Try again!"
        )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    bal = get_balance(user_id, name)

    await update.message.reply_text(
        f"💰 Your balance is {bal} coins"
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
                f"⏳ {name}, you already claimed your daily bonus!\n"
                f"Come back in {hours}h {minutes}m."
            )
            return

    new_balance = update_balance(user_id, name, DAILY_BONUS_AMOUNT)
    set_last_daily_bonus(user_id, now.isoformat())

    await update.message.reply_text(
        f"🎁 {name}, you claimed your daily bonus!\n"
        f"💰 +{DAILY_BONUS_AMOUNT} coins\n"
        f"💰 Balance: {new_balance}\n\n"
        f"⏰ Come back in {DAILY_COOLDOWN_HOURS} hours for more!"
    )


# ---------- MAIN ----------

if not TOKEN:
    raise RuntimeError("TOKEN variable is missing. Add it in Railway > Variables.")

init_db()

# Health-check web server in the background
threading.Thread(target=run_health_server, daemon=True).start()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("bingo", bingo))
app.add_handler(CommandHandler("spin", spin))
app.add_handler(CommandHandler("dice", dice))
app.add_handler(CommandHandler("jackpot", jackpot))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("daily", daily))
app.add_handler(CommandHandler("invite", invite))

print(f"Bot is running. Database: {DB_FILE}")

app.run_polling()
