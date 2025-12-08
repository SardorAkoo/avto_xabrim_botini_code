import asyncio
import random
import logging
import sqlite3
import os
import threading
import time
import re
from datetime import datetime
from pyrogram import Client
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging yoqish
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

ADMIN_ID = 2091226701
BOT_TOKEN = "8220522354:AAHk8lxrgvQAapNvV7u4-d-9A0zPT6ki5oI"

# SQLite bazasi
DB_FILE = "telegram_bot.db"

# Pyrogram API ma'lumotlari
API_ID = 16307694
API_HASH = "de4b653676e085ce3d0f3d64f8741ae4"

# Global o'zgaruvchilar
clients = {}
current_msg_index = {}
is_sending = False
last_send_time = None
min_interval = 20  # Minimal interval (daqiqa)
max_interval = 25  # Maksimal interval (daqiqa)
random_messages = True  # Random xabarlarni yuborish

def init_database():
    """Bazani yaratish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Accounts jadvali
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        display_name TEXT UNIQUE,
        session_name TEXT UNIQUE, 
        phone TEXT UNIQUE,
        country_code TEXT,
        username TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Groups jadvali
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_display_name TEXT,
        group_id TEXT,
        group_title TEXT,
        group_username TEXT,
        is_active INTEGER DEFAULT 1,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_display_name, group_id)
    )''')
    
    # Messages jadvali
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Settings jadvali (yangi)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    conn.close()
    logging.info("✅ Baza yaratildi")

def save_setting(key, value):
    """Setting saqlash"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO settings (key, value) 
        VALUES (?, ?)
    ''', (key, value))
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    """Setting olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else default

def get_next_account_number():
    """Keyingi account raqamini olish (account1, account2, ...)"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT display_name FROM accounts WHERE display_name LIKE "account%"')
    accounts = cursor.fetchall()
    conn.close()
    
    if not accounts:
        return 1
    
    numbers = []
    for acc in accounts:
        try:
            num = int(acc[0].replace("account", ""))
            numbers.append(num)
        except:
            continue
    
    if numbers:
        return max(numbers) + 1
    else:
        return 1

def add_account(session_name, phone="", country_code="", username=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # Yangi display_name yaratish
        account_number = get_next_account_number()
        display_name = f"account{account_number}"
        
        cursor.execute('''
            INSERT INTO accounts (display_name, session_name, phone, country_code, username, is_active) 
            VALUES (?, ?, ?, ?, ?, 1)
        ''', (display_name, session_name, phone, country_code, username))
        conn.commit()
        return display_name
    except sqlite3.IntegrityError as e:
        logging.error(f"Bazaga qo'shishda xato: {e}")
        return None
    finally:
        conn.close()

def get_all_accounts():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT display_name, phone, country_code, username, is_active FROM accounts ORDER BY display_name')
    accounts = cursor.fetchall()
    conn.close()
    return accounts

def get_active_accounts():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT display_name, phone, country_code, username FROM accounts WHERE is_active = 1 ORDER BY display_name')
    accounts = cursor.fetchall()
    conn.close()
    return accounts

def get_account_by_display_name(display_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM accounts WHERE display_name = ?', (display_name,))
    account = cursor.fetchone()
    conn.close()
    return account

def add_group(account_display_name, group_id, group_title="", group_username=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO groups (account_display_name, group_id, group_title, group_username, is_active) 
            VALUES (?, ?, ?, ?, 1)
        ''', (account_display_name, group_id, group_title, group_username))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Guruh qo'shishda xato: {e}")
        return False
    finally:
        conn.close()

def get_active_groups_by_account(account_display_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT group_id, group_title, group_username 
        FROM groups 
        WHERE account_display_name = ? AND is_active = 1 
        ORDER BY group_title
    ''', (account_display_name,))
    groups = cursor.fetchall()
    conn.close()
    return groups

def get_all_groups_by_account(account_display_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT group_id, group_title, group_username, is_active 
        FROM groups 
        WHERE account_display_name = ? 
        ORDER BY group_title
    ''', (account_display_name,))
    groups = cursor.fetchall()
    conn.close()
    return groups

def update_group_status(account_display_name, group_id, is_active):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE groups 
        SET is_active = ? 
        WHERE account_display_name = ? AND group_id = ?
    ''', (is_active, account_display_name, group_id))
    conn.commit()
    conn.close()

def add_message(text):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO messages (text) VALUES (?)', (text,))
    conn.commit()
    conn.close()

def get_all_messages():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM messages ORDER BY id')
    messages = cursor.fetchall()
    conn.close()
    return messages

def get_random_message():
    """Random xabar olish"""
    messages = get_all_messages()
    if not messages:
        return None
    return random.choice(messages)[1]

def update_account_status(display_name, is_active):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE accounts SET is_active = ? WHERE display_name = ?', (is_active, display_name))
    conn.commit()
    conn.close()

def parse_phone_number(phone_input):
    """Telefon raqamini parse qilish"""
    # Avval + ni tekshirish
    if phone_input.startswith('+'):
        # + ni olib tashlash va faqat raqamlarni olish
        phone = re.sub(r'\D', '', phone_input[1:])
    else:
        # Faqat raqamlarni olish
        phone = re.sub(r'\D', '', phone_input)
    
    # Davlat kodlarini aniqlash
    country_codes = {
        '998': 'UZ', '7': 'RU', '77': 'KZ', '993': 'TM',
        '992': 'TJ', '996': 'KG', '380': 'UA', '375': 'BY',
        '90': 'TR', '98': 'IR', '971': 'AE', '966': 'SA',
        '20': 'EG', '91': 'IN', '86': 'CN', '81': 'JP',
        '82': 'KR', '65': 'SG', '60': 'MY', '62': 'ID',
        '63': 'PH', '66': 'TH', '84': 'VN', '1': 'US',
        '44': 'GB', '33': 'FR', '49': 'DE', '39': 'IT',
        '34': 'ES', '55': 'BR', '54': 'AR', '52': 'MX',
    }
    
    # Davlat kodini aniqlash
    country_code = ""
    country_flag = ""
    country_name = ""
    
    # Kodlarni uzunlik bo'yicha saralash (uzundan qisqaga)
    sorted_codes = sorted(country_codes.items(), key=lambda x: len(x[0]), reverse=True)
    
    # Flag emoji uchun
    flag_map = {
        'UZ': "🇺🇿", 'RU': "🇷🇺", 'KZ': "🇰🇿", 'TM': "🇹🇲",
        'TJ': "🇹🇯", 'KG': "🇰🇬", 'UA': "🇺🇦", 'BY': "🇧🇾",
        'TR': "🇹🇷", 'IR': "🇮🇷", 'AE': "🇦🇪", 'SA': "🇸🇦",
        'EG': "🇪🇬", 'IN': "🇮🇳", 'CN': "🇨🇳", 'JP': "🇯🇵",
        'KR': "🇰🇷", 'SG': "🇸🇬", 'MY': "🇲🇾", 'ID': "🇮🇩",
        'PH': "🇵🇭", 'TH': "🇹🇭", 'VN': "🇻🇳", 'US': "🇺🇸",
        'GB': "🇬🇧", 'FR': "🇫🇷", 'DE': "🇩🇪", 'IT': "🇮🇹",
        'ES': "🇪🇸", 'BR': "🇧🇷", 'AR': "🇦🇷", 'MX': "🇲🇽"
    }
    
    # Barcha kodlarni tekshirish
    for code, country in sorted_codes:
        if phone.startswith(code):
            country_code = code
            country_name = country
            break
    
    # Agar hali ham davlat kodi topilmasa
    if not country_code and len(phone) >= 3:
        country_code = phone[:3]
        country_name = "Noma'lum"
    elif not country_code and len(phone) > 0:
        country_code = phone[:min(3, len(phone))]
        country_name = "Noma'lum"
    
    # Flag aniqlash
    country_flag = flag_map.get(country_name, "🌍")
    
    return phone, country_code, country_flag, country_name

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Siz admin emassiz!")
        return
    
    # Bazadan sozlamalarni olish
    global min_interval, max_interval, random_messages
    min_interval = int(get_setting('min_interval', '20'))
    max_interval = int(get_setting('max_interval', '25'))
    random_messages = get_setting('random_messages', 'true').lower() == 'true'
    
    menu = ReplyKeyboardMarkup([
        ["➕ Hisob qo'shish", "📤 Xabar qo'shish"],
        ["🔗 Guruh qo'shish", "👥 Guruhlarni ko'rish"],
        ["▶️ Boshlash", "⏹️ To'xtatish"],
        ["📋 Hisoblar", "📝 Xabarlar", "📊 Statistika"],
        ["⚙️ Interval", "🎲 Random", "🗑️ Hisob o'chirish"],
        ["🌍 Davlatlar", "⚙️ Sozlamalar"]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        f"🤖 **Telegram Auto-Poster Bot**\n\n"
        f"🌍 **Barcha mamlakatlar uchun!**\n\n"
        f"**Joriy sozlamalar:**\n"
        f"• Interval: {min_interval}-{max_interval} daqiqa\n"
        f"• Random xabar: {'✅ Yoqilgan' if random_messages else '❌ O\'chirilgan'}\n\n"
        f"**Foydalanish:**\n"
        "1. ➕ **Hisob qo'shish** - Telefon raqam orqali\n"
        "2. 📤 **Xabar qo'shish** - Xabarlar ro'yxatini to'ldirish\n"
        "3. 🔗 **Guruh qo'shish** - account1, account2 kabi nomlar bilan\n"
        "4. 👥 **Guruhlarni ko'rish** - Har bir hisobdagi guruhlarni ko'rish\n"
        "5. ▶️ **Boshlash** - Avtomatik yuborishni boshlash\n\n"
        f"⚠️ **Diqqat:** Hisob nomlari **account1, account2, account3** kabi bo'ladi",
        reply_markup=menu
    )

async def show_accounts_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hisoblar ro'yxatini chiroyli ko'rsatish"""
    accounts = get_all_accounts()
    if not accounts:
        await update.message.reply_text("📭 **Hech qanday hisob yo'q!**\n\n"
                                       "Hisob qo'shish uchun ➕ **Hisob qo'shish** tugmasini bosing.")
        return
    
    msg = "📋 **HISOBLAR RO'YXATI**\n\n"
    
    for i, acc in enumerate(accounts, 1):
        display_name = acc[0]
        phone = acc[1] if acc[1] else "Noma'lum"
        country_code = acc[2] if acc[2] else "???"
        username = acc[3] if acc[3] else "Noma'lum"
        is_active = acc[4]
        
        # Flagni aniqlash
        flag = "🌍"
        if country_code == '998': flag = "🇺🇿"
        elif country_code == '7': flag = "🇷🇺"
        elif country_code == '77': flag = "🇰🇿"
        elif country_code == '993': flag = "🇹🇲"
        elif country_code == '992': flag = "🇹🇯"
        elif country_code == '996': flag = "🇰🇬"
        elif country_code == '90': flag = "🇹🇷"
        elif country_code == '98': flag = "🇮🇷"
        
        groups = get_active_groups_by_account(display_name)
        total_groups = get_all_groups_by_account(display_name)
        
        status = "✅ Faol" if is_active else "❌ Nofaol"
        
        msg += f"{i}. **{flag} {display_name}**\n"
        msg += f"   👤: @{username}\n"
        msg += f"   📞: +{phone}\n"
        msg += f"   📊: {len(groups)}/{len(total_groups)} ta guruh\n"
        msg += f"   🔄: {status}\n\n"
    
    msg += "➖➖➖➖➖➖➖➖➖➖\n"
    msg += "📌 **Format:** {faol guruhlar}/{jami guruhlar}\n"
    msg += "Guruh qo'shish uchun: **account1**, **account2** kabi nomlarni yozing"
    
    await update.message.reply_text(msg)

async def show_groups_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruhlarni ko'rish"""
    accounts = get_all_accounts()
    if not accounts:
        await update.message.reply_text("❌ **Hech qanday hisob yo'q!**\n\n"
                                       "Avval hisob qo'shing ➕ **Hisob qo'shish**")
        return
    
    msg = "👥 **GURUHLAR RO'YXATI**\n\n"
    
    for account in accounts:
        display_name = account[0]
        phone = account[1]
        country_code = account[2] if account[2] else "???"
        is_active_account = account[4]
        
        if not is_active_account:
            continue
            
        flag = "🌍"
        if country_code == '998': flag = "🇺🇿"
        elif country_code == '7': flag = "🇷🇺"
        elif country_code == '77': flag = "🇰🇿"
        elif country_code == '993': flag = "🇹🇲"
        elif country_code == '992': flag = "🇹🇯"
        elif country_code == '996': flag = "🇰🇬"
        elif country_code == '90': flag = "🇹🇷"
        elif country_code == '98': flag = "🇮🇷"
        
        groups = get_all_groups_by_account(display_name)
        
        # Faol guruhlar soni
        active_groups = sum(1 for g in groups if g[3] == 1)
        
        msg += f"**{flag} {display_name}** (📞 +{phone})\n"
        msg += f"🔗 **Guruhlar: {active_groups}/{len(groups)}**\n"
        
        if groups:
            for i, group in enumerate(groups, 1):
                group_id = group[0]
                group_title = group[1] if group[1] else "Noma'lum nom"
                group_username = group[2] if group[2] else ""
                group_active = "✅" if group[3] == 1 else "❌"
                
                if i <= 8:  # Faqat birinchi 8 ta guruhni ko'rsatish
                    if group_username:
                        msg += f"  {group_active} {i}. {group_title} (@{group_username})\n"
                    else:
                        msg += f"  {group_active} {i}. {group_title} (ID: {group_id})\n"
            
            if len(groups) > 8:
                msg += f"  ... va yana {len(groups) - 8} ta guruh\n"
        else:
            msg += "  📭 Guruh yo'q\n"
        msg += "\n"
    
    msg += "➖➖➖➖➖➖➖➖➖➖\n"
    msg += "✅ - avtomatik xabar yuborish yoqilgan\n"
    msg += "❌ - avtomatik xabar yuborish o'chirilgan"
    
    await update.message.reply_text(msg)

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Statistika ko'rsatish"""
    global is_sending, last_send_time, min_interval, max_interval, random_messages
    
    accounts = get_all_accounts()
    active_accounts = get_active_accounts()
    total_groups = 0
    active_groups = 0
    country_stats = {}
    
    for acc in accounts:
        display_name = acc[0]
        groups = get_all_groups_by_account(display_name)
        total_groups += len(groups)
        active_groups += sum(1 for g in groups if g[3] == 1)
        country_code = acc[2] if acc[2] else "Noma'lum"
        country_stats[country_code] = country_stats.get(country_code, 0) + 1
    
    total_messages = len(get_all_messages())
    total_active_accounts = len(active_accounts)
    
    status_text = "✅ Faol" if is_sending else "❌ Nofaol"
    
    msg = f"📊 **STATISTIKA**\n\n"
    msg += f"• **Hisoblar:** {len(accounts)} ta ({total_active_accounts} ta faol)\n"
    msg += f"• **Guruhlar:** {active_groups}/{total_groups} ta\n"
    msg += f"• **Xabarlar:** {total_messages} ta\n"
    msg += f"• **Holat:** {status_text}\n"
    msg += f"• **Interval:** {min_interval}-{max_interval} daqiqa\n"
    msg += f"• **Random xabar:** {'✅ Yoqilgan' if random_messages else '❌ O\'chirilgan'}\n"
    
    if last_send_time:
        msg += f"• **Oxirgi yuborish:** {last_send_time}\n"
    
    msg += "\n📋 **Hisoblar bo'yicha:**\n"
    
    for acc in accounts:
        display_name = acc[0]
        groups = get_all_groups_by_account(display_name)
        active_count = sum(1 for g in groups if g[3] == 1)
        msg += f"• **{display_name}:** {active_count}/{len(groups)} ta guruh\n"
    
    msg += "\n🌍 **Davlatlar bo'yicha:**\n"
    
    for country, count in country_stats.items():
        flag = ""
        if country == '998': flag = "🇺🇿"
        elif country == '7': flag = "🇷🇺"
        elif country == '77': flag = "🇰🇿"
        elif country == '993': flag = "🇹🇲"
        elif country == '992': flag = "🇹🇯"
        elif country == '996': flag = "🇰🇬"
        elif country == '90': flag = "🇹🇷"
        elif country == '98': flag = "🇮🇷"
        elif country == '1': flag = "🇺🇸"
        elif country == '44': flag = "🇬🇧"
        elif country == '49': flag = "🇩🇪"
        elif country == '86': flag = "🇨🇳"
        elif country == '91': flag = "🇮🇳"
        elif country == '81': flag = "🇯🇵"
        elif country == '82': flag = "🇰🇷"
        else: flag = "🌍"
        
        msg += f"{flag} +{country}: {count} ta\n"
    
    await update.message.reply_text(msg)

async def show_messages_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarlar ro'yxatini ko'rsatish"""
    messages = get_all_messages()
    if not messages:
        await update.message.reply_text("📭 **Hech qanday xabar yo'q!**\n\n"
                                       "Xabar qo'shish uchun 📤 **Xabar qo'shish** tugmasini bosing.")
        return
    
    msg = "📝 **XABARLAR RO'YXATI**\n\n"
    for i, m in enumerate(messages, 1):
        msg_text = m[1]
        if len(msg_text) > 50:
            msg_text = msg_text[:50] + "..."
        msg += f"{i}. {msg_text}\n\n"
    
    msg += f"\n📌 Jami: {len(messages)} ta xabar\n"
    msg += f"🎲 Random rejim: {'✅ Yoqilgan' if random_messages else '❌ O\'chirilgan'}"
    
    await update.message.reply_text(msg)

async def show_interval_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Interval sozlamalarini ko'rsatish"""
    global min_interval, max_interval
    
    await update.message.reply_text(
        f"⚙️ **INTERVAL SOZLAMALARI**\n\n"
        f"📊 **Joriy interval:** {min_interval}-{max_interval} daqiqa\n\n"
        f"📝 **Yangi intervalni quyidagi formatda yuboring:**\n"
        f"• **Min,Max** - masalan: 15,20\n"
        f"• **Faqat raqam** - masalan: 30 (min va max bir xil bo'ladi)\n\n"
        f"**Misollar:**\n"
        f"• 15,25 - 15-25 daqiqa oralig'i\n"
        f"• 10,30 - 10-30 daqiqa oralig'i\n"
        f"• 30 - 30-30 daqiqa (har 30 daqiqada)\n\n"
        f"⚠️ **Minimal qiymat:** 1 daqiqa\n"
        f"⚠️ **Maksimal qiymat:** 1440 daqiqa (24 soat)"
    )
    context.user_data["mode"] = "set_interval"

async def show_random_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Random sozlamalarini ko'rsatish"""
    global random_messages
    
    keyboard = [["✅ Random yoqish", "❌ Random o'chirish"], ["🔙 Orqaga"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"🎲 **RANDOM XABAR SOZLAMALARI**\n\n"
        f"📊 **Joriy holat:** {'✅ Yoqilgan' if random_messages else '❌ O\'chirilgan'}\n\n"
        f"**Random rejim:**\n"
        f"• ✅ **Random yoqilgan** - Har safar tasodifiy xabar yuboriladi\n"
        f"• ❌ **Random o'chirilgan** - Xabarlar ketma-ket yuboriladi\n\n"
        f"⚠️ **Eslatma:** Random yoqilgan bo'lsa, har safar xabarlar ro'yxatidan tasodifiy bitta xabar tanlanadi.",
        reply_markup=reply_markup
    )
    context.user_data["mode"] = "set_random"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_sending, last_send_time, min_interval, max_interval, random_messages
    
    if update.effective_user.id != ADMIN_ID:
        return
    
    text = update.message.text
    mode = context.user_data.get("mode")
    stage = context.user_data.get("stage")
    
    # MENU BOSHLARI
    if text == "➕ Hisob qo'shish":
        await update.message.reply_text(
            "📱 **Telefon raqamingizni yuboring:**\n\n"
            "**Qo'shimcha formatlar:**\n"
            "• +998901234567\n"
            "• +79001234567\n"
            "• +77011234567\n"
            "• +99365123456\n"
            "• +992901234567\n"
            "• +380501234567\n"
            "• +905312345678\n"
            "• +12015550123\n"
            "• +447911123456\n"
            "• +4915112345678\n\n"
            "Yoki faqat raqamlarni:\n"
            "998901234567\n"
            "79001234567\n"
            "905312345678\n"
            "12015550123\n"
            "447911123456\n"
            "4915112345678\n\n"
            "⚠️ **Har qanday davlat telefon raqamini qo'shishingiz mumkin!**"
        )
        context.user_data["mode"] = "add_account"
        context.user_data["stage"] = "phone"
    
    elif text == "📤 Xabar qo'shish":
        await update.message.reply_text("📝 Xabar matnini yuboring:")
        context.user_data["mode"] = "add_message"
    
    elif text == "🔗 Guruh qo'shish":
        accounts = get_all_accounts()
        if not accounts:
            await update.message.reply_text("❌ **Avval hisob qo'shing!**\n\n"
                                          "Birinchi ➕ **Hisob qo'shish** tugmasini bosing.")
            return
        
        # Hisoblar ro'yxatini ko'rsatish
        msg = "📱 **Qaysi hisobga guruh qo'shmoqchisiz?**\n\n"
        msg += "**Mavjud hisoblar:**\n"
        
        for acc in accounts:
            display_name = acc[0]
            phone = acc[1]
            country_code = acc[2] if acc[2] else "???"
            
            flag = "🌍"
            if country_code == '998': flag = "🇺🇿"
            elif country_code == '7': flag = "🇷🇺"
            elif country_code == '77': flag = "🇰🇿"
            elif country_code == '90': flag = "🇹🇷"
            
            msg += f"• **{flag} {display_name}** (📞 +{phone})\n"
        
        msg += "\n✍️ **Hisob nomini yozing:** (masalan: account1)"
        msg += "\n\n⚠️ **Faqat yuqoridagi nomlardan birini yozing**"
        
        await update.message.reply_text(msg)
        context.user_data["mode"] = "add_group_account"
    
    elif text == "👥 Guruhlarni ko'rish":
        await show_groups_list(update, context)
    
    elif text == "▶️ Boshlash":
        is_sending = True
        last_send_time = datetime.now().strftime("%H:%M:%S")
        
        # Barcha hisob va guruhlarni olish
        accounts = get_all_accounts()
        total_groups = 0
        active_groups = 0
        account_groups = {}
        
        for acc in accounts:
            display_name = acc[0]
            groups = get_all_groups_by_account(display_name)
            total_groups += len(groups)
            active_in_account = sum(1 for g in groups if g[3] == 1)
            active_groups += active_in_account
            account_groups[display_name] = f"{active_in_account}/{len(groups)}"
        
        # Qaysi guruhlarga yuborilayotganini ko'rsatish
        msg = "✅ **Avtomatik yuborish boshlandi!**\n\n"
        
        if active_groups > 0:
            msg += f"📊 **Jami:** {len(accounts)} ta hisob, {active_groups}/{total_groups} ta guruh\n\n"
            msg += "📋 **Hisoblar va faol guruhlar soni:**\n"
            
            for display_name, group_count in account_groups.items():
                msg += f"• **{display_name}:** {group_count} ta guruh\n"
            
            msg += f"\n🔄 **Har {min_interval}-{max_interval} daqiqa oralig'ida xabar yuboriladi**\n"
            msg += f"🎲 **Random rejim:** {'✅ Yoqilgan' if random_messages else '❌ O\'chirilgan'}\n"
            msg += f"⏰ **Oxirgi yuborish:** {last_send_time}\n"
            msg += "✅ **Faol hisoblardagi faol guruhlarga yuboriladi**"
        else:
            msg += "⚠️ **Diqqat:** Hech qanday faol guruh yo'q!\n\n"
            msg += "Guruh qo'shganingizda, u avtomatik ravishda faollashtiriladi.\n"
            msg += "Agar guruhni o'chirib qo'yish kerak bo'lsa, ⚙️ **Sozlamalar** bo'limidan foydalaning."
        
        await update.message.reply_text(msg)
    
    elif text == "⏹️ To'xtatish":
        is_sending = False
        await update.message.reply_text("⏹️ **Avtomatik yuborish to'xtatildi!**")
    
    elif text == "📋 Hisoblar":
        await show_accounts_list(update, context)
    
    elif text == "📝 Xabarlar":
        await show_messages_list(update, context)
    
    elif text == "📊 Statistika":
        await show_statistics(update, context)
    
    elif text == "🗑️ Hisob o'chirish":
        accounts = get_all_accounts()
        if not accounts:
            await update.message.reply_text("❌ **Hech qanday hisob yo'q!**")
            return
        
        msg = "🗑️ **O'CHIRISH UCHUN HISOBNI TANLANG**\n\n"
        
        for acc in accounts:
            display_name = acc[0]
            phone = acc[1]
            country_code = acc[2] if acc[2] else "???"
            
            flag = "🌍"
            if country_code == '998': flag = "🇺🇿"
            elif country_code == '7': flag = "🇷🇺"
            elif country_code == '90': flag = "🇹🇷"
            elif country_code == '98': flag = "🇮🇷"
            
            msg += f"• {flag} **{display_name}** (📞 +{phone})\n"
        
        msg += "\n✍️ **O'chirmoqchi bo'lgan hisob nomini yozing:** (masalan: account1)"
        msg += "\n\n⚠️ **Faqat yuqoridagi nomlardan birini yozing**"
        
        await update.message.reply_text(msg)
        context.user_data["mode"] = "delete_account"
    
    elif text == "🌍 Davlatlar":
        await update.message.reply_text(
            "🌍 **Qo'llab-quvvatlanadigan davlatlar:**\n\n"
            "🇺🇿 **O'zbekiston:** +998\n"
            "🇷🇺 **Rossiya:** +7\n"
            "🇰🇿 **Qozog'iston:** +7, +77\n"
            "🇹🇲 **Turkmaniston:** +993\n"
            "🇹🇯 **Tojikiston:** +992\n"
            "🇰🇬 **Qirg'iziston:** +996\n"
            "🇺🇦 **Ukraina:** +380\n"
            "🇧🇾 **Belarus:** +375\n"
            "🇹🇷 **Turkiya:** +90\n"
            "🇮🇷 **Eron:** +98\n"
            "🇦🇪 **BAA:** +971\n"
            "🇸🇦 **Saudiya Arabistoni:** +966\n"
            "🇪🇬 **Misr:** +20\n"
            "🇮🇳 **Hindiston:** +91\n"
            "🇨🇳 **Xitoy:** +86\n"
            "🇺🇸 **AQSh/Kanada:** +1\n"
            "🇬🇧 **Buyuk Britaniya:** +44\n"
            "🇩🇪 **Germaniya:** +49\n"
            "🇫🇷 **Fransiya:** +33\n"
            "🇮🇹 **Italiya:** +39\n"
            "🇪🇸 **Ispaniya:** +34\n"
            "🇦🇺 **Avstraliya:** +61\n"
            "🇯🇵 **Yaponiya:** +81\n"
            "🇰🇷 **Koreya:** +82\n"
            "🇸🇬 **Singapur:** +65\n"
            "🇲🇾 **Malayziya:** +60\n"
            "🇮🇩 **Indoneziya:** +62\n"
            "🇵🇭 **Filippin:** +63\n"
            "🇹🇭 **Tailand:** +66\n"
            "🇻🇳 **Vyetnam:** +84\n"
            "🇧🇷 **Braziliya:** +55\n"
            "🇦🇷 **Argentina:** +54\n"
            "🇲🇽 **Meksika:** +52\n\n"
            "🌍 **va boshqa 200+ davlat...**\n\n"
            "✅ **Har qanday davlat telefon raqamini qo'shishingiz mumkin!**"
        )
    
    elif text == "⚙️ Sozlamalar":
        accounts = get_all_accounts()
        if not accounts:
            await update.message.reply_text("❌ **Hech qanday hisob yo'q!**")
            return
        
        msg = "⚙️ **GURUH SOZLAMALARI**\n\n"
        msg += "**Mavjud hisoblar:**\n"
        
        for acc in accounts:
            display_name = acc[0]
            phone = acc[1]
            
            flag = "🌍"
            if acc[2] == '998': flag = "🇺🇿"
            elif acc[2] == '7': flag = "🇷🇺"
            
            msg += f"• {flag} **{display_name}** (📞 +{phone})\n"
        
        msg += "\n✍️ **Hisob nomini yozing:** (masalan: account1)"
        msg += "\n\n⚠️ **Faqat yuqoridagi nomlardan birini yozing**"
        
        await update.message.reply_text(msg)
        context.user_data["mode"] = "settings_account"
    
    elif text == "⚙️ Interval":
        await show_interval_settings(update, context)
    
    elif text == "🎲 Random":
        await show_random_settings(update, context)
    
    elif text in ["✅ Random yoqish", "❌ Random o'chirish"] and mode == "set_random":
        random_messages = (text == "✅ Random yoqish")
        save_setting('random_messages', 'true' if random_messages else 'false')
        
        await update.message.reply_text(
            f"✅ **Random rejim {'yoqildi' if random_messages else 'o\'chirildi'}!**\n\n"
            f"🎲 **Holat:** {'✅ Random yoqilgan' if random_messages else '❌ Random o\'chirilgan'}\n\n"
            f"⚠️ **Eslatma:** Random rejim yoqilgan bo'lsa, har safar xabarlar ro'yxatidan tasodifiy xabar tanlanadi."
        )
        
        # Asosiy menyuga qaytish
        menu = ReplyKeyboardMarkup([
            ["➕ Hisob qo'shish", "📤 Xabar qo'shish"],
            ["🔗 Guruh qo'shish", "👥 Guruhlarni ko'rish"],
            ["▶️ Boshlash", "⏹️ To'xtatish"],
            ["📋 Hisoblar", "📝 Xabarlar", "📊 Statistika"],
            ["⚙️ Interval", "🎲 Random", "🗑️ Hisob o'chirish"],
            ["🌍 Davlatlar", "⚙️ Sozlamalar"]
        ], resize_keyboard=True)
        
        await update.message.reply_text("📊 **Asosiy menyuga qaytdingiz**", reply_markup=menu)
        context.user_data["mode"] = None
    
    elif text == "🔙 Orqaga" and mode == "set_random":
        # Asosiy menyuga qaytish
        menu = ReplyKeyboardMarkup([
            ["➕ Hisob qo'shish", "📤 Xabar qo'shish"],
            ["🔗 Guruh qo'shish", "👥 Guruhlarni ko'rish"],
            ["▶️ Boshlash", "⏹️ To'xtatish"],
            ["📋 Hisoblar", "📝 Xabarlar", "📊 Statistika"],
            ["⚙️ Interval", "🎲 Random", "🗑️ Hisob o'chirish"],
            ["🌍 Davlatlar", "⚙️ Sozlamalar"]
        ], resize_keyboard=True)
        
        await update.message.reply_text("📊 **Asosiy menyuga qaytdingiz**", reply_markup=menu)
        context.user_data["mode"] = None
    
    else:
        # MODE: SET INTERVAL
        if mode == "set_interval":
            try:
                if ',' in text:
                    parts = text.split(',')
                    if len(parts) != 2:
                        raise ValueError
                    
                    new_min = int(parts[0].strip())
                    new_max = int(parts[1].strip())
                else:
                    new_min = int(text.strip())
                    new_max = new_min
                
                # Validatsiya
                if new_min < 1 or new_max < 1:
                    await update.message.reply_text("❌ **Interval 1 daqiqadan kam bo'lishi mumkin emas!**")
                    return
                
                if new_min > 1440 or new_max > 1440:
                    await update.message.reply_text("❌ **Interval 1440 daqiqadan (24 soat) ko'p bo'lishi mumkin emas!**")
                    return
                
                if new_min > new_max:
                    new_min, new_max = new_max, new_min
                
                min_interval = new_min
                max_interval = new_max
                
                # Bazaga saqlash
                save_setting('min_interval', str(min_interval))
                save_setting('max_interval', str(max_interval))
                
                await update.message.reply_text(
                    f"✅ **Interval muvaffaqiyatli o'zgartirildi!**\n\n"
                    f"📊 **Yangi interval:** {min_interval}-{max_interval} daqiqa\n\n"
                    f"🔄 **Xabar yuborish oralig'i:** {min_interval} dan {max_interval} daqiqagacha\n"
                    f"⏱️ **Har safar tasodifiy vaqt oralig'ida yuboriladi**"
                )
                
                # Asosiy menyuga qaytish
                menu = ReplyKeyboardMarkup([
                    ["➕ Hisob qo'shish", "📤 Xabar qo'shish"],
                    ["🔗 Guruh qo'shish", "👥 Guruhlarni ko'rish"],
                    ["▶️ Boshlash", "⏹️ To'xtatish"],
                    ["📋 Hisoblar", "📝 Xabarlar", "📊 Statistika"],
                    ["⚙️ Interval", "🎲 Random", "🗑️ Hisob o'chirish"],
                    ["🌍 Davlatlar", "⚙️ Sozlamalar"]
                ], resize_keyboard=True)
                
                await update.message.reply_text("📊 **Asosiy menyuga qaytdingiz**", reply_markup=menu)
                context.user_data["mode"] = None
                
            except ValueError:
                await update.message.reply_text(
                    "❌ **Noto'g'ri format!**\n\n"
                    "📝 **To'g'ri formatlar:**\n"
                    "• **Min,Max** - masalan: 15,20\n"
                    "• **Faqat raqam** - masalan: 30\n\n"
                    "**Misollar:**\n"
                    "• 15,25\n"
                    "• 10,30\n"
                    "• 30"
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Xatolik: {str(e)}")
        
        # MODE: ADD ACCOUNT
        elif mode == "add_account":
            if stage == "phone":
                # Telefon raqamini parse qilish
                phone, country_code, country_flag, country_name = parse_phone_number(text)
                
                # Telefon raqamini validatsiya qilish
                if not phone or len(phone) < 8:
                    await update.message.reply_text(
                        "❌ **Noto'g'ri telefon raqami!**\n\n"
                        "**Iltimos, quyidagilarni tekshiring:**\n"
                        "1. Raqam to'g'ri kiritilganligi\n"
                        "2. Davlat kodi to'g'ri ekanligi\n"
                        "3. Raqam Telegramda ro'yxatdan o'tganligi\n\n"
                        "**To'g'ri formatlar:**\n"
                        "• +998901234567\n"
                        "• 998901234567\n"
                        "• 901234567\n"
                        "• +905312345678\n"
                        "• 905312345678\n\n"
                        "⚠️ **Siz xato raqam kiritdingiz!**"
                    )
                    return
                
                context.user_data["phone"] = phone
                context.user_data["country_code"] = country_code
                context.user_data["country_flag"] = country_flag
                context.user_data["country_name"] = country_name
                
                # Session nomini yaratish
                timestamp = int(time.time())
                session_name = f"account_{country_code}_{timestamp}"
                context.user_data["session_name"] = session_name
                
                try:
                    # Pyrogram client yaratish
                    client = Client(
                        name=session_name,
                        api_id=API_ID,
                        api_hash=API_HASH,
                        phone_number=f"+{phone}",
                        in_memory=True
                    )
                    
                    # Kody so'rash uchun connect qilish
                    await client.connect()
                    
                    # Kody so'rash
                    sent_code = await client.send_code(f"+{phone}")
                    context.user_data["phone_code_hash"] = sent_code.phone_code_hash
                    context.user_data["client"] = client
                    
                    await update.message.reply_text(
                        f"{country_flag} **Telefon raqam:** +{phone}\n"
                        f"🏳️ **Davlat:** {country_name} (+{country_code})\n\n"
                        f"📩 Telegramdan kelgan **kodni** yuboring:"
                    )
                    context.user_data["stage"] = "code"
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    if "phone_number_invalid" in error_msg:
                        await update.message.reply_text(
                            "❌ **Noto'g'ri telefon raqami!**\n\n"
                            "⚠️ **Siz xato raqam kiritdingiz!**\n\n"
                            "Iltimos, quyidagilarni tekshiring:\n"
                            "1. Raqam to'g'ri kiritilganligi\n"
                        )
                    elif "phone_number_banned" in error_msg:
                        await update.message.reply_text(
                            "❌ **Telefon raqam bloklangan!**\n\n"
                            "Bu raqam Telegram tomonidan bloklangan."
                        )
                    else:
                        await update.message.reply_text(f"❌ Xatolik: {str(e)[:200]}")
                    context.user_data.clear()
            
            elif stage == "code":
                code = text.strip()
                phone = context.user_data.get("phone")
                session_name = context.user_data.get("session_name")
                country_code = context.user_data.get("country_code", "")
                country_flag = context.user_data.get("country_flag", "🌍")
                phone_code_hash = context.user_data.get("phone_code_hash")
                client = context.user_data.get("client")
                
                try:
                    # Kody tekshirish
                    signed_in = await client.sign_in(
                        phone_number=f"+{phone}",
                        phone_code_hash=phone_code_hash,
                        phone_code=code
                    )
                    
                    # Agar user object qaytsa
                    if hasattr(signed_in, 'id'):
                        me = signed_in
                    else:
                        me = await client.get_me()
                    
                    display_name = add_account(session_name, phone, country_code, me.username or me.first_name)
                    
                    if display_name:
                        # Clientni disconnect qilish
                        await client.disconnect()
                        
                        # Yangi client yaratish (session bilan)
                        new_client = Client(
                            name=session_name,
                            api_id=API_ID,
                            api_hash=API_HASH
                        )
                        await new_client.start()
                        
                        clients[display_name] = new_client
                        current_msg_index[display_name] = 0
                        
                        await update.message.reply_text(
                            f"{country_flag} **✅ HISOB QO'SHILDI!**\n\n"
                            f"📛 **Nomi:** {display_name}\n"
                            f"👤 **Foydalanuvchi:** {me.username or me.first_name}\n"
                            f"📞 **Telefon:** +{phone}\n"
                            f"🏳️ **Davlat:** +{country_code}\n"
                            f"🆔 **Session:** {session_name}\n\n"
                            f"⚠️ **Eslatma:** Guruh qo'shishda **{display_name}** nomidan foydalaning"
                        )
                    else:
                        await update.message.reply_text("❌ Hisob allaqachon mavjud!")
                        await client.disconnect()
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    if "phone_code_invalid" in error_msg:
                        await update.message.reply_text("❌ Noto'g'ri kod! Qaytadan urinib ko'ring.")
                    elif "session_password_needed" in error_msg:
                        await update.message.reply_text("🔐 **2-qadamli autentifikatsiya kerak!**\n\nParolni yuboring:")
                        context.user_data["stage"] = "password"
                    elif "phone_code_expired" in error_msg:
                        await update.message.reply_text("❌ Kod muddati tugagan! Qaytadan boshlang.")
                        if client:
                            await client.disconnect()
                        context.user_data.clear()
                    else:
                        await update.message.reply_text(f"❌ Xatolik: {str(e)[:200]}")
                        if client:
                            await client.disconnect()
                
                finally:
                    if context.user_data.get("stage") != "password":
                        context.user_data.clear()
            
            elif stage == "password":
                password = text.strip()
                client = context.user_data.get("client")
                phone = context.user_data.get("phone")
                session_name = context.user_data.get("session_name")
                country_code = context.user_data.get("country_code", "")
                country_flag = context.user_data.get("country_flag", "🌍")
                
                try:
                    # Parolni tekshirish
                    await client.check_password(password)
                    
                    # Sign in qilish
                    await client.sign_in(password=password)
                    
                    me = await client.get_me()
                    display_name = add_account(session_name, phone, country_code, me.username or me.first_name)
                    
                    if display_name:
                        # Clientni disconnect qilish
                        await client.disconnect()
                        
                        # Yangi client yaratish
                        new_client = Client(
                            name=session_name,
                            api_id=API_ID,
                            api_hash=API_HASH
                        )
                        await new_client.start()
                        
                        clients[display_name] = new_client
                        current_msg_index[display_name] = 0
                        
                        await update.message.reply_text(
                            f"{country_flag} **✅ HISOB QO'SHILDI!**\n\n"
                            f"📛 **Nomi:** {display_name}\n"
                            f"👤 **Foydalanuvchi:** {me.username or me.first_name}\n"
                            f"📞 **Telefon:** +{phone}\n"
                            f"🏳️ **Davlat:** +{country_code}\n"
                            f"🆔 **Session:** {session_name}\n\n"
                            f"⚠️ **Eslatma:** Guruh qo'shishda **{display_name}** nomidan foydalaning"
                        )
                    else:
                        await update.message.reply_text("❌ Hisob allaqachon mavjud!")
                        await client.disconnect()
                    
                except Exception as e:
                    await update.message.reply_text(f"❌ Parol xato: {str(e)[:200]}")
                    if client:
                        await client.disconnect()
                
                finally:
                    context.user_data.clear()
        
        # MODE: ADD MESSAGE
        elif mode == "add_message":
            add_message(text)
            await update.message.reply_text(
                f"✅ **XABAR QO'SHILDI!**\n\n"
                f"📝 {text[:100]}{'...' if len(text) > 100 else ''}"
            )
            context.user_data["mode"] = None
        
        # MODE: ADD GROUP ACCOUNT
        elif mode == "add_group_account":
            input_name = text.strip().lower()
            accounts = get_all_accounts()
            
            selected_account = None
            for acc in accounts:
                display_name = acc[0].lower()
                if display_name == input_name:
                    selected_account = acc[0]
                    break
            
            if selected_account:
                context.user_data["selected_account"] = selected_account
                await update.message.reply_text(
                    f"✅ **{selected_account.upper()}** TANLANDI!\n\n"
                    f"📢 Endi guruh ID yoki linkini yuboring:\n\n"
                    f"**Qo'llash:**\n"
                    f"1. Guruh ID: -100xxxxxxxxxx\n"
                    f"2. Guruh linki: @guruh_nomi\n"
                    f"3. To'liq link: https://t.me/guruh_nomi\n\n"
                    f"**MISOL:**\n"
                    f"• @python_group\n"
                    f"• https://t.me/ai_chat\n"
                    f"• -1001234567890\n\n"
                    f"⚠️ **Diqqat:** Guruhda hisob a'zo bo'lishi kerak! Agar a'zo bo'lmasa, xatolik xabarini olasiz."
                )
                context.user_data["mode"] = "add_group_id"
            else:
                msg = "❌ **Bu hisob topilmadi!**\n\n"
                msg += "**Mavjud hisoblar:**\n"
                
                for acc in accounts:
                    display_name = acc[0]
                    phone = acc[1]
                    country_code = acc[2] if acc[2] else "???"
                    
                    flag = "🌍"
                    if country_code == '998': flag = "🇺🇿"
                    elif country_code == '7': flag = "🇷🇺"
                    
                    msg += f"• **{flag} {display_name}** (📞 +{phone})\n"
                
                msg += "\n✍️ **Yuqoridagi nomlardan birini yozing:**"
                await update.message.reply_text(msg)
        
        # MODE: ADD GROUP ID
        elif mode == "add_group_id":
            group_input = text.strip()
            account_display_name = context.user_data.get("selected_account", "")
            
            if account_display_name:
                try:
                    account_info = get_account_by_display_name(account_display_name)
                    if not account_info:
                        await update.message.reply_text("❌ Hisob topilmadi!")
                        return
                    
                    session_name = account_info[2]
                    
                    # Agar client yo'q bo'lsa, yaratish
                    if account_display_name not in clients:
                        try:
                            client = Client(
                                name=session_name,
                                api_id=API_ID,
                                api_hash=API_HASH
                            )
                            await client.start()
                            clients[account_display_name] = client
                        except Exception as e:
                            await update.message.reply_text(f"❌ Client yaratishda xatolik: {e}")
                            return
                    
                    client = clients[account_display_name]
                    
                    # Guruh ma'lumotlarini olish
                    chat = None
                    try:
                        if group_input.startswith('@'):
                            chat = await client.get_chat(group_input)
                        elif group_input.startswith('https://t.me/'):
                            username = group_input.split('/')[-1]
                            if username.startswith('+'):
                                chat = await client.get_chat(username)
                            else:
                                chat = await client.get_chat(f"@{username}")
                        elif group_input.startswith('-100'):
                            chat = await client.get_chat(int(group_input))
                        else:
                            # Oddiy username (atsiz yoki at bilan)
                            if group_input.startswith('+'):
                                chat = await client.get_chat(group_input)
                            else:
                                chat = await client.get_chat(f"@{group_input}")
                        
                        # Guruh nomi va username ni olish
                        group_title = chat.title
                        group_username = chat.username if hasattr(chat, 'username') else ""
                        
                        # Bazaga qo'shish (avtomatik faollashtiriladi)
                        if add_group(account_display_name, str(chat.id), group_title, group_username):
                            await update.message.reply_text(
                                f"✅ **GURUH QO'SHILDI VA FAOLLASHTIRILDI!**\n\n"
                                f"📱 **Hisob:** {account_display_name}\n"
                                f"📢 **Guruh:** {group_title}\n"
                                f"🆔 **ID:** {chat.id}\n"
                                f"🔗 **Link:** @{group_username if group_username else 'ochiq emas'}\n"
                                f"✅ **Status:** Faol (avtomatik xabar yuborish yoqilgan)\n\n"
                                f"⚠️ **O'chirish uchun:** ⚙️ Sozlamalar bo'limiga o'ting"
                            )
                        else:
                            await update.message.reply_text(
                                "⚠️ **Bu guruh allaqachon qo'shilgan yoki xatolik yuz berdi!**"
                            )
                            
                    except Exception as e:
                        logging.error(f"Guruhni olishda xatolik: {e}")
                        error_msg = str(e)
                        
                        # A'zo emaslik xatosini aniqlash
                        if "USER_NOT_PARTICIPANT" in error_msg or "CHANNEL_PRIVATE" in error_msg or "CHANNEL_INVALID" in error_msg:
                            await update.message.reply_text(
                                f"❌ **Xatolik: Hisob guruhda a'zo emas!**\n\n"
                                f"📱 **Hisob:** {account_display_name}\n"
                                f"📢 **Guruh:** {group_input}\n\n"
                                f"⚠️ **Sabab:** Ushbu hisob guruhda a'zo emas yoki guruh yopiq\n\n"
                                f"**Qanday qilish kerak:**\n"
                                f"1. Guruhga {account_display_name} hisobi bilan qo'shiling\n"
                                f"2. Yoki boshqa guruh linkini yuboring\n"
                                f"3. Yoki guruh ochiq ekanligiga ishonch hosil qiling"
                            )
                        elif "Username not found" in error_msg or "CHANNEL_PRIVATE" in error_msg:
                            await update.message.reply_text(
                                f"❌ **Guruh topilmadi!**\n\n"
                                f"Sabab: Guruh mavjud emas yoki yopiq\n\n"
                                f"**Tekshirish:**\n"
                                f"1. Guruh linki to'g'ri yozilganligi\n"
                                f"2. Guruh mavjudligi\n"
                                f"3. Guruh ochiq ekanligi\n\n"
                                f"**Qayta urinib ko'ring:**\n"
                                f"• @guruh_nomi\n"
                                f"• https://t.me/guruh_nomi"
                            )
                        elif "FloodWait" in error_msg:
                            await update.message.reply_text(
                                "⏳ **Telegram limiti!**\n\n"
                                "Bir necha daqiqadan keyin qayta urinib ko'ring."
                            )
                        else:
                            await update.message.reply_text(
                                f"❌ **Xatolik yuz berdi!**\n\n"
                                f"Sabab: {str(e)[:100]}"
                            )
                
                except Exception as e:
                    logging.error(f"Guruh qo'shishda xatolik: {e}")
                    error_msg = str(e)
                    await update.message.reply_text(
                        f"❌ **Guruh qo'shishda xatolik!**\n\n"
                        f"📱 **Hisob:** {account_display_name}\n"
                        f"📢 **Guruh:** {group_input}\n\n"
                        f"⚠️ **Sabab:** {str(e)[:150]}"
                    )
            else:
                await update.message.reply_text("❌ Xatolik: Hisob tanlanmagan!")
            
            context.user_data["mode"] = None
            context.user_data["selected_account"] = None
        
        # MODE: DELETE ACCOUNT
        elif mode == "delete_account":
            input_name = text.strip().lower()
            accounts = get_all_accounts()
            
            selected_account = None
            for acc in accounts:
                display_name = acc[0].lower()
                if display_name == input_name:
                    selected_account = acc[0]
                    break
            
            if selected_account:
                account_info = get_account_by_display_name(selected_account)
                session_name = account_info[2] if account_info else ""
                
                update_account_status(selected_account, 0)
                
                if selected_account in clients:
                    try:
                        await clients[selected_account].disconnect()
                        del clients[selected_account]
                    except:
                        pass
                
                if selected_account in current_msg_index:
                    del current_msg_index[selected_account]
                
                await update.message.reply_text(
                    f"✅ **{selected_account.upper()}** HISOBI O'CHIRILDI!\n\n"
                    f"🗑️ **Hisob:** {selected_account}\n"
                    f"🔒 **Status:** ❌ Nofaol\n\n"
                    f"⚠️ **Eslatma:** Hisob bazadan o'chirilmaydi, faqat faolligi o'chiriladi.\n"
                    f"Qayta faollashtirish uchun hisob qayta qo'shing."
                )
            else:
                await update.message.reply_text("❌ Bu hisob topilmadi!")
            
            context.user_data["mode"] = None
        
        # MODE: SETTINGS ACCOUNT
        elif mode == "settings_account":
            input_name = text.strip().lower()
            accounts = get_all_accounts()
            
            selected_account = None
            for acc in accounts:
                display_name = acc[0].lower()
                if display_name == input_name:
                    selected_account = acc[0]
                    break
            
            if selected_account:
                groups = get_all_groups_by_account(selected_account)
                
                if not groups:
                    await update.message.reply_text(
                        f"❌ **{selected_account.upper()}** hisobida guruh yo'q!\n\n"
                        f"Avval guruh qo'shing: 🔗 **Guruh qo'shish**"
                    )
                    context.user_data["mode"] = None
                    return
                
                msg = f"⚙️ **{selected_account.upper()} - GURUH SOZLAMALARI**\n\n"
                msg += "✅ - avtomatik xabar yuborish yoqilgan\n"
                msg += "❌ - avtomatik xabar yuborish o'chirilgan\n\n"
                
                for i, group in enumerate(groups, 1):
                    group_id = group[0]
                    group_title = group[1] if group[1] else "Noma'lum"
                    group_username = group[2] if group[2] else ""
                    group_active = "✅" if group[3] == 1 else "❌"
                    
                    if group_username:
                        msg += f"{group_active} {i}. {group_title} (@{group_username})\n"
                    else:
                        msg += f"{group_active} {i}. {group_title} (ID: {group_id})\n"
                
                msg += "\n✍️ **O'zgartirmoqchi bo'lgan guruh raqamini yozing:** (masalan: 1, 2, 3)"
                msg += "\n\n⚠️ **Guruhni raqamini yozib, yoqish/o'chirish tugmasini bosing**"
                
                context.user_data["mode"] = "settings_group_select"
                context.user_data["selected_account"] = selected_account
                context.user_data["groups"] = groups
                
                keyboard = [["✅ Yoqish", "❌ O'chirish"], ["🔙 Orqaga"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                
                await update.message.reply_text(msg, reply_markup=reply_markup)
            else:
                await update.message.reply_text("❌ Bu hisob topilmadi!")
                context.user_data["mode"] = None
        
        # MODE: SETTINGS GROUP SELECT
        elif mode == "settings_group_select":
            if text in ["✅ Yoqish", "❌ O'chirish"]:
                selected_number = context.user_data.get("selected_group_number")
                selected_account = context.user_data.get("selected_account")
                groups = context.user_data.get("groups")
                
                if not selected_number or not selected_account or not groups:
                    await update.message.reply_text("❌ Xatolik! Qaytadan boshlang.")
                    context.user_data.clear()
                    return
                
                try:
                    idx = int(selected_number) - 1
                    if 0 <= idx < len(groups):
                        group = groups[idx]
                        group_id = group[0]
                        group_title = group[1]
                        
                        is_active = 1 if text == "✅ Yoqish" else 0
                        update_group_status(selected_account, group_id, is_active)
                        
                        status_text = "faollashtirildi" if is_active == 1 else "o'chirildi"
                        
                        await update.message.reply_text(
                            f"✅ **{group_title}** guruhi {status_text}!\n\n"
                            f"📱 **Hisob:** {selected_account}\n"
                            f"📢 **Guruh:** {group_title}\n"
                            f"🔧 **Status:** {'✅ Faol' if is_active == 1 else '❌ Nofaol'}\n\n"
                            f"⚠️ **Eslatma:** Boshqa guruhni o'zgartirish uchun raqam yozing"
                        )
                    else:
                        await update.message.reply_text("❌ Noto'g'ri raqam!")
                except:
                    await update.message.reply_text("❌ Xatolik! Raqam kiriting.")
            
            elif text == "🔙 Orqaga":
                menu = ReplyKeyboardMarkup([
                    ["➕ Hisob qo'shish", "📤 Xabar qo'shish"],
                    ["🔗 Guruh qo'shish", "👥 Guruhlarni ko'rish"],
                    ["▶️ Boshlash", "⏹️ To'xtatish"],
                    ["📋 Hisoblar", "📝 Xabarlar", "📊 Statistika"],
                    ["⚙️ Interval", "🎲 Random", "🗑️ Hisob o'chirish"],
                    ["🌍 Davlatlar", "⚙️ Sozlamalar"]
                ], resize_keyboard=True)
                
                await update.message.reply_text(
                    "⚙️ **Sozlamalar menyusiga qaytdingiz**\n\n"
                    "Kerakli tugmani tanlang:",
                    reply_markup=menu
                )
                context.user_data.clear()
            
            else:
                try:
                    number = int(text.strip())
                    groups = context.user_data.get("groups", [])
                    
                    if 1 <= number <= len(groups):
                        group = groups[number - 1]
                        group_title = group[1] if group[1] else "Noma'lum"
                        group_active = "✅ Faol" if group[3] == 1 else "❌ Nofaol"
                        
                        context.user_data["selected_group_number"] = text
                        
                        await update.message.reply_text(
                            f"📢 **Tanlangan guruh:** {group_title}\n"
                            f"🔧 **Joriy holat:** {group_active}\n\n"
                            f"Endi holatni o'zgartirish uchun tugmalardan birini tanlang:"
                        )
                    else:
                        await update.message.reply_text(f"❌ Noto'g'ri raqam! 1 dan {len(groups)} gacha raqam kiriting.")
                except:
                    await update.message.reply_text("❌ Iltimos, raqam kiriting!")
        
        # Hech qanday mode yo'q bo'lsa
        else:
            await update.message.reply_text("❌ Noma'lum buyruq! Menyudan tugmani tanlang.")

async def auto_send_loop():
    """Avtomatik xabar yuborish loopi"""
    global is_sending, last_send_time, min_interval, max_interval, random_messages
    
    while True:
        try:
            if is_sending:
                messages = get_all_messages()
                accounts = get_active_accounts()
                
                if messages and accounts:
                    total_active_groups = 0
                    account_groups_info = {}
                    
                    for acc in accounts:
                        display_name = acc[0]
                        groups = get_all_groups_by_account(display_name)
                        active_groups = sum(1 for g in groups if g[3] == 1)
                        total_active_groups += active_groups
                        account_groups_info[display_name] = active_groups
                    
                    if total_active_groups == 0:
                        logging.warning("⚠️ Hech qanday faol guruh yo'q!")
                        await asyncio.sleep(60)
                        continue
                    
                    logging.info(f"📤 {len(accounts)} ta hisobda {total_active_groups} ta faol guruhga {len(messages)} ta xabar yuborilmoqda...")
                    
                    for account in accounts:
                        display_name = account[0]
                        session_name = account[1] if len(account) > 1 else ""
                        
                        groups = get_all_groups_by_account(display_name)
                        active_groups = [g for g in groups if g[3] == 1]
                        
                        if not active_groups:
                            logging.info(f"  📭 {display_name}: Faol guruh yo'q")
                            continue
                        
                        if display_name not in clients:
                            try:
                                client = Client(
                                    name=session_name,
                                    api_id=API_ID,
                                    api_hash=API_HASH
                                )
                                await client.start()
                                clients[display_name] = client
                                current_msg_index[display_name] = 0
                                logging.info(f"  🔄 {display_name} client yaratildi")
                            except Exception as e:
                                logging.error(f"  ❌ {display_name} client yaratishda xato: {e}")
                                continue
                        
                        client = clients[display_name]
                        
                        # Xabarni tanlash
                        if random_messages:
                            # Random rejimda tasodifiy xabar
                            msg_text = get_random_message()
                            if not msg_text:
                                logging.warning(f"  ❌ {display_name}: Xabarlar yo'q!")
                                continue
                        else:
                            # Ketma-ket rejimda
                            idx = current_msg_index.get(display_name, 0)
                            if len(messages) > 0:
                                msg_index = idx % len(messages)
                                msg_text = messages[msg_index][1]
                                current_msg_index[display_name] = idx + 1
                            else:
                                logging.warning(f"  ❌ {display_name}: Xabarlar yo'q!")
                                continue
                        
                        logging.info(f"  📱 {display_name}: {len(active_groups)} ta faol guruhga xabar yuborilmoqda...")
                        
                        for group in active_groups:
                            try:
                                await client.send_message(int(group[0]), msg_text)
                                logging.info(f"    ✅ {display_name} -> {group[1]}")
                                await asyncio.sleep(1)  # Telegram API limiti uchun
                                
                            except Exception as e:
                                logging.error(f"    ❌ {display_name} -> {group[1]}: {e}")
                    
                    last_send_time = datetime.now().strftime("%H:%M:%S")
                    logging.info(f"✅ Barcha xabarlar yuborildi. Keyingisi {min_interval}-{max_interval} daqiqadan keyin...")
                
                # Sozlanagan intervalda kutish
                delay = random.randint(min_interval * 60, max_interval * 60)
                logging.info(f"⏱️ Keyingi xabar yuborish {delay//60} daqiqadan keyin...")
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(10)
                
        except Exception as e:
            logging.error(f"Auto send loop xatosi: {e}")
            await asyncio.sleep(30)

def auto_send_thread():
    """Alohida threadda ishlaydigan avtomatik yuborish"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(auto_send_loop())
    finally:
        loop.close()

def main():
    print("\n" + "="*60)
    print("🤖 TELEGRAM AUTO-POSTER BOT - BARCHA DAVLATLAR UCHUN")
    print("="*60)
    print("\n🌍 **Qo'llab-quvvatlanadigan davlatlar:**")
    print("🇺🇿 O'zbekiston (+998)  🇷🇺 Rossiya (+7)")
    print("🇰🇿 Qozog'iston (+7)    🇹🇲 Turkmaniston (+993)")
    print("🇹🇯 Tojikiston (+992)   🇰🇬 Qirg'iziston (+996)")
    print("🇺🇦 Ukraina (+380)      🇹🇷 Turkiya (+90)")
    print("🇺🇸 AQSh (+1)          🇬🇧 UK (+44)")
    print("🇩🇪 Germaniya (+49)     🇨🇳 Xitoy (+86)")
    print("🇮🇳 Hindiston (+91)     🇯🇵 Yaponiya (+81)")
    print("🌍 va boshqa 200+ davlat")
    print("\n🚀 **YANGI XUSUSIYATLAR:**")
    print("• ✅ Interval sozlamalari (admin panel orqali)")
    print("• ✅ Random xabar rejimi (yoqish/o'chirish)")
    print("• ✅ Guruh a'zo emaslik xatolari aniq ko'rsatiladi")
    print("• ✅ Xabarlar random yoki ketma-ket yuboriladi")
    print("="*60 + "\n")
    
    # Sozlamalarni bazadan o'qish
    global min_interval, max_interval, random_messages
    min_interval = int(get_setting('min_interval', '20'))
    max_interval = int(get_setting('max_interval', '25'))
    random_messages = get_setting('random_messages', 'true').lower() == 'true'
    
    if os.path.exists(DB_FILE):
        print(f"✅ Baza fayli mavjud: {DB_FILE}")
        print(f"📊 Joriy interval: {min_interval}-{max_interval} daqiqa")
        print(f"🎲 Random rejim: {'Yoqilgan' if random_messages else 'O\'chirilgan'}")
    else:
        print(f"⚠️ Yangi baza yaratilmoqda...")
    
    init_database()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    send_thread = threading.Thread(target=auto_send_thread, daemon=True)
    send_thread.start()
    
    print("✅ **YANGI TUGMALAR:**")
    print("1. ⚙️ Interval - Xabar yuborish intervalini sozlash")
    print("2. 🎲 Random - Random xabar rejimini yoqish/o'chirish")
    print("")
    print("✅ **Interval sozlash formatlari:**")
    print("• 15,20 - 15-20 daqiqa oralig'i")
    print("• 30 - 30 daqiqa (har 30 daqiqada)")
    print("")
    print("✅ **Guruh qo'shish xatolari:**")
    print("• Agar hisob guruhda a'zo bo'lmasa, aniq xabar ko'rsatiladi")
    print("• Guruh mavjud emas bo'lsa, aniq xabar ko'rsatiladi")
    print("="*60)
    print("🚀 Bot ishga tushmoqda...")
    print("📱 Telegram botga /start yuboring")
    print("="*60)
    
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Bot to'xtatildi!")
    except Exception as e:
        print(f"\n❌ Xatolik: {e}")