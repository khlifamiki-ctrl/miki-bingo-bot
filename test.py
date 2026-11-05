"""
Miki Bingo Bot (ሚኪ ቢንጎ) - Production Telegram Bot + Server
Repository: khlifamiki/miki-bingo-bot -> test.py
"""
import os
import random
import threading
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MikiBingo")

# የአካውንት መረጃዎች (Payment Details)
TELEBIRR_NO = "0967063387"
CBE_ACCOUNT = "1000586434814"
ACCOUNT_NAME = "mikiya"
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://khlifamiki.github.io/miki-bingo-app/")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7710928929:AAG_your_token_here")

# የ 1-500 ካርዶች እና የተጠቃሚዎች ዳታ
USERS = {}
AVAILABLE_CARDS = list(range(1, 501))
BINGO_CALLS = []
IS_GAME_RUNNING = False

def get_user(uid, name="ተጫዋች"):
    if uid not in USERS:
        USERS[uid] = {
            "balance": 50.0,
            "win_balance": 0.0,
            "cards": [],
            "name": name,
            "daily_claimed": False,
            "invites": 0
        }
    return USERS[uid]

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    usr = get_user(u.id, u.first_name)
    txt = (
        f"👋 ሰላም {u.first_name}! እንኳን ወደ **ሚኪ ቢንጎ (Miki Bingo)** በደህና መጡ!\n\n"
        f"💰 የጨዋታ ሂሳብ፡ **{usr['balance']:.2f} ETB**\n"
        f"🏆 ያሸነፉት፡ **{usr['win_balance']:.2f} ETB**\n"
        f"🔢 የካርዶች ምርጫ፡ **ከ 1 እስከ 500 ኒዮን ካርዶች**\n\n"
        f"👇 ከታች ያለውን **«🎮 ቢንጎ ጀምር»** በመጫን በስልክዎ መጫወት ይችላሉ!"
    )
    kb = [
        [InlineKeyboardButton("🎮 ቢንጎ ጀምር (Play Mini App)", web_app=WebAppInfo(url=WEBAPP_URL))],
        [
            InlineKeyboardButton("🎰 777 Slots", callback_data="spin"),
            InlineKeyboardButton("💰 ሂሳብ (Balance)", callback_data="bal")
        ],
        [
            InlineKeyboardButton("💳 ገንዘብ አስገባ (Deposit)", callback_data="dep"),
            InlineKeyboardButton("🏦 ወጪ አድርግ (Withdraw)", callback_data="wdr")
        ],
        [
            InlineKeyboardButton("🎁 ዕለታዊ ቦነስ (/daily)", callback_data="daily"),
            InlineKeyboardButton("👥 ጓደኛ ጋብዝ (/invite)", callback_data="invite")
        ]
    ]
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    usr = get_user(u.id, u.first_name)
    msg = (
        f"💰 **የሂሳብ መግለጫ ({u.first_name})**\n\n"
        f"• የጨዋታ ሂሳብ: **{usr['balance']:.2f} ETB**\n"
        f"• የአሸናፊነት ሂሳብ: **{usr['win_balance']:.2f} ETB**\n"
        f"• የያዟቸው ካርዶች: {', '.join(map(str, usr['cards'])) if usr['cards'] else 'ምንም አልተያዘም'}\n\n"
        f"ገንዘብ ለማስገባት /deposit ይጠቀሙ።"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        f"💳 **ገንዘብ ማስገቢያ (Deposit)**\n\n"
        f"1️⃣ **Telebirr**: `{TELEBIRR_NO}`\n"
        f"2️⃣ **CBE ንግድ ባንክ**: `{CBE_ACCOUNT}`\n"
        f"👤 **ስም**: **{ACCOUNT_NAME}**\n\n"
        f"ገንዘቡን ልከው የደረሰኝ ስክሪንሾት (Screenshot) እዚህ ይላኩ፤ ወዲያውኑ ይሞላልዎታል!"
    )
    await update.message.reply_text(txt, parse_mode="Markdown")

async def spin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    usr = get_user(u.id, u.first_name)
    if usr["balance"] < 5:
        await update.message.reply_text("❌ ለስፒን በቂ ሂሳብ የለዎትም (ቢያንስ 5 ETB ያስፈልጋል)። /deposit ይጠቀሙ።")
        return
    usr["balance"] -= 5
    items = ["🍒", "💎", "7️⃣", "🔔", "⭐"]
    r = [random.choice(items) for _ in range(3)]
    res_str = " | ".join(r)
    if r[0] == r[1] == r[2]:
        win = 100.0 if r[0] == "7️⃣" else 50.0
        usr["win_balance"] += win
        ans = f"🎰 [{res_str}]\n🎉 **እንኳን ደስ አለዎት!** {win:.2f} ETB አሸነፉ!"
    else:
        ans = f"🎰 [{res_str}]\n😢 አላሸነፉም፤ እንደገና ይሞክሩ!"
    ans += f"\n\n💰 ቀሪ ሂሳብ፡ {usr['balance']:.2f} ETB"
    await update.message.reply_text(ans, parse_mode="Markdown")

# HTTP Server for Railway Health Check
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "running", "bot": "@miki_bingo_bot", "cards": 500}).encode())

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("deposit", deposit_cmd))
    app.add_handler(CommandHandler("spin", spin_cmd))
    logger.info("Miki Bingo Bot Started...")
    app.run_polling()

if __name__ == "__main__":
    main()
