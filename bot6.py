import asyncio
import random
import logging
import sqlite3
import os
import time
import threading
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, PhoneNumberInvalidError, PhoneCodeInvalidError, PhoneCodeExpiredError

# ========== KONFIGURATSIYA ==========
ADMIN_ID = 2091226701  # O'zingizning Telegram ID'ingiz
BOT_TOKEN = "8220522354:AAHk8lxrgvQAapNvV7u4-d-9A0zPT6ki5oI"  # @BotFather dan olingan token

# Telegram API ma'lumotlari (my.telegram.org dan oling)
API_ID = 16307694
API_HASH = "de4b653676e085ce3d0f3d64f8741ae4"

# Logging yoqish
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# SQLite bazasi
DB_FILE = "telegram_bot.db"

# Session fayllar papkasi
SESSIONS_DIR = "sessions"

# Global o'zgaruvchilar
is_sending = False
last_send_time = None
min_interval = 20  # Minimal interval (daqiqa)
max_interval = 25  # Maksimal interval (daqiqa)
random_messages = True  # Random xabarlarni yuborish

# ========== TELEGRAM CLIENT FUNCTIONS ==========

def init_sessions_dir():
    """Sessions papkasini yaratish"""
    if not os.path.exists(SESSIONS_DIR):
        os.makedirs(SESSIONS_DIR)
        logger.info(f"📁 Sessions papkasi yaratildi: {SESSIONS_DIR}")

def get_session_path(display_name):
    """Session fayl yo'lini olish"""
    return os.path.join(SESSIONS_DIR, f"{display_name}.session")

def session_exists(display_name):
    """Session fayli mavjudligini tekshirish"""
    session_path = get_session_path(display_name)
    return os.path.exists(session_path)

async def create_and_auth_session(user_id, display_name, phone):
    """Yangi session yaratish va avtorizatsiya qilish (foydalanuvchi uchun)"""
    try:
        session_path = get_session_path(display_name)
        
        # Telefon raqamni tozalash
        clean_phone = phone
        if phone.startswith('+'):
            clean_phone = phone[1:]
        
        # Telefon raqamni tekshirish
        if not clean_phone.isdigit():
            return False, "❌ **Noto'g'ri telefon raqam format!** Faqat raqamlar bo'lishi kerak."
        
        # Yangi client yaratish
        client = TelegramClient(
            session_path,
            API_ID,
            API_HASH,
            device_model="Android Phone",
            system_version="10",
            app_version="8.4",
            lang_code="en",
            system_lang_code="en-US"
        )
        
        await client.connect()
        
        if not await client.is_user_authorized():
            try:
                logger.info(f"📱 {clean_phone} raqamiga kod yuborilmoqda...")
                
                # Kod yuborish
                sent_code = await client.send_code_request(
                    phone=clean_phone,
                    force_sms=True
                )
                
                logger.info(f"✅ Kod yuborildi: {clean_phone} (User: {user_id}, Hash: {sent_code.phone_code_hash[:10]}...)")
                
                # SMS kodini bazaga saqlash
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO pending_sessions (display_name, phone, code_hash, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (display_name, clean_phone, sent_code.phone_code_hash, user_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                conn.commit()
                conn.close()
                
                await client.disconnect()
                
                return True, "code_sent"
                
            except FloodWaitError as e:
                await client.disconnect()
                wait_time = e.seconds
                if wait_time > 3600:
                    return False, f"⏳ **Telegram blokladi:** {wait_time//3600} soat kutish kerak"
                else:
                    return False, f"⏳ **Telegram blokladi:** {wait_time//60} daqiqa kutish kerak"
            except PhoneNumberInvalidError:
                await client.disconnect()
                return False, "❌ **Noto'g'ri telefon raqam!** Raqamni tekshiring."
            except Exception as e:
                await client.disconnect()
                logger.error(f"Kod yuborishda xato: {e}")
                return False, f"❌ **Xato:** {str(e)}"
        else:
            # Agar session allaqachon avtorizatsiya qilingan bo'lsa
            me = await client.get_me()
            await client.disconnect()
            
            # Bazada is_active ni yangilash
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('UPDATE accounts SET is_active = 1 WHERE display_name = ? AND user_id = ?', 
                          (display_name, user_id))
            conn.commit()
            conn.close()
            
            logger.info(f"✅ Session avtorizatsiya qilingan: {display_name} (User: {user_id})")
            return True, f"already_authorized: {me.first_name}"
            
    except Exception as e:
        logger.error(f"Session yaratishda xato: {e}")
        return False, f"❌ **Xato:** {str(e)}"

def get_pending_session(display_name, user_id=None):
    """Kutilayotgan session ma'lumotlarini olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if user_id:
        cursor.execute('SELECT phone, code_hash, user_id FROM pending_sessions WHERE display_name = ? AND user_id = ?', 
                      (display_name, user_id))
    else:
        cursor.execute('SELECT phone, code_hash, user_id FROM pending_sessions WHERE display_name = ?', (display_name,))
    
    result = cursor.fetchone()
    conn.close()
    return result

def remove_pending_session(display_name, user_id=None):
    """Kutilayotgan sessionni o'chirish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if user_id:
        cursor.execute('DELETE FROM pending_sessions WHERE display_name = ? AND user_id = ?', (display_name, user_id))
    else:
        cursor.execute('DELETE FROM pending_sessions WHERE display_name = ?', (display_name,))
    
    conn.commit()
    conn.close()

async def enter_code(display_name, code, user_id):
    """Kodni kiritish va sessionni tasdiqlash (foydalanuvchi uchun)"""
    try:
        session_path = get_session_path(display_name)
        
        if not os.path.exists(session_path):
            return False, "❌ **Session fayli topilmadi!** Avval /start buyrug'i bilan hisob qo'shing."
        
        # Pending session ma'lumotlarini olish
        pending_data = get_pending_session(display_name, user_id)
        if not pending_data:
            return False, "❌ **Kutilayotgan session topilmadi!** SMS kodini olganingizga ishonch hosil qiling yoki qayta urinib ko'ring."
        
        phone, code_hash, session_user_id = pending_data
        
        # User ID mosligini tekshirish
        if session_user_id != user_id:
            return False, "❌ **Bu hisob sizga tegishli emas!**"
        
        # Client yaratish
        client = TelegramClient(
            session_path,
            API_ID,
            API_HASH
        )
        
        await client.connect()
        
        try:
            logger.info(f"📱 Kod kiritilmoqda: {display_name}, Kod: {code}")
            
            # Sign in qilish
            await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=code_hash
            )
            
            # Foydalanuvchi ma'lumotlarini olish
            me = await client.get_me()
            await client.disconnect()
            
            # Pending sessionni o'chirish
            remove_pending_session(display_name, user_id)
            
            # Bazada is_active ni yangilash
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('UPDATE accounts SET is_active = 1, username = ? WHERE display_name = ? AND user_id = ?', 
                          (me.username, display_name, user_id))
            conn.commit()
            conn.close()
            
            logger.info(f"✅ Muvaffaqiyatli sign in: {display_name} (User: {user_id}, Name: {me.first_name})")
            
            return True, f"✅ **Session muvaffaqiyatli tasdiqlandi!**\n\n👤 **Ism:** {me.first_name} {me.last_name or ''}\n🔗 **Username:** @{me.username or 'Yoq'}\n📱 **Telefon:** +{phone}\n\nHisobingiz endi faol. Endi guruh qo'shishingiz va xabar yuborishingiz mumkin."
            
        except SessionPasswordNeededError:
            await client.disconnect()
            logger.info(f"🔐 2FA kerak: {display_name}")
            return False, "2fa_required"
            
        except PhoneCodeInvalidError:
            await client.disconnect()
            logger.warning(f"❌ Noto'g'ri kod: {display_name}")
            return False, "❌ **Noto'g'ri kod!** Iltimos, to'g'ri SMS kodini kiriting."
            
        except PhoneCodeExpiredError:
            await client.disconnect()
            logger.warning(f"❌ Kod muddati o'tgan: {display_name}")
            return False, "❌ **Kod muddati o'tgan!** Yangi kod olish uchun qayta urinib ko'ring."
            
        except Exception as e:
            await client.disconnect()
            logger.error(f"Kod kiritishda xato: {e}")
            return False, f"❌ **Kod kiritishda xato:** {str(e)}"
            
    except Exception as e:
        logger.error(f"Kod kiritishda xato: {e}")
        return False, f"❌ **Xato:** {str(e)}"

async def enter_password(display_name, password, user_id):
    """2FA parolini kiritish (foydalanuvchi uchun)"""
    try:
        session_path = get_session_path(display_name)
        
        if not os.path.exists(session_path):
            return False, "❌ **Session fayli topilmadi!**"
        
        # Hisob ma'lumotlarini olish
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT phone FROM accounts WHERE display_name = ? AND user_id = ?', (display_name, user_id))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return False, "❌ **Hisob topilmadi yoki sizga tegishli emas!**"
        
        phone = result[0]
        
        # Client yaratish
        client = TelegramClient(
            session_path,
            API_ID,
            API_HASH
        )
        
        await client.connect()
        
        try:
            logger.info(f"🔐 2FA parol kiritilmoqda: {display_name}")
            
            # Parolni kiritish
            await client.sign_in(password=password)
            
            # Foydalanuvchi ma'lumotlarini olish
            me = await client.get_me()
            await client.disconnect()
            
            # Bazada is_active ni yangilash
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('UPDATE accounts SET is_active = 1, username = ? WHERE display_name = ? AND user_id = ?', 
                          (me.username, display_name, user_id))
            conn.commit()
            conn.close()
            
            logger.info(f"✅ 2FA muvaffaqiyatli: {display_name}")
            
            return True, f"✅ **2FA parol tasdiqlandi!**\n\n👤 **Ism:** {me.first_name} {me.last_name or ''}\n🔗 **Username:** @{me.username or 'Yoq'}\n📱 **Telefon:** +{phone}\n\nHisobingiz endi to'liq faol holatda."
            
        except Exception as e:
            await client.disconnect()
            logger.error(f"Parol kiritishda xato: {e}")
            return False, f"❌ **Parol noto'g'ri:** {str(e)}"
            
    except Exception as e:
        logger.error(f"Parol kiritishda xato: {e}")
        return False, f"❌ **Xato:** {str(e)}"

async def test_session(display_name, user_id):
    """Sessionni test qilish"""
    try:
        session_path = get_session_path(display_name)
        
        if not os.path.exists(session_path):
            return False, "❌ **Session fayli topilmadi!**"
        
        # Hisob ma'lumotlarini olish
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT phone FROM accounts WHERE display_name = ? AND user_id = ?', (display_name, user_id))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return False, "❌ **Hisob topilmadi yoki sizga tegishli emas!**"
        
        phone = result[0]
        
        # Client yaratish
        client = TelegramClient(
            session_path,
            API_ID,
            API_HASH
        )
        
        await client.connect()
        
        if await client.is_user_authorized():
            # Foydalanuvchi ma'lumotlarini olish
            me = await client.get_me()
            await client.disconnect()
            
            return True, f"✅ **Session faol!**\n\n👤 **Ism:** {me.first_name} {me.last_name or ''}\n📞 **Telefon:** +{phone}\n🔗 **Username:** @{me.username or 'Yoq'}\n🆔 **ID:** {me.id}"
        else:
            await client.disconnect()
            return False, "❌ **Session avtorizatsiya qilinmagan!** Kod kiritishingiz kerak."
            
    except Exception as e:
        logger.error(f"Session testda xato: {e}")
        return False, f"❌ **Xato:** {str(e)}"

async def send_message_to_group(display_name, group_identifier, message):
    """Guruhga xabar yuborish"""
    try:
        session_path = get_session_path(display_name)
        
        if not os.path.exists(session_path):
            return False, "❌ **Session fayli topilmadi!**"
        
        # Client yaratish
        client = TelegramClient(
            session_path,
            API_ID,
            API_HASH
        )
        
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            return False, "❌ **Session avtorizatsiya qilinmagan!**"
        
        try:
            # Guruhni topish
            entity = None
            
            # Guruhni turli formatlarda topish
            try:
                if group_identifier.startswith('@'):
                    entity = await client.get_entity(group_identifier)
                elif group_identifier.startswith('https://t.me/'):
                    username = group_identifier.split('/')[-1]
                    if username.startswith('+'):
                        entity = await client.get_entity(username)
                    else:
                        entity = await client.get_entity(f"@{username}")
                elif group_identifier.startswith('-100'):
                    # Channel/Chat ID
                    entity = await client.get_entity(int(group_identifier))
                else:
                    # Username sifatida urinib ko'rish
                    if group_identifier.startswith('+'):
                        entity = await client.get_entity(group_identifier)
                    else:
                        entity = await client.get_entity(f"@{group_identifier}")
            except Exception as e:
                await client.disconnect()
                return False, f"❌ **Guruh topilmadi:** {str(e)}"
            
            # Xabar yuborish
            await client.send_message(entity, message)
            await client.disconnect()
            
            return True, f"✅ **Xabar yuborildi:** {group_identifier}"
            
        except Exception as e:
            await client.disconnect()
            return False, f"❌ **Xabar yuborishda xato:** {str(e)}"
            
    except Exception as e:
        logger.error(f"Xabar yuborishda xato: {e}")
        return False, f"❌ **Xato:** {str(e)}"

# ========== YANGI FUNKSIYALAR ==========

async def send_broadcast_message(context, message_text):
    """Admin barcha foydalanuvchilarga xabar yuborishi"""
    users = get_all_users()
    sent_count = 0
    failed_count = 0
    failed_users = []
    
    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 **ADMIN XABARI**\n\n{message_text}\n\n_Admin tomonidan yuborildi_"
            )
            sent_count += 1
        except Exception as e:
            logger.error(f"Foydalanuvchiga xabar yuborishda xato (ID: {user_id}): {e}")
            failed_count += 1
            failed_users.append(str(user_id))
        await asyncio.sleep(0.1)  # Flooddan qochish uchun
    
    return sent_count, failed_count, failed_users

def check_phone_availability(phone, user_id=None):
    """Telefon raqam boshqa foydalanuvchi tomonidan ishlatilganligini tekshirish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if user_id:
        # O'ziga tegishli raqamni qayta ishlatish mumkin
        cursor.execute('SELECT id, user_id FROM accounts WHERE phone = ?', (phone,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            # Agar raqam o'ziga tegishli bo'lsa, qayta ishlatish mumkin
            if result[1] == user_id:
                return False  # O'z raqamini qayta ishlatish mumkin
            else:
                return True   # Boshqa foydalanuvchi raqamini ishlatib bo'lmaydi
        else:
            return False      # Raqam hech kimda yo'q
    else:
        cursor.execute('SELECT id FROM accounts WHERE phone = ?', (phone,))
        result = cursor.fetchone()
        conn.close()
        return result is not None

def delete_account_from_db(display_name, user_id):
    """Foydalanuvchi hisobini bazadan o'chirish"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Avval hisobni tekshirish
        cursor.execute('SELECT phone FROM accounts WHERE display_name = ? AND user_id = ?', (display_name, user_id))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return False, "❌ **Hisob topilmadi yoki sizga tegishli emas!**"
        
        phone = result[0]
        
        # Hisobni o'chirish
        cursor.execute('DELETE FROM accounts WHERE display_name = ? AND user_id = ?', (display_name, user_id))
        deleted_accounts = cursor.rowcount
        
        # Guruhlarni o'chirish
        cursor.execute('DELETE FROM groups WHERE account_display_name = ? AND user_id = ?', (display_name, user_id))
        deleted_groups = cursor.rowcount
        
        # Pending sessionni o'chirish
        cursor.execute('DELETE FROM pending_sessions WHERE display_name = ? AND user_id = ?', (display_name, user_id))
        
        conn.commit()
        conn.close()
        
        # Session faylini o'chirish
        session_path = get_session_path(display_name)
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
                deleted_session = True
            except:
                deleted_session = False
        else:
            deleted_session = False
        
        # Log qo'shish
        log_session_action(display_name, "delete_account", "success", f"User {user_id} deleted account")
        
        return True, f"✅ **Hisob muvaffaqiyatli o'chirildi!**\n\n📱 **Hisob:** {display_name}\n📞 **Raqam:** +{phone}\n👥 **Guruhlar:** {deleted_groups} ta o'chirildi\n📁 **Session fayli:** {'✅ Oʻchirildi' if deleted_session else '❌ Topilmadi'}"
        
    except Exception as e:
        logger.error(f"Hisobni o'chirishda xato: {e}")
        return False, f"❌ **Xatolik:** {str(e)}"

def get_account_details(user_id, display_name):
    """Hisob ma'lumotlarini olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT phone, country_code, username, is_active, is_premium, subscription_end 
        FROM accounts 
        WHERE user_id = ? AND display_name = ?
    ''', (user_id, display_name))
    result = cursor.fetchone()
    conn.close()
    return result

# ========== DATABASE FUNCTIONS ==========

def init_database():
    """Bazani yaratish"""
    if os.path.exists(DB_FILE):
        print(f"✅ Baza mavjud: {DB_FILE}")
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        display_name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        country_code TEXT,
        username TEXT,
        is_active INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        subscription_end DATETIME,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS groups (
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
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        status TEXT DEFAULT 'pending',
        admin_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_intervals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        min_interval INTEGER DEFAULT 20,
        max_interval INTEGER DEFAULT 25,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS pending_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        display_name TEXT,
        phone TEXT,
        code_hash TEXT,
        user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(display_name, user_id)
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS session_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        display_name TEXT,
        action TEXT,
        status TEXT,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Index yaratish
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON accounts(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_accounts_phone ON accounts(phone)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_intervals_user_id ON user_intervals(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pending_sessions_display_name ON pending_sessions(display_name)')
    
    # Default sozlamalar
    default_settings = [
        ('min_interval', '20'),
        ('max_interval', '25'),
        ('random_messages', 'true'),
        ('welcome_message', 'Botdan foydalanish uchun ruxsat kerak. Ruxsat olish uchun @Okean_manager ga murojaat qiling.'),
        ('admin_contact', '@Okean_manager'),
        ('api_id', str(API_ID)),
        ('api_hash', API_HASH)
    ]
    
    for key, value in default_settings:
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
    
    conn.commit()
    conn.close()
    print("✅ Baza yaratildi/tekshirildi")

def save_setting(key, value):
    """Setting saqlash"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
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

# ========== USER INTERVAL FUNCTIONS ==========

def save_user_interval(user_id, min_interval, max_interval):
    """Foydalanuvchi intervalini saqlash"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_intervals (user_id, min_interval, max_interval) 
        VALUES (?, ?, ?)
    ''', (user_id, min_interval, max_interval))
    conn.commit()
    conn.close()
    logger.info(f"✅ Foydalanuvchi {user_id} intervali saqlandi: {min_interval}-{max_interval} daqiqa")

def get_user_interval(user_id):
    """Foydalanuvchi intervalini olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT min_interval, max_interval FROM user_intervals WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0], result[1]
    else:
        # Default interval
        return 20, 25

def get_next_account_number(user_id):
    """Foydalanuvchi uchun keyingi account raqamini olish (max 5 ta)"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT display_name FROM accounts WHERE user_id = ? AND display_name LIKE "account%"', (user_id,))
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
        # Faqat 5 tagacha ruxsat berish
        if len(numbers) >= 5:
            return None  # 5 tadan ko'p bo'lmasligi kerak
        return max(numbers) + 1
    else:
        return 1

def get_user_accounts_count(user_id):
    """Foydalanuvchi hisoblari soni"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM accounts WHERE user_id = ?', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def add_user_account(user_id, phone="", country_code="", username="", display_name=None):
    """Foydalanuvchi hisobini qo'shish (max 5 ta)"""
    # Avval hisoblar sonini tekshirish
    accounts_count = get_user_accounts_count(user_id)
    if accounts_count >= 5:
        logger.warning(f"Foydalanuvchi {user_id} allaqachon 5 ta hisobga ega")
        return None
    
    # Telefon raqam takrorlanmasligini tekshirish
    if phone and check_phone_availability(phone, user_id):
        logger.warning(f"Bu telefon raqam allaqachon boshqa foydalanuvchi tomonidan ishlatilmoqda: {phone}")
        return None
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        if not display_name:
            account_number = get_next_account_number(user_id)
            if account_number is None:
                logger.warning(f"Foydalanuvchi {user_id} uchun hisob limitiga yetildi (5 ta)")
                return None
            display_name = f"account{account_number}"
        
        cursor.execute('''
            INSERT INTO accounts (user_id, display_name, phone, country_code, username, is_active, is_premium) 
            VALUES (?, ?, ?, ?, ?, 0, 0)
        ''', (user_id, display_name, phone, country_code, username))
        conn.commit()
        
        logger.info(f"✅ Hisob qo'shildi: {display_name} (User: {user_id}, Phone: {phone})")
        
        return display_name
        
    except sqlite3.IntegrityError as e:
        logger.error(f"Bazaga qo'shishda xato: {e}")
        return None
    finally:
        conn.close()

def get_user_accounts(user_id):
    """Foydalanuvchi hisoblarini olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT display_name, phone, country_code, username, is_active, is_premium, subscription_end 
        FROM accounts 
        WHERE user_id = ? 
        ORDER BY display_name
    ''', (user_id,))
    accounts = cursor.fetchall()
    conn.close()
    return accounts

def get_user_by_display_name(display_name):
    """Display name bo'yicha foydalanuvchini topish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM accounts WHERE display_name = ?', (display_name,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_all_users():
    """Barcha foydalanuvchilarni olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT user_id FROM accounts WHERE user_id != ?', (ADMIN_ID,))
    users = cursor.fetchall()
    conn.close()
    return [u[0] for u in users]

def get_user_subscription(user_id):
    """Foydalanuvchi obunasini tekshirish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT subscription_end, is_premium FROM accounts 
        WHERE user_id = ? AND is_active = 1
        ORDER BY subscription_end DESC LIMIT 1
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return None, False
    
    subscription_end, is_premium = result
    return subscription_end, bool(is_premium)

def update_user_subscription(user_id, days):
    """Foydalanuvchiga obuna berish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # subscription_end_str va is_premium ni avvaldan hisoblab qo'yamiz
        if days > 0:
            subscription_end = datetime.now() + timedelta(days=days)
            subscription_end_str = subscription_end.strftime('%Y-%m-%d %H:%M:%S')
            is_premium = 1
        else:
            subscription_end_str = None
            is_premium = 0

        # Avval foydalanuvchi borligini tekshirish
        cursor.execute('SELECT id FROM accounts WHERE user_id = ?', (user_id,))
        account_exists = cursor.fetchone()
        
        if not account_exists:
            # Agar hisob yo'q bo'lsa, yangi hisob yaratish
            account_number = get_next_account_number(user_id)
            if account_number is None:
                return None
            display_name = f"account{account_number}"
            
            cursor.execute('''
                INSERT INTO accounts (user_id, display_name, phone, country_code, username, is_active, is_premium, subscription_end) 
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ''', (user_id, display_name, "", "", "", is_premium, subscription_end_str))
            conn.commit()
        
        cursor.execute('''
            UPDATE accounts 
            SET subscription_end = ?, is_premium = ?, is_active = 1 
            WHERE user_id = ?
        ''', (subscription_end_str, is_premium, user_id))
        
        conn.commit()
        
        if days > 0:
            cursor.execute('UPDATE groups SET is_active = 1 WHERE user_id = ?', (user_id,))
            conn.commit()
        
        return subscription_end_str if days > 0 else None
    except Exception as e:
        logger.error(f"update_user_subscription xatosi: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def delete_user_data(user_id):
    """Foydalanuvchi ma'lumotlarini tozalash"""
    try:
        user_id = int(user_id)

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Session fayllarini o'chirish
        cursor.execute('SELECT display_name FROM accounts WHERE user_id = ?', (user_id,))
        accounts = cursor.fetchall()
        
        for account in accounts:
            display_name = account[0]
            session_path = get_session_path(display_name)
            if os.path.exists(session_path):
                try:
                    os.remove(session_path)
                except:
                    pass
        
        cursor.execute('DELETE FROM accounts WHERE user_id = ?', (user_id,))
        deleted_accounts = cursor.rowcount
        
        # Guruhlarni o'chirish
        cursor.execute('DELETE FROM groups WHERE user_id = ?', (user_id,))
        deleted_groups = cursor.rowcount
        
        # Pending sessionni o'chirish
        cursor.execute('DELETE FROM pending_sessions WHERE user_id = ?', (user_id,))
        
        conn.commit()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"delete_user_data xatosi: {e}")
        return False

def add_request(user_id, username, first_name, last_name):
    """Yangi so'rov qo'shish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Avval pending so'rov borligini tekshirish
    cursor.execute('SELECT id FROM requests WHERE user_id = ? AND status = "pending"', (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        conn.close()
        logger.info(f"⚠️ User {user_id} allaqachon so'rov yuborgan (ID: {existing[0]})")
        return existing[0]
    
    try:
        # Usernameni to'g'ri formatlash
        clean_username = username if username else ""
        
        cursor.execute('''
            INSERT INTO requests (user_id, username, first_name, last_name, status) 
            VALUES (?, ?, ?, ?, "pending")
        ''', (user_id, clean_username, first_name, last_name))
        
        conn.commit()
        
        # Yangi qo'shilgan so'rov ID sini olish
        cursor.execute('SELECT last_insert_rowid()')
        request_id = cursor.fetchone()[0]
        
        conn.close()
        logger.info(f"✅ Yangi so'rov qo'shildi: ID={request_id}, user_id={user_id}, username={clean_username}")
        return request_id
        
    except Exception as e:
        logger.error(f"So'rov qo'shishda xato: {e}")
        conn.close()
        return False

def get_pending_requests():
    """Kutilayotgan so'rovlarni olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, user_id, username, first_name, last_name, created_at 
        FROM requests 
        WHERE status = "pending" 
        ORDER BY created_at ASC
    ''')
    requests = cursor.fetchall()
    conn.close()
    
    logger.info(f"📊 Kutilayotgan so'rovlar soni: {len(requests)}")
    return requests

def get_request_by_id(request_id):
    """So'rovni ID bo'yicha olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def get_request_by_user_id(user_id):
    """So'rovni user_id bo'yicha olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM requests WHERE user_id = ? AND status = "pending" ORDER BY id DESC LIMIT 1', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def update_request_status(request_id, status, admin_note=""):
    """So'rov statusini yangilash"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE requests SET status = ?, admin_note = ? WHERE id = ?', (status, admin_note, request_id))
    conn.commit()
    conn.close()
    logger.info(f"📝 So'rov #{request_id} statusi '{status}' ga o'zgartirildi")
    return True

def add_group_batch(user_id, account_display_name, groups_list):
    """Ko'p guruhlarni bir vaqtda qo'shish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    added_count = 0
    skipped_count = 0
    
    for group_input in groups_list:
        group_input = group_input.strip()
        if not group_input:
            continue
        
        group_id = None
        group_title = group_input
        
        if group_input.startswith('@'):
            group_username = group_input[1:]
            group_id = group_input
        elif group_input.startswith('https://t.me/'):
            group_username = group_input.split('/')[-1]
            if '+' in username:
                group_id = group_username
            else:
                group_id = f"@{group_username}"
        elif group_input.startswith('-100'):
            group_id = group_input
            group_username = ""
        else:
            if group_input.startswith('+'):
                group_id = group_input
                group_username = ""
            else:
                group_id = f"@{group_input}"
                group_username = group_input
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO groups (user_id, account_display_name, group_id, group_title, group_username, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, account_display_name, group_id, group_title, group_username, 1))
            
            if cursor.rowcount > 0:
                added_count += 1
            else:
                skipped_count += 1
                
        except Exception as e:
            logger.error(f"Guruh qo'shishda xato: {e}")
            skipped_count += 1
    
    conn.commit()
    conn.close()
    return added_count, skipped_count

def get_user_groups(user_id, account_display_name):
    """Foydalanuvchi guruhlarini olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, group_id, group_title, group_username, is_active 
        FROM groups 
        WHERE user_id = ? AND account_display_name = ? 
        ORDER BY group_title
    ''', (user_id, account_display_name))
    groups = cursor.fetchall()
    conn.close()
    return groups

def update_group_active_status(group_ids, is_active):
    """Guruhlarning faollik holatini o'zgartirish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    updated_count = 0
    for group_id in group_ids:
        cursor.execute('UPDATE groups SET is_active = ? WHERE id = ?', (is_active, group_id))
        updated_count += cursor.rowcount
    
    conn.commit()
    conn.close()
    return updated_count

def add_user_message(user_id, text):
    """Foydalanuvchi xabarini qo'shish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO messages (user_id, text) VALUES (?, ?)', (user_id, text))
    conn.commit()
    conn.close()

def get_user_messages(user_id):
    """Foydalanuvchi xabarlarini olish"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM messages WHERE user_id = ? ORDER BY id', (user_id,))
    messages = cursor.fetchall()
    conn.close()
    return messages

def delete_user_messages(user_id):
    """Foydalanuvchi xabarlarini tozalash"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM messages WHERE user_id = ?', (user_id,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

def get_random_user_message(user_id):
    """Foydalanuvchi uchun random xabar olish"""
    messages = get_user_messages(user_id)
    if not messages:
        return None
    return random.choice(messages)[1]

def log_session_action(display_name, action, status, message):
    """Session logini saqlash"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO session_logs (display_name, action, status, message)
        VALUES (?, ?, ?, ?)
    ''', (display_name, action, status, message))
    conn.commit()
    conn.close()

# ========== YANGILANGAN ADMIN KEYBOARDS ==========

def get_admin_keyboard():
    """Admin panel tugmalari"""
    return ReplyKeyboardMarkup([
        ["📋 Foydalanuvchilar", "⏳ So'rovlar"],
        ["➕ Ruxsat berish", "🗑️ Hisob o'chirish"],
        ["📊 Statistika", "⚙️ Sozlamalar"],
        ["📢 Hammaga xabar", "🔄 Session boshqarish"],
        ["▶️ Boshlash", "⏸️ To'xtatish"],
        ["🔄 Yangilash", "📋 Hisoblar"]
    ], resize_keyboard=True)

# ========== YANGILANGAN USER KEYBOARDS ==========

def get_user_keyboard():
    """Oddiy foydalanuvchi paneli"""
    return ReplyKeyboardMarkup([
        ["➕ Hisob qo'shish", "🗑️ Hisob o'chirish"],
        ["🔗 Guruh qo'shish", "👥 Guruhlar"],
        ["📤 Xabar qo'shish", "📝 Xabarlar ro'yxati"],
        ["🗑️ Xabarlarni tozalash", "📊 Statistika"],
        ["📋 Hisoblar", "⚙️ Interval sozlash"],
        ["🧪 Test qilish", "🔄 Session qayta yuklash"],
        ["▶️ Boshlash", "⏹️ To'xtatish"]
    ], resize_keyboard=True)

# ========== YANGILANGAN COMMAND HANDLERLAR ==========

async def code_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi uchun kod kiritish"""
    user_id = update.effective_user.id
    
    if not context.args or len(context.args) != 2:
        await update.message.reply_text(
            "❌ **Noto'g'ri format!**\n\n"
            "Format: /code DISPLAY_NAME KOD\n"
            "Misol: /code account1 12345\n\n"
            "📱 Hisobingizni ko'rish: /accounts"
        )
        return
    
    display_name = context.args[0]
    code = context.args[1]
    
    # Hisob foydalanuvchiga tegishliligini tekshirish
    accounts = get_user_accounts(user_id)
    account_exists = any(acc[0] == display_name for acc in accounts)
    
    if not account_exists:
        await update.message.reply_text("❌ **Bu hisob sizga tegishli emas yoki mavjud emas!**")
        return
    
    await update.message.reply_text(f"⏳ **Kod kiritilmoqda:** {display_name}...")
    
    success, message = await enter_code(display_name, code, user_id)
    
    log_session_action(display_name, "enter_code", "success" if success else "failed", message)
    
    await update.message.reply_text(message, reply_markup=get_user_keyboard())

async def password_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi uchun 2FA parol kiritish"""
    user_id = update.effective_user.id
    
    if not context.args or len(context.args) != 2:
        await update.message.reply_text(
            "❌ **Noto'g'ri format!**\n\n"
            "Format: /password DISPLAY_NAME PAROL\n"
            "Misol: /password account1 mypassword\n\n"
            "📱 Hisobingizni ko'rish: /accounts"
        )
        return
    
    display_name = context.args[0]
    password = context.args[1]
    
    # Hisob foydalanuvchiga tegishliligini tekshirish
    accounts = get_user_accounts(user_id)
    account_exists = any(acc[0] == display_name for acc in accounts)
    
    if not account_exists:
        await update.message.reply_text("❌ **Bu hisob sizga tegishli emas yoki mavjud emas!**")
        return
    
    await update.message.reply_text(f"⏳ **Parol kiritilmoqda:** {display_name}...")
    
    success, message = await enter_password(display_name, password, user_id)
    
    log_session_action(display_name, "enter_password", "success" if success else "failed", message)
    
    await update.message.reply_text(message, reply_markup=get_user_keyboard())

async def delete_account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi hisobini o'chirish"""
    user_id = update.effective_user.id
    
    if not context.args:
        # Hisoblar ro'yxatini ko'rsatish
        accounts = get_user_accounts(user_id)
        if not accounts:
            await update.message.reply_text("📭 **Sizda hech qanday hisob yo'q!**")
            return
        
        # Hisoblarni faol guruhlar/jami guruhlar formatida ko'rsatish
        msg = "📋 **HISOBLAR RO'YXATI**\n\n"
        
        for i, acc in enumerate(accounts, 1):
            display_name, phone, _, _, is_active, _, _ = acc
            
            # Hisobdagi faol guruhlar sonini hisoblash
            groups = get_user_groups(user_id, display_name)
            active_groups = sum(1 for g in groups if g[4] == 1)
            total_groups = len(groups)
            
            status = "✅ Faol" if is_active == 1 else "❌ Nofaol"
            
            msg += f"{i}. **{display_name}**\n"
            msg += f"   +{phone}\n"
            msg += f"   {active_groups}/{total_groups} ta guruh\n"
            msg += f"   {status}\n\n"
        
        msg += "✅ **Format:** {faol guruhlar}/{jami guruhlar}\n\n"
        msg += "O'chirish uchun: /delete account1"
        
        await update.message.reply_text(msg, parse_mode='Markdown')
        return
    
    display_name = context.args[0]
    
    # Hisob foydalanuvchiga tegishliligini tekshirish
    accounts = get_user_accounts(user_id)
    account_exists = any(acc[0] == display_name for acc in accounts)
    
    if not account_exists:
        await update.message.reply_text("❌ **Bu hisob sizga tegishli emas yoki mavjud emas!**")
        return
    
    success, message = delete_account_from_db(display_name, user_id)
    await update.message.reply_text(message, reply_markup=get_user_keyboard())

async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi hisoblarini ko'rish"""
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)
    
    if not accounts:
        await update.message.reply_text("📭 **Sizda hech qanday hisob yo'q!**")
        return
    
    # Hisoblarni faol guruhlar/jami guruhlar formatida ko'rsatish
    msg = "📋 **HISOBLAR RO'YXATI**\n\n"
    
    for i, acc in enumerate(accounts, 1):
        display_name, phone, _, _, is_active, _, _ = acc
        
        # Hisobdagi faol guruhlar sonini hisoblash
        groups = get_user_groups(user_id, display_name)
        active_groups = sum(1 for g in groups if g[4] == 1)
        total_groups = len(groups)
        
        status = "✅ Faol" if is_active == 1 else "❌ Nofaol"
        
        msg += f"{i}. **{display_name}**\n"
        msg += f"   +{phone}\n"
        msg += f"   {active_groups}/{total_groups} ta guruh\n"
        msg += f"   {status}\n\n"
    
    msg += "✅ **Format:** {faol guruhlar}/{jami guruhlar}"
    
    try:
        await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=get_user_keyboard())
    except:
        await update.message.reply_text(msg, reply_markup=get_user_keyboard())

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin barcha foydalanuvchilaga xabar yuborish"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ **Bu buyruq faqat admin uchun!**")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📢 **HAMMAGA XABAR YUBORISH**\n\n"
            "Format: /broadcast XABAR_MATNI\n\n"
            "Misol: /broadcast Yangilik: Bot yangilandi!\n\n"
            "⚠️ **Diqqat:** Barcha foydalanuvchilarga xabar yuboriladi."
        )
        return
    
    message_text = " ".join(context.args)
    
    await update.message.reply_text("⏳ **Xabar barcha foydalanuvchilarga yuborilmoqda...**")
    
    sent_count, failed_count, failed_users = await send_broadcast_message(context, message_text)
    
    result_msg = f"📊 **XABAR YUBORISH NATIJASI**\n\n"
    result_msg += f"✅ **Yuborildi:** {sent_count} ta foydalanuvchiga\n"
    result_msg += f"❌ **Yuborilmadi:** {failed_count} ta foydalanuvchiga\n\n"
    
    if failed_users:
        result_msg += f"📝 **Yuborilmagan foydalanuvchilar (faqat birinchi 10 tasi):**\n"
        result_msg += ", ".join(failed_users[:10])
        if len(failed_users) > 10:
            result_msg += f"... va yana {len(failed_users) - 10} ta"
        result_msg += "\n\n"
    
    result_msg += f"📝 **Xabar matni:**\n{message_text[:200]}{'...' if len(message_text) > 200 else ''}"
    
    await update.message.reply_text(result_msg, reply_markup=get_admin_keyboard())

async def test_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi sessionni test qilish"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "🧪 **SESSION TEST QILISH**\n\n"
            "Format: /test DISPLAY_NAME\n"
            "Misol: /test account1\n\n"
            "📱 Hisobingizni ko'rish: /accounts"
        )
        return
    
    display_name = context.args[0]
    
    # Hisob foydalanuvchiga tegishliligini tekshirish
    accounts = get_user_accounts(user_id)
    account_exists = any(acc[0] == display_name for acc in accounts)
    
    if not account_exists:
        await update.message.reply_text("❌ **Bu hisob sizga tegishli emas yoki mavjud emas!**")
        return
    
    await update.message.reply_text(f"⏳ **Session test qilinmoqda:** {display_name}...")
    
    success, message = await test_session(display_name, user_id)
    
    await update.message.reply_text(message, reply_markup=get_user_keyboard())

async def messages_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarlar ro'yxatini ko'rsatish"""
    user_id = update.effective_user.id
    messages = get_user_messages(user_id)
    
    if not messages:
        await update.message.reply_text("📭 **Sizda hech qanday xabar yo'q!**\n\nXabar qo'shish uchun '📤 Xabar qo'shish' tugmasini bosing.")
        return
    
    msg = "📝 **XABARLAR RO'YXATI**\n\n"
    
    for i, message in enumerate(messages[:10], 1):
        msg_id, text = message
        msg += f"{i}. Xabar #{msg_id}\n"
        msg += f"   {text[:50]}...\n\n"
    
    if len(messages) > 10:
        msg += f"... va yana {len(messages) - 10} ta xabar\n\n"
    
    msg += "🔄 Xabarlarni o'zgartirish uchun yangi xabar qo'shing. Avvalgi xabarlar saqlanib qoladi.\n"
    msg += "🗑️ **Xabarlarni tozalash uchun:** '🗑️ Xabarlarni tozalash' tugmasini bosing."
    
    await update.message.reply_text(msg, reply_markup=get_user_keyboard())

async def clear_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarlarni tozalash"""
    user_id = update.effective_user.id
    
    messages_count = len(get_user_messages(user_id))
    
    if messages_count == 0:
        await update.message.reply_text("📭 **Sizda hech qanday xabar yo'q!**", reply_markup=get_user_keyboard())
        return
    
    keyboard = [
        [
            InlineKeyboardButton("✅ HA, tozalash", callback_data="confirm_clear_messages"),
            InlineKeyboardButton("❌ BEKOR QILISH", callback_data="cancel_clear_messages")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⚠️ **DIQQAT!**\n\n"
        f"Siz {messages_count} ta xabarni tozalashga tayyormisiz?\n\n"
        f"Bu amalni bekor qilib bo'lmaydi!",
        reply_markup=reply_markup
    )

async def groups_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruhlar ro'yxatini ko'rsatish"""
    user_id = update.effective_user.id
    
    accounts = get_user_accounts(user_id)
    if not accounts:
        await update.message.reply_text("📭 **Sizda hech qanday hisob yo'q!**\n\nAvval hisob qo'shing.", reply_markup=get_user_keyboard())
        return
    
    keyboard = []
    for acc in accounts:
        display_name = acc[0]
        groups = get_user_groups(user_id, display_name)
        
        if groups:
            keyboard.append([InlineKeyboardButton(f"📱 {display_name} ({len(groups)} ta)", callback_data=f"account_{display_name}")])
    
    if keyboard:
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("👥 **GURUHLAR RO'YXATI**\n\nQaysi hisobning guruhlarini ko'rmoqchisiz?", reply_markup=reply_markup)
    else:
        await update.message.reply_text("📭 **Sizda hech qanday guruh yo'q!**\n\nGuruh qo'shish uchun '🔗 Guruh qo'shish' tugmasini bosing.", reply_markup=get_user_keyboard())

# ========== ASOSIY HANDLERLAR ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    last_name = update.effective_user.last_name or ""
    
    logger.info(f"🚀 /start bosildi: user_id={user_id}, username={username}")
    
    if user_id == ADMIN_ID:
        # Global o'zgaruvchilarni yangilash
        global min_interval, max_interval, random_messages
        min_interval = int(get_setting('min_interval', '20'))
        max_interval = int(get_setting('max_interval', '25'))
        random_messages = get_setting('random_messages', 'true').lower() == 'true'
        
        pending_requests = get_pending_requests()
        
        await update.message.reply_text(
            "👑 **ADMIN PANELI**\n\n"
            f"📊 Jami foydalanuvchilar: {len(get_all_users())}\n"
            f"⏳ Kutilayotgan so'rovlar: {len(pending_requests)}\n\n"
            "Kerakli bo'limni tanlang:",
            reply_markup=get_admin_keyboard()
        )
        
        # Agar so'rovlar bo'lsa, adminni ogohlantirish
        if pending_requests:
            for req in pending_requests:
                req_id, uid, uname, fname, lname, created_at = req
                try:
                    # Created_at ni string formatga o'tkazish
                    if isinstance(created_at, str):
                        date_str = created_at
                    else:
                        date_str = created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else "Noma'lum"
                    
                    # Username formatini tuzatish
                    username_display = f"@{uname}" if uname else "Yo'q"
                    
                    await update.message.reply_text(
                        f"⚠️ **KUTILAYOTGAN SO'ROV**\n\n"
                        f"👤 Foydalanuvchi: {fname} {lname}\n"
                        f"🔗 Username: {username_display}\n"
                        f"🆔 ID: {uid}\n"
                        f"📅 Sana: {date_str}\n\n"
                        f"✅ Ruxsat: /add {uid} 30\n"
                        f"❌ Rad: /reject {req_id}"
                    )
                except Exception as e:
                    logger.error(f"Adminga so'rov yuborishda xato: {e}")
                    await update.message.reply_text(
                        f"⚠️ SO'ROV (ID: {uid})\n"
                        f"Foydalanuvchi: {fname} {lname}\n"
                        f"Username: @{uname or 'Yoq'}\n"
                        f"✅ Ruxsat: /add {uid} 30\n"
                        f"❌ Rad: /reject {req_id}"
                    )
        return
    
    # Oddiy foydalanuvchi
    subscription_end, is_premium = get_user_subscription(user_id)
    
    has_active_subscription = False
    days_left = 0
    
    if subscription_end:
        try:
            sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
            days_left = (sub_date - datetime.now()).days
            if days_left > 0:
                has_active_subscription = True
        except Exception as e:
            logger.error(f"Sanani o'qishda xato: {e}")
            has_active_subscription = False
    
    if has_active_subscription:
        sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
        
        # Foydalanuvchi intervallarini olish
        user_min_interval, user_max_interval = get_user_interval(user_id)
        
        # Hisoblar sonini olish
        accounts_count = get_user_accounts_count(user_id)
        
        await update.message.reply_text(
            f"✅ **OBUNA AKTIV!**\n\n"
            f"👋 **Xush kelibsiz, {first_name}!**\n"
            f"📅 **Qolgan kunlar:** {days_left} kun\n"
            f"⏰ **Tugash sanasi:** {sub_date.strftime('%Y-%m-%d')}\n"
            f"📊 **Hisoblar:** {accounts_count}/5 ta\n"
            f"⏱️ **Interval:** {user_min_interval}-{user_max_interval} daqiqa\n\n"
            f"🤖 **Bot funksiyalaridan foydalaning:**",
            reply_markup=get_user_keyboard()
        )
    else:
        # Obuna yo'q yoki muddati o'tgan
        welcome_message = get_setting('welcome_message', '🤖 Botdan foydalanish uchun ruxsat kerak!\n\nℹ️ Ruxsat olish uchun @Okean_manager ga murojaat qiling.')
        await update.message.reply_text(welcome_message)
        
        # So'rov qo'shish
        request_id = add_request(user_id, username, first_name, last_name)
        
        if request_id:
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"📩 **YANGI SO'ROV!**\n\n"
                    f"👤 Foydalanuvchi: {first_name} {last_name}\n"
                    f"🔗 Username: @{username or 'Yoq'}\n"
                    f"🆔 ID: {user_id}\n"
                    f"📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"✅ Ruxsat berish: /add {user_id} 30\n"
                    f"❌ Rad etish: /reject {request_id}"
                )
            except Exception as e:
                logger.error(f"Admin ga xabar yuborishda xato: {e}")
            
            await update.message.reply_text(
                "✅ **So'rovingiz qabul qilindi!**\n\n"
                "Admin tez orada ruxsat beradi.\n"
                "📩 Xabar: @Okean_manager"
            )
        else:
            # Agar so'rov allaqachon mavjud bo'lsa
            await update.message.reply_text(
                "ℹ️ **Sizning so'rovingiz hali ko'rib chiqilmoqda.**\n\n"
                "Admin javobini kuting yoki @Okean_manager ga murojaat qiling."
            )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha text habarlarni qayta ishlash"""
    user_id = update.effective_user.id
    text = update.message.text
    
    logger.info(f"📝 Text xabar: user_id={user_id}, text={text}")
    
    # Admin bo'lsa
    if user_id == ADMIN_ID:
        await handle_admin_text(update, context, text)
    else:
        await handle_user_text(update, context, text)

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Admin text habarlari"""
    user_id = update.effective_user.id
    mode = context.user_data.get("mode")
    
    global is_sending, last_send_time, random_messages
    
    if text == "📋 Foydalanuvchilar":
        users = get_all_users()
        if not users:
            await update.message.reply_text("📭 **Hech qanday foydalanuvchi yo'q!**")
            return
        
        msg = "📋 **FOYDALANUVCHILAR RO'YXATI**\n\n"
        
        for i, uid in enumerate(users[:20], 1):
            accounts = get_user_accounts(uid)
            subscription_end, is_premium = get_user_subscription(uid)
            
            status = "✅ Premium" if is_premium else "⏰ Aktiv" if subscription_end else "❌ Yo'q"
            accounts_count = len(accounts)
            
            msg += f"{i}. **ID:** {uid}\n"
            msg += f"   📊 **Hisoblar:** {accounts_count} ta\n"
            msg += f"   🔧 **Status:** {status}\n"
            
            if subscription_end:
                try:
                    sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
                    days_left = (sub_date - datetime.now()).days
                    if days_left >= 0:
                        msg += f"   ⏰ **Qolgan:** {days_left} kun\n"
                except:
                    pass
            
            msg += "\n"
        
        if len(users) > 20:
            msg += f"\n... va yana {len(users) - 20} ta foydalanuvchi"
        
        try:
            await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        except Exception as e:
            await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    
    elif text == "⏳ So'rovlar":
        requests = get_pending_requests()
        if not requests:
            await update.message.reply_text("✅ **Kutilayotgan so'rovlar yo'q!**", reply_markup=get_admin_keyboard())
            return
        
        for req in requests:
            req_id, uid, uname, fname, lname, created_at = req
            
            # Created_at ni string formatga o'tkazish
            if isinstance(created_at, str):
                date_str = created_at
            else:
                date_str = created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else "Noma'lum"
            
            # Username formatini tuzatish
            username_display = f"@{uname}" if uname else "Yo'q"
            
            msg = f"📩 **So'rov #{req_id}**\n"
            msg += f"👤 **Foydalanuvchi:** {fname} {lname}\n"
            msg += f"🔗 **Username:** {username_display}\n"
            msg += f"🆔 **ID:** {uid}\n"
            msg += f"📅 **Sana:** {date_str}\n"
            msg += f"✅ **Ruxsat:** /add {uid} 30\n"
            msg += f"❌ **Rad:** /reject {req_id}\n"
            
            try:
                await update.message.reply_text(msg, parse_mode='Markdown')
            except Exception as e:
                await update.message.reply_text(msg)
        
        await update.message.reply_text(f"📊 **Jami kutilayotgan so'rovlar:** {len(requests)} ta", reply_markup=get_admin_keyboard())
    
    elif text == "➕ Ruxsat berish":
        await update.message.reply_text(
            "📝 **RUXSAT BERISH**\n\n"
            "Foydalanuvchi ID va kun sonini yuboring:\n\n"
            "Format: ID KUNLAR yoki /add ID KUNLAR\n"
            "Misollar:\n"
            "• /add 123456789 30 - 30 kunlik ruxsat\n"
            "• 123456789 1 - 1 kunlik ruxsat\n\n"
            "Bekor qilish: /cancel"
        )
        context.user_data["mode"] = "grant_access"
    
    elif text == "🗑️ Hisob o'chirish":
        await update.message.reply_text(
            "🗑️ **HISOB O'CHIRISH**\n\n"
            "Foydalanuvchi ID sini yuboring:\n\n"
            "Format: /remove ID yoki REMOVE ID\n"
            "Misol: /remove 123456789\n\n"
            "Bekor qilish: /cancel"
        )
        context.user_data["mode"] = "delete_user"
    
    elif text == "📊 Statistika":
        users = get_all_users()
        total_accounts = 0
        total_groups = 0
        total_messages = 0
        
        for uid in users:
            accounts = get_user_accounts(uid)
            total_accounts += len(accounts)
            
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM groups WHERE user_id = ?', (uid,))
            total_groups += cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM messages WHERE user_id = ?', (uid,))
            total_messages += cursor.fetchone()[0]
            conn.close()
        
        requests_count = len(get_pending_requests())
        
        msg = "📊 **BOT STATISTIKASI**\n\n"
        msg += f"👥 **Foydalanuvchilar:** {len(users)} ta\n"
        msg += f"📱 **Jami hisoblar:** {total_accounts} ta\n"
        msg += f"👥 **Jami guruhlar:** {total_groups} ta\n"
        msg += f"📝 **Jami xabarlar:** {total_messages} ta\n"
        msg += f"⏳ **Kutilayotgan so'rovlar:** {requests_count} ta\n\n"
        msg += f"🔄 **Avtomatik yuborish:** {'✅ Yoqilgan' if is_sending else '❌ Oʻchirilgan'}\n"
        
        if last_send_time:
            msg += f"⏰ **Oxirgi yuborish:** {last_send_time}\n"
        
        try:
            await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        except Exception as e:
            await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    
    elif text == "⚙️ Sozlamalar":
        keyboard = [
            ["📅 Interval sozlash", "🎲 Random rejim"],
            ["📢 Xush kelib xabari", "🔙 Orqaga"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "⚙️ **BOT SOZLAMALARI**\n\n"
            f"📅 **Interval:** {min_interval}-{max_interval} daqiqa\n"
            f"🎲 **Random xabarlar:** {'✅ Yoqilgan' if random_messages else '❌ Oʻchirilgan'}\n"
            f"📢 **Xush kelib xabari:** {get_setting('welcome_message', 'Mavjud emas')[:50]}...\n\n"
            "Kerakli sozlamani tanlang:",
            reply_markup=reply_markup
        )
    
    elif text == "📅 Interval sozlash":
        await update.message.reply_text(
            f"📅 **INTERVAL SOZLASH**\n\n"
            f"Hozirgi interval: {min_interval}-{max_interval} daqiqa\n\n"
            "Yangi intervalni yuboring:\n"
            "Format: min max\n"
            "Misol: 15 30 (15-30 daqiqa)\n\n"
            "Bekor qilish: /cancel",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "set_interval"
    
    elif text == "🎲 Random rejim":
        current = get_setting('random_messages', 'true').lower() == 'true'
        new_setting = not current
        save_setting('random_messages', str(new_setting).lower())
        
        # Global o'zgaruvchini yangilash
        random_messages = new_setting
        
        await update.message.reply_text(
            f"✅ **Random rejim {'yoqildi' if new_setting else 'oʻchirildi'}!**\n\n"
            f"Hozir: {'🎲 Random xabarlar yuboriladi' if new_setting else '📝 Ketma-ket xabarlar yuboriladi'}",
            reply_markup=get_admin_keyboard()
        )
    
    elif text == "📢 Xush kelib xabari":
        current_msg = get_setting('welcome_message', '🤖 Botdan foydalanish uchun ruxsat kerak!')
        await update.message.reply_text(
            f"📢 **XUSH KELIB XABARI**\n\n"
            f"Hozirgi xabar:\n{current_msg}\n\n"
            "Yangi xabarni yuboring:\n\n"
            "Bekor qilish: /cancel",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "set_welcome"
    
    elif text == "🔙 Orqaga":
        await update.message.reply_text("👑 **Admin Paneli**", reply_markup=get_admin_keyboard())
        context.user_data.clear()
    
    elif text == "📢 Hammaga xabar":
        await update.message.reply_text(
            "📢 **BARCHA FOYDALANUVCHILARGA XABAR YUBORISH**\n\n"
            "Xabar matnini yuboring:\n\n"
            "Bekor qilish: /cancel",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "send_broadcast"
    
    elif text == "🔄 Session boshqarish":
        # Hisoblar ro'yxati
        users = get_all_users()
        
        keyboard = []
        for uid in users[:10]:
            accounts = get_user_accounts(uid)
            for acc in accounts:
                display_name, phone, _, _, is_active, _, _ = acc
                status = "✅" if is_active == 1 else "❌"
                keyboard.append([f"{status} {display_name} ({uid})"])
        
        keyboard.append(["🔙 Orqaga"])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🔄 **SESSION BOSHQARISH**\n\n"
            "Hisobni tanlang:",
            reply_markup=reply_markup
        )
        context.user_data["mode"] = "select_session_account"
    
    elif text.startswith("✅ ") or text.startswith("❌ "):
        if mode == "select_session_account":
            # Format: "✅ account1 (123456789)" yoki "❌ account1 (123456789)"
            status_char = text[0]
            parts = text[2:].split(" (")
            if len(parts) == 2:
                display_name = parts[0].strip()
                user_id_str = parts[1].replace(")", "").strip()
                
                try:
                    target_user_id = int(user_id_str)
                    context.user_data["session_account"] = display_name
                    context.user_data["session_user_id"] = target_user_id
                    
                    # Account ma'lumotlarini olish
                    accounts = get_user_accounts(target_user_id)
                    phone = ""
                    is_active = 0
                    for acc in accounts:
                        if acc[0] == display_name:
                            phone = acc[1]
                            is_active = acc[4]
                            break
                    
                    session_exists_flag = session_exists(display_name)
                    
                    keyboard = []
                    if not session_exists_flag:
                        keyboard.append(["📱 Session yaratish"])
                    else:
                        keyboard.append(["🧪 Sessionni test qilish"])
                        if is_active == 1:
                            keyboard.append(["📤 Test xabar yuborish"])
                    
                    keyboard.append(["🔙 Orqaga"])
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                    
                    status_text = "Faol" if is_active == 1 else "Nofaol"
                    session_text = "Mavjud" if session_exists_flag else "Yo'q"
                    
                    await update.message.reply_text(
                        f"🔄 **SESSION BOSHQARISH**\n\n"
                        f"📱 **Hisob:** {display_name}\n"
                        f"👤 **Foydalanuvchi ID:** {target_user_id}\n"
                        f"📞 **Telefon:** +{phone}\n"
                        f"🔧 **Status:** {status_text}\n"
                        f"📁 **Session fayli:** {session_text}\n\n"
                        f"Kerakli amalni tanlang:",
                        reply_markup=reply_markup
                    )
                    context.user_data["mode"] = "manage_session"
                    
                except ValueError:
                    await update.message.reply_text("❌ **Xatolik: Noto'g'ri format!**")
    
    elif text == "📱 Session yaratish" and mode == "manage_session":
        display_name = context.user_data.get("session_account")
        target_user_id = context.user_data.get("session_user_id")
        
        # Account ma'lumotlarini olish
        accounts = get_user_accounts(target_user_id)
        phone = ""
        for acc in accounts:
            if acc[0] == display_name:
                phone = acc[1]
                break
        
        if not phone:
            await update.message.reply_text("❌ **Telefon raqam topilmadi!**")
            return
        
        await update.message.reply_text(f"⏳ **Session yaratilmoqda:** {display_name}...")
        
        success, message = await create_and_auth_session(target_user_id, display_name, phone)
        
        if success:
            await update.message.reply_text(
                f"✅ **SESSION YARATISH NATIJASI**\n\n"
                f"{message}\n\n"
                f"📱 **Hisob:** {display_name}\n"
                f"📞 **Telefon:** +{phone}\n\n"
                f"Admin endi kodni kiritishi kerak:\n"
                f"`/code {display_name} KOD`"
            )
        else:
            await update.message.reply_text(
                f"❌ **SESSION YARATISH XATOLIK**\n\n"
                f"{message}"
            )
    
    elif text == "🧪 Sessionni test qilish" and mode == "manage_session":
        display_name = context.user_data.get("session_account")
        target_user_id = context.user_data.get("session_user_id")
        
        await update.message.reply_text(f"⏳ **Session test qilinmoqda:** {display_name}...")
        
        success, message = await test_session(display_name, target_user_id)
        
        await update.message.reply_text(f"📝 **TEST NATIJASI**\n\n{message}")
    
    elif text == "📤 Test xabar yuborish" and mode == "manage_session":
        display_name = context.user_data.get("session_account")
        
        await update.message.reply_text(
            f"📤 **TEST XABAR YUBORISH**\n\n"
            f"📱 **Hisob:** {display_name}\n\n"
            f"Guruh ID yoki username ni yuboring:\n"
            f"(@guruh_nomi yoki https://t.me/guruh_nomi)\n\n"
            f"Bekor qilish: /cancel",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "send_test_message"
    
    elif mode == "send_test_message":
        display_name = context.user_data.get("session_account")
        group_identifier = text.strip()
        
        test_message = "🤖 **Test xabar** - Bu bot tomonidan yuborilgan test xabari!"
        
        await update.message.reply_text(f"⏳ **Test xabar yuborilmoqda...**\nHisob: {display_name}\nGuruh: {group_identifier}")
        
        success, result_message = await send_message_to_group(display_name, group_identifier, test_message)
        
        await update.message.reply_text(f"📝 **TEST XABAR NATIJASI**\n\n{result_message}")
        
        context.user_data["mode"] = "manage_session"
    
    elif text == "🔙 Orqaga" and mode in ["select_session_account", "manage_session"]:
        await update.message.reply_text("👑 **Admin Paneli**", reply_markup=get_admin_keyboard())
        context.user_data.clear()
    
    elif text == "▶️ Boshlash":
        # Global o'zgaruvchilarni yangilash
        is_sending = True
        last_send_time = datetime.now().strftime("%H:%M:%S")
        
        await update.message.reply_text(
            "✅ **Avtomatik yuborish yoqildi!**\n\n"
            f"⏰ **Interval:** {min_interval}-{max_interval} daqiqa\n"
            f"🎲 **Random:** {'✅ Yoqilgan' if random_messages else '❌ Oʻchirilgan'}\n\n"
            f"Barcha faol hisoblardagi faol guruhlarga xabar yuboriladi.",
            reply_markup=get_admin_keyboard()
        )
    
    elif text == "⏸️ To'xtatish":
        # Global o'zgaruvchini yangilash
        is_sending = False
        await update.message.reply_text("⏸️ **Avtomatik yuborish to'xtatildi!**", reply_markup=get_admin_keyboard())
    
    elif text == "🔄 Yangilash":
        pending_requests = get_pending_requests()
        await update.message.reply_text(
            f"🔄 **YANGILANDI**\n\n"
            f"📊 **Jami foydalanuvchilar:** {len(get_all_users())}\n"
            f"⏳ **Kutilayotgan so'rovlar:** {len(pending_requests)}",
            reply_markup=get_admin_keyboard()
        )
    
    elif text == "📋 Hisoblar":
        await accounts_command(update, context)
    
    elif mode == "send_broadcast":
        message_text = text
        
        await update.message.reply_text("⏳ **Xabar barcha foydalanuvchilarga yuborilmoqda...**")
        
        sent_count, failed_count, failed_users = await send_broadcast_message(context, message_text)
        
        result_msg = f"📊 **XABAR YUBORISH NATIJASI**\n\n"
        result_msg += f"✅ **Yuborildi:** {sent_count} ta foydalanuvchiga\n"
        result_msg += f"❌ **Yuborilmadi:** {failed_count} ta foydalanuvchiga\n\n"
        
        if failed_users:
            result_msg += f"📝 **Yuborilmagan foydalanuvchilar (faqat birinchi 10 tasi):**\n"
            result_msg += ", ".join(failed_users[:10])
            if len(failed_users) > 10:
                result_msg += f"... va yana {len(failed_users) - 10} ta"
            result_msg += "\n\n"
        
        result_msg += f"📝 **Xabar matni:**\n{message_text[:200]}{'...' if len(message_text) > 200 else ''}"
        
        await update.message.reply_text(result_msg, reply_markup=get_admin_keyboard())
        context.user_data["mode"] = None
    
    elif mode == "grant_access":
        await process_grant_access(update, context, text)
    
    elif mode == "delete_user":
        await process_delete_user(update, context, text)
    
    elif mode == "set_interval":
        await process_set_interval(update, context, text)
    
    elif mode == "set_welcome":
        await process_set_welcome(update, context, text)
    
    elif text.startswith("/add") or text.lower().startswith("add "):
        await process_add_command(update, context, text)
    
    elif text.startswith("/reject") or text.lower().startswith("reject "):
        await process_reject_command(update, context, text)
    
    elif text.startswith("/remove") or text.lower().startswith("remove "):
        await process_remove_command(update, context, text)
    
    elif text == "/cancel":
        await update.message.reply_text("❌ **Bekor qilindi!**", reply_markup=get_admin_keyboard())
        context.user_data.clear()
    
    else:
        await update.message.reply_text("❌ **Noma'lum buyruq!** Menyudagi tugmalardan foydalaning.", reply_markup=get_admin_keyboard())

async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Oddiy foydalanuvchi text habarlari"""
    user_id = update.effective_user.id
    mode = context.user_data.get("mode")
    global is_sending, last_send_time
    
    # Obunani tekshirish
    subscription_end, is_premium = get_user_subscription(user_id)
    
    has_active_subscription = False
    days_left = 0
    
    if subscription_end:
        try:
            sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
            days_left = (sub_date - datetime.now()).days
            if days_left > 0:
                has_active_subscription = True
        except Exception as e:
            logger.error(f"Sanani o'qishda xato: {e}")
            has_active_subscription = False
    
    # Agar obuna aktiv bo'lmasa
    if not has_active_subscription:
        welcome_message = get_setting('welcome_message', '🤖 Botdan foydalanish uchun ruxsat kerak!')
        await update.message.reply_text(welcome_message)
        
        # So'rov qo'shish
        username = update.effective_user.username
        first_name = update.effective_user.first_name
        last_name = update.effective_user.last_name or ""
        
        request_id = add_request(user_id, username, first_name, last_name)
        
        if request_id:
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"📩 **YANGI SO'ROV!**\n\n"
                    f"👤 Foydalanuvchi: {first_name} {last_name}\n"
                    f"🔗 Username: @{username or 'Yoq'}\n"
                    f"🆔 ID: {user_id}\n"
                    f"📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"✅ Ruxsat berish: /add {user_id} 30\n"
                    f"❌ Rad etish: /reject {request_id}"
                )
            except Exception as e:
                logger.error(f"Admin ga xabar yuborishda xato: {e}")
        return
    
    # Agar obuna aktiv bo'lsa
    # ========== TELEFON RAQAM QO'SHISH ==========
    if text == "➕ Hisob qo'shish":
        # Hisoblar sonini tekshirish
        accounts_count = get_user_accounts_count(user_id)
        if accounts_count >= 5:
            await update.message.reply_text(
                "❌ **Hisob limitiga yetdingiz!**\n\n"
                f"Sizda allaqachon {accounts_count} ta hisob mavjud.\n"
                "Har bir foydalanuvchi maksimal 5 ta hisob qo'sha oladi.",
                reply_markup=get_user_keyboard()
            )
            return
        
        # Telefon raqam formatlarini ko'rsatish
        phone_formats = [
            "📱 **Telefon raqamingizni yuboring:**",
            "",
            "Qo'shimcha formatlar:",
            "• +998901234567",
            "• +79001234567",
            "• +77011234567",
            "• +99365123456",
            "• +992901234567",
            "• +380501234567",
            "• +905312345678",
            "• +12015550123",
            "• +447911123456",
            "• +4915112345678",
            "",
            "Yoki faqat raqamlami:",
            "• 998901234567",
            "• 79001234567",
            "• 905312345678",
            "• 12015550123",
            "• 447911123456",
            "• 4915112345678",
            "",
            "Har qonday davlat telefon raqamini qo'shishingiz mumkin!",
            f"\n📊 Sizda {accounts_count}/5 ta hisob mavjud",
            "",
            "Bekor qilish: /cancel"
        ]
        
        await update.message.reply_text(
            "\n".join(phone_formats),
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "add_account"
    
    elif text == "🗑️ Hisob o'chirish":
        await delete_account_command(update, context)
    
    elif text == "🔗 Guruh qo'shish":
        accounts = get_user_accounts(user_id)
        if not accounts:
            await update.message.reply_text("❌ **Avval hisob qo'shing!**", reply_markup=get_user_keyboard())
            return
        
        keyboard = []
        for acc in accounts:
            display_name = acc[0]
            phone = acc[1]
            keyboard.append([f"📱 {display_name} (+{phone})"])
        
        keyboard.append(["🔙 Orqaga"])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📱 **HISOB TANLANG**\n\n"
            "Qaysi hisobga guruh qo'shmoqchisiz?\n\n"
            "Bekor qilish: /cancel",
            reply_markup=reply_markup
        )
        context.user_data["mode"] = "select_account"
    
    elif text == "👥 Guruhlar":
        # Guruhlar ro'yxatini ko'rsatish
        await groups_list_command(update, context)
    
    elif text == "📤 Xabar qo'shish":
        await update.message.reply_text(
            "📝 **XABAR MATNINI YUBORING**\n\n"
            "Bu xabarlar guruhlaringizga yuboriladi.\n\n"
            "Bekor qilish: /cancel",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "add_message"
    
    elif text == "📝 Xabarlar ro'yxati":
        await messages_list_command(update, context)
    
    elif text == "🗑️ Xabarlarni tozalash":
        await clear_messages_command(update, context)
    
    elif text == "📋 Hisoblar":
        await accounts_command(update, context)
    
    elif text == "📊 Statistika":
        accounts = get_user_accounts(user_id)
        total_groups = 0
        active_groups = 0
        total_messages = len(get_user_messages(user_id))
        
        for acc in accounts:
            display_name = acc[0]
            groups = get_user_groups(user_id, display_name)
            total_groups += len(groups)
            active_groups += sum(1 for g in groups if g[4] == 1)
        
        sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
        days_left = (sub_date - datetime.now()).days
        
        # Foydalanuvchi intervalini olish
        user_min_interval, user_max_interval = get_user_interval(user_id)
        
        msg = "📊 **STATISTIKA**\n\n"
        msg += f"📱 **Hisoblar:** {len(accounts)}/5 ta\n"
        msg += f"👥 **Faol guruhlar:** {active_groups}/{total_groups} ta\n"
        msg += f"📝 **Xabarlar:** {total_messages} ta\n"
        msg += f"📅 **Obuna:** {days_left} kun qoldi\n"
        msg += f"⏱️ **Interval:** {user_min_interval}-{user_max_interval} daqiqa\n"
        msg += f"🔄 **Yuborish:** {'✅ Yoqilgan' if is_sending else '❌ Oʻchirilgan'}"
        
        try:
            await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=get_user_keyboard())
        except:
            await update.message.reply_text(msg, reply_markup=get_user_keyboard())
    
    elif text == "⚙️ Interval sozlash":
        # Foydalanuvchi intervalini olish
        user_min_interval, user_max_interval = get_user_interval(user_id)
        
        await update.message.reply_text(
            f"⚙️ **INTERVAL SOZLASH**\n\n"
            f"Hozirgi interval: {user_min_interval}-{user_max_interval} daqiqa\n\n"
            "Yangi intervalni yuboring:\n"
            "Format: min max\n"
            "Misol: 10 20 (10-20 daqiqa)\n\n"
            "Bekor qilish: /cancel",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "set_user_interval"
    
    elif text == "🧪 Test qilish":
        accounts = get_user_accounts(user_id)
        if not accounts:
            await update.message.reply_text("❌ **Sizda hech qanday hisob yo'q!**", reply_markup=get_user_keyboard())
            return
        
        keyboard = []
        for acc in accounts:
            display_name, phone, _, _, is_active, _, _ = acc
            status = "✅" if is_active == 1 else "❌"
            keyboard.append([InlineKeyboardButton(f"{status} {display_name} (+{phone})", callback_data=f"test_acc_{display_name}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("🧪 **SESSION TEST QILISH**\n\nTest qilmoqchi bo'lgan hisobni tanlang:", reply_markup=reply_markup)
    
    elif text == "🔄 Session qayta yuklash":
        accounts = get_user_accounts(user_id)
        if not accounts:
            await update.message.reply_text("❌ **Sizda hech qanday hisob yo'q!**", reply_markup=get_user_keyboard())
            return
        
        keyboard = []
        for acc in accounts:
            display_name, phone, _, _, is_active, _, _ = acc
            status = "✅" if is_active == 1 else "❌"
            keyboard.append([InlineKeyboardButton(f"{status} {display_name} (+{phone})", callback_data=f"reload_session_{display_name}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("🔄 **SESSION QAYTA YUKLASH**\n\nQayta yuklamoqchi bo'lgan hisobni tanlang:", reply_markup=reply_markup)
    
    elif text == "▶️ Boshlash":
        # Global o'zgaruvchilarni yangilash
        is_sending = True
        last_send_time = datetime.now().strftime("%H:%M:%S")
        
        # Foydalanuvchi intervalini olish
        user_min_interval, user_max_interval = get_user_interval(user_id)
        
        await update.message.reply_text(
            "✅ **Avtomatik yuborish boshlandi!**\n\n"
            f"⏰ **Interval:** {user_min_interval}-{user_max_interval} daqiqa\n"
            f"🎲 **Random:** {'✅ Yoqilgan' if random_messages else '❌ Oʻchirilgan'}\n\n"
            f"Barcha faol hisoblardagi faol guruhlarga xabar yuboriladi.",
            reply_markup=get_user_keyboard()
        )
    
    elif text == "⏹️ To'xtatish":
        # Global o'zgaruvchini yangilash
        is_sending = False
        await update.message.reply_text("⏹️ **Avtomatik yuborish to'xtatildi!**", reply_markup=get_user_keyboard())
    
    elif text.startswith("📱 ") and mode == "select_account":
        display_name = text[2:].split(" ")[0]
        context.user_data["selected_account"] = display_name
        
        await update.message.reply_text(
            f"✅ **{display_name} tanlandi!**\n\n"
            "Endi guruhlarni yuboring:\n"
            "• Har bir guruh alohida qatorda\n"
            "• @guruh_nomi yoki https://t.me/guruh_nomi\n\n"
            "Bekor qilish: /cancel",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["mode"] = "add_groups"
    
    elif text == "🔙 Orqaga":
        if context.user_data.get("awaiting_code") or context.user_data.get("awaiting_password"):
            await update.message.reply_text("❌ **Bekor qilindi!**", reply_markup=get_user_keyboard())
            context.user_data.clear()
        else:
            await update.message.reply_text("🤖 **Asosiy menyu**", reply_markup=get_user_keyboard())
            context.user_data.clear()
    
    elif text == "/cancel":
        await update.message.reply_text("❌ **Bekor qilindi!**", reply_markup=get_user_keyboard())
        context.user_data.clear()
    
    # ========== TELEFON RAQAM QO'SHISH MODE ==========
    elif mode == "add_account":
        accounts_count = get_user_accounts_count(user_id)
        if accounts_count >= 5:
            await update.message.reply_text(
                "❌ **Hisob limitiga yetdingiz!**\n\n"
                "Sizda allaqachon 5 ta hisob mavjud.",
                reply_markup=get_user_keyboard()
            )
            context.user_data.clear()
            return
        
        phone = text.strip()
        
        if not phone:
            await update.message.reply_text("❌ **Telefon raqam kiritilmadi!**")
            return
        
        # Telefon raqamni tozalash va tekshirish
        original_phone = phone
        if phone.startswith('+'):
            clean_phone = phone[1:]
        else:
            clean_phone = phone
        
        # Raqamni tekshirish
        if not clean_phone.isdigit():
            await update.message.reply_text("❌ **Noto'g'ri format!** Faqat raqam kiriting.")
            return
        
        # Telefon raqam boshqa foydalanuvchida borligini tekshirish
        if check_phone_availability(clean_phone, user_id):
            await update.message.reply_text(
                "❌ **Bu telefon raqam allaqachon boshqa foydalanuvchi tomonidan ishlatilmoqda!**\n\n"
                "Iltimos, boshqa raqam kiriting.",
                reply_markup=get_user_keyboard()
            )
            context.user_data.clear()
            return
        
        # Davlat kodini aniqlash
        if clean_phone.startswith('998'):
            country_code = "998"
            country_name = "UZ (+998)"
        elif clean_phone.startswith('7'):
            country_code = "7"
            country_name = "RU (+7)"
        elif clean_phone.startswith('90'):
            country_code = "90"
            country_name = "TR (+90)"
        elif clean_phone.startswith('1'):
            country_code = "1"
            country_name = "US (+1)"
        else:
            country_code = clean_phone[:3]
            country_name = f"Country (+{country_code})"
        
        # Display name yaratish
        account_number = get_next_account_number(user_id)
        if account_number is None:
            await update.message.reply_text("❌ **Hisob limitiga yetdingiz!** Maksimum 5 ta hisob.")
            context.user_data.clear()
            return
        
        display_name = f"account{account_number}"
        
        # Hisobni bazaga qo'shish
        result = add_user_account(user_id, phone=clean_phone, country_code=country_code, username="", display_name=display_name)
        
        if result:
            # Ma'lumotlarni context_data ga saqlash
            context.user_data["account_info"] = {
                "display_name": display_name,
                "phone": clean_phone,
                "original_phone": original_phone,
                "country_name": country_name,
                "country_code": country_code
            }
            
            # Hisob qo'shilganini xabar qilish
            await update.message.reply_text(
                f"# @owner_xabarbot\n"
                f"**bot**\n\n"
                f"## qo'shishingiz mumkin!\n"
                f"---\n"
                f"### Telefon raqam: {original_phone}\n"
                f"**Davlat: {country_name}**\n\n"
                f"- **Telegramdan kelgan kodni yuboring:**",
                reply_markup=ReplyKeyboardMarkup([["🔙 Orqaga"]], resize_keyboard=True)
            )
            
            # Session yaratish jarayonini boshlash
            await update.message.reply_text("⏳ **Kod yuborilmoqda...**")
            
            success, message = await create_and_auth_session(user_id, display_name, clean_phone)
            
            if success and message == "code_sent":
                # Kod yuborilganini bildirish
                await update.message.reply_text(
                    f"📱 **Kod yuborildi!**\n\n"
                    f"Telefon raqamingizga SMS kod yuborildi.\n"
                    f"Kodni kiriting:\n"
                    f"`/code {display_name} KOD`",
                    reply_markup=ReplyKeyboardMarkup([["🔙 Orqaga"]], resize_keyboard=True)
                )
                
                # Foydalanuvchining keyingi xabari kod bo'lishini kutish
                context.user_data["awaiting_code"] = True
                context.user_data["code_for_account"] = display_name
            else:
                await update.message.reply_text(
                    f"❌ **Session yaratishda xatolik:**\n\n{message}",
                    reply_markup=get_user_keyboard()
                )
                context.user_data.clear()
        else:
            await update.message.reply_text(
                "❌ **Hisob qo'shishda xatolik!** Telefon raqam allaqachon mavjud yoki hisob limitiga yetdingiz.",
                reply_markup=get_user_keyboard()
            )
            context.user_data.clear()
    
    # ========== KOD KIRITISH MODE ==========
    elif context.user_data.get("awaiting_code"):
        # Agar foydalanuvchi kod kiritishni kutayotgan bo'lsa
        code = text.strip()
        display_name = context.user_data.get("code_for_account")
        account_info = context.user_data.get("account_info", {})
        
        if not display_name or not code:
            await update.message.reply_text("❌ **Kod kiritilmadi!**", reply_markup=get_user_keyboard())
            context.user_data.clear()
            return
        
        # Kodni kiritish
        await update.message.reply_text(f"⏳ **Kod kiritilmoqda:** {display_name}...")
        
        success, message = await enter_code(display_name, code, user_id)
        
        if success:
            # Hisob muvaffaqiyatli qo'shilganini bildirish
            phone_for_display = account_info.get('phone', '')
            if len(phone_for_display) >= 10:
                phone_last_10 = phone_for_display[-10:]
            else:
                phone_last_10 = phone_for_display
            
            await update.message.reply_text(
                f"✅ **HISOB QO'SHILDI!**\n\n"
                f"- **Nomi:** {display_name}\n"
                f"- **Telefon:** {account_info.get('original_phone', 'Nomalum')}\n"
                f"- **Davlat:** {account_info.get('country_name', 'Nomalum')}\n"
                f"- **Session:** account{account_info.get('country_code', '')}{phone_last_10}\n\n"
                f"**Eslatma:** Guruh qoshishda {display_name} nomidan foydalaning",
                reply_markup=get_user_keyboard()
            )
            context.user_data.clear()
        
        elif message == "2fa_required":
            # 2FA parol kerak bo'lsa
            await update.message.reply_text(
                "🔐 **Parolni yuboring:**\n\n"
                "2-qadamil autentifikatsiya kerak!",
                reply_markup=ReplyKeyboardMarkup([["🔙 Orqaga"]], resize_keyboard=True)
            )
            context.user_data["awaiting_password"] = True
            context.user_data["password_for_account"] = display_name
            context.user_data["awaiting_code"] = False
        
        else:
            await update.message.reply_text(
                f"❌ **Xatolik:** {message}\n\n"
                f"Qayta urinib ko'ring:",
                reply_markup=get_user_keyboard()
            )
            context.user_data.clear()
    
    # ========== PAROL KIRITISH MODE ==========
    elif context.user_data.get("awaiting_password"):
        # Agar foydalanuvchi 2FA parol kiritishni kutayotgan bo'lsa
        password = text.strip()
        display_name = context.user_data.get("password_for_account")
        account_info = context.user_data.get("account_info", {})
        
        if not display_name or not password:
            await update.message.reply_text("❌ **Parol kiritilmadi!**", reply_markup=get_user_keyboard())
            context.user_data.clear()
            return
        
        # Parolni kiritish
        await update.message.reply_text(f"⏳ **Parol kiritilmoqda:** {display_name}...")
        
        success, message = await enter_password(display_name, password, user_id)
        
        if success:
            phone_for_display = account_info.get('phone', '')
            if len(phone_for_display) >= 10:
                phone_last_10 = phone_for_display[-10:]
            else:
                phone_last_10 = phone_for_display
            
            await update.message.reply_text(
                f"✅ **HISOB QO'SHILDI!**\n\n"
                f"- **Nomi:** {display_name}\n"
                f"- **Telefon:** {account_info.get('original_phone', 'Nomalum')}\n"
                f"- **Davlat:** {account_info.get('country_name', 'Nomalum')}\n"
                f"- **Session:** account{account_info.get('country_code', '')}{phone_last_10}\n\n"
                f"**Eslatma:** Guruh qo'shishda {display_name} nomidan foydalaning",
                reply_markup=get_user_keyboard()
            )
        else:
            await update.message.reply_text(
                f"❌ **Xatolik:** {message}\n\n"
                f"Qayta urinib ko'ring:",
                reply_markup=get_user_keyboard()
            )
        
        context.user_data.clear()
    
    elif mode == "add_message":
        add_user_message(user_id, text)
        await update.message.reply_text(
            f"✅ **XABAR QO'SHILDI!**\n\n{text[:100]}{'...' if len(text) > 100 else ''}",
            reply_markup=get_user_keyboard()
        )
        context.user_data["mode"] = None
    
    elif mode == "add_groups":
        account_display_name = context.user_data.get("selected_account")
        
        if not account_display_name:
            await update.message.reply_text("❌ **Xatolik: Hisob tanlanmagan!**", reply_markup=get_user_keyboard())
            context.user_data.clear()
            return
        
        groups_input = text.strip()
        groups_list = []
        
        if ',' in groups_input:
            groups_list = [g.strip() for g in groups_input.split(',') if g.strip()]
        else:
            groups_list = [line.strip() for line in groups_input.split('\n') if line.strip()]
        
        if not groups_list:
            await update.message.reply_text("❌ **Hech qanday guruh kiritilmadi!**", reply_markup=get_user_keyboard())
            return
        
        added_count, skipped_count = add_group_batch(user_id, account_display_name, groups_list)
        
        keyboard = [
            [InlineKeyboardButton("✅ Tugatish", callback_data="finish_groups")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📊 **NATIJALAR**\n\n"
            f"✅ **Qo'shildi:** {added_count} ta guruh\n"
            f"⚠️ **O'tkazib yuborildi:** {skipped_count} ta (mavjud)\n\n"
            f"Endi nima qilmoqchisiz?",
            reply_markup=reply_markup
        )
    
    elif mode == "set_user_interval":
        try:
            parts = text.split()
            if len(parts) != 2:
                await update.message.reply_text("❌ **Format:** min max\nMisol: 10 20")
                return
            
            min_val = int(parts[0])
            max_val = int(parts[1])
            
            if min_val <= 0 or max_val <= 0:
                await update.message.reply_text("❌ **Interval 0 dan katta bo'lishi kerak!**")
                return
            
            if min_val >= max_val:
                await update.message.reply_text("❌ **Min interval max dan kichik bo'lishi kerak!**")
                return
            
            # Intervalni saqlash
            save_user_interval(user_id, min_val, max_val)
            
            await update.message.reply_text(
                f"✅ **Interval yangilandi!**\n\n"
                f"📅 **Yangi interval:** {min_val}-{max_val} daqiqa",
                reply_markup=get_user_keyboard()
            )
            context.user_data["mode"] = None
            
        except ValueError:
            await update.message.reply_text("❌ **Noto'g'ri format!** Faqat raqam kiriting.")
        except Exception as e:
            await update.message.reply_text(f"❌ **Xatolik:** {str(e)}", reply_markup=get_user_keyboard())
            context.user_data["mode"] = None
    
    else:
        await update.message.reply_text("❌ **Noma'lum buyruq!** Menyudagi tugmalardan foydalaning yoki /start ni bosing.", reply_markup=get_user_keyboard())

# ========== QOLGAN FUNKSIYALAR ==========

def parse_id_days(raw_text: str):
    """raw_text ichidan ID va kunlarni oladi (bardoshli)"""
    if not raw_text:
        return None, None
    s = raw_text.strip()
    s = s.lstrip('/')  # /add ... bo'lsa olib tashla
    # agar boshida 'add' so'zi bo'lsa olib tashla
    if s.lower().startswith('add '):
        s = s[4:].strip()
    parts = s.split()
    if len(parts) < 2:
        return None, None
    try:
        user_id = int(parts[0])
        days = int(parts[1])
        return user_id, days
    except:
        return None, None

def parse_single_id(raw_text: str):
    if not raw_text:
        return None
    s = raw_text.strip()
    s = s.lstrip('/')
    # remove command word if present
    if s.lower().startswith('reject '):
        s = s[7:].strip()
    if s.lower().startswith('remove '):
        s = s[7:].strip()
    parts = s.split()
    if not parts:
        return None
    try:
        return int(parts[0])
    except:
        return None

async def process_grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Grant access from admin mode"""
    try:
        target_user_id, days = parse_id_days(text)
        if target_user_id is None or days is None:
            await update.message.reply_text("❌ **Noto'g'ri format!** To'g'ri format: ID KUNLAR yoki /add ID KUNLAR")
            return
        
        if days <= 0:
            await update.message.reply_text("❌ **Kunlar soni 0 dan katta bo'lishi kerak!**")
            return
        
        subscription_end = update_user_subscription(target_user_id, days)
        
        if subscription_end:
            sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
            
            # So'rovni approved qilish
            request = get_request_by_user_id(target_user_id)
            if request:
                update_request_status(request[0], "approved", f"Admin tomonidan {days} kun ruxsat berildi")
            
            # Foydalanuvchiga xabar yuborish
            try:
                await context.bot.send_message(
                    target_user_id,
                    f"🎉 **TABRIKLAYMIZ!**\n\n"
                    f"Sizga {days} kunlik ruxsat berildi!\n"
                    f"⏰ **Tugash sanasi:** {sub_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"🤖 **Endi botdan to'liq foydalanishingiz mumkin!**\n"
                    f"Yangilash uchun /start ni bosing."
                )
            except Exception as e:
                logger.error(f"Foydalanuvchiga xabar yuborishda xato: {e}")
            
            await update.message.reply_text(
                f"✅ **RUXSAT BERILDI!**\n\n"
                f"👤 **Foydalanuvchi ID:** {target_user_id}\n"
                f"📅 **Kunlar:** {days} kun\n"
                f"⏰ **Tugash sanasi:** {sub_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Foydalanuvchi endi botdan foydalana oladi.",
                reply_markup=get_admin_keyboard()
            )
        else:
            await update.message.reply_text("❌ **Ruxsat berishda xatolik!**", reply_markup=get_admin_keyboard())
        
        context.user_data["mode"] = None
        
    except Exception as e:
        await update.message.reply_text(f"❌ **Xatolik:** {str(e)}", reply_markup=get_admin_keyboard())
        context.user_data["mode"] = None

async def process_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        target_user_id = parse_single_id(text)
        if target_user_id is None:
            await update.message.reply_text("❌ **Noto'g'ri ID!** Faqat raqam kiriting.")
            return
        delete_user_data(target_user_id)
        
        await update.message.reply_text(
            f"✅ **FOYDALANUVCHI O'CHIRILDI!**\n\n"
            f"👤 **Foydalanuvchi ID:** {target_user_id}\n\n"
            f"Barcha ma'lumotlar tozalandi.",
            reply_markup=get_admin_keyboard()
        )
        
        try:
            await context.bot.send_message(
                target_user_id,
                "⚠️ **SIZNING HISOBINGIZ O'CHIRILDI!**\n\n"
                "Barcha ma'lumotlaringiz tozalandi.\n"
                "Qayta foydalanish uchun @Okean_manager ga murojaat qiling."
            )
        except:
            pass
        
        context.user_data["mode"] = None
        
    except Exception as e:
        await update.message.reply_text(f"❌ **Xatolik:** {str(e)}", reply_markup=get_admin_keyboard())
        context.user_data["mode"] = None

async def process_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ **Format:** min max\nMisol: 15 30")
            return
        
        min_val = int(parts[0])
        max_val = int(parts[1])
        
        if min_val <= 0 or max_val <= 0:
            await update.message.reply_text("❌ **Interval 0 dan katta bo'lishi kerak!**")
            return
        
        if min_val >= max_val:
            await update.message.reply_text("❌ **Min interval max dan kichik bo'lishi kerak!**")
            return
        
        save_setting('min_interval', str(min_val))
        save_setting('max_interval', str(max_val))
        
        # Global o'zgaruvchilarni yangilash
        global min_interval, max_interval
        min_interval = min_val
        max_interval = max_val
        
        await update.message.reply_text(
            f"✅ **Interval yangilandi!**\n\n"
            f"📅 **Yangi interval:** {min_interval}-{max_interval} daqiqa",
            reply_markup=get_admin_keyboard()
        )
        context.user_data["mode"] = None
        
    except ValueError:
        await update.message.reply_text("❌ **Noto'g'ri format!** Faqat raqam kiriting.")
    except Exception as e:
        await update.message.reply_text(f"❌ **Xatolik:** {str(e)}", reply_markup=get_admin_keyboard())
        context.user_data["mode"] = None

async def process_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    save_setting('welcome_message', text)
    
    await update.message.reply_text(
        f"✅ **Xush kelib xabari yangilandi!**\n\n"
        f"Yangi xabar:\n{text[:200]}{'...' if len(text) > 200 else ''}",
        reply_markup=get_admin_keyboard()
    )
    context.user_data["mode"] = None

async def process_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = None):
    """/add command or called from text handler"""
    try:
        raw_text = text if text is not None else (update.message.text if update.message and update.message.text else "")
        target_user_id, days = parse_id_days(raw_text)
        if target_user_id is None or days is None:
            # try context.args fallback
            if context.args and len(context.args) >= 2:
                try:
                    target_user_id = int(context.args[0])
                    days = int(context.args[1])
                except:
                    await update.message.reply_text("❌ **Format:** /add ID KUNLAR\nMisol: /add 123456789 30")
                    return
            else:
                await update.message.reply_text("❌ **Format:** /add ID KUNLAR\nMisol: /add 123456789 30")
                return
        
        if days <= 0:
            await update.message.reply_text("❌ **Kunlar soni 0 dan katta bo'lishi kerak!**")
            return
        
        subscription_end = update_user_subscription(target_user_id, days)
        
        if subscription_end:
            sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
            
            # So'rovni approved qilish
            request = get_request_by_user_id(target_user_id)
            if request:
                update_request_status(request[0], "approved", f"Admin tomonidan {days} kun ruxsat berildi")
            
            # Foydalanuvchiga xabar yuborish
            try:
                await context.bot.send_message(
                    target_user_id,
                    f"🎉 **TABRIKLAYMIZ!**\n\n"
                    f"Sizga {days} kunlik ruxsat berildi!\n"
                    f"⏰ **Tugash sanasi:** {sub_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"🤖 **Endi botdan to'liq foydalanishingiz mumkin!**\n"
                    f"Yangilash uchun /start ni bosing."
                )
            except Exception as e:
                logger.error(f"Foydalanuvchiga xabar yuborishda xato: {e}")
            
            await update.message.reply_text(
                f"✅ **RUXSAT BERILDI!**\n\n"
                f"👤 **Foydalanuvchi ID:** {target_user_id}\n"
                f"📅 **Kunlar:** {days} kun\n"
                f"⏰ **Tugash sanasi:** {sub_date.strftime('%Y-%m-%d %H:%M:%S')}",
                reply_markup=get_admin_keyboard()
            )
        else:
            await update.message.reply_text("❌ **Ruxsat berishda xatolik!**", reply_markup=get_admin_keyboard())
            
    except Exception as e:
        await update.message.reply_text(f"❌ **Xatolik:** {str(e)}", reply_markup=get_admin_keyboard())

async def process_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = None):
    """/remove or 'remove 123'"""
    try:
        raw_text = text if text is not None else (update.message.text if update.message and update.message.text else "")
        target_user_id = parse_single_id(raw_text)
        if target_user_id is None:
            await update.message.reply_text("❌ **Format:** /remove ID\nMisol: /remove 123456789")
            return
        
        delete_user_data(target_user_id)
        
        await update.message.reply_text(
            f"✅ **FOYDALANUVCHI O'CHIRILDI!**\n\n"
            f"👤 **Foydalanuvchi ID:** {target_user_id}\n\n"
            f"Barcha ma'lumotlar tozalandi.",
            reply_markup=get_admin_keyboard()
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ **Xatolik:** {str(e)}", reply_markup=get_admin_keyboard())

async def process_reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = None):
    """/reject or 'reject 1'"""
    try:
        raw_text = text if text is not None else (update.message.text if update.message and update.message.text else "")
        request_id = parse_single_id(raw_text)
        if request_id is None:
            await update.message.reply_text("❌ **Format:** /reject REQUEST_ID\nMisol: /reject 1")
            return
        
        # So'rovni olish
        request = get_request_by_id(request_id)
        if not request:
            await update.message.reply_text(f"❌ **So'rov #{request_id} topilmadi!**", reply_markup=get_admin_keyboard())
            return
        
        # So'rovni rejected qilish
        update_request_status(request_id, "rejected", "Admin tomonidan rad etildi")
        
        # Foydalanuvchiga xabar yuborish
        target_user_id = request[1]  # user_id
        first_name = request[3]
        last_name = request[4]
        
        try:
            await context.bot.send_message(
                target_user_id,
                f"❌ **SIZNING SO'ROVINGIZ RAD ETILDI!**\n\n"
                f"👤 **Foydalanuvchi:** {first_name} {last_name}\n"
                f"🆔 **ID:** {target_user_id}\n\n"
                f"Qayta urinish uchun @Okean_manager ga murojaat qiling."
            )
        except Exception as e:
            logger.error(f"Foydalanuvchiga xabar yuborishda xato: {e}")
        
        await update.message.reply_text(
            f"✅ **SO'ROV RAD ETILDI!**\n\n"
            f"📝 **So'rov ID:** #{request_id}\n"
            f"👤 **Foydalanuvchi:** {first_name} {last_name}\n"
            f"🆔 **ID:** {target_user_id}\n\n"
            f"Foydalanuvchi xabarlangan.",
            reply_markup=get_admin_keyboard()
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ **Xatolik:** {str(e)}", reply_markup=get_admin_keyboard())

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        await update.message.reply_text("❌ **Bekor qilindi!**", reply_markup=get_admin_keyboard())
    else:
        await update.message.reply_text("❌ **Bekor qilindi!**", reply_markup=get_user_keyboard())
    context.user_data.clear()

# ========== YANGILANGAN CALLBACK HANDLER ==========

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data.startswith("delete_acc_"):
        display_name = data.replace("delete_acc_", "")
        
        # Hisob foydalanuvchiga tegishliligini tekshirish
        accounts = get_user_accounts(user_id)
        account_exists = any(acc[0] == display_name for acc in accounts)
        
        if not account_exists:
            await query.edit_message_text("❌ **Bu hisob sizga tegishli emas!**")
            return
        
        success, message = delete_account_from_db(display_name, user_id)
        await query.edit_message_text(message)
        
        await asyncio.sleep(2)
        await context.bot.send_message(
            chat_id=user_id,
            text="🤖 **Asosiy menyu**",
            reply_markup=get_user_keyboard()
        )
    
    elif data.startswith("test_acc_"):
        display_name = data.replace("test_acc_", "")
        
        # Hisob foydalanuvchiga tegishliligini tekshirish
        accounts = get_user_accounts(user_id)
        account_exists = any(acc[0] == display_name for acc in accounts)
        
        if not account_exists:
            await query.edit_message_text("❌ **Bu hisob sizga tegishli emas!**")
            return
        
        await query.edit_message_text(f"⏳ **Session test qilinmoqda:** {display_name}...")
        
        success, message = await test_session(display_name, user_id)
        
        await query.edit_message_text(message)
        
        await asyncio.sleep(2)
        await context.bot.send_message(
            chat_id=user_id,
            text="🤖 **Asosiy menyu**",
            reply_markup=get_user_keyboard()
        )
    
    elif data.startswith("reload_session_"):
        display_name = data.replace("reload_session_", "")
        
        # Hisob foydalanuvchiga tegishliligini tekshirish
        accounts = get_user_accounts(user_id)
        account_exists = any(acc[0] == display_name for acc in accounts)
        
        if not account_exists:
            await query.edit_message_text("❌ **Bu hisob sizga tegishli emas!**")
            return
        
        # Hisob ma'lumotlarini olish
        phone = ""
        for acc in accounts:
            if acc[0] == display_name:
                phone = acc[1]
                break
        
        if not phone:
            await query.edit_message_text("❌ **Telefon raqam topilmadi!**")
            return
        
        await query.edit_message_text(f"⏳ **Session qayta yuklanmoqda:** {display_name}...")
        
        # Avval eski session faylini o'chirish
        session_path = get_session_path(display_name)
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
            except:
                pass
        
        # Yangi session yaratish
        success, message = await create_and_auth_session(user_id, display_name, phone)
        
        if success:
            await query.edit_message_text(
                f"✅ **SESSION QAYTA YUKLANDI!**\n\n"
                f"📱 **Hisob:** {display_name}\n"
                f"📞 **Telefon:** +{phone}\n\n"
                f"Telefon raqamingizga SMS kod yuborildi.\n"
                f"Kodni kiriting:\n"
                f"`/code {display_name} KOD`"
            )
        else:
            await query.edit_message_text(
                f"❌ **Session qayta yuklashda xatolik:**\n\n{message}"
            )
    
    elif data == "confirm_clear_messages":
        deleted_count = delete_user_messages(user_id)
        
        if deleted_count > 0:
            await query.edit_message_text(f"✅ **XABARLAR TOZALANDI!**\n\n🗑️ **O'chirildi:** {deleted_count} ta xabar")
        else:
            await query.edit_message_text("📭 **Sizda hech qanday xabar yo'q edi!**")
        
        await asyncio.sleep(2)
        await context.bot.send_message(
            chat_id=user_id,
            text="🤖 **Asosiy menyu**",
            reply_markup=get_user_keyboard()
        )
    
    elif data == "cancel_clear_messages":
        await query.edit_message_text("❌ **Bekor qilindi!** Xabarlar saqlanib qoldi.")
        
        await asyncio.sleep(1)
        await context.bot.send_message(
            chat_id=user_id,
            text="🤖 **Asosiy menyu**",
            reply_markup=get_user_keyboard()
        )
    
    elif data == "cancel_delete" or data == "cancel_test":
        await query.edit_message_text("❌ **Bekor qilindi!**")
        await asyncio.sleep(1)
        await context.bot.send_message(
            chat_id=user_id,
            text="🤖 **Asosiy menyu**",
            reply_markup=get_user_keyboard()
        )
    
    elif data == "manage_groups":
        accounts = get_user_accounts(user_id)
        if not accounts:
            await query.edit_message_text("❌ **Hech qanday hisob yo'q!**")
            return
        
        keyboard = []
        for acc in accounts:
            display_name = acc[0]
            groups = get_user_groups(user_id, display_name)
            
            if groups:
                keyboard.append([InlineKeyboardButton(f"📱 {display_name} ({len(groups)} ta)", callback_data=f"account_{display_name}")])
        
        if keyboard:
            keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text("⚙️ **GURUHLARNI BOSHQARISH**\n\nHisobni tanlang:", reply_markup=reply_markup)
        else:
            await query.edit_message_text("❌ **Hech qanday guruh yo'q!**")
    
    elif data.startswith("account_"):
        display_name = data.replace("account_", "")
        groups = get_user_groups(user_id, display_name)
        
        if not groups:
            await query.edit_message_text(f"❌ **{display_name} hisobida guruh yo'q!**")
            return
        
        keyboard = []
        for group in groups:
            group_id, group_title, group_username, is_active = group[1], group[2], group[3], group[4]
            status = "✅" if is_active == 1 else "❌"
            
            if group_username:
                text = f"{status} {group_title} (@{group_username})"
            else:
                text = f"{status} {group_title}"
            
            keyboard.append([InlineKeyboardButton(text, callback_data=f"group_{group[0]}")])
        
        keyboard.append([
            InlineKeyboardButton("✅ Hammasini yoqish", callback_data=f"enable_all_{display_name}"),
            InlineKeyboardButton("❌ Hammasini o'chirish", callback_data=f"disable_all_{display_name}")
        ])
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="manage_groups")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚙️ **{display_name} - GURUHLAR**\n\n"
            f"✅ - faol (xabar yuboriladi)\n"
            f"❌ - nofaol (xabar yuborilmaydi)\n\n"
            f"Guruhni tanlang yoki barchasini o'zgartiring:",
            reply_markup=reply_markup
        )
    
    elif data.startswith("group_"):
        group_id = int(data.replace("group_", ""))
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('SELECT is_active FROM groups WHERE id = ?', (group_id,))
        current_status = cursor.fetchone()[0]
        
        new_status = 0 if current_status == 1 else 1
        
        cursor.execute('UPDATE groups SET is_active = ? WHERE id = ?', (new_status, group_id))
        conn.commit()
        
        cursor.execute('SELECT group_title, account_display_name FROM groups WHERE id = ?', (group_id,))
        group_title, account_name = cursor.fetchone()
        conn.close()
        
        status_text = "faollashtirildi" if new_status == 1 else "o'chirildi"
        
        await query.edit_message_text(f"✅ **{group_title}** guruhi {status_text}!\n\n📱 **Hisob:** {account_name}\n📢 **Guruh:** {group_title}\n🔧 **Status:** {'✅ Faol' if new_status == 1 else '❌ Nofaol'}")
        
        await asyncio.sleep(2)
        await button_callback(update, context)
    
    elif data.startswith("enable_all_"):
        display_name = data.replace("enable_all_", "")
        
        groups = get_user_groups(user_id, display_name)
        group_ids = [g[0] for g in groups]
        
        if group_ids:
            update_group_active_status(group_ids, 1)
        
        await query.edit_message_text(f"✅ **{display_name}**\n\nBarcha guruhlar faollashtirildi!\nJami: {len(group_ids)} ta guruh")
    
    elif data.startswith("disable_all_"):
        display_name = data.replace("disable_all_", "")
        
        groups = get_user_groups(user_id, display_name)
        group_ids = [g[0] for g in groups]
        
        if group_ids:
            update_group_active_status(group_ids, 0)
        
        await query.edit_message_text(f"✅ **{display_name}**\n\nBarcha guruhlar o'chirildi!\nJami: {len(group_ids)} ta guruh")
    
    elif data == "finish_groups":
        await query.edit_message_text("✅ **Guruhlar muvaffaqiyatli qo'shildi!**\n\nEndi asosiy menyudan boshqa funksiyalardan foydalanishingiz mumkin.")
        context.user_data.clear()
    
    elif data == "back_to_main":
        if user_id == ADMIN_ID:
            await context.bot.send_message(chat_id=user_id, text="👑 **Admin Paneli**", reply_markup=get_admin_keyboard())
        else:
            await context.bot.send_message(chat_id=user_id, text="🤖 **Asosiy menyu**", reply_markup=get_user_keyboard())
        await query.delete_message()

# ========== YANGILANGAN AUTO SEND LOOP ==========

async def auto_send_loop():
    """Avtomatik xabar yuborish loopi"""
    global is_sending, last_send_time
    
    print("🔄 Avtomatik yuborish loopi ishga tushdi...")
    
    # Session papkasini yaratish
    init_sessions_dir()
    
    while True:
        try:
            if is_sending:
                users = get_all_users()
                total_sent = 0
                total_failed = 0
                
                for user_id in users:
                    subscription_end, is_premium = get_user_subscription(user_id)
                    
                    if not subscription_end:
                        continue
                    
                    try:
                        sub_date = datetime.strptime(subscription_end, '%Y-%m-%d %H:%M:%S')
                        if datetime.now() > sub_date:
                            continue
                    except:
                        continue
                    
                    accounts = get_user_accounts(user_id)
                    
                    # Foydalanuvchi intervalini olish
                    user_min_interval, user_max_interval = get_user_interval(user_id)
                    
                    for acc in accounts:
                        display_name, phone, _, _, is_active, _, _ = acc
                        
                        if is_active != 1:
                            continue
                        
                        groups = get_user_groups(user_id, display_name)
                        active_groups = [g for g in groups if g[4] == 1]
                        
                        if not active_groups:
                            continue
                        
                        messages = get_user_messages(user_id)
                        
                        if not messages:
                            continue
                        
                        # Random xabar olish
                        msg_text = get_random_user_message(user_id)
                        
                        if not msg_text:
                            continue
                        
                        # Har bir guruhga xabar yuborish
                        for group in active_groups:
                            group_id = group[1]
                            
                            # Haqiqiy xabar yuborish
                            success, result = await send_message_to_group(display_name, group_id, msg_text)
                            
                            if success:
                                total_sent += 1
                                logger.info(f"✅ {display_name} -> {group[2]}: {msg_text[:50]}...")
                            else:
                                total_failed += 1
                                logger.error(f"❌ {display_name} -> {group[2]}: {result}")
                            
                            # Har bir xabar o'rtasida 3-8 soniya kutish
                            await asyncio.sleep(random.uniform(3, 8))
                    
                    # Har bir foydalanuvchi uchun o'z intervalida kutish
                    if total_sent > 0:
                        user_delay = random.randint(user_min_interval * 60, user_max_interval * 60)
                        logger.info(f"⏰ {user_id} uchun keyingi yuborishga {user_delay//60} daqiqa qoldi...")
                        await asyncio.sleep(user_delay)
                
                if total_sent > 0 or total_failed > 0:
                    last_send_time = datetime.now().strftime("%H:%M:%S")
                    logger.info(f"📊 NATIJA: {total_sent} ta xabar yuborildi, {total_failed} ta xatolik")
                else:
                    logger.info("ℹ️ Hech qanday xabar yuborilmadi (aktiv guruhlar yo'q)")
                    
                # Umumiy kutish
                await asyncio.sleep(60)
            
            else:
                await asyncio.sleep(30)
                
        except Exception as e:
            logger.error(f"Auto send loop xatosi: {e}")
            await asyncio.sleep(30)

def start_auto_send():
    """Auto send loopni alohida threadda ishga tushirish"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(auto_send_loop())

# ========== MAIN FUNCTION ==========

def main():
    print("\n" + "="*60)
    print("🤖 TELEGRAM BOT ADMIN PANELI (TO'LIQ TELEFON RAQAM ULANISHI QO'SHILGAN)")
    print("="*60)
    
    # Baza va sessions papkasini yaratish
    init_database()
    init_sessions_dir()
    
    print(f"\n✅ Baza fayli: {DB_FILE}")
    print(f"✅ Sessions papkasi: {SESSIONS_DIR}")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"📡 API ID: {API_ID}")
    print("="*60)
    
    try:
        # Auto send loopni alohida threadda ishga tushirish
        auto_send_thread = threading.Thread(target=start_auto_send, daemon=True)
        auto_send_thread.start()
        
        # Botni ishga tushirish
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Handlerlarni qo'shish
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("cancel", cancel_command))
        application.add_handler(CommandHandler("code", code_user_command))
        application.add_handler(CommandHandler("password", password_user_command))
        application.add_handler(CommandHandler("accounts", accounts_command))
        application.add_handler(CommandHandler("delete", delete_account_command))
        application.add_handler(CommandHandler("broadcast", broadcast_command))
        application.add_handler(CommandHandler("test", test_user_command))
        
        # Asosiy komanda handlerlari
        application.add_handler(CommandHandler("add", process_add_command))
        application.add_handler(CommandHandler("reject", process_reject_command))
        application.add_handler(CommandHandler("remove", process_remove_command))
        
        # Message handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_handler(CallbackQueryHandler(button_callback))
        
        print("\n🚀 Bot ishga tushmoqda...")
        print("👑 Admin: /start ni bosing")
        print("\n🎯 TELEFON RAQAM ULANISHI ENDI ISHLAYDI:")
        print("  1. '➕ Hisob qo'shish' tugmasini bosing")
        print("  2. Telefon raqamingizni yuboring (+998XXXXXXX)")
        print("  3. SMS kodini oling")
        print("  4. /code account1 KOD buyrug'i bilan kodni kiriting")
        print("  5. Agar 2FA bo'lsa: /password account1 PAROL")
        print("\n🎯 BOSHQA FOYDALANUVCHI KOMMANDALARI:")
        print("  /accounts - Barcha hisoblarni ko'rish")
        print("  /delete DISPLAY_NAME - Hisobni o'chirish")
        print("  /test DISPLAY_NAME - Sessionni test qilish")
        print("\n🎯 ADMIN KOMMANDALARI:")
        print("  /broadcast XABAR - Barcha foydalanuvchilarga xabar yuborish")
        print("  /add ID KUNLAR - Ruxsat berish")
        print("  /reject ID - So'rovni rad etish")
        print("  /remove ID - Foydalanuvchini o'chirish")
        print("="*60)
        print("⚠️ DIQQAT: Telefon raqam ulanishi uchun my.telegram.org dan olingan")
        print("          API_ID va API_HASH to'g'ri kiritilganligiga ishonch hosil qiling!")
        print("="*60)
        
        # Polling
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        print(f"\n❌ Xatolik: {e}")
        logger.error(f"Main xatosi: {e}")

if __name__ == "__main__":
    main()