import os
import sqlite3
from datetime import datetime, timezone, timedelta
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import random

# TOKEN is read from Railway's environment Variables (never hardcode it in code)
TOKEN = os.environ.get("TOKEN")

DB_FILE = "bingo.db"
DAILY_BONUS_AMOUNT = 100
DAILY_COOLDOWN_HOURS = 24

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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Safe migration in case an older version of the table already exists
    try:
        cur.execute("ALTER TABLE users ADD COLUMN last_daily_bonus TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    conn.close()

def get_balance(user_id, username):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)",
            (user_id, username, 0)
        )
        conn.commit()
        balance = 0
    else:
        balance = row[0]
    conn.close()
    return balance

def update_balance(user_id, username, delta):
    # Ensures the user exists first
    get_balance(user_id, username)
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET balance = balance + ?, username = ? WHERE user_id = ?",
        (delta, username, user_id)
    )
    conn.commit()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_balance = cur.fetchone()[0]
    conn.close()
    return new_balance

def get_last_daily_bonus(user_id, username):
    get_balance(user_id, username)  # ensure user exists
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

# ---------- KEYBOARD ----------

keyboard = [
    ["/bingo", "/spin", "/dice"],
    ["/jackpot", "/balance"],
    ["/daily"]
]

reply_markup = ReplyKeyboardMarkup(
    keyboard,
    resize_keyboard=True
)

# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎉 WELCOME TO WIN BIRR BINGO! 🎉\n\n"
        "🎮 Play games and win coins!\n\n"
        "🎡 /spin - Spin wheel\n"
        "🎲 /dice - Roll dice\n"
        "🎯 /bingo - Play bingo\n"
        "💰 /balance - Check coins\n"
        "🎁 /daily - Daily bonus\n\n"
        "👇 Choose a button below 👇"
    )
    await update.message.reply_text(text, reply_markup=reply_markup)

async def bingo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    number = random.randint(1, 100)

    if number > 90:
        new_balance = update_balance(user_id, name, 100)
        msg = (
            f"🏆 {name} got bingo number {number}!\n"
            f"🎁 You won 100 coins!\n"
            f"💰 Balance: {new_balance}"
        )
    else:
        get_balance(user_id, name)  # make sure user exists
        msg = f"🎯 {name} got bingo number {number}"

    await update.message.reply_text(msg)

async def spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    reward = random.choice([10, 20, 50, 100])
    new_balance = update_balance(user_id, name, reward)

    await update.message.reply_text(
        f"🎡 {name} won {reward} coins!\n💰 Balance: {new_balance}"
    )

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    roll = random.randint(1, 6)

    if roll == 6:
        new_balance = update_balance(user_id, name, 200)
        msg = (
            f"🎲 Dice rolled: {roll}\n"
            f"🏆 You won 200 coins!\n"
            f"💰 Balance: {new_balance}"
        )
    else:
        get_balance(user_id, name)
        msg = (
            f"🎲 Dice rolled: {roll}\n"
            "❌ Try again!"
        )

    await update.message.reply_text(msg)

async def jackpot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    lucky = random.randint(1, 20)

    if lucky == 7:
        new_balance = update_balance(user_id, name, 1000)
        await update.message.reply_text(
            f"💎 JACKPOT!\n🏆 {name} won 1000 coins!\n💰 Balance: {new_balance}"
        )
    else:
        get_balance(user_id, name)
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

init_db()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("bingo", bingo))
app.add_handler(CommandHandler("spin", spin))
app.add_handler(CommandHandler("dice", dice))
app.add_handler(CommandHandler("jackpot", jackpot))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("daily", daily))

print("Bot is running with database and daily bonus...")

app.run_polling()
