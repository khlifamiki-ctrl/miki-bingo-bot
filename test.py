import os
import random
import logging
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# የሎግ መ정보ዎች መከታተያ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ከ አካባቢ ተለዋዋጮች (Environment Variables) መረጃዎችን ማንበብ
TOKEN = os.getenv("TOKEN", "YOUR_BOT_TOKEN_HERE")
BOT_USERNAME = os.getenv("BOT_USERNAME", "miki_bingo_bot")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.isdigit()]

# የክፍያ አካውንቶች መረጃ
PAYMENT_INFO = """
📥 **ገንዘብ ገቢ ማድረጊያ አካውንቶች (Official)**

• **TeleBirr:** 0967063387 (ስም: mikiya)
• **CBE Birr:** 1000586434814 (ስም: mikiya)
• **M-Pesa:** 0967063387 (ስም: mikiya)
• **E-Birr:** 0967063387 (ስም: mikiya)

⚠️ *ማሳሰቢያ: ገንዘብ ካስተላለፉ በኋላ የደረሰኝ ፎቶ (Screenshot) ለቦቱ ይላኩ።*
"""

# የተጫዋቾች ዴታቤዝ (በጊዚያዊነት በMemory ውስጥ የሚቀመጥ)
USER_DATA = {}

def get_user(user_id, username="ተጫዋች"):
    if user_id not in USER_DATA:
        USER_DATA[user_id] = {
            "username": username,
            "balance": 100,  # የነጻ መጀመሪያ ቦነስ ሳንቲም
            "invited_by": None,
            "invited_count": 0
        }
    return USER_DATA[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id, user.username)
    
    # ሪፈራል መኖሩን ማረጋገጥ
    if context.args and context.args.isdigit():
        referrer_id = int(context.args)
        if referrer_id != user.id and db_user["invited_by"] is None:
            db_user["invited_by"] = referrer_id
            ref_user = get_user(referrer_id)
            ref_user["balance"] += 100
            ref_user["invited_count"] += 1
            await context.bot.send_message(
                chat_id=referrer_id, 
                text=f"👥 በአገናኝ ሊንክዎ አዲስ ተጫዋች ተመዝግቧል! +100 ሳንቲም ተሰጥቶዎታል።"
            )

    welcome_text = f"🎱 **እንኳን ወደ Miki Bingo በሰላም መጡ!** @{user.username}\n\n🎯 የቀጥታ የቢንጎ እና የዕድል ጨዋታዎችን ተጫውተው ይሸልሙ። መመሪያዎችን ለማየት /rules ይበሉ።"
    
    # የእርስዎ የ GitHub Pages ሊንክ እዚህ ጋር ገብቷል
    keyboard = [
        [InlineKeyboardButton("🎱 Play Miki Bingo", web_app=WebAppInfo(url="https://github.io"))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def bingo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    numbers = random.sample(range(1, 76), 5)
    await update.message.reply_text(f"🎯 **የቢንጎ ቁጥር መሳቢያ**\n\nየወጡት ቁጥሮች: {', '.join(map(str, numbers))}\n\nከሚኒ አፑ ላይ ሙሉ ጨዋታውን ይጫወቱ!")

async def spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rewards = [10, 20, 50, 0, 100]
    result = random.choice(rewards)
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    db_user["balance"] += result
    await update.message.reply_text(f"🎡 **የእድል መንኮራኩር**\n\nእንኳን ደስ አለዎት! መንኮራኩሩን አሽከርክረው **{result} ሳንቲም** አሸንፈዋል።\n💰 አጠቃላይ ቀሪ ሂሳብዎ: {db_user['balance']} ሳንቲም")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    await update.message.reply_text(f"💰 **የአካውንትዎ መረጃ**\n\n• ተጫዋች: @{db_user['username']}\n• ቀሪ ሂሳብ: {db_user['balance']} ሳንቲም")

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PAYMENT_INFO, parse_mode="Markdown")

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(chat_id=admin_id, text=f"📥 አዲስ የክፍያ ማረጋገጫ ደረሰኝ ከ @{update.effective_user.username} ተልኳል። እባክዎ ያረጋግጡ።")
            await context.bot.forward_message(chat_id=admin_id, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
    await update.message.reply_text("✅ ደረሰኝዎ ደርሶናል። በአጭር ጊዜ ውስጥ ተረጋግጦ ሂሳብዎ ላይ ይደመራል። እናመሰግናለን!")

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules_text = "📖 **የቢንጎ አጨዋወት መመሪያ**\n\n1. በካርድዎ ላይ ያሉትን ቁጥሮች በመጠቀም ጨዋታ ይጀምሩ。\n2. የቢንጎ ቁጥሮች በየተራ ሲወጡ የእርስዎ ካርድ ላይ ካሉ ይንኳቸው።\n3. በአግድም፣ በቁመት ወይም በሰያፍ መስመር የሞላ መጀመሪያ 'BINGO' በመጫን ያሸንፋል!"
    await update.message.reply_text(rules_text, parse_mode="Markdown")

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bingo", bingo))
    application.add_handler(CommandHandler("spin", spin))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(CommandHandler("rules", rules))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_screenshot))

    application.run_polling()

if __name__ == '__main__':
    main()
import os
import random
import logging
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# የሎግ መ정보ዎች መከታተያ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ከ አካባቢ ተለዋዋጮች (Environment Variables) መረጃዎችን ማንበብ
TOKEN = os.getenv("TOKEN", "YOUR_BOT_TOKEN_HERE")
BOT_USERNAME = os.getenv("BOT_USERNAME", "miki_bingo_bot")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.isdigit()]

# የክፍያ አካውንቶች መረጃ
PAYMENT_INFO = """
📥 **ገንዘብ ገቢ ማድረጊያ አካውንቶች (Official)**

• **TeleBirr:** 0967063387 (ስም: mikiya)
• **CBE Birr:** 1000586434814 (ስም: mikiya)
• **M-Pesa:** 0967063387 (ስም: mikiya)
• **E-Birr:** 0967063387 (ስም: mikiya)

⚠️ *ማሳሰቢያ: ገንዘብ ካስተላለፉ በኋላ የደረሰኝ ፎቶ (Screenshot) ለቦቱ ይላኩ።*
"""

# የተጫዋቾች ዴታቤዝ (በጊዚያዊነት በMemory ውስጥ የሚቀመጥ)
USER_DATA = {}

def get_user(user_id, username="ተጫዋች"):
    if user_id not in USER_DATA:
        USER_DATA[user_id] = {
            "username": username,
            "balance": 100,  # የነጻ መጀመሪያ ቦነስ ሳንቲም
            "invited_by": None,
            "invited_count": 0
        }
    return USER_DATA[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id, user.username)
    
    # ሪፈራል መኖሩን ማረጋገጥ
    if context.args and context.args.isdigit():
        referrer_id = int(context.args)
        if referrer_id != user.id and db_user["invited_by"] is None:
            db_user["invited_by"] = referrer_id
            ref_user = get_user(referrer_id)
            ref_user["balance"] += 100
            ref_user["invited_count"] += 1
            await context.bot.send_message(
                chat_id=referrer_id, 
                text=f"👥 በአገናኝ ሊንክዎ አዲስ ተጫዋች ተመዝግቧል! +100 ሳንቲም ተሰጥቶዎታል።"
            )

    welcome_text = f"🎱 **እንኳን ወደ Miki Bingo በሰላም መጡ!** @{user.username}\n\n🎯 የቀጥታ የቢንጎ እና የዕድል ጨዋታዎችን ተጫውተው ይሸልሙ። መመሪያዎችን ለማየት /rules ይበሉ።"
    
    # የእርስዎ የ GitHub Pages ሊንክ እዚህ ጋር ገብቷል
    keyboard = [
        [InlineKeyboardButton("🎱 Play Miki Bingo", web_app=WebAppInfo(url="https://github.io"))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def bingo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    numbers = random.sample(range(1, 76), 5)
    await update.message.reply_text(f"🎯 **የቢንጎ ቁጥር መሳቢያ**\n\nየወጡት ቁጥሮች: {', '.join(map(str, numbers))}\n\nከሚኒ አፑ ላይ ሙሉ ጨዋታውን ይጫወቱ!")

async def spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rewards = [10, 20, 50, 0, 100]
    result = random.choice(rewards)
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    db_user["balance"] += result
    await update.message.reply_text(f"🎡 **የእድል መንኮራኩር**\n\nእንኳን ደስ አለዎት! መንኮራኩሩን አሽከርክረው **{result} ሳንቲም** አሸንፈዋል።\n💰 አጠቃላይ ቀሪ ሂሳብዎ: {db_user['balance']} ሳንቲም")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    await update.message.reply_text(f"💰 **የአካውንትዎ መረጃ**\n\n• ተጫዋች: @{db_user['username']}\n• ቀሪ ሂሳብ: {db_user['balance']} ሳንቲም")

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PAYMENT_INFO, parse_mode="Markdown")

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(chat_id=admin_id, text=f"📥 አዲስ የክፍያ ማረጋገጫ ደረሰኝ ከ @{update.effective_user.username} ተልኳል። እባክዎ ያረጋግጡ።")
            await context.bot.forward_message(chat_id=admin_id, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
    await update.message.reply_text("✅ ደረሰኝዎ ደርሶናል። በአጭር ጊዜ ውስጥ ተረጋግጦ ሂሳብዎ ላይ ይደመራል። እናመሰግናለን!")

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules_text = "📖 **የቢንጎ አጨዋወት መመሪያ**\n\n1. በካርድዎ ላይ ያሉትን ቁጥሮች በመጠቀም ጨዋታ ይጀምሩ。\n2. የቢንጎ ቁጥሮች በየተራ ሲወጡ የእርስዎ ካርድ ላይ ካሉ ይንኳቸው።\n3. በአግድም፣ በቁመት ወይም በሰያፍ መስመር የሞላ መጀመሪያ 'BINGO' በመጫን ያሸንፋል!"
    await update.message.reply_text(rules_text, parse_mode="Markdown")

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bingo", bingo))
    application.add_handler(CommandHandler("spin", spin))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(CommandHandler("rules", rules))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_screenshot))

    application.run_polling()

if __name__ == '__main__':
    main()
