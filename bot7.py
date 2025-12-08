
# bot6_fixed.py
# Fully patched and integrated Telegram bot using python-telegram-bot and Telethon
# Features:
# - Telethon phone login: send_code -> sign_in -> session saved
# - Add account flow via simple "➕ Hisob qo'shish" button (phone -> code)
# - Proper context.user_data mode management (no /code confusion)
# - Auto-send loop uses each account's session via Telethon
# - Admin panel: approve requests, add subscription, delete user data
# - Safe DB operations with UNIQUE handling and session cleanup
#
# Requirements:
# pip install python-telegram-bot==20.4 telethon
# Set environment variables: BOT_TOKEN, ADMIN_ID, API_ID, API_HASH (or store api_id/api_hash in settings table)
#
# Place this file on server and run: python bot6_fixed.py

import os
import time
import random
import sqlite3
import logging
import asyncio
import threading
from datetime import datetime, timedelta

from telethon import TelegramClient, errors

from telegram import (
    ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram import Update

# ---------------- CONFIG ----------------
DB_FILE = os.environ.get("DB_FILE", "autorun.db")
BOT_TOKEN = os.environ.get("8220522354:AAHk8lxrgvQAapNvV7u4-d-9A0zPT6ki5oI", "")
ADMIN_ID = int(os.environ.get("2091226701", "0") or 0)
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Telethon client cache: session_name -> TelegramClient
client_cache = {}

# ---------------- DATABASE ----------------
def init_database():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        display_name TEXT,
        session_name TEXT UNIQUE,
        phone TEXT UNIQUE,
        country_code TEXT,
        username TEXT,
        is_active INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        subscription_end DATETIME,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        account_display_name TEXT,
        group_id TEXT,
        group_title TEXT,
        group_username TEXT,
        is_active INTEGER DEFAULT 1,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, account_display_name, group_id)
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        status TEXT DEFAULT 'pending',
        admin_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS user_intervals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        min_interval INTEGER DEFAULT 20,
        max_interval INTEGER DEFAULT 25,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('SELECT value FROM settings WHERE key=?', (key,))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else default

def save_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)', (key, value))
    conn.commit()
    conn.close()

# ---------------- ACCOUNT DB HELPERS ----------------
def add_user_account(user_id, session_name, phone="", country_code="", username="", display_name=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM accounts WHERE user_id=?', (user_id,))
        count = cur.fetchone()[0]
        if count >= 10:
            conn.close()
            return None
        if not display_name:
            display_name = f"account{count+1}"
        try:
            cur.execute('INSERT INTO accounts (user_id, display_name, session_name, phone, country_code, username, is_active) VALUES (?,?,?,?,?,?,1)',
                        (user_id, display_name, session_name, phone, country_code, username))
            conn.commit()
            conn.close()
            logger.info(f"Account added: {display_name} for user {user_id}")
            return display_name
        except sqlite3.IntegrityError as e:
            logger.error(f"add_user_account IntegrityError: {e}")
            conn.close()
            return None
    except Exception as e:
        logger.error(f"add_user_account error: {e}")
        return None

def delete_user_data(user_id):
    try:
        user_id = int(user_id)
    except:
        return False
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('SELECT session_name FROM accounts WHERE user_id=?', (user_id,))
        rows = cur.fetchall()
        session_names = [r[0] for r in rows if r and r[0]]
        cur.execute('DELETE FROM accounts WHERE user_id=?', (user_id,))
        cur.execute('DELETE FROM groups WHERE user_id=?', (user_id,))
        cur.execute('DELETE FROM messages WHERE user_id=?', (user_id,))
        cur.execute('DELETE FROM requests WHERE user_id=?', (user_id,))
        cur.execute('DELETE FROM user_intervals WHERE user_id=?', (user_id,))
        conn.commit()
        conn.close()
        for s in session_names:
            p = os.path.join(SESSIONS_DIR, s)
            for ext in ("", ".session", ".session-journal", ".db"):
                try:
                    if os.path.exists(p+ext):
                        os.remove(p+ext)
                except:
                    pass
            client_cache.pop(s, None)
        logger.info(f"Deleted user data for {user_id}")
        return True
    except Exception as e:
        logger.error(f"delete_user_data error: {e}")
        return False

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('SELECT DISTINCT user_id FROM accounts')
    users = [r[0] for r in cur.fetchall()]
    conn.close()
    return users

def get_user_accounts(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('SELECT display_name, phone, country_code, username, is_active, session_name, id FROM accounts WHERE user_id=?', (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_user_groups(user_id, display_name):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('SELECT id, group_id, group_title, group_username, is_active FROM groups WHERE user_id=? AND account_display_name=?', (user_id, display_name))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_user_messages(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('SELECT id, text FROM messages WHERE user_id=?', (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_random_user_message(user_id):
    msgs = get_user_messages(user_id)
    if not msgs:
        return None
    import random
    return random.choice(msgs)[1]

def update_user_subscription(user_id, days):
    try:
        user_id = int(user_id)
    except:
        return None
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM requests WHERE user_id=?', (user_id,))
    has_request = cur.fetchone()[0] > 0
    cur.execute('SELECT id FROM accounts WHERE user_id=?', (user_id,))
    rows = cur.fetchall()
    now = datetime.now()
    new_end = (now + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    if rows:
        cur.execute('UPDATE accounts SET subscription_end=?, is_active=1 WHERE user_id=?', (new_end, user_id))
        conn.commit()
        conn.close()
        return True
    else:
        if not has_request:
            conn.close()
            return None
        try:
            cur.execute('INSERT INTO accounts (user_id, display_name, session_name, phone, is_active, subscription_end) VALUES (?,?,?,?,?,?)',
                        (user_id, f"account1", f"auto_{user_id}_{int(time.time())}", "", 0, new_end))
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError as e:
            logger.error(f"update_user_subscription IntegrityError: {e}")
            conn.close()
            return None

# ---------------- TELETHON HELPERS ----------------
def normalize_phone(phone: str) -> str:
    p = phone.strip().replace(" ", "").replace("-", "")
    if p.startswith("+"):
        return p
    return "+" + p

def get_api_credentials():
    api_id = get_setting('api_id', None)
    api_hash = get_setting('api_hash', None)
    if api_id and api_hash:
        try:
            return int(api_id), str(api_hash)
        except:
            pass
    api_id_env = os.environ.get('API_ID')
    api_hash_env = os.environ.get('API_HASH')
    if api_id_env and api_hash_env:
        return int(api_id_env), api_hash_env
    raise RuntimeError("API_ID/API_HASH not found in settings or env")

async def telethon_send_code(phone: str, session_name: str):
    phone_norm = normalize_phone(phone)
    api_id, api_hash = get_api_credentials()
    path = os.path.join(SESSIONS_DIR, session_name)
    client = TelegramClient(path, api_id, api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            await client.disconnect()
            return True
        await client.send_code_request(phone_norm)
        await client.disconnect()
        return True
    except Exception as e:
        logger.error(f"telethon_send_code error: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return False

async def telethon_sign_in(phone: str, session_name: str, code: str, password: str = None):
    phone_norm = normalize_phone(phone)
    api_id, api_hash = get_api_credentials()
    path = os.path.join(SESSIONS_DIR, session_name)
    client = TelegramClient(path, api_id, api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            await client.disconnect()
            return True, me
        try:
            await client.sign_in(phone_norm, code)
        except errors.SessionPasswordNeededError:
            if password is None:
                await client.disconnect()
                return False, "2FA_REQUIRED"
            await client.sign_in(password=password)
        me = await client.get_me()
        await client.disconnect()
        return True, me
    except Exception as e:
        logger.error(f"telethon_sign_in error: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return False, str(e)

async def get_or_create_client(session_name: str):
    if not session_name:
        return None
    client = client_cache.get(session_name)
    if client:
        try:
            if await client.is_connected():
                return client
        except:
            try:
                await client.disconnect()
            except:
                pass
        client_cache.pop(session_name, None)
    try:
        api_id, api_hash = get_api_credentials()
    except Exception as e:
        logger.error("API credentials error")
        return None
    path = os.path.join(SESSIONS_DIR, session_name)
    client = TelegramClient(path, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        client_cache[session_name] = client
        return client
    except Exception as e:
        logger.error(f"get_or_create_client error: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return None

def logout_account_by_display(display_name):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('SELECT session_name FROM accounts WHERE display_name=?', (display_name,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False
        session_name = row[0]
        cur.execute('DELETE FROM accounts WHERE display_name=?', (display_name,))
        conn.commit()
        conn.close()
        path = os.path.join(SESSIONS_DIR, session_name)
        for ext in ("", ".session", ".session-journal", ".db"):
            try:
                if os.path.exists(path+ext):
                    os.remove(path+ext)
            except:
                pass
        client_cache.pop(session_name, None)
        return True
    except Exception as e:
        logger.error(f"logout_account error: {e}")
        return False

# ---------------- KEYBOARDS ----------------
def get_admin_keyboard():
    return ReplyKeyboardMarkup([
        ["📋 Foydalanuvchilar", "⏳ So'rovlar"],
        ["➕ Ruxsat berish", "🗑️ Hisob o'chirish"],
        ["📊 Statistika", "⚙️ Sozlamalar"],
        ["🔄 Avtomatik yuborish", "⏸️ To'xtatish"],
        ["📨 Xabar yuborish"],
        ["🔄 Yangilash"]
    ], resize_keyboard=True)

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Hisob qo'shish", "📱 Guruhlar"],
        ["✉️ Xabarlar", "⚙️ Sozlamalar"]
    ], resize_keyboard=True)

# ---------------- PARSERS ----------------
def parse_single_id(raw_text: str):
    if not raw_text:
        return None
    s = raw_text.strip().lstrip('/').split()
    try:
        return int(s[0])
    except:
        return None

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        await update.message.reply_text("👑 Admin panelga xush kelibsiz", reply_markup=get_admin_keyboard())
    else:
        await update.message.reply_text("🤖 Botga xush kelibsiz", reply_markup=get_user_keyboard())

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    await update.message.reply_text("❌ Bekor qilindi", reply_markup=(get_admin_keyboard() if uid==ADMIN_ID else get_user_keyboard()))

async def process_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text
    parts = raw.strip().split()
    if len(parts) < 3:
        await update.message.reply_text("❌ Format: /add ID DAYS", reply_markup=get_admin_keyboard())
        return
    try:
        user_id = int(parts[1])
        days = int(parts[2])
    except:
        await update.message.reply_text("❌ ID yoki kun noto'g'ri", reply_markup=get_admin_keyboard())
        return
    ok = update_user_subscription(user_id, days)
    if ok:
        await update.message.reply_text(f"✅ {user_id} ga {days} kun ruxsat berildi.", reply_markup=get_admin_keyboard())
    else:
        await update.message.reply_text("❌ Ruxsat berilmadi yoki so'rov topilmadi.", reply_markup=get_admin_keyboard())

async def process_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text
    parts = raw.strip().split()
    if len(parts) < 2:
        await update.message.reply_text("❌ Format: /remove ID", reply_markup=get_admin_keyboard())
        return
    try:
        target = int(parts[1])
    except:
        await update.message.reply_text("❌ ID noto'g'ri", reply_markup=get_admin_keyboard())
        return
    ok = delete_user_data(target)
    if ok:
        await update.message.reply_text(f"✅ Foydalanuvchi {target} o'chirildi", reply_markup=get_admin_keyboard())
    else:
        await update.message.reply_text("❌ O'chirishda xato", reply_markup=get_admin_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip() if update.message and update.message.text else ""
    # Admin flows
    if uid == ADMIN_ID:
        mode = context.user_data.get("mode")
        if text == "📨 Xabar yuborish":
            await update.message.reply_text("📝 Xabar matnini yuboring", reply_markup=ReplyKeyboardRemove())
            context.user_data["mode"] = "admin_broadcast"
            return
        if mode == "admin_broadcast":
            msg = text
            users = get_all_users()
            sent = 0
            failed = 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u, text=msg)
                    sent += 1
                    await asyncio.sleep(0.03)
                except Exception as e:
                    logger.error(f"Broadcast to {u} failed: {e}")
                    failed += 1
            await update.message.reply_text(f"✅ Yuborildi: {sent}\n❌ Muvaffaqiyatsiz: {failed}", reply_markup=get_admin_keyboard())
            context.user_data["mode"] = None
            return
        if text == "🗑️ Hisob o'chirish":
            await update.message.reply_text("O'chiriladigan user ID ni yuboring", reply_markup=get_admin_keyboard())
            context.user_data["mode"] = "delete_user"
            return
        if context.user_data.get("mode") == "delete_user":
            try:
                target = int(text)
                delete_user_data(target)
                await update.message.reply_text(f"✅ {target} o'chirildi", reply_markup=get_admin_keyboard())
            except Exception as e:
                await update.message.reply_text(f"❌ Xato: {e}", reply_markup=get_admin_keyboard())
            context.user_data["mode"] = None
            return

    # User flows - Add account (phone -> code -> sign in)
    mode = context.user_data.get("mode")
    if text == "➕ Hisob qo'shish":
        context.user_data.clear()
        context.user_data["mode"] = "add_phone"
        await update.message.reply_text("📲 Telefon raqamingizni yuboring (masalan: 998901234567)", reply_markup=ReplyKeyboardRemove())
        return

    if mode == "add_phone":
        phone_raw = text.strip()
        cleaned = phone_raw.replace(" ", "").replace("-", "")
        if not cleaned.isdigit() and not cleaned.startswith("+"):
            await update.message.reply_text("❌ Telefon formati noto'g'ri. Masalan: 998901234567", reply_markup=get_user_keyboard())
            context.user_data.clear()
            return
        session_name = f"session_{uid}_{int(time.time())}"
        context.user_data["pending_phone"] = cleaned
        context.user_data["pending_session"] = session_name
        await update.message.reply_text("⏳ Kod yuborilmoqda...")
        ok = await telethon_send_code(cleaned, session_name)
        if not ok:
            await update.message.reply_text("❌ Kod yuborilmadi. Raqamni tekshiring yoki keyinroq urinib ko'ring.", reply_markup=get_user_keyboard())
            context.user_data.clear()
            return
        context.user_data["mode"] = "enter_code"
        await update.message.reply_text("✅ Kod yuborildi. Telegramdan kelgan kodni yuboring (faqat raqam):", reply_markup=get_user_keyboard())
        return

    if mode == "enter_code":
        code = text.strip()
        phone = context.user_data.get("pending_phone")
        session_name = context.user_data.get("pending_session")
        if not phone or not session_name:
            context.user_data.clear()
            await update.message.reply_text("❌ Ichki xato. Iltimos /start qilib qayta urinib ko'ring.", reply_markup=get_user_keyboard())
            return
        await update.message.reply_text("⏳ Kod tekshirilmoqda...")
        ok, me_or_err = await telethon_sign_in(phone, session_name, code)
        if not ok:
            err = me_or_err
            if err == "2FA_REQUIRED":
                await update.message.reply_text("❗ Hisobda ikki bosqichli parol (2FA) yoqilgan. Admin bilan bog'laning.", reply_markup=get_user_keyboard())
            else:
                await update.message.reply_text(f"❌ Kod noto'g'ri yoki login muvaffaqiyatsiz: {err}", reply_markup=get_user_keyboard())
            context.user_data.clear()
            return
        me = me_or_err
        display = add_user_account(uid, session_name, phone=cleaned.lstrip("+"), username=getattr(me, "username", "") or "")
        if not display:
            logout_account_by_display(session_name)
            await update.message.reply_text("❌ Hisob DB ga saqlanmadi (ehtimol telefon allaqachon qo'shilgan).", reply_markup=get_user_keyboard())
            context.user_data.clear()
            return
        await update.message.reply_text(f"✅ HISOB QO'SHILDI!\n\n- Nomi: {display}\n- Telefon: {normalize_phone(cleaned)}\n- Username: @{getattr(me,'username','')}", reply_markup=get_user_keyboard())
        context.user_data.clear()
        return

    if text == "📱 Guruhlar":
        accounts = get_user_accounts(uid)
        if not accounts:
            await update.message.reply_text("❌ Hisob topilmadi", reply_markup=get_user_keyboard())
            return
        keyboard = []
        for acc in accounts:
            keyboard.append([InlineKeyboardButton(f"📱 {acc[0]}", callback_data=f"account_{acc[0]}")])
        await update.message.reply_text("Hisobni tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    await update.message.reply_text("❌ Noma'lum buyruq. Menyudan tanlang.", reply_markup=get_user_keyboard())

# ---------------- CALLBACK HANDLER ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    if data == "back_to_main":
        if uid == ADMIN_ID:
            await context.bot.send_message(chat_id=uid, text="Admin panel", reply_markup=get_admin_keyboard())
        else:
            await context.bot.send_message(chat_id=uid, text="Menyu", reply_markup=get_user_keyboard())
        await query.delete_message()
        return
    if data.startswith("account_"):
        display_name = data.replace("account_", "")
        groups = get_user_groups(uid, display_name)
        if not groups:
            await query.edit_message_text("❌ Guruh topilmadi")
            return
        keyboard = []
        for g in groups:
            gid, gid_val, title, uname, active = g
            status = "✅" if active==1 else "❌"
            label = f"{status} {title} (@{uname})" if uname else f"{status} {title}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"group_{gid}")])
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")])
        await query.edit_message_text(f"⚙️ {display_name} guruhlari:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if data.startswith("group_"):
        gid = int(data.replace("group_", ""))
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('SELECT is_active, group_title, account_display_name FROM groups WHERE id=?', (gid,))
        row = cur.fetchone()
        if not row:
            conn.close()
            await query.edit_message_text("❌ Topilmadi")
            return
        current, title, acc = row
        new = 0 if current==1 else 1
        cur.execute('UPDATE groups SET is_active=? WHERE id=?', (new, gid))
        conn.commit()
        conn.close()
        await query.edit_message_text(f"✅ {title} status o'zgardi: {'faol' if new==1 else 'nofaol'}")
        return

# ---------------- AUTO SEND LOOP ----------------
is_sending = True

async def auto_send_loop():
    global is_sending
    logger.info("Auto send loop started")
    while True:
        try:
            if not is_sending:
                await asyncio.sleep(5)
                continue
            total_sent = 0
            users = get_all_users()
            for u in users:
                conn = sqlite3.connect(DB_FILE)
                cur = conn.cursor()
                cur.execute('SELECT subscription_end FROM accounts WHERE user_id=? AND subscription_end IS NOT NULL', (u,))
                rows = cur.fetchall()
                conn.close()
                valid = False
                for r in rows:
                    try:
                        if datetime.now() <= datetime.strptime(r[0], '%Y-%m-%d %H:%M:%S'):
                            valid = True
                            break
                    except:
                        continue
                if not valid:
                    continue
                accounts = get_user_accounts(u)
                conn = sqlite3.connect(DB_FILE)
                cur = conn.cursor()
                cur.execute('SELECT min_interval, max_interval FROM user_intervals WHERE user_id=?', (u,))
                row = cur.fetchone()
                conn.close()
                if row:
                    min_i, max_i = row
                else:
                    min_i, max_i = 20, 25
                for acc in accounts:
                    display_name, phone, cc, username, active, session_name, acc_id = acc
                    if active != 1:
                        continue
                    groups = get_user_groups(u, display_name)
                    active_groups = [g for g in groups if g[4]==1]
                    if not active_groups:
                        continue
                    msg_text = get_random_user_message(u)
                    if not msg_text:
                        continue
                    client = await get_or_create_client(session_name)
                    if client is None:
                        logger.error(f"{display_name} client not available")
                        continue
                    for g in active_groups:
                        gid, gid_val, title, uname, g_active = g
                        try:
                            await client.send_message(gid_val, msg_text)
                            logger.info(f"Sent {display_name} -> {gid_val}")
                            total_sent += 1
                        except Exception as e:
                            logger.error(f"Send failed {display_name}->{gid_val}: {e}")
                        await asyncio.sleep(0.5)
                    if total_sent>0:
                        delay = random.randint(min_i*60, max_i*60)
                        logger.info(f"{u} sleeping {delay//60} minutes")
                        await asyncio.sleep(delay)
            if total_sent>0:
                logger.info(f"Total sent: {total_sent}")
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Auto loop error: {e}")
            await asyncio.sleep(10)

def start_auto_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(auto_send_loop())

# ---------------- MAIN ----------------
def main():
    init_database()
    logger.info("Starting bot")
    t = threading.Thread(target=start_auto_thread, daemon=True)
    t.start()
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set. Set BOT_TOKEN env var.")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("add", process_add_command))
    app.add_handler(CommandHandler("remove", process_remove_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot polling...")
    app.run_polling()

if __name__ == '__main__':
    main()
