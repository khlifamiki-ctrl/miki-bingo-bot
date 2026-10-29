import os
import json
import hmac
import hashlib
import time
import asyncio
import sqlite3
import threading
import random
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qsl
from datetime import datetime, timezone, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

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

# Live Bingo round (virtual coins only - no cash in or out)
LIVE_ENTRY_FEE = 10        # coins to join a round
LIVE_LOBBY_SECONDS = 60    # time to wait for players
LIVE_DRAW_SECONDS = 5      # seconds between numbers
LIVE_MIN_PLAYERS = 2
LIVE_MAX_PLAYERS = 30
LIVE_CARD_POOL = 100       # numbered cards to choose from in each round
LIVE_MAX_CARDS = 4         # cards one player may hold

# Sending coins between players
SEND_MIN_AMOUNT = 10

# Cooldowns in seconds for each game (change these numbers as you like)
COOLDOWNS = {
    "spin": 3600,      # 1 hour
    "dice": 1800,      # 30 minutes
    "bingo": 600,      # 10 minutes
    "jackpot": 1800,   # 30 minutes
    "send": 60,        # 1 minute between transfers
    "redeem": 10,      # gift codes: slows down guessing
}


# ---------- TINY HEALTH CHECK SERVER ----------

# Address of the Mini App page (only this site may call the API from a browser)
MINIAPP_ORIGIN = os.environ.get("MINIAPP_ORIGIN", "https://khlifamiki-ctrl.github.io")
INITDATA_MAX_AGE = 24 * 3600   # Telegram login data older than this is refused


def verify_init_data(init_data):
    """Checks Telegram's signature on the Mini App login data.
    Returns the Telegram user (dict) if it is genuine, otherwise None."""
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
        "rank": get_rank(user_id),
        "daily_left": daily_seconds_left(user_id, name),
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
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Miki Bingo Bot is running")

        def do_POST(self):
            path = self.path.split("?")[0]
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
            elif path == "/api/daily":
                ok, bal, left = claim_daily(int(user["id"]), user.get("first_name") or "Player")
                self._json(200, {"ok": ok, "balance": bal, "left": left, "amount": DAILY_BONUS_AMOUNT})
            elif path == "/api/redeem":
                code = dict(parse_qsl(self.path.partition("?")[2])).get("code", "")
                self._json(200, api_redeem(user, code))
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount INTEGER,
            max_uses INTEGER DEFAULT 0,
            used INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gift_redemptions (
            code TEXT,
            user_id INTEGER,
            PRIMARY KEY (code, user_id)
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

def transfer_coins(from_id, to_id, amount):
    """Moves coins in ONE database transaction. Returns False if not enough coins."""
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
    """Takes the entry fee and records it, in one transaction. False if not enough coins."""
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
    """Takes the fee for one more card and adds it to the player's total, in one transaction."""
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
    """Gives one card's fee back (only used while the round is still in the lobby)."""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (fee, user_id))
    cur.execute("UPDATE live_round_players SET fee = fee - ? WHERE user_id = ?", (fee, user_id))
    cur.execute("DELETE FROM live_round_players WHERE fee <= 0")
    conn.commit()
    conn.close()


def settle_live_round(winner_id, pot):
    """Pays the pot to the winner and clears the round, in one transaction."""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (pot, winner_id))
    cur.execute("DELETE FROM live_round_players")
    conn.commit()
    conn.close()


def refund_live_round():
    """Gives every entry fee back and clears the round. Returns how many players."""
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


def clean_code(text):
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())[:20]


def create_gift_code(code, amount, max_uses):
    """Admin: makes (or updates) a gift code. max_uses 0 = unlimited. Each player can use a code once."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR REPLACE INTO gift_codes (code, amount, max_uses, used) VALUES "
        "(?, ?, ?, COALESCE((SELECT used FROM gift_codes WHERE code = ?), 0))",
        (code, amount, max_uses, code)
    )
    conn.commit()
    conn.close()


def redeem_gift_code(user_id, code):
    """One transaction. Returns ('ok', amount), ('invalid', 0), ('used', 0) or ('empty', 0)."""

conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT amount FROM gift_codes WHERE code = ?", (code,))
        row = cur.fetchone()
        if not row:
            return "invalid", 0
        amount = row[0]
        try:
            cur.execute("INSERT INTO gift_redemptions (code, user_id) VALUES (?, ?)", (code, user_id))
        except sqlite3.IntegrityError:
            return "used", 0
        cur.execute(
            "UPDATE gift_codes SET used = used + 1 WHERE code = ? AND (max_uses = 0 OR used < max_uses)",
            (code,)
        )
        if cur.rowcount == 0:
            conn.rollback()
            return "empty", 0
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        return "ok", amount
    finally:
        conn.close()


def daily_seconds_left(user_id, name):
    last = get_last_daily_bonus(user_id, name)
    if not last:
        return 0
    left = timedelta(hours=DAILY_COOLDOWN_HOURS) - (datetime.now(timezone.utc) - datetime.fromisoformat(last))
    return max(0, int(left.total_seconds()))


def claim_daily(user_id, name):
    """Same rule as /daily, for the Mini App. Returns (ok, balance, seconds_left)."""
    left = daily_seconds_left(user_id, name)
    if left > 0:
        return False, get_balance(user_id, name), left
    new_balance = update_balance(user_id, name, DAILY_BONUS_AMOUNT)
    set_last_daily_bonus(user_id, datetime.now(timezone.utc).isoformat())
    return True, new_balance, DAILY_COOLDOWN_HOURS * 3600


def api_redeem(user, raw_code):
    user_id = int(user["id"])
    name = user.get("first_name") or "Player"
    get_balance(user_id, name)
    wait = get_cooldown_remaining(user_id, "redeem")
    if wait > 0:
        return {"result": "wait", "left": wait, "balance": get_balance(user_id, name)}
    set_cooldown(user_id, "redeem")   # also after a wrong code, so codes can't be guessed fast
    code = clean_code(raw_code)
    status, amount = redeem_gift_code(user_id, code) if code else ("invalid", 0)
    return {"result": status, "amount": amount, "balance": get_balance(user_id, name)}


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
    ["/daily", "/invite", "/live"]
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
        f"🎱 /live - Live Bingo round ({LIVE_ENTRY_FEE} coins)\n"
        "💌 /send - Send coins to a friend\n"
        "🆔 /id - Show your ID\n"
        "👥 /invite - Invite friends\n"
        "🎟 /redeem - Use a gift code\n\n"
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


# ---------- LIVE BINGO ROUND ----------

LIVE = {"round": None}   # the one round that is running right now
LIVE_LAST = {"result": None}   # how the most recent round ended (shown in the Mini App)
APP_STATE = {"loop": None, "bot": None}   # filled when the bot starts (used by the web API)
BG_TASKS = set()

def spawn(coro):
    """Runs a coroutine in the background and keeps a reference so it is not dropped."""
    task = asyncio.create_task(coro)
    BG_TASKS.add(task)
    task.add_done_callback(BG_TASKS.discard)
    return task

CLAIM_KB = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🎯 BINGO!", callback_data="bingo_claim")]]
)


def make_card():
    """5x5 card: B 1-15, I 16-30, N 31-45, G 46-60, O 61-75. Centre (0) is free."""
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
    """A fresh round: LIVE_CARD_POOL numbered cards, nobody owns any yet."""
    return {
        "state": "lobby", "players": {}, "drawn": [], "created": time.time(),
        "pool": {n: make_card() for n in range(1, LIVE_CARD_POOL + 1)},
        "owner": {},   # card number -> user id
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
        "🎱 <b>LIVE BINGO</b>\n"
        f"💰 Pot: {pot} coins · 👥 {len(rnd['players'])} players\n"
    )
    if drawn:
        text += f"🔢 Now: <b>{ball(drawn[-1])}</b> ({len(drawn)}/75)\n"
        before = ", ".join(ball(x) for x in drawn[-6:-1][::-1])
        if before:
            text += f"Before: {before}\n"
    else:
        left = max(0, int(LIVE_LOBBY_SECONDS - (time.time() - rnd["created"])))
        text += f"⏳ Starting in ~{left}s (needs {LIVE_MIN_PLAYERS}+ players)\n"
    nos = sorted(player["cards"])
    text += f"\n🎟 Card #{nos[0]}\n<pre>" + render_card(player["cards"][nos[0]], set(drawn)) + "</pre>\n"
    if len(nos) > 1:
        text += f"➕ +{len(nos) - 1} more card(s) in the Mini App\n"
    text += "Full row, column or diagonal? Press BINGO! 👇"
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
            pass  # message unchanged / flood limit: skip this update

    await asyncio.gather(*(one(p) for p in list(rnd["players"].values())))


async def broadcast(bot, rnd, text):
    async def one(player):
        try:
            await bot.send_message(chat_id=player["chat_id"], text=text)
        except Exception:
            pass

    await asyncio.gather(*(one(p) for p in list(rnd["players"].values())))


def claim_win(rnd, winner_id):
    """Runs without any await, so only ONE claim can ever win. Returns the pot or 0."""

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
    await broadcast(bot, rnd, f"{reason}\n💰 Your entry coins were refunded.")


async def run_round(bot, rnd):
    try:
        await asyncio.sleep(LIVE_LOBBY_SECONDS)
        if len(rnd["players"]) < LIVE_MIN_PLAYERS:
            await end_round_refund(bot, rnd, "😕 Not enough players joined this round.")
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
            await end_round_refund(bot, rnd, "🎱 All 75 numbers were called and nobody won.")
    except Exception as e:
        print(f"Live round error: {e}")
        await end_round_refund(bot, rnd, "⚠️ Something went wrong, the round was cancelled.")


def try_pick(user_id, name, chat_id, bot, card_no):
    """Buys one numbered card (and opens a new round if none is waiting).
    Must run on the bot's event loop. Returns (status, round).
    Statuses: picked, taken, invalid, limit, no_coins, running, full."""
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

    # No await below, so the round state stays consistent
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
    """Gives a card back (lobby only) and refunds its fee."""
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
    if not rnd["players"]:           # last player left: the round simply disappears
        rnd["state"] = "finished"
        LIVE["round"] = None
        task = rnd.get("task")
        if task:
            task.cancel()
    return "removed"


def try_join(user_id, name, chat_id, bot):
    """Quick join (used by /live in the chat): gives one random free card.
    Returns (status, round, player). Statuses: joined, already, running, full, no_coins."""
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
        f"🏆 BINGO! {winner_name} won the pot of {pot} coins!\n"
        "Send /live to play again!"
    )


async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    get_balance(user_id, name)

    status, rnd, player = try_join(user_id, name, chat_id, context.bot)

    if status == "running":
        await update.message.reply_text(
            f"🎱 A round is in progress ({len(rnd['drawn'])}/75 numbers called).\n"
            "Send /live again in a minute to join the next one!"
        )
    elif status == "already":
        await update.message.reply_text("✅ You are already in this round. Watch your card above!")
    elif status == "full":
        await update.message.reply_text("🚫 This round is full. Try the next one!")
    elif status == "no_coins":
        bal = get_balance(user_id, name)
        await update.message.reply_text(
            f"💸 Live Bingo costs {LIVE_ENTRY_FEE} coins but you have {bal}.\n"
            "🎡 Use /spin or 🎁 /daily to earn free coins!"
        )
    elif status == "joined":
        msg = await update.message.reply_text(
            render_board(rnd, player), parse_mode="HTML", reply_markup=CLAIM_KB
        )
        player["message_id"] = msg.message_id
    else:
        await update.message.reply_text("🚫 Could not join this round. Try again!")


async def live_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    rnd = LIVE["round"]

    if not rnd or user_id not in rnd["players"]:
        await query.answer("This round is over. Send /live to play again!", show_alert=True)
        return
    if rnd["state"] != "running":
        await query.answer("The round has not started yet.", show_alert=False)
        return

    player = rnd["players"][user_id]
    drawn = rnd["drawn"]

    if player["locked_at"] == len(drawn):
        await query.answer("Wait for the next number before trying again.")
        return

    if player_bingo(player, set(drawn)):
        pot = claim_win(rnd, user_id)
        if pot:
            await query.answer(f"🎉 BINGO! You won {pot} coins!", show_alert=True)
            await announce_win(context.bot, rnd, player["name"], pot)
        else:
            await query.answer("Too late, someone was faster!", show_alert=True)
    else:
        player["locked_at"] = len(drawn)
        await query.answer("❌ Not a BINGO yet! Keep watching.", show_alert=True)


# ---------- LIVE BINGO FOR THE MINI APP (web API) ----------

def live_state_for(user_id):
    """Snapshot of the live round for one player. Runs on the bot's event loop."""
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
        # The round may have been won a split second ago by someone faster
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
    """Called from the web server thread: runs the work on the bot's own event loop,
    so the live round is only ever touched by one thread."""
    loop = APP_STATE["loop"]
    if loop is None:
        raise RuntimeError("bot is still starting")
    return asyncio.run_coroutine_threadsafe(make_coro(), loop).result(timeout=timeout)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    get_balance(user_id, name)
    await update.message.reply_text(
        f"🆔 Your ID: {user_id}\n\n"
        "Share it with a friend so they can send you coins:\n"
        f"/send {user_id} <amount>"
    )


async def send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    get_balance(user_id, name)

    parsed = parse_target_and_amount(context)
    if not parsed:
        await update.message.reply_text(
            "Usage: /send <user_id> <amount>\n"
            "Your friend can get their ID with /id"
        )
        return

    target_id, amount = parsed

if target_id == user_id:
        await update.message.reply_text("🚫 You can't send coins to yourself.")
        return
    if amount < SEND_MIN_AMOUNT:
        await update.message.reply_text(f"🚫 Minimum is {SEND_MIN_AMOUNT} coins.")
        return
    if not user_exists(target_id):
        await update.message.reply_text("❌ User not found. They must press /start first.")
        return
    if await blocked_by_cooldown(update, user_id, name, "send"):
        return

    if not transfer_coins(user_id, target_id, amount):
        bal = get_balance(user_id, name)
        await update.message.reply_text(
            f"💸 Not enough coins. You have {bal}."
        )
        return

    set_cooldown(user_id, "send")
    my_balance = get_balance(user_id, name)
    await update.message.reply_text(
        f"✅ Sent {amount} coins to {target_id}.\n💰 Your balance: {my_balance}"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎁 {name} sent you {amount} coins!\n💰 Balance: {get_balance(target_id, None)}"
        )
    except Exception:
        pass  # user may have blocked the bot


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


async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    get_balance(user_id, name)
    if not context.args:
        await update.message.reply_text("Usage: /redeem <CODE>")
        return
    if await blocked_by_cooldown(update, user_id, name, "redeem"):
        return
    set_cooldown(user_id, "redeem")
    status, amount = redeem_gift_code(user_id, clean_code(context.args[0]))
    messages = {
        "ok": f"🎁 Code accepted! +{amount} coins\n💰 Balance: {get_balance(user_id, name)}",
        "used": "🚫 You already used this code.",
        "empty": "😕 This code has no uses left.",
        "invalid": "❌ Wrong code.",
    }
    await update.message.reply_text(messages[status])


async def newcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    a = context.args
    if len(a) not in (2, 3) or not a[1].isdigit() or int(a[1]) <= 0 or (len(a) == 3 and not a[2].isdigit()):
        await update.message.reply_text("Usage: /newcode <CODE> <amount> [max_uses]\n(max_uses 0 or empty = unlimited)")
        return
    code = clean_code(a[0])
    if not code:
        await update.message.reply_text("❌ The code needs letters or numbers.")
        return
    max_uses = int(a[2]) if len(a) == 3 else 0
    create_gift_code(code, int(a[1]), max_uses)
    await update.message.reply_text(
        f"✅ Code {code}: +{a[1]} coins per player, max uses: {max_uses or 'unlimited'}"
    )


# ---------- MAIN ----------

if not TOKEN:
    raise RuntimeError("TOKEN variable is missing. Add it in Railway > Variables.")

init_db()

# If the bot restarted in the middle of a live round, give the entry fees back
_refunded = refund_live_round()
if _refunded:
    print(f"Refunded {_refunded} players from an interrupted live round")

# Health-check web server in the background
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
app.add_handler(CallbackQueryHandler(live_claim, pattern="^bingo_claim$"))
app.add_handler(CommandHandler("give", give))
app.add_handler(CommandHandler("take", take))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("user", userinfo))
app.add_handler(CommandHandler("redeem", redeem))
app.add_handler(CommandHandler("newcode", newcode))

print(f"Bot is running. Database: {DB_FILE}")

app.run_polling()
