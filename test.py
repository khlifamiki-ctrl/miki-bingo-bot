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

# Admin Telegram user IDs (comma separated). Can be overridden with the
# ADMIN_IDS variable in Railway. Default is the owner's ID.
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "6644747606").split(",")
    if x.strip().isdigit()
}

# Entry fee (coins) for each paid game. /spin and /daily stay free.
GAME_COSTS = {
    "dice": 20,
    "bingo": 10,
    "jackpot": 50,
}

# Prize (coins) when the player wins. Net profit = prize - fee.
DICE_PRIZE = 100
BINGO_PRIZE = 80
JACKPOT_PRIZE = 800

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


def get_stats():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM users")
    total_users, total_coins = cur.fetchone()
    cur.execute(
        "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-1 day')"
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


async def not_enough_coins(update, user_id, name, game):
    """Returns True (and tells the user) if they can't afford the game."""
    cost = GAME_COSTS[game]
    bal = get_balance(user_id, name)
    if bal < cost:
        await update.message.reply_text(
            f"💸 {name}, /{game} costs {cost} coins but you have {bal}.\n"
            "🎡 Use /spin or 🎁 /daily to earn free coins!"
        )
        return True
    return False


# ---------- KEYBOARD ----------

keyboard = [
    ["/bingo", "/spin", "/dice"],
    ["/jackpot", "/balance", "/top"],
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
        "🎡 /spin - Spin wheel (free)\n"
        f"🎲 /dice - Roll dice ({GAME_COSTS['dice']} coins)\n"
        f"🎯 /bingo - Play bingo ({GAME_COSTS['bingo']} coins)\n"
        f"💎 /jackpot - Jackpot ({GAME_COSTS['jackpot']} coins)\n"
        "💰 /balance - Check coins\n"
        "🎁 /daily - Daily bonus\n"
        "🏆 /top - Leaderboard\n"
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
    if await not_enough_coins(update, user_id, name, "bingo"):
        return
    set_cooldown(user_id, "bingo")
    update_balance(user_id, name, -GAME_COSTS["bingo"])

    number = random.randint(1, 100)

    if number > 90:
        new_balance = update_balance(user_id, name, BINGO_PRIZE)
        msg = (
            f"🏆 {name} got bingo number {number}!\n"
            f"🎁 You won {BINGO_PRIZE} coins! (net +{BINGO_PRIZE - GAME_COSTS['bingo']})\n"
            f"💰 Balance: {new_balance}"
        )
    else:
        new_balance = get_balance(user_id, name)
        msg = (
            f"🎯 {name} got bingo number {number}\n"
            f"❌ No win (-{GAME_COSTS['bingo']} coins)\n"
            f"💰 Balance: {new_balance}"
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
        f"🎡 {name} won {reward} coins!\n💰 Balance: {new_balance}"
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
            f"🎲 Dice rolled: {roll}\n"
            f"🏆 You won {DICE_PRIZE} coins! (net +{DICE_PRIZE - GAME_COSTS['dice']})\n"
            f"💰 Balance: {new_balance}"
        )
    else:
        new_balance = get_balance(user_id, name)
        msg = (
            f"🎲 Dice rolled: {roll}\n"
            f"❌ Try again! (-{GAME_COSTS['dice']} coins)\n"
            f"💰 Balance: {new_balance}"
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
            f"💎 JACKPOT!\n🏆 {name} won {JACKPOT_PRIZE} coins! (net +{JACKPOT_PRIZE - GAME_COSTS['jackpot']})\n💰 Balance: {new_balance}"
        )
    else:
        new_balance = get_balance(user_id, name)
        await update.message.reply_text(
            f"❌ No jackpot! (-{GAME_COSTS['jackpot']} coins)\n"
            f"🎰 Try again!\n💰 Balance: {new_balance}"
        )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    bal = get_balance(user_id, name)

    await update.message.reply_text(
        f"💰 Your balance is {bal} coins"
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    my_balance = get_balance(user_id, name)

    rows = get_top_users(10)
    medals = ["🥇", "🥈", "🥉"]

    lines = ["🏆 LEADERBOARD - Top 10\n"]
    for i, (username, bal) in enumerate(rows):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {username or 'Player'} - {bal} coins")

    lines.append(f"\n📍 Your rank: #{get_rank(user_id)} ({my_balance} coins)")

    await update.message.reply_text("\n".join(lines))


# ---------- ADMIN COMMANDS (only for ADMIN_IDS) ----------

def is_admin(update: Update):
    return update.effective_user.id in ADMIN_IDS


def parse_target_and_amount(context):
    """Returns (user_id, amount) from '/cmd <user_id> <amount>' or None."""
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
        await update.message.reply_text("Usage: /give <user_id> <amount>")
        return

    target_id, amount = parsed
    if not user_exists(target_id):
        await update.message.reply_text("❌ User not found. They must press /start first.")
        return

    new_balance = update_balance(target_id, None, amount)
    await update.message.reply_text(
        f"✅ Gave {amount} coins to {target_id}.\n💰 New balance: {new_balance}"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎁 You received {amount} coins!\n💰 Balance: {new_balance}"
        )
    except Exception:
        pass  # user may have blocked the bot


async def take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    parsed = parse_target_and_amount(context)
    if not parsed:
        await update.message.reply_text("Usage: /take <user_id> <amount>")
        return

    target_id, amount = parsed
    if not user_exists(target_id):
        await update.message.reply_text("❌ User not found.")
        return

    current = get_balance(target_id, None)
    amount = min(amount, current)  # never go below 0
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
    await update.message.reply_text(
        "👤 USER INFO\n\n"
        f"🆔 ID: {target_id}\n"
        f"📛 Name: {username}\n"
        f"💰 Balance: {bal}\n"
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
app.add_handler(CommandHandler("top", top))
app.add_handler(CommandHandler("give", give))
app.add_handler(CommandHandler("take", take))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("user", userinfo))

print(f"Bot is running. Database: {DB_FILE}")

app.run_polling()
