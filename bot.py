import logging
import sqlite3
import uuid
import requests
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Log konfiguratsiyasi
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = "8220522354:AAHk8lxrgvQAapNvV7u4-d-9A0zPT6ki5oI"
ADMIN_IDS = [2091226701]  # O'zingizning ID ni qo'shing

# Database funksiyalari
def init_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            language TEXT DEFAULT 'uz',
            balance INTEGER DEFAULT 0,
            free_tries INTEGER DEFAULT 1,
            ai_access_until TIMESTAMP,
            subscription_until TIMESTAMP,
            referral_code TEXT UNIQUE,
            registered_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            order_type TEXT,
            topic TEXT,
            language TEXT,
            content TEXT,
            status TEXT DEFAULT 'completed',
            used_free_try BOOLEAN DEFAULT FALSE,
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_message TEXT,
            ai_response TEXT,
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    referral_code = str(uuid.uuid4())[:8]
    ai_access_until = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        cursor.execute(
            '''INSERT OR IGNORE INTO users 
            (user_id, username, first_name, referral_code, ai_access_until) 
            VALUES (?, ?, ?, ?, ?)''',
            (user_id, username, first_name, referral_code, ai_access_until)
        )
        conn.commit()
    except Exception as e:
        print(f"Error adding user: {e}")
    
    conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            'user_id': result[0],
            'username': result[1],
            'first_name': result[2],
            'language': result[3],
            'balance': result[4],
            'free_tries': result[5],
            'ai_access_until': result[6],
            'subscription_until': result[7],
            'referral_code': result[8],
            'registered_date': result[9]
        }
    return None

def parse_datetime(dt_string):
    """Datetime string ni to'g'ri parse qilish"""
    if not dt_string:
        return None
    
    try:
        # Millisecondlarni olib tashlash
        if '.' in dt_string:
            dt_string = dt_string.split('.')[0]
        
        # Turli formatlarni sinab ko'rish
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d'
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(dt_string, fmt)
            except ValueError:
                continue
        
        return None
    except:
        return None

def update_user_balance(user_id, amount):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def update_user_language(user_id, language):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET language = ? WHERE user_id = ?', (language, user_id))
    conn.commit()
    conn.close()

def use_free_try(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET free_tries = free_tries - 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_user_stats():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    conn.close()
    return total_users

def is_admin(user_id):
    return user_id in ADMIN_IDS

def can_user_use_service(user_id, service_type):
    """Foydalanuvchi xizmatdan foydalana olishini tekshirish"""
    user_data = get_user_data(user_id)
    if not user_data:
        return False
    
    # 1. Bepul urinishlar tekshirish
    if user_data['free_tries'] > 0:
        return True
    
    # 2. AI access tekshirish
    if user_data['ai_access_until']:
        ai_until = parse_datetime(user_data['ai_access_until'])
        if ai_until and ai_until > datetime.now():
            return True
    
    # 3. Subscription tekshirish
    if user_data['subscription_until']:
        sub_until = parse_datetime(user_data['subscription_until'])
        if sub_until and sub_until > datetime.now():
            return True
    
    # 4. Balans tekshirish
    if service_type == 'presentation' and user_data['balance'] >= 4000:
        return True
    elif service_type == 'referat' and user_data['balance'] >= 4000:
        return True
    elif service_type == 'ai_chat' and user_data['balance'] >= 1000:
        return True
    
    return False

# BEPUL AI API SERVICE
class AIService:
    def __init__(self):
        self.api_key = "AIzaSyC3bBfR9V4V7V8V9W0X1Y2Z3A4B5C6D7E8F"  # O'z API key ingizni qo'ying
    
    def chat_with_ai(self, message, language='uz'):
        """Bepul AI orqali javob olish"""
        try:
            return self._generate_with_gemini(message, language), 'gemini'
        except Exception as e:
            print(f"AI xatosi: {e}")
            return self._get_fallback_response(message, language), 'fallback'
    
    def _generate_with_gemini(self, message, language):
        """Google Gemini API"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={self.api_key}"
        
        # Til va kontekstga qarab prompt
        if language == 'uz':
            system_prompt = "Siz foydali va do'stona AI yordamchisisiz. O'zbek tilida javob bering. Savollarga aniq, tushunarli va batafsil javob bering."
        elif language == 'ru':
            system_prompt = "Вы полезный и дружелюбный AI помощник. Отвечайте на русском языке. Давайте точные, понятные и подробные ответы на вопросы."
        else:
            system_prompt = "You are a helpful and friendly AI assistant. Answer in English. Provide accurate, clear and detailed answers to questions."
        
        full_prompt = f"{system_prompt}\n\nUser: {message}\n\nAssistant:"
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": full_prompt
                }]
            }]
        }
        
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                result = response.json()
                return result['candidates'][0]['content']['parts'][0]['text']
            else:
                # Agar API ishlamasa, fallback response
                return self._get_fallback_response(message, language)
        except:
            return self._get_fallback_response(message, language)
    
    def _get_fallback_response(self, message, language):
        """AI ishlamasa standart javoblar"""
        if language == 'uz':
            return f"🤖 Sizning savolingiz: \"{message}\"\n\nMen sun'iy intellekt yordamchisiman. Hozircha real AI API ga ulana olmayapman, lekin sizga quyidagi xizmatlarni taklif qilaman:\n\n• 📊 Taqdimot yaratish\n• 📝 Referat tayyorlash\n• 📚 Mustaqil ish yaratish\n\nIltimos, yuqoridagi xizmatlardan birini tanlang!"
        elif language == 'ru':
            return f"🤖 Ваш вопрос: \"{message}\"\n\nЯ помощник искусственного интеллекта. В настоящее время я не могу подключиться к API ИИ, но могу предложить вам следующие услуги:\n\n• 📊 Создание презентаций\n• 📝 Подготовка рефератов\n• 📚 Создание самостоятельных работ\n\nПожалуйста, выберите одну из вышеуказанных услуг!"
        else:
            return f"🤖 Your question: \"{message}\"\n\nI am an AI assistant. Currently I cannot connect to AI API, but I can offer you these services:\n\n• 📊 Create presentations\n• 📝 Prepare essays\n• 📚 Create independent work\n\nPlease choose one of the services above!"

# AI Service ni ishga tushirish
ai_service = AIService()

# Tarjima funksiyalari
def get_text(language, key):
    texts = {
        'uz': {
            'welcome': "🤖 *Slide Maker AI* ga xush kelibsiz!",
            'main_menu': "🏠 Asosiy menyu",
            'services': "🛠 Xizmatlar",
            'ai_chat': "🤖 Sun'iy intellekt",
            'subscription': "💰 Obuna",
            'balance': "💰 Balans",
            'free_tries': "Bepul urinishlar",
            'ai_until': "Sun'iy intellekt",
            'back': "🔙 Orqaga",
            'enter_topic': "Mavzuni kiriting:",
            'example_topic': "Misol: *Iqtisodiyotda innovatsion texnologiyalar*",
            'processing': "⏳ Tayyorlanmoqda...",
            'no_access': "❌ Ushbu xizmatdan foydalanish uchun ruxsatingiz yo'q",
            'activate_subscription': "Obunani faollashtirishingiz yoki bepul bonuslarni qo'lga kiritishingiz mumkin"
        },
        'ru': {
            'welcome': "🤖 Добро пожаловать в *Slide Maker AI*!",
            'main_menu': "🏠 Главное меню",
            'services': "🛠 Услуги",
            'ai_chat': "🤖 Искусственный интеллект",
            'subscription': "💰 Подписка",
            'balance': "💰 Баланс",
            'free_tries': "Бесплатные попытки",
            'ai_until': "Искусственный интеллект",
            'back': "🔙 Назад",
            'enter_topic': "Введите тему:",
            'example_topic': "Пример: *Инновационные технологии в экономике*",
            'processing': "⏳ Обрабатывается...",
            'no_access': "❌ У вас нет доступа к этой услуге",
            'activate_subscription': "Вы можете активировать подписку или получить бесплатные бонусы"
        },
        'en': {
            'welcome': "🤖 Welcome to *Slide Maker AI*!",
            'main_menu': "🏠 Main Menu",
            'services': "🛠 Services",
            'ai_chat': "🤖 Artificial Intelligence",
            'subscription': "💰 Subscription",
            'balance': "💰 Balance",
            'free_tries': "Free Tries",
            'ai_until': "Artificial Intelligence",
            'back': "🔙 Back",
            'enter_topic': "Enter topic:",
            'example_topic': "Example: *Innovative technologies in economics*",
            'processing': "⏳ Processing...",
            'no_access': "❌ You don't have access to this service",
            'activate_subscription': "You can activate subscription or get free bonuses"
        }
    }
    return texts.get(language, texts['uz']).get(key, key)

# ==================== ASOSIY HANDLERLAR ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    
    add_user(user_id, username, first_name)
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    total_users = get_user_stats()
    
    welcome_text = f"{get_text(language, 'welcome')}\n\n"
    welcome_text += f"👤 **{first_name}**\n"
    welcome_text += f"📊 {total_users:,} oylik foydalanuvchi\n\n"
    
    # AI access ma'lumotlari
    if user_data:
        if user_data['ai_access_until']:
            ai_until = parse_datetime(user_data['ai_access_until'])
            if ai_until and ai_until > datetime.now():
                welcome_text += f"⭐ {get_text(language, 'ai_until')}: {ai_until.strftime('%d.%m.%Y %H:%M')} gacha\n"
        
        if user_data['free_tries'] > 0:
            welcome_text += f"🎁 {get_text(language, 'free_tries')}: {user_data['free_tries']} ta\n"
    
    keyboard = [
        [InlineKeyboardButton(get_text(language, 'services'), callback_data="services")],
        [InlineKeyboardButton(get_text(language, 'ai_chat'), callback_data="ai_chat")],
        [InlineKeyboardButton(get_text(language, 'subscription'), callback_data="subscription")],
        [InlineKeyboardButton("🌐 Tilni o'zgartirish", callback_data="change_language")],
        [InlineKeyboardButton("📖 Qo'llanma", callback_data="manual")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def services_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    keyboard = [
        [InlineKeyboardButton("📊 Yangi taqdimot yaratish", callback_data="new_presentation")],
        [InlineKeyboardButton("📚 Mustaqil ish tayyorlash", callback_data="independent_work")],
        [InlineKeyboardButton("📝 Referat tayyorlash", callback_data="referat")],
        [InlineKeyboardButton(get_text(language, 'ai_chat'), callback_data="ai_chat")],
        [InlineKeyboardButton(get_text(language, 'back'), callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🛠 *{get_text(language, 'services')}*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ==================== MUSTAQIL ISH TAYYORLASH ====================

async def independent_work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    # Foydalanish huquqini tekshirish
    if not can_user_use_service(user_id, 'referat'):
        keyboard = [
            [InlineKeyboardButton(get_text(language, 'subscription'), callback_data="subscription")],
            [InlineKeyboardButton(get_text(language, 'back'), callback_data="services")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"❌ *Ushbu xizmatdan foydalanish uchun hozircha ruxsatingiz yo'q.*\n\n"
            f"{get_text(language, 'activate_subscription')}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    context.user_data['waiting_for_topic'] = 'independent_work'
    
    # Savollar ro'yxati
    questions = [
        "📝 *Mustaqil ish mavzusini kiriting:*",
        "🔢 *Necha sahifali mustaqil ish kerak?*",
        "🎯 *Qaysi sohaga tegishli?* (Masalan: Iqtisodiyot, Pedagogika, Texnika)",
        "📚 *Qanday manbalardan foydalanish kerak?*",
        "⏰ *Qachongacha tayyor bo'lishi kerak?*"
    ]
    
    context.user_data['current_question'] = 0
    context.user_data['independent_work_answers'] = []
    
    keyboard = [
        [InlineKeyboardButton(get_text(language, 'cancel'), callback_data="services")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        questions[0] + f"\n\n{get_text(language, 'example_topic')}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_independent_work_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    if not context.user_data.get('waiting_for_topic') == 'independent_work':
        return
    
    current_question = context.user_data.get('current_question', 0)
    answers = context.user_data.get('independent_work_answers', [])
    
    # Javobni saqlash
    answers.append(update.message.text)
    context.user_data['independent_work_answers'] = answers
    
    # Savollar ro'yxati
    questions = [
        "📝 *Mustaqil ish mavzusini kiriting:*",
        "🔢 *Necha sahifali mustaqil ish kerak?*", 
        "🎯 *Qaysi sohaga tegishli?* (Masalan: Iqtisodiyot, Pedagogika, Texnika)",
        "📚 *Qanday manbalardan foydalanish kerak?*",
        "⏰ *Qachongacha tayyor bo'lishi kerak?*"
    ]
    
    # Keyingi savolga o'tish
    current_question += 1
    context.user_data['current_question'] = current_question
    
    if current_question < len(questions):
        # Yangi savolni yuborish
        await update.message.reply_text(
            questions[current_question],
            parse_mode='Markdown'
        )
    else:
        # Barcha savollarga javob berilgan
        await process_independent_work_order(update, context, answers)

async def process_independent_work_order(update: Update, context: ContextTypes.DEFAULT_TYPE, answers):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    # Ma'lumotlarni olish
    topic = answers[0]
    pages = answers[1]
    field = answers[2]
    sources = answers[3]
    deadline = answers[4]
    
    # Kredit ishlatish
    use_free_try(user_id)
    
    # Tayyorlanmoqda xabari
    processing_msg = await update.message.reply_text(
        f"{get_text(language, 'processing')}\n\n"
        f"📝 *Mavzu:* {topic}\n"
        f"🔢 *Sahifalar:* {pages}\n"
        f"🎯 *Soha:* {field}\n"
        f"⏰ *Muddat:* {deadline}",
        parse_mode='Markdown'
    )
    
    # AI orqali mustaqil ish yaratish
    ai_prompt = f"""
    Mustaqil ish yaratish uchun quyidagi ma'lumotlar:
    Mavzu: {topic}
    Sahifalar soni: {pages}
    Soha: {field}
    Manbalar: {sources}
    Muddat: {deadline}
    
    Batafsil mustaqil ish yozing.
    """
    
    try:
        ai_response, api_used = ai_service.chat_with_ai(ai_prompt, language)
        
        # Natijani yuborish
        result_text = f"✅ *Mustaqil ish tayyor!*\n\n"
        result_text += f"📝 **Mavzu:** {topic}\n"
        result_text += f"🔢 **Sahifalar:** {pages}\n"
        result_text += f"🎯 **Soha:** {field}\n"
        result_text += f"⏰ **Muddat:** {deadline}\n\n"
        result_text += f"📄 **Mustaqil Ish:**\n{ai_response}\n\n"
        result_text += f"💫 Bepul urinish ishlatildi"
        
        await processing_msg.edit_text(result_text, parse_mode='Markdown')
        
    except Exception as e:
        await processing_msg.edit_text(
            f"❌ Xatolik yuz berdi: {str(e)}\n\n"
            f"Iltimos, keyinroq urinib ko'ring.",
            parse_mode='Markdown'
        )
    
    # Kontekstni tozalash
    context.user_data.pop('waiting_for_topic', None)
    context.user_data.pop('current_question', None)
    context.user_data.pop('independent_work_answers', None)

# ==================== SUN'IY INTELEKT SUHBATI ====================

async def ai_chat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    context.user_data['in_ai_chat'] = True
    
    keyboard = [
        [InlineKeyboardButton(get_text(language, 'back'), callback_data="services")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🤖 *Sun'iy Intellekt Yordamchisi*\n\n"
        "Men sizga har qanday savollaringizga javob berishga tayyorman!\n"
        "Savolingizni yuboring...",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    if not context.user_data.get('in_ai_chat'):
        return
    
    # Foydalanish huquqini tekshirish
    if not can_user_use_service(user_id, 'ai_chat'):
        await update.message.reply_text(
            f"{get_text(language, 'no_access')}\n\n"
            f"{get_text(language, 'activate_subscription')}",
            parse_mode='Markdown'
        )
        return
    
    # Kredit ishlatish
    use_free_try(user_id)
    
    # Fikrlash xabarini yuborish
    thinking_msg = await update.message.reply_text("🤔 Fikrlayapman...")
    
    try:
        # AI dan javob olish
        ai_response, api_used = ai_service.chat_with_ai(user_message, language)
        
        # Foydalanuvchiga javobni yuborish
        response_text = f"🤖 **AI Yordamchi:**\n\n{ai_response}\n\n"
        response_text += f"💫 Bepul urinish ishlatildi"
        
        await thinking_msg.edit_text(response_text, parse_mode='Markdown')
        
    except Exception as e:
        await thinking_msg.edit_text(
            f"❌ Xatolik yuz berdi: {str(e)}\n\n"
            f"Iltimos, keyinroq urinib ko'ring.",
            parse_mode='Markdown'
        )

# ==================== QOLGAN HANDLERLAR ====================

async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    keyboard = [
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz")],
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")],
        [InlineKeyboardButton(get_text(language, 'back'), callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🌐 *Tilni tanlang*\n\n"
        f"Выберите язык\n"
        f"Select language",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    language = query.data.replace('lang_', '')
    
    update_user_language(user_id, language)
    
    await query.answer(f"Til {language} ga o'zgartirildi!", show_alert=True)
    await start_from_callback(update, context)

async def start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback orqali start ni chaqirish"""
    query = update.callback_query
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    total_users = get_user_stats()
    
    welcome_text = f"{get_text(language, 'welcome')}\n\n"
    welcome_text += f"👤 **{user_data['first_name']}**\n"
    welcome_text += f"📊 {total_users:,} oylik foydalanuvchi\n\n"
    
    if user_data:
        if user_data['ai_access_until']:
            ai_until = parse_datetime(user_data['ai_access_until'])
            if ai_until and ai_until > datetime.now():
                welcome_text += f"⭐ {get_text(language, 'ai_until')}: {ai_until.strftime('%d.%m.%Y %H:%M')} gacha\n"
        
        if user_data['free_tries'] > 0:
            welcome_text += f"🎁 {get_text(language, 'free_tries')}: {user_data['free_tries']} ta\n"
    
    keyboard = [
        [InlineKeyboardButton(get_text(language, 'services'), callback_data="services")],
        [InlineKeyboardButton(get_text(language, 'ai_chat'), callback_data="ai_chat")],
        [InlineKeyboardButton(get_text(language, 'subscription'), callback_data="subscription")],
        [InlineKeyboardButton("🌐 Tilni o'zgartirish", callback_data="change_language")],
        [InlineKeyboardButton("📖 Qo'llanma", callback_data="manual")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def subscription_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    text = "💰 *Obuna*\n\n"
    text += "Tez orada obuna xizmati ishga tushadi!\n"
    text += "Hozircha bepul urinishlardan foydalaning."
    
    keyboard = [
        [InlineKeyboardButton(get_text(language, 'back'), callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def manual_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    language = user_data['language'] if user_data else 'uz'
    
    text = "📖 *Qo'llanma*\n\n"
    text += "Botdan foydalanish bo'yicha qo'llanma tez orada qo'shiladi!"
    
    keyboard = [
        [InlineKeyboardButton(get_text(language, 'back'), callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def end_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI suhbatini tugatish"""
    query = update.callback_query
    context.user_data.pop('in_ai_chat', None)
    await services_menu(update, context)

# ==================== ASOSIY FUNKSIYA ====================

def main():
    init_db()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlerlar
    application.add_handler(CommandHandler("start", start))
    
    # Callback query handlerlar
    application.add_handler(CallbackQueryHandler(services_menu, pattern="services"))
    application.add_handler(CallbackQueryHandler(independent_work, pattern="independent_work"))
    application.add_handler(CallbackQueryHandler(ai_chat_start, pattern="ai_chat"))
    application.add_handler(CallbackQueryHandler(change_language, pattern="change_language"))
    application.add_handler(CallbackQueryHandler(set_language, pattern="^lang_"))
    application.add_handler(CallbackQueryHandler(subscription_menu, pattern="subscription"))
    application.add_handler(CallbackQueryHandler(manual_menu, pattern="manual"))
    application.add_handler(CallbackQueryHandler(start_from_callback, pattern="main_menu"))
    application.add_handler(CallbackQueryHandler(end_ai_chat, pattern="back"))
    
    # Message handlerlar
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_independent_work_questions))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))
    
    application.run_polling()

if __name__ == '__main__':
    main()