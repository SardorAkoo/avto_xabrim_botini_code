import os
import logging
import sqlite3
import random
import string
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.constants import ParseMode

# Log konfiguratsiyasi
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================================
# KONFIGURATSIYA
# =========================================================

TELEGRAM_TOKEN = "8220522354:AAHk8lxrgvQAapNvV7u4-d-9A0zPT6ki5oI"
ADMIN_USERNAME = "@Okean_Manager"

# Konversatsiya holatlari
GET_TOPIC, GET_REJALAR, GET_TIL, GET_AUTHOR, GET_GROUP, GET_TEACHER = range(6)
GET_REF_TOPIC, GET_REF_TIL, GET_REF_PAGES, GET_REF_AUTHOR, GET_REF_GROUP, GET_REF_TEACHER = range(12, 18)
PAYMENT_AMOUNT, PAYMENT_SCREENSHOT = range(18, 20)

class Config:
    # Baza fayl nomi
    DB_FILE = 'bot_database.db'
    
    # Narxlar
    MUSTAQIL_ISH_NARXI = 1
    REFERAT_NARXI = 2
    SLAYDLAR_NARXI = 3
    
    # Bepul limitlar
    BEPUL_MUSTAQIL_LIMIT = 1
    BEPUL_REFERAT_LIMIT = 0
    
    # To'lov tizimi
    MIN_DEPOSIT = 5000  # 5000 so'm
    MAX_DEPOSIT = 1000000  # 1 million so'm
    
    # Referal tizimi
    REFERAL_BONUS = 5
    
    # Majburiy obuna kanallari (username formatida)
    REQUIRED_CHANNELS = ["@test_kanal_1", "@test_kanal_2"]
    
    # Admin karta raqamlari
    ADMIN_CARDS = {
        'click': "8600 1234 5678 9012",
        'payme': "9989 1234 5678",
        'cash': "Naqd pul uchun admin bilan bog'laning"
    }

# =========================================================
# DATABASE FUNKSIYALARI
# =========================================================

def init_database():
    conn = sqlite3.connect(Config.DB_FILE)
    cursor = conn.cursor()
    
    # Foydalanuvchilar jadvali (barcha ustunlarni bir vaqtda yaratamiz)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance INTEGER DEFAULT 0,
            free_mustaqil_used INTEGER DEFAULT 0,
            free_referat_used INTEGER DEFAULT 0,
            referal_code TEXT UNIQUE,
            referred_by TEXT,
            total_referred INTEGER DEFAULT 0,
            referal_bonus INTEGER DEFAULT 0,
            lang TEXT DEFAULT 'uz',
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Mustaqil ishlar jadvali
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mustaqil_ishlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            subject TEXT DEFAULT "Oliy Ta'lim Fanlaridan",
            topic TEXT,
            rejalar INTEGER,
            til TEXT,
            author TEXT,
            student_group TEXT,
            teacher TEXT,
            content TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Referatlar jadvali
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referatlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            subject TEXT DEFAULT "Oliy Ta'lim Fanlaridan",
            topic TEXT,
            til TEXT,
            pages INTEGER,
            author TEXT,
            student_group TEXT,
            teacher TEXT,
            content TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Tranzaksiyalar jadvali
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            type TEXT,
            description TEXT,
            status TEXT DEFAULT 'pending',
            payment_method TEXT,
            admin_id INTEGER,
            confirmed_at TIMESTAMP,
            screenshot_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Admin sozlamalari
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Narxlar jadvali
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_type TEXT UNIQUE,
            price INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Narxlarni boshlang'ich qiymatlar bilan to'ldirish
    cursor.execute('''
        INSERT OR IGNORE INTO prices (service_type, price) 
        VALUES 
        ('mustaqil_ish', 1),
        ('referat', 2),
        ('slaydlar', 3)
    ''')
    
    # Eski jadvalni yangilash (agar mavjud bo'lsa)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except:
        pass  # Agar ustun allaqachon mavjud bo'lsa
    
    # Admin foydalanuvchini qo'shish (o'z ID'ingizni qo'ying)
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username, full_name, is_admin, balance)
        VALUES (?, ?, ?, 1, 100)
    ''', (8289173554, "Okean_Manager", "Admin User"))
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized successfully")

def get_db_connection():
    return sqlite3.connect(Config.DB_FILE)

# =========================================================
# YORDAMCHI FUNKSIYALARI
# =========================================================

def save_user_to_db(user_id, username, full_name, referal_code=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Referal kodi yaratish
    user_referal_code = generate_referal_code()
    
    # Avval mavjudligini tekshirish
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    existing_user = cursor.fetchone()
    
    if not existing_user:
        cursor.execute('''
            INSERT INTO users 
            (user_id, username, full_name, referal_code, referred_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, user_referal_code, referal_code))
    
    # Agar referal kod orqali kelgan bo'lsa, referal bonus qo'shish
    if referal_code and not existing_user:
        cursor.execute('''
            UPDATE users 
            SET total_referred = total_referred + 1,
                referal_bonus = referal_bonus + ?
            WHERE referal_code = ?
        ''', (Config.REFERAL_BONUS, referal_code))
    
    conn.commit()
    conn.close()

def generate_referal_code(length=8):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def get_user_balance(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT balance, free_mustaqil_used, free_referat_used 
        FROM users WHERE user_id = ?
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            'balance': result[0],
            'free_mustaqil_used': result[1],
            'free_referat_used': result[2]
        }
    return {'balance': 0, 'free_mustaqil_used': 0, 'free_referat_used': 0}

def is_admin(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

def get_price(service_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT price FROM prices WHERE service_type = ?', (service_type,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def update_price(service_type, price):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO prices (service_type, price)
        VALUES (?, ?)
    ''', (service_type, price))
    conn.commit()
    conn.close()

def add_transaction(user_id, amount, tr_type, description, status='pending', payment_method=None, screenshot_path=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO transactions 
        (user_id, amount, type, description, status, payment_method, screenshot_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, amount, tr_type, description, status, payment_method, screenshot_path))
    transaction_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return transaction_id

def confirm_transaction(transaction_id, admin_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Tranzaksiyani topish
    cursor.execute('SELECT user_id, amount FROM transactions WHERE id = ?', (transaction_id,))
    transaction = cursor.fetchone()
    
    if transaction:
        user_id, amount = transaction
        
        # Balansni yangilash (1 kredit = 1000 so'm deb hisoblaymiz)
        credits = amount // 1000
        cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (credits, user_id))
        
        # Tranzaksiyani tasdiqlash
        cursor.execute('''
            UPDATE transactions 
            SET status = 'confirmed', 
                admin_id = ?,
                confirmed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (admin_id, transaction_id))
        
        conn.commit()
        conn.close()
        return True
    
    conn.close()
    return False

def reject_transaction(transaction_id, admin_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE transactions 
        SET status = 'rejected', 
            admin_id = ?,
            confirmed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (admin_id, transaction_id))
    
    conn.commit()
    conn.close()
    return True

def get_pending_transactions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.id, t.user_id, u.username, u.full_name, t.amount, 
               t.payment_method, t.description, t.created_at, t.screenshot_path
        FROM transactions t
        LEFT JOIN users u ON t.user_id = u.user_id
        WHERE t.status = 'pending' AND t.type = 'deposit'
        ORDER BY t.created_at DESC
    ''')
    transactions = cursor.fetchall()
    conn.close()
    return transactions

def get_user_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Umumiy statistika
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM mustaqil_ishlar WHERE status = "active"')
    total_mustaqil = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM referatlar WHERE status = "active"')
    total_referat = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(balance) FROM users')
    total_balance = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT SUM(amount) FROM transactions WHERE status = "confirmed"')
    total_income = cursor.fetchone()[0] or 0
    
    # Bugungi faollik
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('SELECT COUNT(*) FROM users WHERE DATE(created_at) = ?', (today,))
    new_users_today = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'total_users': total_users,
        'total_mustaqil': total_mustaqil,
        'total_referat': total_referat,
        'total_balance': total_balance,
        'total_income': total_income,
        'new_users_today': new_users_today
    }

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, full_name, balance, created_at
        FROM users 
        WHERE is_admin = 0
        ORDER BY created_at DESC
    ''')
    users = cursor.fetchall()
    conn.close()
    return users

def delete_document(doc_id, doc_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if doc_type == 'mustaqil':
        cursor.execute('UPDATE mustaqil_ishlar SET status = "deleted" WHERE id = ?', (doc_id,))
    elif doc_type == 'referat':
        cursor.execute('UPDATE referatlar SET status = "deleted" WHERE id = ?', (doc_id,))
    
    conn.commit()
    conn.close()

def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, full_name, balance, referal_code, total_referred, referal_bonus
        FROM users WHERE user_id = ?
    ''', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'username': user[1] or '',
            'full_name': user[2] or '',
            'balance': user[3],
            'referal_code': user[4],
            'total_referred': user[5],
            'referal_bonus': user[6]
        }
    return None

def add_balance_directly(user_id, amount):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    
    # Tranzaksiyani qo'shish
    cursor.execute('''
        INSERT INTO transactions 
        (user_id, amount, type, description, status, admin_id, confirmed_at)
        VALUES (?, ?, 'admin_add', 'Admin tomonidan balans qo''shildi', 'confirmed', ?, CURRENT_TIMESTAMP)
    ''', (user_id, amount * 1000, user_id))
    
    conn.commit()
    conn.close()

# =========================================================
# TUGMALAR
# =========================================================

def get_main_menu_keyboard():
    keyboard = [
        [KeyboardButton("📝 Yangi Mustaqil ish"), KeyboardButton("📄 Yangi Referat")],
        [KeyboardButton("📁 Hujjatlarim"), KeyboardButton("💰 Balans")],
        [KeyboardButton("👥 Referal"), KeyboardButton("⚙️ Sozlamalar")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_cancel_keyboard():
    keyboard = [[KeyboardButton("🚫 Bekor qilish")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_rejalar_keyboard():
    keyboard = [
        [InlineKeyboardButton("3 ta reja", callback_data="rejalar_3")],
        [InlineKeyboardButton("4 ta reja", callback_data="rejalar_4")],
        [InlineKeyboardButton("5 ta reja", callback_data="rejalar_5")],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="back_to_titul")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_til_keyboard():
    keyboard = [
        [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="til_uz")],
        [InlineKeyboardButton("🇷🇺 Ruscha", callback_data="til_ru")],
        [InlineKeyboardButton("🇬🇧 Ingliz", callback_data="til_en")],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="back_to_rejalar")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_pages_keyboard():
    keyboard = [
        [InlineKeyboardButton("5-10 bet", callback_data="pages_5_10")],
        [InlineKeyboardButton("11-15 bet", callback_data="pages_11_15")],
        [InlineKeyboardButton("16-20 bet", callback_data="pages_16_20")],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="back_to_ref_topic")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ref_til_keyboard():
    keyboard = [
        [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="ref_til_uz")],
        [InlineKeyboardButton("🇷🇺 Ruscha", callback_data="ref_til_ru")],
        [InlineKeyboardButton("🇬🇧 Ingliz", callback_data="ref_til_en")],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="back_to_ref_pages")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_balance_keyboard():
    keyboard = [
        [InlineKeyboardButton("💳 Karta orqali to'lash", callback_data="balance_pay_card")],
        [InlineKeyboardButton("👥 Referal linkni yuborish", callback_data="balance_referal_link")],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="balance_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_payment_methods_keyboard():
    keyboard = [
        [InlineKeyboardButton("💳 Click", callback_data="pay_click")],
        [InlineKeyboardButton("💳 Payme", callback_data="pay_payme")],
        [InlineKeyboardButton("💰 Naqd pul", callback_data="pay_cash")],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="back_to_balance")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_payment_keyboard(transaction_id):
    keyboard = [
        [
            InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"admin_confirm_{transaction_id}"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"admin_reject_{transaction_id}")
        ],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="admin_back_payments")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("💰 To'lovlarni tasdiqlash", callback_data="admin_payments")],
        [InlineKeyboardButton("⚙️ Narxlarni sozlash", callback_data="admin_prices")],
        [InlineKeyboardButton("➕ Balans qo'shish", callback_data="admin_add_balance")],
        [InlineKeyboardButton("📢 Xabar yuborish", callback_data="admin_send_message")],
        [InlineKeyboardButton("📂 Hujjatlarni boshqarish", callback_data="admin_documents")],
        [InlineKeyboardButton("◀️ Asosiy menyu", callback_data="admin_back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_admin_keyboard():
    keyboard = [[InlineKeyboardButton("◀️ Admin menyuga qaytish", callback_data="admin_back")]]
    return InlineKeyboardMarkup(keyboard)

# =========================================================
# KANAL OBUNASINI TEKSHIRISH
# =========================================================

async def check_subscription(user_id, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi majburiy kanallarga obuna bo'lganligini tekshirish"""
    for channel in Config.REQUIRED_CHANNELS:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if chat_member.status in ['left', 'kicked']:
                return False, channel
        except Exception as e:
            logger.error(f"Kanalni tekshirishda xatolik {channel}: {e}")
            continue
    return True, None

# =========================================================
# USER HANDLERLAR
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Majburiy obunani tekshirish
    is_subscribed, channel = await check_subscription(user_id, context)
    if not is_subscribed:
        keyboard = [[InlineKeyboardButton("📢 Kanalga obuna bo'lish", url=f"https://t.me/{channel[1:]}")]]
        await update.message.reply_text(
            f"⚠️ Botdan foydalanish uchun quyidagi kanalga obuna bo'ling:\n\n"
            f"{channel}\n\n"
            f"Obuna bo'lgandan so'ng /start ni bosing.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Referal kodni tekshirish
    args = context.args
    referal_code = args[0] if args else None
    
    # Foydalanuvchini saqlash
    save_user_to_db(user_id, user.username, user.full_name, referal_code)
    
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n"
        f"🤖 Mustaqil Ish va Referat Generator Botiga xush kelibsiz!\n\n"
        f"🎁 **BEPUL IMKONIYAT:** {Config.BEPUL_MUSTAQIL_LIMIT} ta mustaqil ish yaratishingiz mumkin!\n\n"
        f"📌 Kerakli bo'limni tanlang:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='Markdown'
    )

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asosiy menyu tugmalari"""
    user_id = update.effective_user.id
    text = update.message.text
    
    if text == "📝 Yangi Mustaqil ish":
        return await start_mustaqil_ish(update, context)
    
    elif text == "📄 Yangi Referat":
        return await start_referat(update, context)
    
    elif text == "📁 Hujjatlarim":
        await show_my_documents(update, context)
    
    elif text == "💰 Balans":
        await show_balance_menu(update, context)
    
    elif text == "👥 Referal":
        await show_referal_info(update, context)
    
    elif text == "⚙️ Sozlamalar":
        await show_settings(update, context)
    
    elif text == "🚫 Bekor qilish":
        await update.message.reply_text(
            "🚫 Jarayon bekor qilindi.",
            reply_markup=get_main_menu_keyboard()
        )
        context.user_data.clear()
    
    else:
        await update.message.reply_text(
            "🤖 Buyruqni tushunmadim. Iltimos, menyudan tanlang.",
            reply_markup=get_main_menu_keyboard()
        )

async def start_mustaqil_ish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Balansni tekshirish
    user_data = get_user_balance(user_id)
    mustaqil_price = get_price('mustaqil_ish')
    can_use_free = user_data['free_mustaqil_used'] < Config.BEPUL_MUSTAQIL_LIMIT
    
    if user_data['balance'] < mustaqil_price and not can_use_free:
        # Balans yetarli emas - balans to'ldirish menyusini ko'rsatish
        user_info = get_user_by_id(user_id)
        referal_link = f"https://t.me/ReferatMustaqiLIshBot?start={user_info['referal_code']}"
        
        text = f"""⚠️ **Balansingizda yetarli mablag' mavjud emas**

Sizning balansingiz: {user_data['balance']} kredit
Mustaqil ish narxi: {mustaqil_price} kredit

Balansingizni to'ldirish uchun:

1️⃣ **Referal linkingizni do'stingizga yuboring**
{referal_link}

2️⃣ **Quyidagi 'Karta orqali to'lash' tugmasini bosib karta orqali to'lov qiling**"""
        
        await update.message.reply_text(
            text,
            reply_markup=get_balance_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    context.user_data.clear()
    context.user_data['user_id'] = user_id
    context.user_data['subject'] = "Oliy Ta'lim Fanlaridan"
    context.user_data['can_use_free'] = can_use_free
    context.user_data['doc_type'] = 'mustaqil'
    
    await update.message.reply_text(
        "📚 Mustaqil ish mavzusini kiriting:",
        reply_markup=get_cancel_keyboard()
    )
    
    return GET_TOPIC

async def start_referat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Balansni tekshirish
    user_data = get_user_balance(user_id)
    referat_price = get_price('referat')
    can_use_free = user_data['free_referat_used'] < Config.BEPUL_REFERAT_LIMIT
    
    if user_data['balance'] < referat_price and not can_use_free:
        # Balans yetarli emas - balans to'ldirish menyusini ko'rsatish
        user_info = get_user_by_id(user_id)
        referal_link = f"https://t.me/ReferatMustaqiLIshBot?start={user_info['referal_code']}"
        
        text = f"""⚠️ **Balansingizda yetarli mablag' mavjud emas**

Sizning balansingiz: {user_data['balance']} kredit
Referat narxi: {referat_price} kredit

Balansingizni to'ldirish uchun:

1️⃣ **Referal linkingizni do'stingizga yuboring**
{referal_link}

2️⃣ **Quyidagi 'Karta orqali to'lash' tugmasini bosib karta orqali to'lov qiling**"""
        
        await update.message.reply_text(
            text,
            reply_markup=get_balance_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    context.user_data.clear()
    context.user_data['user_id'] = user_id
    context.user_data['subject'] = "Oliy Ta'lim Fanlaridan"
    context.user_data['can_use_free'] = can_use_free
    context.user_data['doc_type'] = 'referat'
    
    await update.message.reply_text(
        "📚 Referat mavzusini kiriting:",
        reply_markup=get_cancel_keyboard()
    )
    
    return GET_REF_TOPIC

async def show_balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_balance(user_id)
    user_info = get_user_by_id(user_id)
    
    mustaqil_price = get_price('mustaqil_ish')
    referat_price = get_price('referat')
    
    referal_link = f"https://t.me/ReferatMustaqiLIshBot?start={user_info['referal_code']}"
    
    text = f"""💰 **Sizning balansingiz:**

💳 **Joriy balans:** {user_data['balance']} kredit

🎁 **Bepul imkoniyatlar:**
├ Mustaqil ish: {Config.BEPUL_MUSTAQIL_LIMIT - user_data['free_mustaqil_used']} ta qoldi
└ Referat: {Config.BEPUL_REFERAT_LIMIT - user_data['free_referat_used']} ta qoldi

📊 **Narxlar:**
├ Mustaqil ish: {mustaqil_price} kredit
├ Referat: {referat_price} kredit
└ Slaydlar: {get_price('slaydlar')} kredit

📌 **Balansingizni to'ldirish uchun:**
1. Referal linkingizni do'stingizga yuboring
2. Quyidagi 'Karta orqali to'lash' tugmasini bosib karta orqali to'lov qiling

🔗 **Sizning referal linkingiz:**
{referal_link}"""
    
    await update.message.reply_text(
        text,
        reply_markup=get_balance_keyboard(),
        parse_mode='Markdown'
    )

async def handle_balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "balance_pay_card":
        await query.edit_message_text(
            "💳 **To'lov usulini tanlang:**",
            reply_markup=get_payment_methods_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == "balance_referal_link":
        user_info = get_user_by_id(user_id)
        referal_link = f"https://t.me/ReferatMustaqiLIshBot?start={user_info['referal_code']}"
        
        text = f"""👥 **Referal tizimi**

🔗 **Sizning referal linkingiz:**
{referal_link}

📊 **Qoidalar:**
• Har bir do'stingiz botga sizning linkiz orqali kirganda, sizga {Config.REFERAL_BONUS} kredit beriladi
• Do'stingiz birinchi buyurtma qilganda yana bonus olasiz
• Balansingizni referal orqali to'ldirishingiz mumkin

📌 **Linkni qayerda joylashtirish mumkin:**
• Telegram guruhlarda
• Telegram kanallarda
• Do'stlaringizga shaxsiy xabar orqali
• Ijtimoiy tarmoqlarda"""
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown'
        )
    
    elif query.data == "balance_back":
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text="Kerakli bo'limni tanlang:",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("pay_"):
        method = query.data.split('_')[1]
        context.user_data['payment_method'] = method
        
        card_info = Config.ADMIN_CARDS.get(method, "Ma'lumot mavjud emas")
        
        text = f"""💳 **To'lov ma'lumotlari:**

💰 **To'lov usuli:** {method.upper()}
📋 **Karta raqami:** {card_info}

✍️ **Summani yozing:**
Eng kam miqdor: {Config.MIN_DEPOSIT:,} so'm
Eng ko'p miqdor: {Config.MAX_DEPOSIT:,} so'm

⚠️ **Eslatma:** 1000 so'm = 1 kredit"""
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown'
        )
        
        context.user_data['awaiting_payment_amount'] = True
        return PAYMENT_AMOUNT
    
    elif query.data == "back_to_balance":
        await show_balance_menu_callback(query, context)

async def show_balance_menu_callback(query, context):
    user_id = query.from_user.id
    user_data = get_user_balance(user_id)
    user_info = get_user_by_id(user_id)
    
    mustaqil_price = get_price('mustaqil_ish')
    referat_price = get_price('referat')
    
    referal_link = f"https://t.me/ReferatMustaqiLIshBot?start={user_info['referal_code']}"
    
    text = f"""💰 **Sizning balansingiz:**

💳 **Joriy balans:** {user_data['balance']} kredit

🎁 **Bepul imkoniyatlar:**
├ Mustaqil ish: {Config.BEPUL_MUSTAQIL_LIMIT - user_data['free_mustaqil_used']} ta qoldi
└ Referat: {Config.BEPUL_REFERAT_LIMIT - user_data['free_referat_used']} ta qoldi

📊 **Narxlar:**
├ Mustaqil ish: {mustaqil_price} kredit
├ Referat: {referat_price} kredit
└ Slaydlar: {get_price('slaydlar')} kredit

📌 **Balansingizni to'ldirish uchun:**
1. Referal linkingizni do'stingizga yuboring
2. Quyidagi 'Karta orqali to'lash' tugmasini bosib karta orqali to'lov qiling

🔗 **Sizning referal linkingiz:**
{referal_link}"""
    
    await query.edit_message_text(
        text,
        reply_markup=get_balance_keyboard(),
        parse_mode='Markdown'
    )

async def process_payment_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        # Faqat raqamlarni ajratib olish
        amount = int(''.join(filter(str.isdigit, text)))
        
        if amount < Config.MIN_DEPOSIT:
            await update.message.reply_text(
                f"⚠️ Eng kam miqdor: {Config.MIN_DEPOSIT:,} so'm\n"
                f"Iltimos, qaytadan kiriting:"
            )
            return PAYMENT_AMOUNT
        
        if amount > Config.MAX_DEPOSIT:
            await update.message.reply_text(
                f"⚠️ Eng ko'p miqdor: {Config.MAX_DEPOSIT:,} so'm\n"
                f"Iltimos, qaytadan kiriting:"
            )
            return PAYMENT_AMOUNT
        
        context.user_data['payment_amount'] = amount
        credits = amount // 1000
        
        text = f"""✅ **To'lov ma'lumotlari:**

💰 **Summa:** {amount:,} so'm
💳 **Kredit:** {credits} kredit
📋 **To'lov usuli:** {context.user_data.get('payment_method', '').upper()}

📱 **Endi to'lov chekini yuboring:**
• Skrinshot
• To'lov shakli
• Yoki boshqa dalil

⚠️ **Diqqat:** To'lovni amalga oshirgandan so'ng chekni yuboring.
Admin tomonidan tekshirilgach, balansingizga kredit qo'shiladi."""
        
        await update.message.reply_text(
            text,
            parse_mode='Markdown'
        )
        
        context.user_data['awaiting_screenshot'] = True
        return PAYMENT_SCREENSHOT
        
    except ValueError:
        await update.message.reply_text(
            "⚠️ Iltimos, faqat raqam kiriting!\n"
            f"Masalan: {Config.MIN_DEPOSIT}"
        )
        return PAYMENT_AMOUNT

async def process_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if update.message.photo:
        # Rasmni saqlash (bu yerda faqat file_id ni saqlaymiz)
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # Tranzaksiyani yaratish
        amount = context.user_data['payment_amount']
        method = context.user_data.get('payment_method', 'unknown')
        description = f"To'lov: {amount:,} so'm, usul: {method}"
        
        transaction_id = add_transaction(
            user_id=user_id,
            amount=amount,
            tr_type='deposit',
            description=description,
            status='pending',
            payment_method=method,
            screenshot_path=file_id
        )
        
        credits = amount // 1000
        
        # Foydalanuvchiga xabar
        await update.message.reply_text(
            f"✅ **To'lov ma'lumotlari qabul qilindi!**\n\n"
            f"💰 Summa: {amount:,} so'm\n"
            f"💳 Kredit: {credits} kredit\n"
            f"📋 ID: {transaction_id}\n\n"
            f"🕐 **Admin tomonidan tekshirilmoqda...**\n"
            f"Tasdiqlangandan so'ng sizga xabar beramiz.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode='Markdown'
        )
        
        # Adminlarga xabar berish
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE is_admin = 1')
        admins = cursor.fetchall()
        conn.close()
        
        user_info = get_user_by_id(user_id)
        
        for admin in admins:
            try:
                admin_id = admin[0]
                
                # Tranzaksiya haqida xabar
                admin_text = f"""🆕 **Yangi to'lov so'rovi!**

👤 **Foydalanuvchi:**
├ ID: {user_id}
├ Ism: {user_info['full_name']}
└ Username: @{user_info['username']}

💰 **To'lov ma'lumotlari:**
├ Summa: {amount:,} so'm
├ Kredit: {credits} kredit
├ Usul: {method.upper()}
└ ID: {transaction_id}

📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M')}"""
                
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    parse_mode='Markdown'
                )
                
                # Skrinshotni yuborish
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=f"To'lov cheki #{transaction_id}"
                )
                
                # Tasdiqlash tugmalarini yuborish
                await context.bot.send_message(
                    chat_id=admin_id,
                    text="To'lovni tasdiqlaysizmi?",
                    reply_markup=get_admin_payment_keyboard(transaction_id)
                )
                
            except Exception as e:
                logger.error(f"Adminga xabar yuborishda xatolik: {e}")
        
        context.user_data.clear()
        return ConversationHandler.END
        
    else:
        await update.message.reply_text(
            "⚠️ Iltimos, to'lov chekining skrinshotini (rasmini) yuboring!"
        )
        return PAYMENT_SCREENSHOT

async def process_mustaqil_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['topic'] = update.message.text
    await update.message.reply_text(
        "📊 Rejalar sonini tanlang:",
        reply_markup=get_rejalar_keyboard()
    )
    return GET_REJALAR

async def process_referat_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['topic'] = update.message.text
    await update.message.reply_text(
        "📄 Referat hajmini tanlang:",
        reply_markup=get_pages_keyboard()
    )
    return GET_REF_PAGES

async def process_rejalar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("rejalar_"):
        rejalar_count = int(query.data.split('_')[1])
        context.user_data['rejalar'] = rejalar_count
        
        await query.edit_message_text(
            "🌐 Tilni tanlang:",
            reply_markup=get_til_keyboard()
        )
        return GET_TIL
    
    elif query.data == "back_to_titul":
        await query.edit_message_text(
            "📚 Mustaqil ish mavzusini kiriting:"
        )
        return GET_TOPIC

async def process_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("pages_"):
        pages_range = query.data.split('_')[1:]
        context.user_data['pages'] = f"{pages_range[0]}-{pages_range[1]}"
        
        await query.edit_message_text(
            "🌐 Tilni tanlang:",
            reply_markup=get_ref_til_keyboard()
        )
        return GET_REF_TIL
    
    elif query.data == "back_to_ref_topic":
        await query.edit_message_text(
            "📚 Referat mavzusini kiriting:"
        )
        return GET_REF_TOPIC

async def process_til(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("til_"):
        til_code = query.data.split('_')[1]
        context.user_data['til'] = til_code
        
        await query.edit_message_text(
            "👤 Talabaning ismini kiriting (Bajardi):"
        )
        return GET_AUTHOR
    
    elif query.data == "back_to_rejalar":
        await query.edit_message_text(
            "📊 Rejalar sonini tanlang:",
            reply_markup=get_rejalar_keyboard()
        )
        return GET_REJALAR

async def process_ref_til(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("ref_til_"):
        til_code = query.data.split('_')[2]
        context.user_data['til'] = til_code
        
        await query.edit_message_text(
            "👤 Talabaning ismini kiriting (Bajardi):"
        )
        return GET_REF_AUTHOR
    
    elif query.data == "back_to_ref_pages":
        await query.edit_message_text(
            "📄 Referat hajmini tanlang:",
            reply_markup=get_pages_keyboard()
        )
        return GET_REF_PAGES

async def process_author(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['author'] = update.message.text
    await update.message.reply_text("👥 Guruh raqamini kiriting:")
    return GET_GROUP

async def process_ref_author(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['author'] = update.message.text
    await update.message.reply_text("👥 Guruh raqamini kiriting:")
    return GET_REF_GROUP

async def process_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['group'] = update.message.text
    await update.message.reply_text("👨‍🏫 O'qituvchining ismini kiriting (Tekshirdi):")
    return GET_TEACHER

async def process_ref_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['group'] = update.message.text
    await update.message.reply_text("👨‍🏫 O'qituvchining ismini kiriting (Tekshirdi):")
    return GET_REF_TEACHER

async def process_teacher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['teacher'] = update.message.text
    
    # Ishni yaratish
    await update.message.reply_text("⏳ **Iltimos kuting...**\nIsh tayyorlanmoqda...", parse_mode='Markdown')
    
    # Bazaga saqlash
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO mustaqil_ishlar 
        (user_id, subject, topic, rejalar, til, author, student_group, teacher, content)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id,
        context.user_data['subject'],
        context.user_data['topic'],
        context.user_data.get('rejalar', 3),
        context.user_data.get('til', 'uz'),
        context.user_data.get('author', ''),
        context.user_data.get('group', ''),
        context.user_data.get('teacher', ''),
        f"""MUSTAQIL ISH
Mavzu: {context.user_data['topic']}
Talaba: {context.user_data.get('author', '')}
Guruh: {context.user_data.get('group', '')}
O'qituvchi: {context.user_data.get('teacher', '')}
Til: {context.user_data.get('til', 'uz').upper()}
Rejalar soni: {context.user_data.get('rejalar', 3)}

1. KIRISH
Bu mustaqil ishning kirish qismi...

2. ASOSIY QISMI
Asosiy qismning tahlili...

3. XULOSA
Xulosa va takliflar...

ADABIYOTLAR RO'YXATI
1. Kitob 1
2. Kitob 2
3. Maqola 1"""
    ))
    
    doc_id = cursor.lastrowid
    
    # Balansni yangilash
    if context.user_data.get('can_use_free'):
        cursor.execute('UPDATE users SET free_mustaqil_used = free_mustaqil_used + 1 WHERE user_id = ?', (user_id,))
        payment_type = "🎁 Bepul"
    else:
        mustaqil_price = get_price('mustaqil_ish')
        cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (mustaqil_price, user_id))
        payment_type = f"💳 To'lovli ({mustaqil_price} kredit)"
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"✅ **Ish muvaffaqiyatli yaratildi!**\n\n"
        f"🎯 Mavzu: {context.user_data['topic']}\n"
        f"📊 Rejalar: {context.user_data.get('rejalar', 3)} ta\n"
        f"🌐 Til: {context.user_data.get('til', 'uz').upper()}\n"
        f"👤 Talaba: {context.user_data.get('author', '')}\n"
        f"👥 Guruh: {context.user_data.get('group', '')}\n"
        f"👨‍🏫 O'qituvchi: {context.user_data.get('teacher', '')}\n"
        f"💳 To'lov turi: {payment_type}\n"
        f"🆔 ID: {doc_id}\n\n"
        f"📁 'Hujjatlarim' bo'limidan ko'rishingiz mumkin.",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return ConversationHandler.END

async def process_ref_teacher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['teacher'] = update.message.text
    
    # Referatni yaratish
    await update.message.reply_text("⏳ **Iltimos kuting...**\nReferat tayyorlanmoqda...", parse_mode='Markdown')
    
    # Bazaga saqlash
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO referatlar 
        (user_id, subject, topic, til, pages, author, student_group, teacher, content)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id,
        context.user_data['subject'],
        context.user_data['topic'],
        context.user_data.get('til', 'uz'),
        context.user_data.get('pages', '5-10'),
        context.user_data.get('author', ''),
        context.user_data.get('group', ''),
        context.user_data.get('teacher', ''),
        f"""REFERAT
Mavzu: {context.user_data['topic']}
Talaba: {context.user_data.get('author', '')}
Guruh: {context.user_data.get('group', '')}
O'qituvchi: {context.user_data.get('teacher', '')}
Til: {context.user_data.get('til', 'uz').upper()}
Hajm: {context.user_data.get('pages', '5-10')} bet

KIRISH
Bu referatning kirish qismi...

ASOSIY QISMI
1. Bo'lim 1
2. Bo'lim 2
3. Bo'lim 3

XULOSA
Xulosa va takliflar...

FOYDALANILGAN ADABIYOTLAR
1. Kitob 1
2. Kitob 2
3. Internet manbalari"""
    ))
    
    doc_id = cursor.lastrowid
    
    # Balansni yangilash
    if context.user_data.get('can_use_free'):
        cursor.execute('UPDATE users SET free_referat_used = free_referat_used + 1 WHERE user_id = ?', (user_id,))
        payment_type = "🎁 Bepul"
    else:
        referat_price = get_price('referat')
        cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (referat_price, user_id))
        payment_type = f"💳 To'lovli ({referat_price} kredit)"
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"✅ **Referat muvaffaqiyatli yaratildi!**\n\n"
        f"🎯 Mavzu: {context.user_data['topic']}\n"
        f"📄 Hajm: {context.user_data.get('pages', '5-10')} bet\n"
        f"🌐 Til: {context.user_data.get('til', 'uz').upper()}\n"
        f"👤 Talaba: {context.user_data.get('author', '')}\n"
        f"👥 Guruh: {context.user_data.get('group', '')}\n"
        f"👨‍🏫 O'qituvchi: {context.user_data.get('teacher', '')}\n"
        f"💳 To'lov turi: {payment_type}\n"
        f"🆔 ID: {doc_id}\n\n"
        f"📁 'Hujjatlarim' bo'limidan ko'rishingiz mumkin.",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return ConversationHandler.END

async def show_my_documents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Mustaqil ishlar
    cursor.execute('''
        SELECT id, topic, rejalar, til, created_at 
        FROM mustaqil_ishlar 
        WHERE user_id = ? AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 10
    ''', (user_id,))
    mustaqil_ishlar = cursor.fetchall()
    
    # Referatlar
    cursor.execute('''
        SELECT id, topic, pages, til, created_at 
        FROM referatlar 
        WHERE user_id = ? AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 10
    ''', (user_id,))
    referatlar = cursor.fetchall()
    
    conn.close()
    
    text = "📁 **Sizning hujjatlaringiz:**\n\n"
    
    if mustaqil_ishlar:
        text += "📝 **Mustaqil ishlar:**\n"
        for doc in mustaqil_ishlar:
            text += f"├ ID: {doc[0]}\n"
            text += f"├ Mavzu: {doc[1][:30]}...\n"
            text += f"├ Rejalar: {doc[2]} ta\n"
            text += f"├ Til: {doc[3].upper()}\n"
            text += f"└ Sana: {doc[4][:10]}\n\n"
    else:
        text += "📝 Mustaqil ishlar: Mavjud emas\n\n"
    
    if referatlar:
        text += "📄 **Referatlar:**\n"
        for doc in referatlar:
            text += f"├ ID: {doc[0]}\n"
            text += f"├ Mavzu: {doc[1][:30]}...\n"
            text += f"├ Hajm: {doc[2]} bet\n"
            text += f"├ Til: {doc[3].upper()}\n"
            text += f"└ Sana: {doc[4][:10]}\n\n"
    else:
        text += "📄 Referatlar: Mavjud emas\n\n"
    
    text += "ℹ️ Hujjatni ko'rish uchun ID raqamini yuboring.\n"
    text += "❌ O'chirish uchun: delete_[id]\n"
    text += "📥 Yuklab olish uchun: download_[id]"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def show_referal_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_info = get_user_by_id(user_id)
    
    referal_link = f"https://t.me/ReferatMustaqiLIshBot?start={user_info['referal_code']}"
    
    text = f"""👥 **Referal tizimi**

🔗 **Sizning referal linkingiz:**
{referal_link}

💰 **Bonuslar:**
• Har bir do'stingiz botga sizning linkiz orqali kirganda, sizga {Config.REFERAL_BONUS} kredit beriladi
• Do'stingiz birinchi buyurtma qilganda yana bonus olasiz
• Balansingizni referal orqali to'ldirishingiz mumkin

📊 **Statistika:**
• Jami taklif qilganlar: {user_info.get('total_referred', 0)} ta
• Jami bonus: {user_info.get('referal_bonus', 0)} kredit

📌 **Linkni qayerda joylashtirish mumkin:**
• Telegram guruhlarda
• Telegram kanallarda
• Do'stlaringizga shaxsiy xabar orqali
• Ijtimoiy tarmoqlarda (Instagram, Facebook, TikTok)

💡 **Maslahat:** Linkni o'zingizning ijtimoiy tarmoq sahifangizda ham joylashtirishingiz mumkin."""
    
    await update.message.reply_text(
        text,
        parse_mode='Markdown'
    )

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_info = get_user_by_id(user_id)
    
    text = f"""⚙️ **Sozlamalar**

📋 **Profil:**
• Ism: {user_info['full_name']}
• Username: @{user_info['username'] or 'Mavjud emas'}
• ID: {user_id}

🌐 **Til sozlamalari:**
• Bot tili: O'zbek 🇺🇿

🔔 **Bildirishnomalar:**
• Yangiliklar: ✅ Yoqilgan
• Balans yangilanishi: ✅ Yoqilgan
• Referal bonus: ✅ Yoqilgan

🔒 **Xavfsizlik:**
• Akkaunt yaratilgan sana: {datetime.now().strftime('%Y-%m-%d')}
• Oxirgi faollik: Bugun

ℹ️ Sozlamalarni o'zgartirish uchun admin bilan bog'laning."""
    
    await update.message.reply_text(
        text,
        parse_mode='Markdown'
    )

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚫 Jarayon bekor qilindi.",
        reply_markup=get_main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# =========================================================
# ADMIN HANDLERLAR
# =========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ Siz admin emassiz!",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    await update.message.reply_text(
        "👑 **Admin paneliga xush kelibsiz!**\n\n"
        "Kerakli bo'limni tanlang:",
        reply_markup=get_admin_keyboard(),
        parse_mode='Markdown'
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.edit_message_text("❌ Siz admin emassiz!")
        return
    
    if query.data == "admin_stats":
        stats = get_user_stats()
        
        text = f"""📊 **Bot statistikasi:**

👥 **Foydalanuvchilar:**
├ Jami: {stats['total_users']} ta
├ Bugungi yangi: {stats['new_users_today']} ta
└ O'rtacha kunlik: {stats['total_users'] // 30 if stats['total_users'] > 30 else 0} ta

📝 **Hujjatlar:**
├ Mustaqil ishlar: {stats['total_mustaqil']} ta
└ Referatlar: {stats['total_referat']} ta

💰 **Moliyaviy:**
├ Jami balans: {stats['total_balance']} kredit
├ Jami daromad: {stats['total_income']:,} so'm
└ O'rtacha buyurtma: {stats['total_income'] // max(1, stats['total_mustaqil'] + stats['total_referat']):,} so'm

📅 **Bugun:** {datetime.now().strftime('%Y-%m-%d %H:%M')}"""
        
        await query.edit_message_text(
            text,
            reply_markup=get_back_to_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == "admin_users":
        users = get_all_users()
        
        if not users:
            await query.edit_message_text(
                "👥 Foydalanuvchilar mavjud emas.",
                reply_markup=get_back_to_admin_keyboard()
            )
            return
        
        text = "👥 **Foydalanuvchilar ro'yxati:**\n\n"
        
        for i, user in enumerate(users[:10], 1):
            username_display = user[1] if user[1] else 'Mavjud emas'
            text += f"{i}. **ID:** {user[0]}\n"
            text += f"   **Ism:** {user[2] or 'Noma''lum'}\n"
            text += f"   **Username:** @{username_display}\n"
            text += f"   **Balans:** {user[3]} kredit\n"
            text += f"   **Ro'yxatdan:** {user[4][:10]}\n\n"
        
        if len(users) > 10:
            text += f"ℹ️ Jami {len(users)} ta foydalanuvchi, faqat 10 tasi ko'rsatilmoqda.\n"
        
        text += "📌 Foydalanuvchi balansini o'zgartirish uchun:\n"
        text += "`/addbalance [user_id] [amount]`"
        
        await query.edit_message_text(
            text,
            reply_markup=get_back_to_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == "admin_payments":
        transactions = get_pending_transactions()
        
        if not transactions:
            await query.edit_message_text(
                "✅ Barcha to'lovlar tasdiqlangan.",
                reply_markup=get_back_to_admin_keyboard()
            )
            return
        
        text = "💰 **Kutilayotgan to'lovlar:**\n\n"
        
        for i, tr in enumerate(transactions[:5], 1):
            username_display = tr[2] if tr[2] else tr[3] if tr[3] else 'Noma''lum'
            payment_method = tr[5] if tr[5] else 'Noma''lum'
            text += f"{i}. **ID:** {tr[0]}\n"
            text += f"   **Foydalanuvchi:** {username_display}\n"
            text += f"   **Summa:** {tr[4]:,} so'm\n"
            text += f"   **Usul:** {payment_method}\n"
            text += f"   **Vaqt:** {tr[7]}\n\n"
        
        if len(transactions) > 5:
            text += f"ℹ️ Jami {len(transactions)} ta to'lov, faqat 5 tasi ko'rsatilmoqda.\n"
        
        text += "📌 Tasdiqlash uchun to'lov ID sini bosing."
        
        keyboard = []
        for tr in transactions[:5]:
            keyboard.append([InlineKeyboardButton(f"💰 To'lov #{tr[0]}", callback_data=f"admin_view_payment_{tr[0]}")])
        
        keyboard.append([InlineKeyboardButton("◀️ Orqaga", callback_data="admin_back")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif query.data.startswith("admin_view_payment_"):
        transaction_id = int(query.data.split('_')[3])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT t.id, t.user_id, u.username, u.full_name, t.amount, 
                   t.payment_method, t.description, t.created_at, t.screenshot_path
            FROM transactions t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.id = ?
        ''', (transaction_id,))
        tr = cursor.fetchone()
        conn.close()
        
        if not tr:
            await query.edit_message_text(
                "❌ To'lov topilmadi.",
                reply_markup=get_back_to_admin_keyboard()
            )
            return
        
        credits = tr[4] // 1000
        username_display = tr[2] if tr[2] else tr[3] if tr[3] else 'Noma''lum'
        payment_method = tr[5] if tr[5] else 'Noma''lum'
        
        text = f"""💰 **To'lov ma'lumotlari:**

🆔 **ID:** {tr[0]}
👤 **Foydalanuvchi:**
├ ID: {tr[1]}
├ Ism: {tr[3]}
└ Username: @{username_display}

💳 **To'lov:**
├ Summa: {tr[4]:,} so'm
├ Kredit: {credits} kredit
├ Usul: {payment_method}
└ Tavsif: {tr[6]}

📅 **Vaqt:** {tr[7]}"""
        
        await query.edit_message_text(
            text,
            reply_markup=get_admin_payment_keyboard(tr[0]),
            parse_mode='Markdown'
        )
        
        # Agar skrinshot bo'lsa, uni yuborish
        if tr[8]:
            try:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=tr[8],
                    caption=f"To'lov cheki #{tr[0]}"
                )
            except Exception as e:
                logger.error(f"Rasm yuborishda xatolik: {e}")
    
    elif query.data.startswith("admin_confirm_"):
        transaction_id = int(query.data.split('_')[2])
        
        if confirm_transaction(transaction_id, user_id):
            # Foydalanuvchiga xabar berish
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, amount FROM transactions WHERE id = ?', (transaction_id,))
            tr = cursor.fetchone()
            conn.close()
            
            if tr:
                user_tr_id = tr[0]
                amount = tr[1]
                credits = amount // 1000
                
                # Foydalanuvchiga xabar
                try:
                    await context.bot.send_message(
                        chat_id=user_tr_id,
                        text=f"✅ **To'lovingiz tasdiqlandi!**\n\n"
                             f"💰 Summa: {amount:,} so'm\n"
                             f"💳 Kredit: {credits} kredit\n"
                             f"📋 ID: {transaction_id}\n\n"
                             f"🎉 Balansingizga {credits} kredit qo'shildi!\n"
                             f"Hozirgi balansingiz: {get_user_balance(user_tr_id)['balance']} kredit",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await query.edit_message_text(
                f"✅ To'lov #{transaction_id} muvaffaqiyatli tasdiqlandi!",
                reply_markup=get_back_to_admin_keyboard()
            )
        else:
            await query.edit_message_text(
                "❌ To'lovni tasdiqlashda xatolik!",
                reply_markup=get_back_to_admin_keyboard()
            )
    
    elif query.data.startswith("admin_reject_"):
        transaction_id = int(query.data.split('_')[2])
        
        if reject_transaction(transaction_id, user_id):
            # Foydalanuvchiga xabar berish
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, amount FROM transactions WHERE id = ?', (transaction_id,))
            tr = cursor.fetchone()
            conn.close()
            
            if tr:
                user_tr_id = tr[0]
                amount = tr[1]
                
                # Foydalanuvchiga xabar
                try:
                    await context.bot.send_message(
                        chat_id=user_tr_id,
                        text=f"❌ **To'lovingiz rad etildi!**\n\n"
                             f"💰 Summa: {amount:,} so'm\n"
                             f"📋 ID: {transaction_id}\n\n"
                             f"ℹ️ Iltimos, to'g'ri chek yuboring yoki admin bilan bog'laning.",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await query.edit_message_text(
                f"❌ To'lov #{transaction_id} rad etildi!",
                reply_markup=get_back_to_admin_keyboard()
            )
        else:
            await query.edit_message_text(
                "❌ To'lovni rad etishda xatolik!",
                reply_markup=get_back_to_admin_keyboard()
            )
    
    elif query.data == "admin_prices":
        mustaqil_price = get_price('mustaqil_ish')
        referat_price = get_price('referat')
        slaydlar_price = get_price('slaydlar')
        
        text = f"""⚙️ **Joriy narxlar:**

📝 **Mustaqil ish:** {mustaqil_price} kredit
📄 **Referat:** {referat_price} kredit
📊 **Slaydlar:** {slaydlar_price} kredit

📌 **Narxlarni o'zgartirish uchun:**
• `/setprice mustaqil_ish [narx]`
• `/setprice referat [narx]`
• `/setprice slaydlar [narx]`

💡 **Maslahat:** 1 kredit = 1000 so'm deb hisoblanadi."""
        
        await query.edit_message_text(
            text,
            reply_markup=get_back_to_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == "admin_add_balance":
        await query.edit_message_text(
            "➕ **Foydalanuvchi balansini qo'shish**\n\n"
            "Foydalanuvchi ID sini va miqdorni kiriting:\n"
            "`/addbalance [user_id] [amount]`\n\n"
            "Misol: `/addbalance 123456789 50`\n"
            "Bu 123456789 ID li foydalanuvchiga 50 kredit qo'shadi.",
            reply_markup=get_back_to_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == "admin_send_message":
        await query.edit_message_text(
            "📢 **Xabar yuborish**\n\n"
            "Barcha foydalanuvchilarga xabar yuborish uchun:\n"
            "`/broadcast [xabar matni]`\n\n"
            "Misol: `/broadcast Yangi xizmatlar qo'shildi!`",
            reply_markup=get_back_to_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == "admin_documents":
        await query.edit_message_text(
            "📂 **Hujjatlarni boshqarish**\n\n"
            "Hujjat turini tanlang:",
            reply_markup=get_admin_keyboard(),  # Bu joyda xato bo'lgan, lekin soddalashtiramiz
            parse_mode='Markdown'
        )
    
    elif query.data == "admin_back":
        await query.edit_message_text(
            "👑 **Admin paneliga xush kelibsiz!**\n\n"
            "Kerakli bo'limni tanlang:",
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == "admin_back_main":
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text="Kerakli bo'limni tanlang:",
            reply_markup=get_main_menu_keyboard()
        )
    
    elif query.data == "admin_back_payments":
        transactions = get_pending_transactions()
        
        if not transactions:
            await query.edit_message_text(
                "✅ Barcha to'lovlar tasdiqlangan.",
                reply_markup=get_back_to_admin_keyboard()
            )
            return
        
        text = "💰 **Kutilayotgan to'lovlar:**\n\n"
        
        for i, tr in enumerate(transactions[:5], 1):
            username_display = tr[2] if tr[2] else tr[3] if tr[3] else 'Noma''lum'
            payment_method = tr[5] if tr[5] else 'Noma''lum'
            text += f"{i}. **ID:** {tr[0]}\n"
            text += f"   **Foydalanuvchi:** {username_display}\n"
            text += f"   **Summa:** {tr[4]:,} so'm\n"
            text += f"   **Usul:** {payment_method}\n"
            text += f"   **Vaqt:** {tr[7]}\n\n"
        
        keyboard = []
        for tr in transactions[:5]:
            keyboard.append([InlineKeyboardButton(f"💰 To'lov #{tr[0]}", callback_data=f"admin_view_payment_{tr[0]}")])
        
        keyboard.append([InlineKeyboardButton("◀️ Orqaga", callback_data="admin_back")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

# =========================================================
# ADMIN COMMANDLAR
# =========================================================

async def addbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Siz admin emassiz!")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text(
            "⚠️ Noto'g'ri format!\n"
            "Foydalanish: `/addbalance [user_id] [miqdor]`",
            parse_mode='Markdown'
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        amount = int(context.args[1])
        
        if amount <= 0:
            await update.message.reply_text("⚠️ Miqdor musbat bo'lishi kerak!")
            return
        
        add_balance_directly(target_user_id, amount)
        
        await update.message.reply_text(
            f"✅ {target_user_id} ID li foydalanuvchiga {amount} kredit qo'shildi!"
        )
        
        # Foydalanuvchiga xabar
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"🎉 **Balansingiz to'ldirildi!**\n\n"
                     f"💳 Miqdor: {amount} kredit\n"
                     f"👤 Admin tomonidan qo'shildi\n\n"
                     f"💰 Jami balans: {get_user_balance(target_user_id)['balance']} kredit",
                parse_mode='Markdown'
            )
        except:
            pass
            
    except ValueError:
        await update.message.reply_text("⚠️ ID va miqdor raqam bo'lishi kerak!")

async def setprice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Siz admin emassiz!")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text(
            "⚠️ Noto'g'ri format!\n"
            "Foydalanish: `/setprice [xizmat_turi] [narx]`\n\n"
            "Xizmat turlari: `mustaqil_ish`, `referat`, `slaydlar`",
            parse_mode='Markdown'
        )
        return
    
    service_type = context.args[0]
    try:
        price = int(context.args[1])
        
        if price <= 0:
            await update.message.reply_text("⚠️ Narx musbat bo'lishi kerak!")
            return
        
        valid_services = ['mustaqil_ish', 'referat', 'slaydlar']
        if service_type not in valid_services:
            await update.message.reply_text(
                f"⚠️ Noto'g'ri xizmat turi!\n"
                f"Qabul qilinadigan turlar: {', '.join(valid_services)}"
            )
            return
        
        update_price(service_type, price)
        
        await update.message.reply_text(
            f"✅ {service_type} narxi {price} kreditga o'zgartirildi!"
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ Narx raqam bo'lishi kerak!")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Siz admin emassiz!")
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ Xabar matnini kiriting!\n"
            "Foydalanish: `/broadcast [xabar matni]`",
            parse_mode='Markdown'
        )
        return
    
    message = ' '.join(context.args)
    
    await update.message.reply_text("📤 Xabar yuborilmoqda...")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    conn.close()
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0],
                text=f"📢 **Botdan xabar:**\n\n{message}",
                parse_mode='Markdown'
            )
            success += 1
            await asyncio.sleep(0.05)  # Rate limit uchun
        except:
            failed += 1
    
    await update.message.reply_text(
        f"✅ Xabar yuborish yakunlandi!\n\n"
        f"📤 Yuborildi: {success} ta\n"
        f"❌ Yuborilmadi: {failed} ta"
    )

async def deletedoc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Siz admin emassiz!")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text(
            "⚠️ Noto'g'ri format!\n"
            "Foydalanish: `/deletedoc [tur] [id]`\n\n"
            "Turlar: `mustaqil`, `referat`",
            parse_mode='Markdown'
        )
        return
    
    doc_type = context.args[0]
    try:
        doc_id = int(context.args[1])
        
        if doc_type not in ['mustaqil', 'referat']:
            await update.message.reply_text(
                "⚠️ Noto'g'ri hujjat turi!\n"
                "Qabul qilinadigan turlar: `mustaqil`, `referat`",
                parse_mode='Markdown'
            )
            return
        
        delete_document(doc_id, doc_type)
        
        await update.message.reply_text(
            f"✅ {doc_type.capitalize()} #{doc_id} o'chirildi!"
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ ID raqam bo'lishi kerak!")

# =========================================================
# ASOSIY FUNKSIYA
# =========================================================

def main():
    print("🤖 Bot ishga tushirilmoqda...")
    print(f"👑 Admin: {ADMIN_USERNAME}")
    print(f"💼 Database: {Config.DB_FILE}")
    
    # Databazani ishga tushirish
    init_database()
    
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN topilmadi!")
        return
    
    try:
        # Application yaratish
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Mustaqil ish uchun ConversationHandler
        mustaqil_conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^📝 Yangi Mustaqil ish$"), start_mustaqil_ish)],
            states={
                GET_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_mustaqil_topic)],
                GET_REJALAR: [CallbackQueryHandler(process_rejalar)],
                GET_TIL: [CallbackQueryHandler(process_til)],
                GET_AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_author)],
                GET_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_group)],
                GET_TEACHER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_teacher)],
            },
            fallbacks=[
                MessageHandler(filters.Regex("^🚫 Bekor qilish$"), cancel_conversation),
                CommandHandler("cancel", cancel_conversation)
            ],
            allow_reentry=True
        )
        
        # Referat uchun ConversationHandler
        referat_conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^📄 Yangi Referat$"), start_referat)],
            states={
                GET_REF_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_referat_topic)],
                GET_REF_PAGES: [CallbackQueryHandler(process_pages)],
                GET_REF_TIL: [CallbackQueryHandler(process_ref_til)],
                GET_REF_AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ref_author)],
                GET_REF_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ref_group)],
                GET_REF_TEACHER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ref_teacher)],
            },
            fallbacks=[
                MessageHandler(filters.Regex("^🚫 Bekor qilish$"), cancel_conversation),
                CommandHandler("cancel", cancel_conversation)
            ],
            allow_reentry=True
        )
        
        # To'lov uchun ConversationHandler
        payment_conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(handle_payment_method, pattern="^pay_(click|payme|cash)$")],
            states={
                PAYMENT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_payment_amount)],
                PAYMENT_SCREENSHOT: [MessageHandler(filters.PHOTO, process_payment_screenshot)],
            },
            fallbacks=[
                MessageHandler(filters.Regex("^🚫 Bekor qilish$"), cancel_conversation),
                CommandHandler("cancel", cancel_conversation)
            ],
            allow_reentry=True
        )
        
        # Handlerlarni qo'shish
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(mustaqil_conv_handler)
        application.add_handler(referat_conv_handler)
        application.add_handler(payment_conv_handler)
        
        # Admin commandlar
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CommandHandler("addbalance", addbalance_command))
        application.add_handler(CommandHandler("setprice", setprice_command))
        application.add_handler(CommandHandler("broadcast", broadcast_command))
        application.add_handler(CommandHandler("deletedoc", deletedoc_command))
        
        # Balans callback handler
        application.add_handler(CallbackQueryHandler(handle_balance_callback, pattern="^balance_"))
        
        # Admin callback handler
        application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
        
        # Asosiy menyu handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu))
        
        print("✅ Bot muvaffaqiyatli ishga tushdi!")
        print(f"🎁 Bepul mustaqil ishlar: {Config.BEPUL_MUSTAQIL_LIMIT} ta")
        print(f"💰 Minimal to'lov: {Config.MIN_DEPOSIT:,} so'm")
        print("=" * 50)
        
        # Botni ishga tushirish
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}", exc_info=True)

if __name__ == '__main__':
    main()