from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import random

TOKEN = "8670452563:AAFp_eKsW2lJCxu4JfwVM4nTHsGUlGzG1zU"

scores = {}
balances = {}

keyboard = [
    ["/bingo", "/spin", "/dice"],
    ["/jackpot", "/balance"]
]

reply_markup = ReplyKeyboardMarkup(
    keyboard,
    resize_keyboard=True
)

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

    await update.message.reply_text(
        text,
        reply_markup=reply_markup
    )

async def bingo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user.first_name
    number = random.randint(1, 100)

    if user not in balances:
        balances[user] = 0

    if number > 90:
        balances[user] += 100

        msg = (
            f"🏆 {user} got bingo number {number}!\n"
            f"🎁 You won 100 coins!\n"
            f"💰 Balance: {balances[user]}"
        )
    else:
        msg = f"🎯 {user} got bingo number {number}"

    await update.message.reply_text(msg)

async def spin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user.first_name

    if user not in balances:
        balances[user] = 0

    reward = random.choice([10, 20, 50, 100])

    balances[user] += reward

    await update.message.reply_text(
        f"🎡 {user} won {reward} coins!\n💰 Balance: {balances[user]}"
    )

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user.first_name

    if user not in balances:
        balances[user] = 0

    roll = random.randint(1, 6)

    if roll == 6:
        balances[user] += 200

        msg = (
            f"🎲 Dice rolled: {roll}\n"
            f"🏆 You won 200 coins!\n"
            f"💰 Balance: {balances[user]}"
        )
    else:
        msg = (
            f"🎲 Dice rolled: {roll}\n"
            "❌ Try again!"
        )

    await update.message.reply_text(msg)

async def jackpot(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user.first_name

    if user not in balances:
        balances[user] = 0

    lucky = random.randint(1, 20)

    if lucky == 7:
        balances[user] += 1000

        await update.message.reply_text(
            f"💎 JACKPOT!\n🏆 {user} won 1000 coins!\n💰 Balance: {balances[user]}"
        )
    else:
        await update.message.reply_text(
            "❌ No jackpot!\n🎰 Try again!"
        )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user.first_name

    if user not in balances:
        balances[user] = 0

    await update.message.reply_text(
        f"💰 Your balance is {balances[user]} coins"
    )

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("bingo", bingo))
app.add_handler(CommandHandler("spin", spin))
app.add_handler(CommandHandler("dice", dice))
app.add_handler(CommandHandler("jackpot", jackpot))
app.add_handler(CommandHandler("balance", balance))

print("Bot is running...")

app.run_polling()