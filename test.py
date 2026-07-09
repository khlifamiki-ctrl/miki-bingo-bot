import os
import sqlite3
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import random

# TOKEN is read from Railway's environment Variables (never hardcode it in code)
TOKEN = os.environ.get("TOKEN")

DB_FILE = "bingo.db"

# ---------- DATABASE SETUP ----------

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
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

# ---------- KEYBOARD ----------

keyboard = [
    ["/bingo", "/spin", "/dice"],
    ["/jackpot", "/balance"]
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
        "💰 /balance - Check coins\n\n"
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

# ---------- MAIN ----------

init_db()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("bingo", bingo))
app.add_handler(CommandHandler("spin", spin))
app.add_handler(CommandHandler("dice", dice))
app.add_handler(CommandHandler("jackpot", jackpot))
app.add_handler(CommandHandler("balance", balance))

print("Bot is running with database...")

app.run_polling()
