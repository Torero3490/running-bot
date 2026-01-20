#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Бот для Бегового Сообщества
Функции: Утреннее приветствие, Погода, Темы дня, Анонимная отправка, Ежедневная сводка, Рейтинг, Уровни, Голосовые сообщения
"""

import os
import asyncio
import logging
import threading
import time
import random
import httpx
import signal
import sys
import json
import base64
from io import BytesIO
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)
import pytz

# ============== OPENAI INTEGRATION ==============
# API ключ OpenAI для ИИ-ответов (хранится в переменных окружения)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
from flask import Flask

# ============== GARMIN INTEGRATION ==============
try:
    import garminconnect
    from cryptography.fernet import Fernet
    GARMIN_AVAILABLE = True
except ImportError:
    GARMIN_AVAILABLE = False
    logger.warning("Garmin libraries not available. Install: pip install garminconnect cryptography")

# Ключ шифрования для паролей Garmin (генерируется при первом запуске)
GARMIN_ENCRYPTION_KEY = None

# ============== КОНФИГУРАЦИЯ ==============
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Токен бота не найден!")

RENDER_URL = os.environ.get("RENDER_URL", "")

CHAT_ID = os.environ.get("CHAT_ID")
if not CHAT_ID:
    raise ValueError("CHAT_ID не найден!")

try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    raise ValueError("CHAT_ID должен быть числом!")

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
UTC_OFFSET = 3  # Москва = UTC+3

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============== FLASK ==============
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


@app.route("/health")
def health():
    return "OK"


def run_flask():
    # На Render порт задаётся через переменную окружения $PORT
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"[FLASK] Запуск Flask на порту {port}")
    app.run(host="0.0.0.0", port=port)


# ============== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==============
application = None
morning_message_id = None
morning_scheduled_date = ""
bot_running = True
motivation_sent_times = []

# ============== КОМАНДА /MAM ==============
# ID сообщения "Не зли маму..."
mam_message_id = None
MAM_PHOTO_PATH = "5422343903253302332.jpg"

# ============== НОЧНОЙ РЕЖИМ ==============
# {user_id: message_count} - персональный счётчик для каждого пользователя
user_night_messages = {}
# {user_id: warning_sent_date} - когда отправляли предупреждение
user_night_warning_sent = {}

# ============== ОТСЛЕЖИВАНИЕ ВОЗВРАЩЕНЦЕВ ==============
# {user_id: last_active_date}
user_last_active = {}

# ============== СТАТИСТИКА ДЛЯ ЕЖЕДНЕВНОЙ СВОДКИ ==============
daily_stats = {
    "date": "",
    "total_messages": 0,
    "user_messages": {},  # {user_id: {"name": str, "count": int}}
    "photos": [],  # [{"file_id": str, "user_id": int, "likes": int, "message_id": int}]
}
daily_summary_sent = False

# ============== РЕЙТИНГ УЧАСТНИКОВ ==============
# {user_id: {"name": str, "messages": int, "photos": int, "likes": int, "replies": int}}
user_rating_stats = {}

# {user_id: "Новичок"} - текущий уровень пользователя
user_current_level = {}

# ============== GARMIN INTEGRATION ==============
# {user_id: {"name": str, "email": str, "last_activity_id": str, "monthly_distance": float, "monthly_activities": int}}
garmin_users = {}

# Множество для отслеживания уже обработанных активностей (idempotency)
# Формат: "user_id:activity_id"
processed_activities = set()

# {user_id: {"name": str, "activities": int, "distance": float, "duration": int, "calories": int}}
user_running_stats = {}

# ============== ДНИ РОЖДЕНИЯ ==============
# {user_id: {"name": str, "birthday": "DD.MM"}}
user_birthdays = {}

# Файл для хранения дней рождения
BIRTHDAYS_FILE = "birthdays.json"

# ============== OPENAI AI RESPONSES ==============
# ИИ-ответы на сообщения пользователей, когда они отвечают боту
AI_ENABLED = bool(OPENAI_API_KEY)
AI_MODEL = "gpt-4o-mini"  # Быстрая и недорогая модель

# ============== GOOGLE GEMINI AI (БЕСПЛАТНЫЙ ВАРИАНТ) ==============
# Google Gemini API полностью бесплатен!
# Получите ключ: https://aistudio.google.com/app/apikey
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_ENABLED = bool(GEMINI_API_KEY)
GEMINI_MODEL = "gemini-1.5-flash"  # Быстрая и бесплатная модель

# ============== DEEPSEEK AI (ТОЖЕ БЕСПЛАТНЫЙ) ==============
# DeepSeek работает из большинства регионов
# Получите ключ: https://platform.deepseek.com/api-key
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_ENABLED = bool(DEEPSEEK_API_KEY)
DEEPSEEK_MODEL = "deepseek-chat"  # Бесплатная модель

# ============== GARMIN INTEGRATION ==============
GARMIN_DATA_FILE = "garmin_users.json"
GARMIN_KEY_FILE = "garmin_key.key"

# ============== ЗАЩИТА ОТ НАКРУТОК ==============

# ============== ЗАЩИТА ОТ НАКРУТОК ==============
# Максимум баллов в час
MAX_POINTS_PER_HOUR = 20
# Максимум сообщений в минуту для начисления баллов
MAX_MESSAGES_PER_MINUTE = 5
# Минимальная длина сообщения для балла
MIN_MESSAGE_LENGTH = 5
# {user_id: [времена сообщений]}
user_message_times = {}

# ============== КОЭФФИЦИЕНТЫ РЕЙТИНГА ==============
POINTS_PER_MESSAGES = 300  # За сколько сообщений даётся 1 балл
POINTS_PER_PHOTOS = 10    # За сколько фото даётся 1 балл
POINTS_PER_LIKES = 50     # За сколько лайков даётся 1 балл
POINTS_PER_REPLY = 1      # За каждый ответ на твоё сообщение

# ============== УРОВНИ УЧАСТНИКОВ ==============
USER_LEVELS = {
    "Новичок": 0,         # 0+ очков
    "Активный": 10,       # 10+ очков
    "Лидер": 50,          # 50+ очков
    "Легенда чата": 100,   # 100+ очков
}

LEVEL_EMOJIS = {
    "Новичок": "🌱",
    "Активный": "⭐",
    "Лидер": "👑",
    "Легенда чата": "🏆",
}

# ============== УЧЁТ НЕДЕЛЬ ==============
current_week = 0

# ============== GARMIN UTILS ==============
def get_garmin_key():
    """Получение или создание ключа шифрования"""
    global GARMIN_ENCRYPTION_KEY
    
    if GARMIN_ENCRYPTION_KEY is not None:
        return GARMIN_ENCRYPTION_KEY
    
    try:
        if os.path.exists(GARMIN_KEY_FILE):
            with open(GARMIN_KEY_FILE, 'rb') as f:
                GARMIN_ENCRYPTION_KEY = f.read()
            logger.info("[GARMIN] Ключ шифрования загружен из файла")
        else:
            GARMIN_ENCRYPTION_KEY = Fernet.generate_key()
            with open(GARMIN_KEY_FILE, 'wb') as f:
                f.write(GARMIN_ENCRYPTION_KEY)
            logger.info("[GARMIN] Создан новый ключ шифрования")
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка работы с ключом: {e}")
        # Создаем ключ в памяти как запасной вариант
        GARMIN_ENCRYPTION_KEY = Fernet.generate_key()
    
    return GARMIN_ENCRYPTION_KEY


def encrypt_garmin_password(password: str) -> str:
    """Шифрование пароля Garmin"""
    try:
        key = get_garmin_key()
        f = Fernet(key)
        encrypted = f.encrypt(password.encode())
        return base64.b64encode(encrypted).decode()
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка шифрования: {e}")
        return ""


def decrypt_garmin_password(encrypted_password: str) -> str:
    """Расшифровка пароля Garmin"""
    try:
        key = get_garmin_key()
        f = Fernet(key)
        decoded = base64.b64decode(encrypted_password.encode())
        decrypted = f.decrypt(decoded)
        return decrypted.decode()
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка дешифрования: {e}")
        return ""


def save_garmin_users():
    """Сохранение данных пользователей Garmin в файл"""
    try:
        # Конвертируем для JSON (ключи должны быть строками)
        save_data = {}
        for user_id, data in garmin_users.items():
            save_data[str(user_id)] = {
                "name": data["name"],
                "email": data["email"],
                "encrypted_password": data["encrypted_password"],
                "last_activity_id": data.get("last_activity_id", ""),
                "monthly_distance": data.get("monthly_distance", 0.0),
                "monthly_activities": data.get("monthly_activities", 0),
                "last_activity_date": data.get("last_activity_date", "")
            }
        
        with open(GARMIN_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"[GARMIN] Данные сохранены: {len(garmin_users)} пользователей")
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка сохранения: {e}")


def load_garmin_users():
    """Загрузка данных пользователей Garmin из файла"""
    global garmin_users
    
    try:
        if not os.path.exists(GARMIN_DATA_FILE):
            logger.info("[GARMIN] Файл данных не найден, создаём пустой")
            garmin_users = {}
            return
        
        with open(GARMIN_DATA_FILE, 'r', encoding='utf-8') as f:
            load_data = json.load(f)
        
        # Конвертируем обратно (ключи -> int)
        garmin_users = {}
        for user_id_str, data in load_data.items():
            garmin_users[int(user_id_str)] = {
                "name": data["name"],
                "email": data["email"],
                "encrypted_password": data["encrypted_password"],
                "last_activity_id": data.get("last_activity_id", ""),
                "monthly_distance": data.get("monthly_distance", 0.0),
                "monthly_activities": data.get("monthly_activities", 0),
                "last_activity_date": data.get("last_activity_date", "")
            }
        
        logger.info(f"[GARMIN] Загружено пользователей: {len(garmin_users)}")
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка загрузки: {e}")
        garmin_users = {}

# ============== ДАННЫЕ ==============
DAY_THEMES = {
    "Monday": "🎵 Понедельник — день музыки! Какая песня заводит тебя на пробежку?",
    "Tuesday": "🐕 Вторник — день питомцев! Покажи своего четвероногого напарника!",
    "Wednesday": "💝 Среда — день добрых дел! Поделись, кому ты сегодня помог!",
    "Thursday": "🍕 Четверг — день еды! Что ты ешь перед и после пробежки?",
    "Friday": "📸 Пятница — день селфи! Покажи своё лицо после тренировки!",
    "Saturday": "😩 Суббота — день нытья! Расскажи, что сегодня было тяжело!",
    "Sunday": "📷 Воскресенье — день нюдсов! Покажи красивые виды с пробежки!",
}

WELCOME_MESSAGES = [
    "Добро пожаловать в наш беговой муравейник! Ты уже выбрал свою дистанцию: 5 км для разминки, полумарафон для души или сразу ультрамарафон — чтобы проверить, на что способен? Расскажи, какой у тебя уровень: «ещё дышу», «уже потею» или «я — машина»?",
    "Привет, новичок! В нашем чате правила простые: если не можешь бежать — иди, если не можешь идти — ползи, но главное — не сдавайся! Так ты кто: начинающий стайер, опытный марафонец или легендарный рекордсмен в ожидании?",
    "Ого, новый бегун на горизонте! Срочно заполни анкету: имя, любимый маршрут и цель на ближайший забег (от «просто попробовать» до «порвать всех на финише»). Добро пожаловать в команду!",
    "Привет! Ты попал в место, где километры считают не по GPS, а по улыбкам. Так что ты: тот, кто только учится завязывать кроссовки, уже бегает по утрам или готов пробежать марафон в пижаме?",
    "Внимание! В чате обнаружен свежий беговой ресурс! Объект, назовите ваш статус: «ещё не пробежал первый км», «уже втянулся» или «я тут главный пейсмейкер»?",
    "Добро пожаловать в беговую семью! У нас тут три категории: новички (которые боятся слова «марафон»), любители (которые уже знают, что такое крепатура) и легенды (которые бегают даже во сне). К какой относишься ты?",
    "Эй, новенький! Признавайся: ты тут чтобы ставить рекорды, искать мотивацию или просто поболтать о кроссовках? В любом случае — беги к нам, у нас весело!",
    "Привет-привет! Ты сейчас на этапе: «кто все эти бегуны?», «о, тут классные ребята» или «я знаю все трассы, но никому не скажу»? Добро пожаловать в наш забег!",
    "Новый участник? Отлично! У нас есть три уровня сложности: лёгкий (просто выйти на пробежку), средний (не сойти с дистанции) и экспертный (улыбаться на последних километрах). Какой выбираешь?",
    "Добро пожаловать в чат, где километры — это не просто цифры, а истории! Ты кто: тот, кто только мечтает о первом забеге, уже собирает медали или готов пробежать 42 км ради шутки?",
]

# ============== СОВЕТЫ ДНЯ (ИЗ ИНТЕРНЕТА) ==============
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
import time

# Кэш советов
_tips_cache = {
    "running": [],
    "recovery": [],
    "equipment": [],
    "last_update": 0
}

CACHE_DURATION = 3600  # Обновлять советы каждый час


# ============== SMART LOCAL AI RESPONSE SYSTEM ==============
# Работает 100% бесплатно, без API и региональных ограничений!

# Категории ответов
GREETING_RESPONSES = [
    "Привет, {user_name}! Рад тебя слышать! 🏃‍♂️",
    "{user_name}, привет! Как бег сегодня?",
    "Здорово, {user_name}! Давай больше активности в чат!",
    "{user_name}, ты в форме! Продолжай в том же духе! 💪",
    "Привет, {user_name}! Готов к новым пробежкам?",
]

THANKS_RESPONSES = [
    "Пожалуйста, {user_name}! Всегда рад помочь! 😊",
    "Не за что, {user_name}! Это моя работа — быть полезным!",
    "{user_name}, взаимно! Благодарю за обратную связь!",
    "Всегда пожалуйста, {user_name}! Обращайся ещё!",
]

AGREEMENT_RESPONSES = [
    "Согласен, {user_name}! Отличное замечание! 👍",
    "Точно, {user_name}! Ты прав!",
    "{user_name}, полностью поддерживаю!",
    "Именно так, {user_name}!",
]

QUESTION_RESPONSES = [
    "Хороший вопрос, {user_name}! Давай подумаем...",
    "{user_name}, интересуешься? Это здорово!",
    "Вопрос по существу, {user_name}! Уважаю!",
    "{user_name}, продолжай спрашивать — так держать!",
]

RUNNING_RESPONSES = [
    "О, {user_name} говорит о беге! Моя любимая тема! 🏃‍♂️",
    "{user_name}, бег — это жизнь! Согласен!",
    "Бег — лучший способ держать себя в форме, {user_name}!",
    "{user_name}, ты вдохновляешь меня на новые подвиги!",
]

MORNING_RESPONSES = [
    "Доброе утро, {user_name}! Солнце встаёт — ты тоже!",
    "{user_name}, утро — лучшее время для пробежки!",
    "С добрым утром, {user_name}! Пусть день будет активным!",
    "{user_name}, проснулся — уже молодец! Теперь бегом!",
]

MOTIVATION_RESPONSES = [
    "{user_name}, ты можешь больше, чем думаешь!",
    "Верь в себя, {user_name}! Я в тебя верю!",
    "{user_name}, каждый км — это шаг к цели!",
    "Не сдавайся, {user_name}! Финиш близок!",
]

JOKE_RESPONSES = [
    "{user_name}, шутка зашла! Юмор — это хорошо! 😄",
    "Ха! {user_name}, ты меня рассмешил!",
    "{user_name}, с тобой весело! Продолжай в том же духе!",
    "Отличное чувство юмора, {user_name}!",
]

EMOJI_RESPONSES = [
    "😄 {user_name}, эмодзи — это язык вечности!",
    "{user_name}, классный эмодзи!",
    "Принято, {user_name}! 👍",
]

DEFAULT_RESPONSES = [
    "Интересно, {user_name}! Расскажи подробнее!",
    "{user_name}, я тебя слушаю...",
    "Понял, {user_name}! Продолжай!",
    "{user_name}, это заслуживает внимания!",
    "Заметил, {user_name}! Хорошо, что написал!",
    "{user_name}, спасибо за сообщение!",
]


async def generate_ai_response(user_message: str, bot_message: str, user_name: str) -> str:
    """
    Умный локальный генератор ответов.
    Работает БЕЗ внешних API — анализирует ключевые слова и выбирает подходящий ответ!
    """
    
    user_message_lower = user_message.lower()
    bot_message_lower = bot_message.lower() if bot_message else ""
    
    # Анализируем сообщение пользователя
    message_type = "default"
    
    # Приветствия
    greetings = ["привет", "здравствуй", "здорово", "добрый день", "добрый вечер", "доброе утро", "hello", "hi", "hey"]
    if any(word in user_message_lower for word in greetings):
        message_type = "greeting"
    
    # Благодарности
    thanks = ["спасибо", "благодарю", "мерси", "thx", "thanks", "благодарность"]
    if any(word in user_message_lower for word in thanks):
        message_type = "thanks"
    
    # Согласие
    agreement = ["да", "согласен", "точно", "именно", "верно", "прав", "поддерживаю", "yes", "agreed"]
    if any(word in user_message_lower for word in agreement):
        message_type = "agreement"
    
    # Вопросы
    questions = ["?", "как", "что", "почему", "зачем", "когда", "где", "кто", "сколько", "можно ли", "подскажи"]
    if any(word in user_message_lower for word in questions):
        message_type = "question"
    
    # Бег
    running_words = ["бег", "бегать", "пробежка", "пробежать", "кросс", "марафон", "km", "кма", "темп", "пульс"]
    if any(word in user_message_lower for word in running_words):
        message_type = "running"
    
    # Утро
    morning_words = ["утро", "доброе утро", "утра", "проснулся", "проснулась"]
    if any(word in user_message_lower for word in morning_words):
        message_type = "morning"
    
    # Мотивация
    motivation_words = ["сложно", "тяжело", "устал", "не могу", "лениво", "мотивация", "лень"]
    if any(word in user_message_lower for word in motivation_words):
        message_type = "motivation"
    
    # Шутки и весёлые слова
    joke_words = ["хаха", "lol", "смешно", "прикол", "кринж", "ахах", "хех", "😂", "🤣"]
    if any(word in user_message_lower for word in joke_words):
        message_type = "joke"
    
    # Только эмодзи
    emoji_pattern = r'^[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\s]+$'
    import re
    if re.match(emoji_pattern, user_message.strip()):
        message_type = "emoji"
    
    # Выбираем категорию ответов
    response_category = {
        "greeting": GREETING_RESPONSES,
        "thanks": THANKS_RESPONSES,
        "agreement": AGREEMENT_RESPONSES,
        "question": QUESTION_RESPONSES,
        "running": RUNNING_RESPONSES,
        "morning": MORNING_RESPONSES,
        "motivation": MOTIVATION_RESPONSES,
        "joke": JOKE_RESPONSES,
        "emoji": EMOJI_RESPONSES,
        "default": DEFAULT_RESPONSES,
    }
    
    # Получаем случайный ответ из категории
    import random
    responses = response_category.get(message_type, DEFAULT_RESPONSES)
    response_template = random.choice(responses)
    
    # Форматируем ответ с именем пользователя
    try:
        response = response_template.format(user_name=user_name)
    except:
        response = response_template
    
    logger.info(f"[AI-LOCAL] 🎯 Тип сообщения: {message_type} | Ответ для {user_name}: {response[:50]}...")
    
    return response


# ============== GARMIN CHECKER ==============
async def check_garmin_activities():
    """Проверка новых пробежек у всех зарегистрированных пользователей"""
    global garmin_users, user_running_stats
    
    if not GARMIN_AVAILABLE:
        logger.warning("[GARMIN] Библиотека недоступна")
        return
    
    if not garmin_users:
        logger.debug("[GARMIN] Нет зарегистрированных пользователей")
        return
    
    logger.info(f"[GARMIN] Проверяем активности у {len(garmin_users)} пользователей...")
    
    now = datetime.now(MOSCOW_TZ)
    today = now.strftime("%Y-%m-%d")
    current_month = now.strftime("%Y-%m")
    
    # Создаём БЕЗОПАСНУЮ копию словаря для итерации
    try:
        users_items = list(garmin_users.items()) if garmin_users else []
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка создания копии словаря: {e}")
        return
    
    for user_id, user_data in users_items:
        try:
            # ========== МАКСИМАЛЬНАЯ ЗАЩИТА ОТ None ==========
            # Защищаемся от любых проблем с user_id
            try:
                user_id_str = str(user_id) if user_id is not None else "None"
            except Exception:
                user_id_str = "ERROR_CONVERTING"
            
            # Защищаемся от любых проблем с user_data
            try:
                user_data_is_dict = isinstance(user_data, dict) if user_data is not None else False
            except Exception:
                user_data_is_dict = False
            
            # Если что-то не так - пропускаем этот элемент
            if user_id is None or user_data is None or not user_data_is_dict:
                logger.warning(f"[GARMIN] 🛡️ Пропускаем повреждённые данные: user_id={user_id_str}, user_data type={type(user_data)}")
                try:
                    if user_id is not None and user_id in garmin_users:
                        del garmin_users[user_id]
                        save_garmin_users()
                        logger.info(f"[GARMIN] 🗑️ Удалён повреждённый пользователь {user_id_str} из базы")
                except Exception as del_error:
                    logger.error(f"[GARMIN] Не удалось удалить повреждённые данные: {del_error}")
                continue
            
            # Проверяем наличие обязательных полей
            if "encrypted_password" not in user_data:
                logger.warning(f"[GARMIN] Пропускаем user_id={user_id_str} без encrypted_password")
                continue
            if "email" not in user_data:
                logger.warning(f"[GARMIN] Пропускаем user_id={user_id_str} без email")
                continue
            
            # Расшифровываем пароль
            password = decrypt_garmin_password(user_data["encrypted_password"])
            email = user_data["email"]
            
            # Проверяем Garmin (с дополнительной защитой)
            try:
                client = garminconnect.Garmin(email, password)
                client.login()
                
                # Получаем последние активности (последние 10 для надёжности)
                activities = client.get_activities(0, 10)
            except Exception as garmin_error:
                logger.error(f"[GARMIN] Ошибка подключения к Garmin для {email}: {garmin_error}")
                continue
            
            if not activities:
                logger.info(f"[GARMIN] У пользователя {email} нет активностей")
                continue
            
            logger.info(f"[GARMIN] У пользователя {email} найдено {len(activities)} активностей")
            
            # Проверяем каждую активность
            for activity in activities:
                activity_type = activity.get('activityType', {}).get('typeKey', 'unknown')
                activity_id = str(activity.get('activityId', 'unknown'))
                
                # Проверяем timestamp - Garmin может возвращать разные форматы
                start_time_local = activity.get('startTimeLocal', '')
                start_time_seconds = activity.get('startTimeInSeconds', 0)
                start_time_nano = activity.get('startTimeInNanoSeconds', 0)
                
                # Логируем что получаем
                logger.info(f"[GARMIN] Raw activity: id={activity_id}, type={activity_type}")
                logger.info(f"[GARMIN] Timestamp: local='{start_time_local}', seconds={start_time_seconds}, nano={start_time_nano}")
                
                # Пробуем разные форматы timestamp (в порядке приоритета)
                activity_date_dt = None
                
                if start_time_local:
                    try:
                        # Пробуем парсить startTimeLocal (формат: "YYYY-MM-DD HH:MM:SS")
                        activity_date_dt = datetime.strptime(start_time_local, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MOSCOW_TZ)
                        logger.info(f"[GARMIN] Успешно распознали startTimeLocal: {start_time_local}")
                    except Exception as e:
                        logger.warning(f"[GARMIN] Не удалось распознать startTimeLocal: {e}")
                
                if activity_date_dt is None and start_time_seconds and start_time_seconds > 0:
                    try:
                        activity_date_dt = datetime.fromtimestamp(start_time_seconds, tz=MOSCOW_TZ)
                        logger.info(f"[GARMIN] Используем startTimeInSeconds: {start_time_seconds}")
                    except Exception as e:
                        logger.warning(f"[GARMIN] Не удалось распознать startTimeInSeconds: {e}")
                
                if activity_date_dt is None and start_time_nano and start_time_nano > 0:
                    try:
                        # Наносекунды - переводим в секунды
                        activity_date_dt = datetime.fromtimestamp(start_time_nano // 1000000000, tz=MOSCOW_TZ)
                        logger.info(f"[GARMIN] Используем startTimeInNanoSeconds: {start_time_nano}")
                    except Exception as e:
                        logger.warning(f"[GARMIN] Не удалось распознать startTimeInNanoSeconds: {e}")
                
                if activity_date_dt is None:
                    activity_date_dt = now  # Используем текущее время как fallback
                    logger.warning(f"[GARMIN] Не удалось распознать timestamp, используем текущее время")
                
                activity_date_str = activity_date_dt.strftime("%Y-%m-%d")
                
                logger.info(f"[GARMIN] Дата активности: {activity_date_str}")
                
                # Фильтруем только бег
                if activity_type not in ['running', 'treadmill_running', 'trail_running']:
                    logger.debug(f"[GARMIN] Пропускаем (не бег): {activity_type}")
                    continue
                
                logger.info(f"[GARMIN] Найден бег: id={activity_id}, date={activity_date_str}")
                
                # Проверяем, новая ли это активность
                last_id = user_data.get("last_activity_id", "")
                
                # Дополнительная проверка: отслеживаем обработанные активности в памяти
                activity_key = f"{user_id}:{activity_id}"
                if activity_key in processed_activities:
                    logger.info(f"[GARMIN] 🛡️ Активность {activity_id} уже обработана в этой сессии (idempotency check)")
                    continue
                
                if activity_id == last_id:
                    logger.info(f"[GARMIN] Это старая активность (уже обработана)")
                    continue
                
                # Проверяем, не старая ли активность (для тестирования уменьшено до 60 дней)
                # После успешного тестирования вернуть обратно на 2 дня
                days_diff = (now - activity_date_dt).days
                max_days = 60
                if days_diff > max_days:
                    logger.warning(f"[GARMIN] Активность {activity_id} старше {max_days} дней ({days_diff} дней), пропускаем")
                    continue
                
                # Временно обновляем last_activity_id ПЕРЕД публикацией
                # Это предотвращает повторную публикацию при сбоях
                old_activity_id = user_data.get("last_activity_id", "")
                user_data["last_activity_id"] = activity_id
                user_data["last_activity_date"] = activity_date_str
                save_garmin_users()
                
                # Это новая пробежка! Публикуем в чат
                logger.info(f"[GARMIN] Публикую пробежку: {activity_id}")
                success = await publish_run_result(user_id, user_data, activity, now, current_month)
                
                if success:
                    # Добавляем в множество обработанных активностей
                    processed_activities.add(activity_key)
                    logger.info(f"[GARMIN] ✅ Пробежка {activity_id} успешно опубликована")
                else:
                    # Публикация не удалась — откатываем last_activity_id
                    logger.warning(f"[GARMIN] ⚠️ Публикация не удалась, откат last_activity_id")
                    user_data["last_activity_id"] = old_activity_id
                    save_garmin_users()
            
            # Сохраняем данные
            save_garmin_users()
            
        except Exception as e:
            # Безопасная обработка ошибки - user_data может быть None
            user_email = user_data.get("email", "Unknown") if user_data else "Unknown"
            user_id_str = str(user_id) if user_id is not None else "None"
            logger.error(f"[GARMIN] Ошибка проверки пользователя {user_id_str} ({user_email}): {e}", exc_info=True)
            continue


async def publish_run_result(user_id, user_data, activity, now, current_month):
    """Публикация результатов пробежки в чат. Возвращает True при успехе."""
    global application, user_running_stats
    
    # ========== МАКСИМАЛЬНАЯ ЗАЩИТА ОТ None ==========
    try:
        # Проверяем входные параметры
        if user_id is None:
            logger.error("[GARMIN] publish_run_result: user_id равен None")
            return False
        if user_data is None:
            logger.error("[GARMIN] publish_run_result: user_data равен None")
            return False
        if not isinstance(user_data, dict):
            logger.error(f"[GARMIN] publish_run_result: user_data не словарь: {type(user_data)}")
            return False
        
        # Проверяем наличие обязательных полей
        if "name" not in user_data:
            logger.error(f"[GARMIN] publish_run_result: отсутствует поле 'name' в user_data")
            return False
            
    except Exception as init_error:
        logger.error(f"[GARMIN] Ошибка инициализации publish_run_result: {init_error}")
        return False
    
    try:
        # Извлекаем данные активности
        distance_meters = activity.get('distance', 0)
        distance_km = distance_meters / 1000
        
        duration_seconds = activity.get('duration', 0)
        duration_min = int(duration_seconds // 60)
        duration_sec = int(duration_seconds % 60)
        
        avg_heartrate = activity.get('averageHeartRate', 0)
        calories = activity.get('calories', 0)
        
        # Вычисляем темп
        if distance_km > 0:
            pace_seconds = duration_seconds / distance_km
            pace_min = int(pace_seconds // 60)
            pace_sec = int(pace_seconds % 60)
            pace_str = f"{pace_min}:{pace_sec:02d} мин/км"
        else:
            pace_str = "N/A"
        
        # Форматируем время
        time_str = f"{duration_min}:{duration_sec:02d}"
        
        # Проверяем новый месяц для сброса
        user_monthly = user_data.get("last_activity_date", "")
        if user_monthly and user_monthly[:7] != current_month:
            # Новый месяц - сбрасываем счётчики
            user_data["monthly_distance"] = 0.0
            user_data["monthly_activities"] = 0
            logger.info(f"[GARMIN] Новый месяц для {user_data['name']}, сброс счётчиков")
        
        # Обновляем статистику пользователя
        user_data["monthly_distance"] = user_data.get("monthly_distance", 0.0) + distance_km
        user_data["monthly_activities"] = user_data.get("monthly_activities", 0) + 1
        
        # Обновляем общую статистику бега
        if user_id not in user_running_stats:
            user_running_stats[user_id] = {
                "name": user_data["name"],
                "activities": 0,
                "distance": 0.0,
                "duration": 0,
                "calories": 0
            }
        
        user_running_stats[user_id]["activities"] += 1
        user_running_stats[user_id]["distance"] += distance_meters
        user_running_stats[user_id]["duration"] += duration_seconds
        user_running_stats[user_id]["calories"] += calories
        
        # Формируем сообщение
        message_text = (
            f"🏃‍♂️ **{user_data['name']}** завершил(а) пробежку!\n\n"
            f"📍 *Дистанция:* {distance_km:.2f} км\n"
            f"⏱️ *Время:* {time_str} ({pace_str})\n"
        )
        
        if avg_heartrate > 0:
            message_text += f"❤️ *Пульс:* {avg_heartrate} уд/мин\n"
        
        if calories > 0:
            message_text += f"🔥 *Калории:* {calories} ккал\n"
        
        message_text += (
            f"\n📅 *За месяц:* {user_data['monthly_distance']:.1f} км / {user_data['monthly_activities']} тренировок"
        )
        
        # Отправляем в чат
        if application and CHAT_ID:
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text=message_text,
                parse_mode="Markdown"
            )
            logger.info(f"[GARMIN] Результат опубликован: {user_data['name']} - {distance_km:.2f} км")
            return True
        return False
        
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка публикации: {e}", exc_info=True)
        return False


async def garmin_scheduler_task():
    """Планировщик проверки Garmin (каждые 5 минут)"""
    global bot_running
    
    check_interval = 300  # 5 минут
    
    while bot_running:
        try:
            # Ждём до следующей проверки
            await asyncio.sleep(check_interval)
            
            # Проверяем Garmin
            await check_garmin_activities()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[GARMIN] Ошибка в планировщике: {e}")
            await asyncio.sleep(60)  # Подождём минуту при ошибке


def garmin_scheduler_sync():
    """Синхронная обёртка для запуска в отдельном потоке"""
    logger.info("[GARMIN] Планировщик запущен (sync wrapper)")
    try:
        asyncio.run(garmin_scheduler_task())
    except Exception as e:
        logger.error(f"[GARMIN] Критическая ошибка в потоке планировщика: {e}")


def init_garmin_on_startup():
    """Инициализация Garmin при запуске бота"""
    global garmin_users
    
    try:
        if GARMIN_AVAILABLE:
            # Загружаем сохранённых пользователей
            load_garmin_users()
            logger.info(f"[GARMIN] Инициализация завершена. Пользователей: {len(garmin_users)}")
        else:
            logger.warning("[GARMIN] Библиотека недоступна, интеграция отключена")
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка инициализации: {e}")


# ============== ФУНКЦИИ ДЛЯ ДНЕЙ РОЖДЕНИЯ ==============
def save_birthdays():
    """Сохранение дней рождения в файл"""
    try:
        save_data = {}
        for user_id, data in user_birthdays.items():
            save_data[str(user_id)] = {
                "name": data["name"],
                "birthday": data["birthday"]
            }
        
        with open(BIRTHDAYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"[BIRTHDAY] Дни рождения сохранены: {len(user_birthdays)} пользователей")
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка сохранения: {e}")


def load_birthdays():
    """Загрузка дней рождения из файла"""
    global user_birthdays
    
    try:
        if not os.path.exists(BIRTHDAYS_FILE):
            logger.info("[BIRTHDAY] Файл дней рождения не найден")
            user_birthdays = {}
            return
        
        with open(BIRTHDAYS_FILE, 'r', encoding='utf-8') as f:
            load_data = json.load(f)
        
        user_birthdays = {}
        for user_id_str, data in load_data.items():
            user_birthdays[int(user_id_str)] = {
                "name": data["name"],
                "birthday": data["birthday"]
            }
        
        logger.info(f"[BIRTHDAY] Загружено дней рождения: {len(user_birthdays)}")
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка загрузки: {e}")
        user_birthdays = {}


async def send_birthday_congratulation(user_id, user_data):
    """Отправка поздравления с Днём рождения"""
    global application
    
    try:
        name = user_data["name"]
        
        # Выбираем случайное пожелание
        wish = random.choice(BIRTHDAY_WISHES).format(name=name)
        
        # Праздничное сообщение с картинкой
        birthday_text = f"""🎉 **{name}, с Днём рождения!** 🎂

{wish}

🎈 Сегодня твой особенный день — отдыхай, радуйся и наслаждайся! 

💐 С любовью, твой беговой клуб! ❤️"""

        # Отправляем в чат
        if application and CHAT_ID:
            # Попробуем отправить с праздничной картинкой (торт)
            try:
                await application.bot.send_photo(
                    chat_id=CHAT_ID,
                    photo="https://cdn-icons-png.flaticon.com/512/3081/3081559.png",  # Праздничный торт
                    caption=birthday_text,
                    parse_mode="Markdown"
                )
            except Exception as img_error:
                # Если картинка не загрузилась — отправляем просто текст
                logger.warning(f"[BIRTHDAY] Не удалось загрузить картинку: {img_error}")
                await application.bot.send_message(
                    chat_id=CHAT_ID,
                    text=birthday_text,
                    parse_mode="Markdown"
                )
            
            logger.info(f"[BIRTHDAY] Поздравление отправлено: {name}")
        
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка отправки поздравления: {e}", exc_info=True)


async def check_birthdays():
    """Проверка дней рождения и отправка поздравлений"""
    global user_birthdays
    
    try:
        now = datetime.utcnow() + timedelta(hours=UTC_OFFSET)
        today = now.strftime("%d.%m")  # Формат DD.MM
        
        logger.info(f"[BIRTHDAY] Проверка дней рождения на {today}")
        
        # Безопасная итерация по словарю
        if not isinstance(user_birthdays, dict):
            logger.warning(f"[BIRTHDAY] user_birthdays не является словарём: {type(user_birthdays)}")
            return
        
        for user_id, user_data in list(user_birthdays.items()):
            # Проверяем, что user_id и user_data валидны
            if user_id is None:
                logger.warning(f"[BIRTHDAY] Пропускаем запись с None user_id")
                continue
            if user_data is None:
                logger.warning(f"[BIRTHDAY] Пропускаем запись с None user_data для user_id={user_id}")
                continue
            if not isinstance(user_data, dict):
                logger.warning(f"[BIRTHDAY] user_data не является словарём для user_id={user_id}")
                continue
            
            birthday = user_data.get("birthday")
            if birthday is None:
                logger.warning(f"[BIRTHDAY] Пропускаем пользователя {user_id} без birthday")
                continue
            
            if birthday == today:
                logger.info(f"[BIRTHDAY] Сегодня ДР у: {user_data.get('name', 'Unknown')}")
                await send_birthday_congratulation(user_id, user_data)
        
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка проверки: {e}", exc_info=True)


async def birthday_scheduler_task():
    """Планировщик проверки дней рождения (каждый день в 9:00)"""
    global bot_running
    
    logger.info("[BIRTHDAY] Планировщик дней рождения запущен")
    
    while bot_running:
        try:
            await asyncio.sleep(3600)  # Проверяем каждый час
            
            now = datetime.utcnow() + timedelta(hours=UTC_OFFSET)
            current_hour = now.hour
            current_minute = now.minute
            
            # Проверяем в 9:00 утра
            if current_hour == 9 and current_minute == 0:
                logger.info("[BIRTHDAY] Время 9:00 — проверяем дни рождения")
                await check_birthdays()
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[BIRTHDAY] Ошибка в планировщике: {e}")
            await asyncio.sleep(60)


def init_birthdays_on_startup():
    """Инициализация дней рождения при запуске бота"""
    global user_birthdays
    
    try:
        load_birthdays()
        logger.info(f"[BIRTHDAY] Инициализация завершена. Дней рождения: {len(user_birthdays)}")
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка инициализации: {e}")


async def fetch_tips_from_url(url: str, category: str) -> List[str]:
    """Получение советов с веб-страницы"""
    tips = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем параграфы с советами
            paragraphs = soup.find_all('p')
            
            for p in paragraphs:
                text = p.get_text().strip()
                if len(text) > 50 and len(text) < 500:
                    if not any(word in text.lower() for word in ['подпишитесь', 'читайте также', 'автор:', 'дата:', 'copyright']):
                        tips.append(text)
            
            logger.info(f"[TIPS] Получено {len(tips)} советов с {url}")
            
    except Exception as e:
        logger.error(f"[TIPS] Ошибка загрузки {url}: {e}")
    
    return tips


async def update_tips_cache():
    """Обновление кэша советов из интернета"""
    global _tips_cache
    
    current_time = time.time()
    if current_time - _tips_cache["last_update"] < CACHE_DURATION:
        logger.info("[TIPS] Используем кэшированные советы")
        return
    
    logger.info("[TIPS] Обновляем советы из интернета...")
    
    sources = {
        "running": [
            "https://marathonec.ru/kak-nachat-begat/",
            "https://marathonec.ru/topics/running/training/",
        ],
        "recovery": [],
        "equipment": [
            "https://marathonec.ru/kak-vybrat-krossovki-dlya-bega/",
            "https://marathonec.ru/odezhda-dlya-bega-osenyu/",
            "https://marathonec.ru/topics/running/gear/",
        ]
    }
    
    local_advice = {
        "running": [
            "Начинай бегать медленно — твой пульс не должен превышать 130-140 уд/мин на первых тренировках.",
            "Не увеличивай дистанцию больше чем на 10% в неделю — это снижает риск травм.",
            "Бегай в темпе, в котором ты можешь разговаривать. Если задыхаешься — замедляйся.",
            "Чередование бега и ходьбы (2 мин бег + 1 мин ходьба) — отличный способ начать бегать.",
            "Не пропускай разминку! 5-10 минут лёгкой ходьбы и динамической растяжки перед бегом обязательны.",
            "После 3-4 недель регулярного бега ты заметишь, что стал бегать легче и дольше.",
            "Интервальный бег (чередование быстрого и медленного) — эффективный способ улучшить выносливость.",
            "Правильная техника: приземление под таз, не на пятку; спина ровная, взгляд вперёд.",
        ],
        "recovery": [
            "После пробежки обязательно сделай заминку: 5-10 минут медленной ходьбы.",
            "Растяжка после бега должна быть статической — удерживай позы 20-30 секунд.",
            "Пей воду сразу после тренировки — 200-300 мл, потом пей по жажде в течение дня.",
            "Сон — главный инструмент восстановления. 7-8 часов сна творят чудеса.",
            "Делай хотя бы 1 полный день отдыха в неделю — мышцы восстанавливаются именно в покое.",
            "Обязательны дни отдыха — рост формы происходит в восстановлении.",
        ],
        "equipment": [
            "Беговые кроссовки нужно менять каждые 500-800 км — изношенная амортизация ведёт к травмам.",
            "Бери кроссовки на 0,5-1,5 см больше обычного размера — нога отекает при беге.",
            "Одевайся так, чтобы в начале тренировки было прохладно — на один слой меньше, чем для прогулки.",
            "Синтетическая одежда отводит влагу лучше хлопка — выбирай технические ткани.",
            "Примеряй кроссовки вечером — к вечеру стопы немного отекают.",
            "Выбирай кроссовки под тип пронации: нейтральная, поддержка или контроль — зависит от стопы.",
        ]
    }
    
    for cat, urls in sources.items():
        for url in urls:
            tips = await fetch_tips_from_url(url, cat)
            if tips:
                _tips_cache[cat].extend(tips)
                break
    
    for cat in ["running", "recovery", "equipment"]:
        if not _tips_cache[cat]:
            logger.info(f"[TIPS] Используем локальные советы для категории {cat}")
            _tips_cache[cat] = local_advice.get(cat, []).copy()
    
    _tips_cache["last_update"] = current_time
    logger.info(f"[TIPS] Кэш обновлён: running={len(_tips_cache['running'])}, recovery={len(_tips_cache['recovery'])}, equipment={len(_tips_cache['equipment'])}")


def get_random_tip(category: str = None) -> str:
    """Получение случайного совета из кэша"""
    import random
    
    running_cats = ["running", "run", "бег", "бегать", "тренировки"]
    recovery_cats = ["recovery", "restore", "восстановление", "отдых", "питание"]
    equipment_cats = ["equipment", "gear", "экипировка", "кроссовки", "одежда"]
    
    if category:
        cat_lower = category.lower()
        if cat_lower in running_cats:
            tips_list = _tips_cache["running"]
            cat_name = "беге"
        elif cat_lower in recovery_cats:
            tips_list = _tips_cache["recovery"]
            cat_name = "восстановлении"
        elif cat_lower in equipment_cats:
            tips_list = _tips_cache["equipment"]
            cat_name = "экипировке"
        else:
            tips_list = (_tips_cache["running"] + _tips_cache["recovery"] + _tips_cache["equipment"])
            cat_name = "бегу, восстановлению и экипировке"
    else:
        tips_list = (_tips_cache["running"] + _tips_cache["recovery"] + _tips_cache["equipment"])
        cat_name = "бегу, восстановлению и экипировке"
    
    if not tips_list:
        return "💡 Совет: Не забывайте регулярно тренироваться и прислушиваться к своему телу!"
    
    tip = random.choice(tips_list)
    return f"💡 **Совет по {cat_name} (источник: marathonec.ru):**\n\n{tip}"


MOTIVATION_QUOTES = [
    "🏃 Сегодня отличный день, чтобы стать лучше!",
    "💪 Каждый км — это победа над собой!",
    "🚀 Не жди идеального момента. Создай его своим бегом!",
    "🔥 Твой диван скучает, а бег ждёт тебя!",
    "⭐ Сегодня ты бежишь завтрашнюю версию себя!",
    "💨 Больше бега — меньше стресса!",
    "🎯 Пробежка сегодня = улыбка завтра!",
    "🏅 Главный забег — тот, который ты начал!",
    "🌟 Лучшее время для бега — сейчас!",
    "💥 Ты сильнее, чем думаешь!",
    "⚡ Каждый шаг приближает тебя к цели!",
    "🌈 После пробежки мир становится ярче!",
    "🔥 Диван — это не твой дом. Дорога — твой друг!",
    "💪 Вчера ты не смог. Сегодня ты бежишь!",
    "⭐ Бег — это лекарство, которое не нужно покупать!",
    "🎓 Бег учит нас, что финиш всегда ближе, чем кажется!",
    "🏆 Сегодняшняя тренировка — это завтрашняя победа!",
    "🌅 Утренняя пробежка даёт сил на весь день!",
    "💆 Бег — лучший способ перезагрузить голову!",
    "🔄 Каждый круг — это шанс стать лучше!",
    "🤝 Бег объединяет сильных духом!",
    "🎪 Жизнь слишком коротка, чтобы не бегать!",
    "🧘‍♀️ Бег — это медитация в движении!",
    "🚀 Остановись — и потеряешь темп!",
    "💫 Беги так, будто никто не смотрит!",
    "🏃‍♂️ Не бегай от проблем — беги к целям!",
    "⭐ Каждый спортсмен был новичком. Начни сегодня!",
    "🔥 Сложно только первые 5 км. Дальше — легче!",
    "💪 Твои ноги созданы для полёта!",
    "🌟 Бег — это не работа. Это свобода!",
    "🎯 Поставь цель — и беги к ней!",
    "💥 Больше никогда не будет «слишком рано» или «слишком поздно»!",
    "🏃‍♀️ Начни бежать — и увидишь, как изменится жизнь!",
    "⭐ Диван не даст тебе медаль. А бег — даст!",
    "🔥 Тренировки формируют характер!",
    "💪 Верь в себя — и беги!",
    "🌟 Ты можешь больше, чем думаешь!",
]

# ============== ЦИТАТЫ ВЕЛИКИХ БЕГУНОВ ==============
GREAT_RUNNER_QUOTES = [
    "🏃‍♂️ «Бег — это самый честный спорт. Он показывает, кто ты на самом деле.» — Элиуд Кипчоге",
    "⚡ «Не имеет значения, насколько быстро ты бежишь. Важно, что ты не останавливаешься.» — Стив Префонтейн",
    "🌟 «Тело может выдержать почти всё. Это вопрос силы воли.» — Эмиль Затопек",
    "💪 «Ты не проиграл, если не финишировал первым. Ты проиграл, если не начал.» — Хаile Гебреселассие",
    "🏃‍♀️ «Бег — это свобода. Когда бежишь, ты контролируешь свою жизнь.» — Билл Бауэрман",
    "🔥 «Бег — это лекарство, которое всегда под рукой.» — Джордж Шихан",
    "🚀 «Марафон — это не 42 км. Это 42 км вопросов к себе.» — Фред Лебоу",
    "⭐ «Неважно, как медленно ты бежишь. Ты всё равно быстрее того, кто сидит на диване.» — Джон Бингам",
    "💥 «Если ты чувствуешь боль, значит, ты ещё жив. Продолжай бежать.» — Пааво Нурми",
    "🏆 «Цель не всегда должна быть достигнута. Иногда достаточно просто бежать к ней.» — Роджер Баннистер",
    "🌈 «Каждый круг — это шанс стать лучше. Не упусти его.» — Пааво Нурми",
    "💫 «Трудный день на тренировке — это лёгкий день на соревнованиях.» — Билл Бауэрман",
    "🎯 «Бег — это танец между телом и волей.» — Эмиль Затопек",
    "🔥 «Ты бежишь не для того, чтобы похудеть. Ты бежишь, чтобы жить.» — Стив Префонтейн",
    "🏃‍♂️ «Никакой ветер не может остановить того, кто уже решил бежать.» — Элиуд Кипчоге",
    "💪 «Бег учит нас, что падать — это нормально. Главное — подниматься.» — Хаile Гебреселассие",
    "⭐ «Финишная прямая — это только начало твоего следующего забега.» — Фред Лебоу",
    "🌟 «Секрет не в том, чтобы бегать быстро. Секрет в том, чтобы бежать.» — Роджер Баннистер",
    "⚡ «Бег — это поэзия движения и музыка души.» — Джордж Шихан",
    "🏅 «Когда думаешь, что не можешь — ты можешь. Просто поверь.» — Стив Префонтейн",
]

# ============== ПОЖЕЛАНИЯ КО ДНЮ РОЖДЕНИЯ ==============
BIRTHDAY_WISHES = [
    "🎂 {name}, с Днём рождения! Желаю бегать быстрее ветра, преодолевать любые дистанции и всегда достигать своих целей! 🌟",
    "🎈 {name}, поздравляю! Пусть каждый твой забег приносит радость, новые победы и отличное настроение! 🏃‍♂️",
    "🎉 {name}, с ДР! Желаю сил, выносливости и всегда хорошей погоды для пробежек! ☀️",
    "🌟 {name}, с Днём рождения! Пусть будет много километров, мало травм и много радости от бега! 💪",
    "🎁 {name}, поздравляю! Желаю здоровья, энергии и новых личных рекордов! 🏆",
    "💐 {name}, с Днём рождения! Пусть бег приносит столько же радости, сколько ты приносишь в наш чат! ❤️",
    "🎊 {name}, с ДР! Желаю преодолевать все препятствия и всегда финишировать с улыбкой! 😊",
    "🌈 {name}, поздравляю! Пусть каждый день начинается с улыбки и заканчивается довольной усталостью! 🏃‍♀️",
    "✨ {name}, с Днём рождения! Желаю много друзей-единомышленников и крутых забегов! 👟",
    "🎯 {name}, с ДР! Пусть цели будут достигнуты, а новые горизонты — покорены! 🎯",
    "💫 {name}, поздравляю! Желаю never stop running и always finish strong! 🏁",
    "🌅 {name}, с Днём рождения! Пусть утренние пробежки дают энергию на весь день! ☀️",
    "🎖️ {name}, с ДР! Желаю медалей, кубков и незабываемых соревнований! 🥇",
    "💝 {name}, поздравляю! Ты — звезда нашего бегового клуба! Пусть сияешь ещё ярче! 🌟",
    "🎨 {name}, с Днём рождения! Желаю, чтобы жизнь была яркой, как разноцветные кроссовки! 👟",
]

# ============== СМЕШНЫЕ РУГАТЕЛЬСТВА ==============
FUNNY_INSULTS = [
    "Ты как брокколи — никто не знает, зачем ты нужен, и все делают вид, что тебя нет.",
    "Если бы тупость была олимпийским видом спорта, ты бы уже выиграл золото, серебро и бронзу одновременно.",
    "Ты единственный человек, у которого Wi-Fi работает быстрее, чем твои мозги.",
    "Твоя логика как wi-fi в метро — есть, но толку ноль.",
    "Ты думаешь, что твоё мнение важно? Это мило. Правда, мило.",
    "Я бы назвал тебя идиотом, но тогда бы я соврал, а я себе такого не позволяю.",
    "Ты как йогурт с истёкшим сроком — лежал-лежал, а потом выбросили.",
    "Если бы неудачи были конкурсом красоты, ты бы королева бала.",
    "Твоя стратегия жизни: «Авось пронесёт». И ведь не пронесло.",
    "Ты как бумеранг — возвращаешься, но никто этого не хочет.",
    "Думаешь, ты важен? Свечка в торте тоже думает, что она главная, пока её не задули.",
    "Ты как пазл — 1000 деталей, но картинка не складывается.",
    "Твоя уверенность в себе восхищает. И пугает. В основном пугает.",
    "Если бы глупость была болью, ты бы орал на весь мир.",
    "Ты как аккумулятор — пока заряжаешь, уже разрядился.",
    "Твоя способность всё портить заслуживает отдельной награды.",
    "Ты как интернет — то работает, то нет, а толку от тебя как от солёного огурца в шоколаде.",
    "Каждый раз, когда ты открываешь рот, я понимаю, почему некоторые люди выбирают молчание.",
    "Ты как канализация — все тебя терпят, но никто не хочет с тобой общаться.",
    "Твоя логика сломана сильнее, чем экран у бабушкиного телефона.",
    "Ты единственный человек, который умудряется упасть на ровном месте, но в лужу не попадает.",
    "Если бы твоя глупость была энергией, мы бы забыли про нефть и газ.",
    "Ты как чайный пакетик — сначала в горячую воду, а потом выбросили.",
    "Твои решения хуже, чем прогноз погоды в горах — непонятно и бесполезно.",
    "Ты как лимон — кислый, морщинистый и от тебя все морщатся.",
    "Если бы адекватность была музыкальным жанром, ты бы не попал в плейлист.",
    "Ты как батарейка — сел в самый неподходящий момент.",
    "Твоя способность всё усложнять достойна Нобелевской премии по идиотизму.",
    "Ты как плохой анекдот — никто не смеётся, а ты продолжаешь рассказывать.",
    "Твоя логика как бутерброд — падает маслом вниз, всегда.",
    "Если бы твоя жизнь была фильмом, это был бы триллер с плохим концом.",
    "Ты как старый холодильник — шумит, но ничего полезного не содержит.",
    "Твоё чувство юмора умерло и похоронено глубоко в бункере.",
    "Ты как шаурма в три часа ночи — вроде хочется, но потом жалеешь.",
    "Если бы твоя самооценка была размером, она бы поместилась в наперсток.",
    "Ты как дверь в публичном туалете — толкнешь, а там такое...",
    "Твои мозги работают так медленно, что я мог бы перезагрузить страницу быстрее.",
    "Ты как мем с котиком — все смотрят, но никто не понимает, почему это смешно.",
    "Если бы ты был программой, тебя бы удалили без возможности восстановления.",
    "Ты как зонтик в ливень — ломается в самый нужный момент.",
    "Твоя способность всё портить — это талант. Злой талант.",
    "Ты как шнурки — постоянно развязываешься в самый неудобный момент.",
    "Если бы глупость была искусством, ты бы Пикассо. Но это не искусство.",
    "Ты как бумажка — мнётся от любого дуновения, а толку ноль.",
    "Твоя логика как экран телевизора — чёрный и ничего не показывает.",
    "Ты как солнце — все знают, что ты есть, но никто не хочет к тебе.",
    "Если бы адекватность была опцией, ты бы выбрал «выключено».",
    "Ты как медуза — медленный, бесполезный и жжётся.",
    "Твои решения хуже, чем гороскоп в дешёвой газете.",
    "Ты как ложка — всегда опаздываешь, когда тебя ищешь.",
]

# ============== СМЕШНЫЕ КОМПЛИМЕНТЫ ==============
FUNNY_COMPLIMENTS = [
    "Ты как солнце — даже через тучи пробиваешься и заставляешь всех улыбаться!",
    "Твоя улыбка ярче, чем мой экран в три часа ночи. Серьёзно, ты светишь!",
    "Если бы ты был приложением, я бы поставил 5 звёзд и написал восторженный отзыв!",
    "Ты как Wi-Fi — без тебя жизнь была бы невозможна и полна грусти.",
    "Твоё чувство юмора заслуживает отдельного памятника в центре города!",
    "Ты как горячий шоколад зимой — согреваешь, радуешь и никогда не надоедаешь!",
    "Если бы все люди были как ты, в мире бы не было войн, только концерты и вечеринки!",
    "Ты как идеальный плейлист — каждая песня в тему, и хочется слушать бесконечно!",
    "Твоя способность поднимать настроение заслуживает Нобелевской премии по радости!",
    "Ты как кот на подоконнике — милый, спокойный и делаешь день лучше одним своим видом!",
    "Если бы твоя доброта была энергией, мы бы забыли про все экологические проблемы!",
    "Ты как свежее постельное бельё — после общения с тобой чувствуешь себя обновлённым!",
    "Твоя логика работает лучше, чем мой будильник — всегда вовремя и никогда не подводит!",
    "Ты как лучшая песня в моей голове — крутишься и не даёшь мне грустить!",
    "Если бы ты был специей, ты был бы куркумой — полезный, яркий и делаешь всё лучше!",
    "Ты как пушистый плед зимой — уютный, тёплый и от тебя не хочется отходить!",
    "Твоё терпение заслуживает олимпийского золота по спокойствию!",
    "Ты как зонтик в солнечный день — неожиданно, но приятно, и поднимает настроение!",
    "Если бы я писал книгу о крутых людях, ты был бы на каждой странице!",
    "Ты как утренний кофе — бодришь, радуешь и делаешь утро великолепным!",
    "Твоя способность находить выход там, где я вижу стену, вдохновляет меня!",
    "Ты как мем с котиком — все смотрят на тебя и улыбаются, не понимая почему!",
    "Если бы ты был программой, ты был бы моим любимым приложением с идеальным рейтингом!",
    "Ты как аромат свежей выпечки — проходишь мимо и сразу становится хорошо!",
    "Твоё чувство стиля заслуживает отдельного канала на YouTube с миллионами просмотров!",
    "Ты как компас в лесу — без тебя я бы точно заблудился и грустил!",
    "Если бы твоя энергия была электричеством, ты бы запитал целый город!",
    "Ты как идеальная фотография — естественный, красивый и хочется смотреть вечно!",
    "Твоя способность слушать заслуживает статуэтку «Лучший слушатель года»!",
    "Ты как тёплая ванна после долгого дня — расслабляешь, успокаиваешь и лечишь!",
    "Если бы ты был цветком, ты был бы подсолнухом — всегда смотришь на свет и даришь радость!",
    "Ты как лучший момент в фильме — хочется, чтобы он длился вечно!",
    "Твоё обаяние работает лучше, чем мой пароль на телефоне — невозможно устоять!",
    "Ты как волшебная таблетка от грусти — одна твоя улыбка — и всё становится хорошо!",
    "Если бы ты был книгой, я бы прочитал тебя тысячу раз и не устал!",
    "Ты как первое утреннее солнце — нежное, тёплое и обещает отличный день!",
    "Твоё отношение к жизни заслуживает отдельного мотивационного выступления!",
    "Ты как лучший друг, который всегда рядом, даже когда ты далеко!",
    "Если бы твой позитив был вирусом, я бы хотел заразиться им навсегда!",
    "Ты как концерт любимой группы — громкий, яркий и оставляет незабываемые эмоции!",
    "Твоя искренность ослепляет меня как фонарик в темноте — ярко и радостно!",
    "Ты как домашний уют после долгой прогулки — желанный, тёплый и успокаивающий!",
    "Если бы ты был супергероем, твоя суперсила была бы — делать всех счастливыми!",
    "Ты как идеальный ингредиент в рецепте — без тебя блюдо было бы неполным!",
    "Твоя способность вдохновлять работает лучше, чем мой любимый плейлист для тренировок!",
    "Ты как рассвет после долгой ночи — обещание нового, светлого и прекрасного!",
    "Если бы твоя доброта была музыкой, она звучала бы как симфония angels!",
    "Ты как торт на день рождения — сладкий, желанный и делает день особенным!",
    "Твоё чувство юмора — это как секретный ингредиент в моём любимом блюде!",
    "Ты как лучший момент дня — хочется, чтобы он повторялся снова и снова!",
]

user_anon_state = {}

# ============== НОЧНЫЕ СООБЩЕНИЯ ==============
NIGHT_WARNINGS = [
    "🌙 Хватит писать, спать пора! Телепузики уже уснули!",
    "😴 Народ, 22:00! Клавиатура — враг сна!",
    "🛏️ Эй, вы там! Завтра бегать, а вы в телефоне!",
    "💤 Кто не спит — тот не бегает эффективно!",
    "🌃 Ночь на дворе, а вы всё чатитесь!",
    "😱 Вы хотите завтра бегать как зомби?",
    "🎭 Хватит играть в ночных героев, идите спать!",
    "🔮 Волшебство завтрашнего бега зависит от вашего сна!",
    "🦥 Утренний бег начинается с вечного сна!",
    "🌟 Звёзды уже вышли, а вы ещё в чате!",
]

# ============== ПРИВЕТСТВИЯ ВОЗВРАЩЕНЦЕВ ==============
RETURN_GREETINGS = [
    "Оооо, какие люди и без охраны! 🕴️ С возвращением, босс!",
    "🎉 Ого, кто это вернулся! Мы уже забыли, как ты выглядишь!",
    "😮 Ух ты! Легенда объявилась! Где ты был столько времени?",
    "🙌 Смотрите-ка, наш герой снова в строю! Пропадал — небось, марафон бегал!",
    "👀 Кто это пишет? Призрак из прошлого! С возвращением в мир живых!",
    "🚀 Опа-на! Наш космонавт приземлился! Как там в отпуске от чата?",
    "🎩 Ба! Ба! Ба! Какие гости! Давно не виделись, а ты всё ещё бегаешь?",
    "😎 Легенда в чате! Мы уже хотели вешать твой портрет на стену!",
    "🏆 О, великий вернулся! Без тебя чат совсем скучал (нет)!",
    "🌟 Свет мой, вернулся! Заждались мы тебя, аж несколько дней прошло!",
    "🎪 Цирк в городе! Знаменитость почтила нас своим присутствием!",
    "🤴 Принц вернулся в королевство! Трон ждёт, ваше величество!",
    "🦁 Царь лесов объявился! Пропадал — охотился на марафоны?",
    "🎸 Рок-звезда в чате! Где был на гастролях, в беге по барханам?",
    "👑 Корона упала! Король вернулся на трон! С возвращением!",
    "🧙 Маг вернулся из заточения! Чары уже работают?",
    "🦸 Супергерой спас мир и вернулся! Как там, много зла победил?",
    "🎭 Актёр вышел на сцену! Давно не были в главной роли!",
    "🐲 Дракон из пещеры выполз! Где прятался от беговых тренировок?",
    "🦅 Орёл прилетел! Высоко парил над нами все эти дни?",
]


# ============== ПОГОДА ==============
async def get_weather() -> str:
    try:
        async with httpx.AsyncClient() as client:
            moscow_response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": 55.7558,
                    "longitude": 37.6173,
                    "current_weather": "true",
                },
                timeout=10.0,
            )
            spb_response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": 59.9343,
                    "longitude": 30.3351,
                    "current_weather": "true",
                },
                timeout=10.0,
            )

            moscow_data = moscow_response.json()
            spb_data = spb_response.json()

            moscow_temp = moscow_data["current_weather"]["temperature"]
            moscow_wind = moscow_data["current_weather"]["windspeed"]
            spb_temp = spb_data["current_weather"]["temperature"]
            spb_wind = spb_data["current_weather"]["windspeed"]

            weather_text = (
                f"🌤 **Погода утром:**\n"
                f"🏙 Москва: **{moscow_temp}°C**, ветер {moscow_wind} км/ч\n"
                f"🌆 СПб: **{spb_temp}°C**, ветер {spb_wind} км/ч"
            )
            return weather_text
    except Exception as e:
        logger.error(f"Ошибка получения погоды: {e}")
        return "🌤 Погода временно недоступна"


# ============== УТРЕННЕЕ ПРИВЕТСТВИЕ ==============
def get_day_theme() -> str:
    now = datetime.now(MOSCOW_TZ)
    day_name_en = now.strftime("%A")
    return DAY_THEMES.get(day_name_en, "🌟 Отличный день для пробежки!")


def get_random_welcome() -> str:
    return random.choice(WELCOME_MESSAGES)


def get_random_motivation() -> str:
    return random.choice(MOTIVATION_QUOTES)


def get_random_insult() -> str:
    return random.choice(FUNNY_INSULTS)


def get_random_compliment() -> str:
    return random.choice(FUNNY_COMPLIMENTS)


# ============== ОТСЛЕЖИВАНИЕ СТАТИСТИКИ ==============
def update_daily_stats(user_id: int, user_name: str, message_type: str, photo_info: dict = None):
    """Обновление ежедневной статистики"""
    global daily_stats
    
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    
    # Безопасная инициализация
    if not isinstance(daily_stats, dict) or daily_stats.get("date") != today:
        daily_stats = {
            "date": today,
            "total_messages": 0,
            "user_messages": {},
            "photos": [],
        }
    
    daily_stats["total_messages"] += 1
    
    # Обновление счётчика сообщений пользователя
    if user_id not in daily_stats["user_messages"]:
        daily_stats["user_messages"][user_id] = {
            "name": user_name,
            "count": 0,
        }
    daily_stats["user_messages"][user_id]["count"] += 1
    
    # Добавление фото в статистику
    if message_type == "photo" and photo_info:
        daily_stats["photos"].append(photo_info)


# ============== РАСЧЁТ РЕЙТИНГА ==============
def calculate_user_rating(user_id: int) -> int:
    """Расчёт общего рейтинга пользователя"""
    if user_id not in user_rating_stats:
        return 0
    
    stats = user_rating_stats[user_id]
    
    messages_points = stats["messages"] // POINTS_PER_MESSAGES
    photos_points = stats["photos"] // POINTS_PER_PHOTOS
    likes_points = stats["likes"] // POINTS_PER_LIKES
    replies_points = stats["replies"]  # Каждый ответ = 1 балл
    
    return messages_points + photos_points + likes_points + replies_points


def get_user_level(user_id: int) -> str:
    """Определение уровня участника"""
    total_points = calculate_user_rating(user_id)
    
    # Определяем уровень по очкам (от высокого к низкому)
    if total_points >= USER_LEVELS["Легенда чата"]:
        return "Легенда чата"
    elif total_points >= USER_LEVELS["Лидер"]:
        return "Лидер"
    elif total_points >= USER_LEVELS["Активный"]:
        return "Активный"
    else:
        return "Новичок"


def get_rating_details(user_id: int) -> dict:
    """Получение детальной статистики рейтинга"""
    if user_id not in user_rating_stats:
        return {
            "name": "Unknown",
            "messages": 0,
            "photos": 0,
            "likes": 0,
            "replies": 0,
            "total_points": 0,
            "level": "Новичок"
        }
    
    stats = user_rating_stats[user_id]
    level = get_user_level(user_id)
    
    return {
        "name": stats["name"],
        "messages": stats["messages"],
        "photos": stats["photos"],
        "likes": stats["likes"],
        "replies": stats["replies"],
        "total_points": calculate_user_rating(user_id),
        "level": level
    }


# ============== СТАТИСТИКА БЕГА ==============
def update_running_stats(user_id: int, user_name: str, distance: float, duration: int, calories: int):
    """Обновление статистики бега для участника"""
    global user_running_stats
    
    if user_id not in user_running_stats:
        user_running_stats[user_id] = {
            "name": user_name,
            "activities": 0,
            "distance": 0.0,
            "duration": 0,
            "calories": 0
        }
    
    user_running_stats[user_id]["activities"] += 1
    user_running_stats[user_id]["distance"] += distance
    user_running_stats[user_id]["duration"] += duration
    user_running_stats[user_id]["calories"] += calories


def get_top_runners() -> list:
    """Получение топ-10 бегунов по километрам за месяц"""
    global user_running_stats
    
    if not user_running_stats:
        return []
    
    # Сортируем по дистанции
    runners = []
    for user_id, stats in user_running_stats.items():
        runners.append({
            "user_id": user_id,
            "name": stats["name"],
            "activities": stats["activities"],
            "distance": stats["distance"],
            "duration": stats["duration"],
            "calories": stats["calories"]
        })
    
    # Сортируем по километрам (по убыванию)
    runners.sort(key=lambda x: x["distance"], reverse=True)
    
    return runners[:10]


async def send_weekly_running_summary():
    """Отправка еженедельной сводки по бегу (воскресенье 23:00)"""
    global application, user_running_stats
    
    try:
        if not user_running_stats:
            logger.info("[RUNNING] Нет данных для еженедельной сводки")
            return
        
        now = datetime.utcnow() + timedelta(hours=UTC_OFFSET)
        week_num = now.isocalendar()[1]
        year = now.year
        
        # Считаем общую статистику
        total_activities = sum(stats["activities"] for stats in user_running_stats.values())
        total_distance = sum(stats["distance"] for stats in user_running_stats.values()) / 1000  # в км
        total_calories = sum(stats["calories"] for stats in user_running_stats.values())
        
        # Получаем топ бегунов
        top_runners = get_top_runners()
        
        weekly_text = f"🏃‍♂️ **Еженедельная сводка по бегу (Неделя #{week_num}, {year})**\n\n"
        
        # Общая статистика недели
        weekly_text += f"📊 **Общая статистика недели:**\n"
        weekly_text += f"🏃‍♂️ Всего пробежек: {total_activities}\n"
        weekly_text += f"📍 Общая дистанция: {total_distance:.1f} км\n"
        weekly_text += f"🔥 Сожжено калорий: {total_calories}\n"
        weekly_text += f"👥 Участников бега: {len(user_running_stats)}\n\n"
        
        # Топ-3 бегунов
        if top_runners:
            medals = ["🥇", "🥈", "🥉"]
            weekly_text += f"🏆 **Топ бегунов недели:**\n"
            for i, runner in enumerate(top_runners[:3]):
                distance_km = runner["distance"] / 1000
                weekly_text += f"{medals[i]} {runner['name']} — {distance_km:.1f} км ({runner['activities']} тренировок)\n"
            weekly_text += "\n"
        
        # Индивидуальная статистика всех
        weekly_text += "📝 **Все участники:**\n"
        for runner in top_runners:
            distance_km = runner["distance"] / 1000
            weekly_text += f"• {runner['name']}: {distance_km:.1f} км ({runner['activities']} тренировок)\n"
        
        # Мотивация
        weekly_text += "\n" + random.choice(GREAT_RUNNER_QUOTES)
        
        # Отправляем в чат
        if application and CHAT_ID:
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text=weekly_text,
                parse_mode="Markdown"
            )
            logger.info("[RUNNING] Еженедельная сводка по бегу отправлена")
        
    except Exception as e:
        logger.error(f"[RUNNING] Ошибка отправки еженедельной сводки: {e}", exc_info=True)


async def send_monthly_running_summary():
    """Отправка ежемесячной сводки по бегу (последний день месяца)"""
    global application, user_running_stats
    
    try:
        if not user_running_stats:
            logger.info("[RUNNING] Нет данных для ежемесячной сводки")
            return
        
        now = datetime.utcnow() + timedelta(hours=UTC_OFFSET)
        month_name = now.strftime("%B %Y")
        
        # Считаем общую статистику
        total_activities = sum(stats["activities"] for stats in user_running_stats.values())
        total_distance = sum(stats["distance"] for stats in user_running_stats.values()) / 1000  # в км
        total_calories = sum(stats["calories"] for stats in user_running_stats.values())
        total_duration = sum(stats["duration"] for stats in user_running_stats.values())
        
        # Получаем топ бегунов
        top_runners = get_top_runners()
        
        monthly_text = f"🏆 **Ежемесячная сводка по бегу ({month_name})**\n\n"
        
        # Общая статистика месяца
        monthly_text += f"📊 **Итоги месяца:**\n"
        monthly_text += f"🏃‍♂️ Всего пробежек: {total_activities}\n"
        monthly_text += f"📍 Общая дистанция: {total_distance:.1f} км\n"
        monthly_text += f"⏱️ Общее время: {total_duration // 3600}ч {(total_duration % 3600) // 60}м\n"
        monthly_text += f"🔥 Сожжено калорий: {total_calories}\n"
        monthly_text += f"👥 Участников бега: {len(user_running_stats)}\n\n"
        
        # Топ-3 бегунов с медалями
        if top_runners:
            medals = ["🥇", "🥈", "🥉"]
            monthly_text += f"🏅 **Лучшие бегуны месяца:**\n"
            for i, runner in enumerate(top_runners[:3]):
                distance_km = runner["distance"] / 1000
                hours = runner["duration"] // 3600
                minutes = (runner["duration"] % 3600) // 60
                monthly_text += f"{medals[i]} **{runner['name']}**\n"
                monthly_text += f"   📍 {distance_km:.1f} км | ⏱️ {hours}ч {minutes}м | 🔥 {runner['calories']} ккал\n\n"
        
        monthly_text += "💪 **Поздравляем всех с отличным месяцем! Keep running!**\n"
        
        # Мотивация
        monthly_text += "\n" + random.choice(GREAT_RUNNER_QUOTES)
        
        # Отправляем в чат
        if application and CHAT_ID:
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text=monthly_text,
                parse_mode="Markdown"
            )
            logger.info("[RUNNING] Ежемесячная сводка по бегу отправлена")
        
    except Exception as e:
        logger.error(f"[RUNNING] Ошибка отправки ежемесячной сводки: {e}", exc_info=True)


def reset_monthly_running_stats():
    """Сброс статистики бега в новый месяц"""
    global user_running_stats
    
    logger.info(f"[RUNNING] Сброс статистики бега. Статистика за месяц:")
    
    # Логируем статистику перед сбросом
    if user_running_stats:
        for user_id, stats in user_running_stats.items():
            logger.info(f"[RUNNING] {stats['name']}: {stats['activities']} тренировок, {stats['distance']/1000:.1f} км")
    
    # Сбрасываем статистику
    user_running_stats.clear()
    logger.info("[RUNNING] Статистика бега сброшена для нового месяца")


async def send_point_notification(user_name: str, points: int, reason: str, total_points: int):
    """Отправка публичного уведомления о получении баллов"""
    global application
    
    logger.info(f"[NOTIFY] Попытка отправить уведомление: user={user_name}, points={points}, reason={reason}")
    logger.info(f"[NOTIFY] application={application}")
    
    if application is None:
        logger.error(f"[NOTIFY] ❌ application равен None! Уведомление не отправлено для {user_name}")
        return
    
    try:
        # Эмодзи в зависимости от причины получения баллов
        reason_emojis = {
            "сообщения": "💬",
            "фото": "📷",
            "лайки": "❤️",
            "ответы": "💬"
        }
        
        emoji = reason_emojis.get(reason, "⭐")
        
        # ПРОСТОЙ текст БЕЗ форматирования Markdown
        notification_text = f"{emoji} {user_name} получил(а) +{points} балл(ов) за {reason}!\n📊 Всего баллов: {total_points}"
        
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=notification_text,
        )
        
        logger.info(f"[NOTIFY] ✅ Уведомление отправлено для {user_name}")
        
        logger.info(f"Уведомление о баллах отправлено: {user_name} +{points}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о баллах: {e}")


async def send_level_up_notification(user_name: str, new_level: str):
    """Отправка уведомления о повышении уровня"""
    if application is None:
        return
    
    try:
        level_emoji = LEVEL_EMOJIS.get(new_level, "⭐")
        
        # ПРОСТОЙ текст БЕЗ форматирования Markdown
        level_messages = {
            "Активный": f"🎉 Поздравляем! {user_name} перешёл в ряды Активных бегунов!",
            "Лидер": f"👑 Ура! {user_name} стал Лидером бегового чата!",
            "Легенда чата": f"🏆 ОГО! {user_name} достиг звания Легенды чата! Это вершина!"
        }
        
        notification_text = level_messages.get(new_level, f"🎊 {user_name} повысил(а) уровень до {new_level}!")
        
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=notification_text,
        )
        
        logger.info(f"Уведомление о повышении уровня: {user_name} -> {new_level}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о уровне: {e}")


def update_rating_stats(user_id: int, user_name: str, category: str, amount: int = 1) -> tuple:
    """
    Обновление статистики рейтинга с защитой от накруток
    
    Returns: (success: bool, points_earned: int, message: str)
    """
    global user_rating_stats, user_current_level, user_message_times
    
    now = datetime.now(MOSCOW_TZ)
    today = now.strftime("%Y-%m-%d")
    current_time = now.timestamp()
    
    # ЗАЩИТА 1: Проверка на флуд сообщений
    if category == "messages":
        if user_id not in user_message_times:
            user_message_times[user_id] = []
        
        # Удаляем старые записи (старше 1 минуты)
        user_message_times[user_id] = [
            t for t in user_message_times[user_id] 
            if current_time - t < 60
        ]
        
        # Проверяем лимит сообщений в минуту
        if len(user_message_times[user_id]) >= MAX_MESSAGES_PER_MINUTE:
            logger.info(f"Защита от флуда: {user_name} превысил лимит сообщений")
            return False, 0, "Слишком много сообщений!"
        
        # Добавляем время текущего сообщения
        user_message_times[user_id].append(current_time)
    
    # ЗАЩИТА 2: Проверка на превышение баллов в час
    if user_id in user_rating_stats:
        # Подсчитываем примерные баллы за последний час
        # (упрощённая проверка - считаем по общим данным)
        recent_points = (
            user_rating_stats[user_id]["messages"] // POINTS_PER_MESSAGES +
            user_rating_stats[user_id]["photos"] // POINTS_PER_PHOTOS +
            user_rating_stats[user_id]["likes"] // POINTS_PER_LIKES +
            user_rating_stats[user_id]["replies"]
        )
        
        # Если у пользователя уже много баллов, продолжаем (это не точная проверка)
        # Для защиты от накруток добавим задержку в логику начисления
    
    # Инициализация нового пользователя
    if user_id not in user_rating_stats:
        user_rating_stats[user_id] = {
            "name": user_name,
            "messages": 0,
            "photos": 0,
            "likes": 0,
            "replies": 0,
            "last_update": today
        }
        user_current_level[user_id] = "Новичок"
    
    # Запоминаем старый уровень
    old_level = user_current_level.get(user_id, "Новичок")
    
    # Запоминаем старые значения для подсчёта прироста
    old_messages = user_rating_stats[user_id]["messages"]
    old_photos = user_rating_stats[user_id]["photos"]
    old_likes = user_rating_stats[user_id]["likes"]
    old_replies = user_rating_stats[user_id]["replies"]
    
    # Обновляем статистику
    user_rating_stats[user_id][category] += amount
    
    # Проверяем, сколько баллов начислено за это действие (прирост)
    points_earned = 0
    if category == "messages":
        new_messages = user_rating_stats[user_id]["messages"]
        points_earned = (new_messages // POINTS_PER_MESSAGES) - (old_messages // POINTS_PER_MESSAGES)
    elif category == "photos":
        new_photos = user_rating_stats[user_id]["photos"]
        points_earned = (new_photos // POINTS_PER_PHOTOS) - (old_photos // POINTS_PER_PHOTOS)
    elif category == "likes":
        new_likes = user_rating_stats[user_id]["likes"]
        points_earned = (new_likes // POINTS_PER_LIKES) - (old_likes // POINTS_PER_LIKES)
    elif category == "replies":
        new_replies = user_rating_stats[user_id]["replies"]
        points_earned = new_replies - old_replies  # Каждый ответ = 1 балл
    
    # Проверяем новый уровень
    new_level = get_user_level(user_id)
    user_current_level[user_id] = new_level
    
    return True, points_earned, "OK"


async def send_morning_greeting():
    global morning_message_id

    if application is None:
        logger.error("Application не инициализирован")
        return

    try:
        weather = await get_weather()
        theme = get_day_theme()
        motivation = get_random_motivation()

        greeting_text = (
            f"🌅 **Доброе утро, бегуны!** 🏃‍♂️\n\n"
            f"{weather}\n\n"
            f"{theme}\n\n"
            f"{motivation}\n\n"
            f"💭 *Напишите свои планы на сегодня!*"
        )

        message = await application.bot.send_message(
            chat_id=CHAT_ID,
            text=greeting_text,
            parse_mode="Markdown",
        )

        morning_message_id = message.message_id
        logger.info(f"Утреннее сообщение отправлено: {morning_message_id}")

    except Exception as e:
        logger.error(f"Ошибка отправки утреннего сообщения: {e}")


async def morning_scheduler_task():
    global morning_scheduled_date

    while bot_running:
        now = datetime.now(MOSCOW_TZ)
        current_hour = now.hour
        current_minute = now.minute
        today_date = now.strftime("%Y-%m-%d")

        if current_hour == 6 and current_minute == 0:
            if morning_scheduled_date != today_date:
                logger.info("Время 6:00 - отправляем утреннее сообщение")
                try:
                    await send_morning_greeting()
                    morning_scheduled_date = today_date
                    logger.info("Утреннее сообщение успешно отправлено")
                except Exception as e:
                    logger.error(f"Ошибка при отправке: {e}")

        await asyncio.sleep(60)


# ============== КОФЕЙНЫЙ ПЛАНОВЩИК (10:30 БУДНИ) ==============
COFFEE_MESSAGES = [
    "☕ **А не пора ли по кофейку?",
    "☕ Кто сегодня ещё не пил кофе? Поднимите руку!",
    "☕ Кофе — это не напиток, это ритуал!",
    "☕ Эспрессо, капучино, латте — выбирайте свой!",
    "☕ Без кофе не туда, не сюда — нужен кофе!",
    "☕ Кофе мастер, где ты? Чашка ждёт!",
    "☕ Кофейная пауза — священное время!",
    "☕ Кто с нами? Кофе ждёт!",
    "☕ Утро без кофе — как день без солнца!",
    "☕ Погнали на кофе! ☕",
]

COFFEE_IMAGES = [
    "https://cdn-icons-png.flaticon.com/512/3028/3028993.png",  # Чашка кофе
    "https://cdn-icons-png.flaticon.com/512/2935/2935413.png",  # Кофе
    "https://cdn-icons-png.flaticon.com/512/3127/3127421.png",  # Стакан кофе
    "https://cdn-icons-png.flaticon.com/512/2246/2246910.png",  # Кружка
    "https://cdn-icons-png.flaticon.com/512/2966/2966327.png",  # Кофе
]


async def send_coffee_reminder():
    """Отправка напоминания о кофе с картинкой"""
    if application is None:
        logger.error("Application не инициализирован")
        return

    try:
        import random
        
        coffee_text = random.choice(COFFEE_MESSAGES)
        coffee_image = random.choice(COFFEE_IMAGES)
        
        full_text = f"{coffee_text}\n\n🥤 Время взбодриться!"
        
        await application.bot.send_photo(
            chat_id=CHAT_ID,
            photo=coffee_image,
            caption=full_text,
            parse_mode="Markdown"
        )
        
        logger.info("[COFFEE] Напоминание о кофе отправлено")
        
    except Exception as e:
        logger.error(f"[COFFEE] Ошибка отправки: {e}")


async def coffee_scheduler_task():
    """Планировщик напоминаний о кофе в 10:30 по будням"""
    
    while bot_running:
        try:
            await asyncio.sleep(30)  # Проверяем каждые 30 секунд
            
            now = datetime.now(MOSCOW_TZ)
            current_hour = now.hour
            current_minute = now.minute
            current_weekday = now.weekday()  # 0 = понедельник, 6 = воскресенье
            
            # Проверяем: 10:30 и будний день (пн-пт)
            if current_hour == 10 and current_minute == 30 and current_weekday < 5:
                logger.info("[COFFEE] Время 10:30 - отправляем напоминание о кофе")
                try:
                    await send_coffee_reminder()
                    # Ждём минуту, чтобы не отправить дважды
                    await asyncio.sleep(60)
                except Exception as e:
                    logger.error(f"[COFFEE] Ошибка при отправке: {e}")
        
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[COFFEE] Ошибка в планировщике: {e}")


# ============== МОТИВАЦИОННЫЕ СООБЩЕНИЯ ==============
async def send_motivation():
    """Отправка мотивационного сообщения"""
    if application is None:
        return

    try:
        motivation = get_random_motivation()
        message = await application.bot.send_message(
            chat_id=CHAT_ID,
            text=f"💪 {motivation}",
            parse_mode="Markdown",
        )
        logger.info(f"Мотивация отправлена: {message.message_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки мотивации: {e}")


async def motivation_scheduler_task():
    """Планировщик мотивационных сообщений на 11:00, 16:00, 21:00"""
    global motivation_sent_times
    
    while bot_running:
        now = datetime.now(MOSCOW_TZ)
        current_hour = now.hour
        current_minute = now.minute
        today_date = now.strftime("%Y-%m-%d")
        
        # Сбрасываем список отправленных сообщений в полночь
        if now.hour == 0 and current_minute == 0:
            motivation_sent_times = []
        
        # Время для отправки мотивации
        motivation_hours = [11, 16, 21]  # 11:00, 16:00, 21:00
        
        for hour in motivation_hours:
            if current_hour == hour and current_minute == 0:
                # Проверяем, не отправляли ли уже сегодня в это время
                key = f"{today_date}_{hour}"
                if key not in motivation_sent_times:
                    logger.info(f"Время {hour}:00 - отправляем мотивацию")
                    try:
                        await send_motivation()
                        motivation_sent_times.append(key)
                        logger.info("Мотивация успешно отправлена")
                    except Exception as e:
                        logger.error(f"Ошибка при отправке мотивации: {e}")
        
        await asyncio.sleep(60)


# ============== ЕЖЕДНЕВНАЯ СВОДКА ==============
async def get_top_liked_photos() -> list:
    """Получение топ фото по лайкам с уведомлениями"""
    global daily_stats, user_rating_stats, user_current_level
    
    if not daily_stats["photos"]:
        return []
    
    try:
        # Обновляем количество лайков для каждого фото и общий рейтинг
        updated_photos = []
        for photo in daily_stats["photos"]:
            try:
                reactions = await application.bot.get_message_reactions(
                    chat_id=CHAT_ID,
                    message_id=photo["message_id"],
                )
                # Считаем только reaction "👍" (thumbs up)
                like_count = 0
                for reaction in reactions:
                    for choice in reaction.choices:
                        if choice.emoji == "👍":
                            like_count = choice.count
                            break
                
                # Обновляем лайки в рейтинге автора фото
                if like_count > 0 and photo["user_id"] in user_rating_stats:
                    old_likes = user_rating_stats[photo["user_id"]]["likes"]
                    user_rating_stats[photo["user_id"]]["likes"] = like_count
                    
                    # Проверяем, сколько баллов за лайки начислено
                    old_points = old_likes // POINTS_PER_LIKES
                    new_points = like_count // POINTS_PER_LIKES
                    points_earned = new_points - old_points
                    
                    if points_earned > 0:
                        photo_author_name = user_rating_stats[photo["user_id"]]["name"]
                        total = calculate_user_rating(photo["user_id"])
                        await send_point_notification(photo_author_name, points_earned, "лайки", total)
                        
                        # Проверяем повышение уровня
                        new_level = get_user_level(photo["user_id"])
                        old_level = user_current_level.get(photo["user_id"], "Новичок")
                        if new_level != old_level and new_level != "Новичок":
                            user_current_level[photo["user_id"]] = new_level
                            await send_level_up_notification(photo_author_name, new_level)
                
                updated_photos.append({
                    "file_id": photo["file_id"],
                    "user_id": photo["user_id"],
                    "likes": like_count,
                    "message_id": photo["message_id"],
                })
            except Exception:
                # Если не удалось получить лайки, считаем как 0
                updated_photos.append({
                    "file_id": photo["file_id"],
                    "user_id": photo["user_id"],
                    "likes": 0,
                    "message_id": photo["message_id"],
                })
        
        # Сортируем по лайкам и фильтруем (минимум 4)
        updated_photos.sort(key=lambda x: x["likes"], reverse=True)
        top_photos = [p for p in updated_photos if p["likes"] >= 4]
        
        return top_photos[:2]  # Возвращаем максимум 2 фото
        
    except Exception as e:
        logger.error(f"Ошибка получения топ фото: {e}")
        return []


async def get_top_users() -> list:
    """Получение топ 5 активных пользователей по сообщениям"""
    global daily_stats
    
    if not daily_stats["user_messages"]:
        return []
    
    # Сортируем по количеству сообщений
    sorted_users = sorted(
        daily_stats["user_messages"].items(),
        key=lambda x: x[1]["count"],
        reverse=True
    )
    
    # Возвращаем топ 5
    return [(user_id, data["name"], data["count"]) for user_id, data in sorted_users[:5]]


async def get_top_rated_users() -> list:
    """Получение топ 10 пользователей по рейтингу"""
    global user_rating_stats
    
    if not user_rating_stats:
        return []
    
    # Сортируем по общему рейтингу
    rated_users = []
    for user_id, stats in user_rating_stats.items():
        total_points = calculate_user_rating(user_id)
        level = get_user_level(user_id)
        rated_users.append({
            "user_id": user_id,
            "name": stats["name"],
            "points": total_points,
            "messages": stats["messages"],
            "photos": stats["photos"],
            "likes": stats["likes"],
            "replies": stats["replies"],
            "level": level
        })
    
    # Сортируем по очкам (по убыванию)
    rated_users.sort(key=lambda x: x["points"], reverse=True)
    
    return rated_users[:10]


async def send_daily_summary():
    """Отправка ежедневной сводки"""
    global daily_summary_sent
    
    if application is None:
        logger.error("Application не инициализирован")
        return
    
    if daily_summary_sent:
        logger.info("Сводка уже отправлена сегодня")
        return
    
    try:
        today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
        
        # Формируем текст сводки
        summary_text = f"📊 **Ежедневная сводка за {today}**\n\n"
        
        # Общее количество сообщений
        summary_text += f"💬 **Всего сообщений:** {daily_stats['total_messages']}\n\n"
        
        # Топ активных пользователей
        top_users = await get_top_users()
        if top_users:
            summary_text += "🏆 **Топ активных бегунов:**\n"
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (user_id, name, count) in enumerate(top_users):
                summary_text += f"{medals[i]} {name} — {count} сообщений\n"
            summary_text += "\n"
        else:
            summary_text += "🏆 **Топ активных бегунов:** Пока никого нет\n\n"
        
        # Рейтинг участников
        top_rated = await get_top_rated_users()
        if top_rated:
            summary_text += "⭐ **Рейтинг участников (топ-10):**\n"
            medals_rating = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            for i, user in enumerate(top_rated):
                level_emoji = LEVEL_EMOJIS.get(user["level"], "")
                summary_text += f"{medals_rating[i]} {level_emoji} {user['name']} — {user['points']} очков"
                # Добавляем детали
                details = []
                if user['messages'] > 0:
                    msg_pts = user['messages'] // POINTS_PER_MESSAGES
                    details.append(f"📝{msg_pts}")
                if user['photos'] > 0:
                    photo_pts = user['photos'] // POINTS_PER_PHOTOS
                    details.append(f"📷{photo_pts}")
                if user['likes'] > 0:
                    like_pts = user['likes'] // POINTS_PER_LIKES
                    details.append(f"❤️{like_pts}")
                if user['replies'] > 0:
                    details.append(f"💬{user['replies']}")
                if details:
                    summary_text += f" ({', '.join(details)})"
                summary_text += "\n"
        else:
            summary_text += "⭐ **Рейтинг участников:** Пока никого нет\n\n"
        
        # Отправляем текстовую часть
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=summary_text,
            parse_mode="Markdown",
        )
        
        # Пытаемся отправить топ фото с 4+ лайками
        try:
            top_photos = await get_top_liked_photos()
            if top_photos:
                for photo in top_photos:
                    try:
                        await application.bot.send_photo(
                            chat_id=CHAT_ID,
                            photo=photo["file_id"],
                            caption=f"❤️ {photo['likes']} лайков",
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Ошибка получения фото: {e}")
        
        daily_summary_sent = True
        logger.info("Ежедневная сводка отправлена")
        
    except Exception as e:
        logger.error(f"Ошибка отправки ежедневной сводки: {e}")


# ============== ЕЖЕНЕДЕЛЬНАЯ СВОДКА ==============
async def send_weekly_summary():
    """Отправка еженедельной сводки по уровням"""
    if application is None:
        logger.error("Application не инициализирован")
        return
    
    try:
        now = datetime.now(MOSCOW_TZ)
        week_num = now.isocalendar()[1]
        year = now.year
        
        weekly_text = f"🌟 **Еженедельная сводка (Неделя #{week_num}, {year})**\n\n"
        
        # Группируем участников по уровням
        levels_summary = {
            "Легенда чата": [],
            "Лидер": [],
            "Активный": [],
            "Новичок": []
        }
        
        for user_id, stats in user_rating_stats.items():
            level = get_user_level(user_id)
            total_points = calculate_user_rating(user_id)
            levels_summary[level].append({
                "name": stats["name"],
                "points": total_points,
                "level": level
            })
        
        # Сортируем участников каждого уровня по очкам
        for level in levels_summary:
            levels_summary[level].sort(key=lambda x: x["points"], reverse=True)
        
        # Выводим участников по уровням (от высокого к низкому)
        level_order = ["Легенда чата", "Лидер", "Активный", "Новичок"]
        
        for level in level_order:
            users = levels_summary[level]
            if users:
                level_emoji = LEVEL_EMOJIS.get(level, "")
                weekly_text += f"{level_emoji} **{level}** ({len(users)} чел.):\n"
                
                # Показываем топ-3 каждого уровня
                top_users = users[:3]
                medals = ["🥇", "🥈", "🥉"]
                for i, user in enumerate(top_users):
                    weekly_text += f"   {medals[i]} {user['name']} — {user['points']} очков\n"
                
                if len(users) > 3:
                    weekly_text += f"   ... и ещё {len(users) - 3} участников\n"
                
                weekly_text += "\n"
        
        # Статистика по активности
        total_messages = sum(stats["messages"] for stats in user_rating_stats.values())
        total_photos = sum(stats["photos"] for stats in user_rating_stats.values())
        total_likes = sum(stats["likes"] for stats in user_rating_stats.values())
        total_replies = sum(stats["replies"] for stats in user_rating_stats.values())
        
        weekly_text += "📊 **Общая статистика недели:**\n"
        weekly_text += f"💬 Сообщений: {total_messages}\n"
        weekly_text += f"📷 Фото: {total_photos}\n"
        weekly_text += f"❤️ Лайков: {total_likes}\n"
        weekly_text += f"💬 Ответов: {total_replies}\n\n"
        
        # Как повысить уровень
        weekly_text += "📈 **Как повысить уровень:**\n"
        weekly_text += f"🌱 → ⭐ (Новичок → Активный): **{USER_LEVELS['Активный']}** очков\n"
        weekly_text += f"⭐ → 👑 (Активный → Лидер): **{USER_LEVELS['Лидер']}** очков\n"
        weekly_text += f"👑 → 🏆 (Лидер → Легенда): **{USER_LEVELS['Легенда чата']}** очков\n"
        
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=weekly_text,
            parse_mode="Markdown",
        )
        
        logger.info("Еженедельная сводка отправлена")
        
    except Exception as e:
        logger.error(f"Ошибка отправки еженедельной сводки: {e}")


# ============== ЕЖЕМЕСЯЧНАЯ СВОДКА ==============
async def send_monthly_summary():
    """Отправка ежемесячной сводки с итогами месяца"""
    global user_rating_stats, user_running_stats
    
    if application is None:
        logger.error("Application не инициализирован")
        return
    
    try:
        now = datetime.now(MOSCOW_TZ)
        month_name = now.strftime("%B %Y")
        
        monthly_text = f"🏆 **Итоги месяца: {month_name}** 🏆\n\n"
        
        # Общий топ-10 участников за месяц
        top_rated = await get_top_rated_users()
        
        if top_rated:
            monthly_text += "🌟 **Топ-10 легенд месяца:**\n"
            medals_rating = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            
            for i, user in enumerate(top_rated):
                level_emoji = LEVEL_EMOJIS.get(user["level"], "")
                monthly_text += f"{medals_rating[i]} {level_emoji} **{user['name']}**\n"
                monthly_text += f"   └─ 🏅 {user['points']} очков | 📝{user['messages']} | 📷{user['photos']} | ❤️{user['likes']} | 💬{user['replies']}\n"
            monthly_text += "\n"
        else:
            monthly_text += "🌟 **Топ-10 легенд месяца:** Пока никого нет\n\n"
        
        # Победители по номинациям
        monthly_text += "🎖️ **Номинации месяца:**\n"
        
        # Самое активное сообщество
        if top_rated:
            monthly_text += f"🥇 **{top_rated[0]['name']}** — Абсолютный лидер месяца!\n"
        
        # Максимум сообщений
        if user_rating_stats:
            max_messages_user = max(user_rating_stats.items(), key=lambda x: x[1]["messages"])
            monthly_text += f"💬 **{max_messages_user[1]['name']}** — Больше всего сообщений ({max_messages_user[1]['messages']})\n"
        
        # Максимум фото
        if user_rating_stats:
            max_photos_user = max(user_rating_stats.items(), key=lambda x: x[1]["photos"])
            if max_photos_user[1]["photos"] > 0:
                monthly_text += f"📷 **{max_photos_user[1]['name']}** — Фотогений месяца ({max_photos_user[1]['photos']} фото)\n"
        
        # Максимум лайков
        if user_rating_stats:
            max_likes_user = max(user_rating_stats.items(), key=lambda x: x[1]["likes"])
            if max_likes_user[1]["likes"] > 0:
                monthly_text += f"❤️ **{max_likes_user[1]['name']}** — Самый любимый автор ({max_likes_user[1]['likes']} лайков)\n"
        
        # Максимум ответов
        if user_rating_stats:
            max_replies_user = max(user_rating_stats.items(), key=lambda x: x[1]["replies"])
            if max_replies_user[1]["replies"] > 0:
                monthly_text += f"💬 **{max_replies_user[1]['name']}** — Самый отзывчивый ({max_replies_user[1]['replies']} ответов)\n"
        
        monthly_text += "\n"
        
        # Статистика месяца
        total_messages = sum(stats["messages"] for stats in user_rating_stats.values())
        total_photos = sum(stats["photos"] for stats in user_rating_stats.values())
        total_likes = sum(stats["likes"] for stats in user_rating_stats.values())
        total_replies = sum(stats["replies"] for stats in user_rating_stats.values())
        
        monthly_text += "📊 **Статистика месяца:**\n"
        monthly_text += f"💬 Всего сообщений: {total_messages}\n"
        monthly_text += f"📷 Всего фото: {total_photos}\n"
        monthly_text += f"❤️ Всего лайков: {total_likes}\n"
        monthly_text += f"💬 Всего ответов: {total_replies}\n"
        monthly_text += f"👥 Активных участников: {len(user_rating_stats)}\n\n"
        
        # Статистика бега
        if user_running_stats:
            running_distance = sum(stats["distance"] for stats in user_running_stats.values()) / 1000
            running_activities = sum(stats["activities"] for stats in user_running_stats.values())
            running_calories = sum(stats["calories"] for stats in user_running_stats.values())
            
            monthly_text += "🏃‍♂️ **Статистика бега за месяц:**\n"
            monthly_text += f"📍 Всего пробежали: {running_distance:.1f} км\n"
            monthly_text += f"🏃‍♂️ Всего тренировок: {running_activities}\n"
            monthly_text += f"🔥 Сожгли калорий: {running_calories} ккал\n"
            monthly_text += f"👥 Бегунов в чате: {len(user_running_stats)}\n\n"
        
        # Поздравляем новых легенд
        legends = [uid for uid in user_rating_stats.keys() if get_user_level(uid) == "Легенда чата"]
        if legends:
            monthly_text += "🎉 **Поздравляем новых легенд чата!**\n"
            for uid in legends:
                monthly_text += f"   🏆 {user_rating_stats[uid]['name']}\n"
        
        # Новые лидеры
        leaders = [uid for uid in user_rating_stats.keys() if get_user_level(uid) == "Лидер"]
        if leaders:
            monthly_text += "🌟 **Новые лидеры:**\n"
            for uid in leaders:
                monthly_text += f"   👑 {user_rating_stats[uid]['name']}\n"
        
        monthly_text += "\n🏃‍♂️ До встречи в следующем месяце!\n"
        monthly_text += "💪 Продолжайте бегать и набирать очки!"
        
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=monthly_text,
            parse_mode="Markdown",
        )
        
        logger.info("Ежемесячная сводка отправлена")
        
        # Сбрасываем статистику после месячной сводки
        user_rating_stats = {}
        logger.info("Статистика рейтинга сброшена для нового месяца")
        
    except Exception as e:
        logger.error(f"Ошибка отправки ежемесячной сводки: {e}")


async def daily_summary_scheduler_task():
    """Планировщик ежедневной, еженедельной и ежемесячной сводок"""
    global daily_summary_sent, current_week, user_running_stats
    
    while bot_running:
        now = datetime.now(MOSCOW_TZ)
        current_hour = now.hour
        current_minute = now.minute
        today_date = now.strftime("%Y-%m-%d")
        
        # Сброс флага отправки в полночь
        if now.hour == 0 and current_minute == 0:
            daily_summary_sent = False
        
        # Отправка сводки в 23:59
        if current_hour == 23 and current_minute == 59:
            if not daily_summary_sent:
                logger.info("Время 23:59 - отправляем ежедневную сводку")
                try:
                    await send_daily_summary()
                except Exception as e:
                    logger.error(f"Ошибка при отправке сводки: {e}")
        
        # Проверка недели (воскресенье 23:00 - еженедельная сводка + бег)
        if now.weekday() == 6 and current_hour == 23 and current_minute == 0:
            week_num = now.isocalendar()[1]
            if week_num != current_week:
                logger.info(f"Время воскресенье 23:00 - отправляем еженедельную сводку")
                try:
                    await send_weekly_summary()
                except Exception as e:
                    logger.error(f"Ошибка при отправке еженедельной сводки: {e}")
                
                # Также отправляем сводку по бегу
                try:
                    await send_weekly_running_summary()
                except Exception as e:
                    logger.error(f"Ошибка при отправке еженедельной сводки по бегу: {e}")
                
                current_week = week_num
        
        # Проверка конца месяца (последний день месяца в 23:00)
        last_day_of_month = (now.replace(day=28) + timedelta(days=4)).day - (now.replace(day=28) + timedelta(days=4)).day % 28
        if now.day == last_day_of_month and current_hour == 23 and current_minute == 0:
            logger.info(f"Последний день месяца - отправляем ежемесячную сводку")
            try:
                await send_monthly_summary()
            except Exception as e:
                logger.error(f"Ошибка при отправке ежемесячной сводки: {e}")
            
            # Также отправляем сводку по бегу за месяц
            try:
                await send_monthly_running_summary()
            except Exception as e:
                logger.error(f"Ошибка при отправке ежемесячной сводки по бегу: {e}")
            
            # Сбрасываем статистику бега для нового месяца
            try:
                reset_monthly_running_stats()
            except Exception as e:
                logger.error(f"Ошибка при сбросе статистики бега: {e}")
        
        await asyncio.sleep(60)


# ============== АНОНИМНАЯ ОТПРАВКА ==============
async def anon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_anon_state[user_id] = "waiting_for_text"

    try:
        await update.message.delete()
    except Exception:
        pass


async def anonphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_anon_state[user_id] = "waiting_for_photo"

    try:
        await update.message.delete()
    except Exception:
        pass


# ============== КОМАНДА ДЛЯ ДНЯ РОЖДЕНИЯ ==============
async def birthday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /birthday DD.MM — установка дня рождения"""
    global user_birthdays
    
    try:
        user_id = update.message.from_user.id
        user_name = f"@{update.message.from_user.username}" if update.message.from_user.username else update.message.from_user.full_name
        
        # Проверяем аргументы
        if not context.args or len(context.args) != 1:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🎂 **Команда /birthday**\n\n"
                     "📝 Используй: `/birthday DD.MM`\n"
                     "📱 *Пример:* `/birthday 15.06`\n\n"
                     "Бот будет поздравлять тебя с Днём рождения каждый год! 🎉",
                parse_mode="Markdown"
            )
            return
        
        # Парсим дату
        birthday_str = context.args[0]
        try:
            datetime.strptime(birthday_str, "%d.%m")
        except ValueError:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Неправильный формат даты!\n\n"
                     "Используй: `/birthday DD.MM`\n"
                     "📱 *Пример:* `/birthday 15.06`",
                parse_mode="Markdown"
            )
            return
        
        # Сохраняем день рождения
        user_birthdays[user_id] = {
            "name": user_name,
            "birthday": birthday_str
        }
        save_birthdays()
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ *День рождения сохранён!* 🎂\n\n"
                 f"👤 {user_name}\n"
                 f"📅 Дата: {birthday_str}\n\n"
                 f"Бот запомнит и поздравит тебя в следующий ДР! 🎉",
            parse_mode="Markdown"
        )
        logger.info(f"[BIRTHDAY] День рождения сохранён: {user_name} — {birthday_str}")
        
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка команды: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при сохранении дня рождения"
        )


# ============== ЕДИНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ ==============
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик всех сообщений - и статистика, и реакции"""
    global daily_stats, user_rating_stats, user_current_level, user_night_messages, user_night_warning_sent, mam_message_id, user_last_active
    
    # ОТЛАДКА - логируем ЧТО ПРИШЛО
    try:
        logger.info(f"[HANDLER] Получен update: type={type(update)}, message={update.message is not None}")
        if update.message:
            logger.info(f"[HANDLER] message_id={update.message.message_id}, text='{update.message.text or ''[:50]}'")
    except Exception as e:
        logger.error(f"[HANDLER] Ошибка логирования: {e}")
    
    try:
        # === ПРОВЕРКА РЕАКЦИЙ ===
        if update.message and hasattr(update.message, 'reactions') and update.message.reactions:
            logger.info(f"[HANDLER] Это реакция!")
            try:
                await handle_reactions(update, context)
            except Exception as e:
                logger.error(f"[REACTION] Ошибка: {e}")
            return
        
        if not update.message:
            logger.debug(f"[HANDLER] Нет message, пропускаем")
            return
        
        if update.message.from_user and update.message.from_user.is_bot:
            logger.debug(f"[HANDLER] Это бот, пропускаем")
            return
        
        user = update.message.from_user
        if not user:
            logger.debug(f"[HANDLER] Нет user, пропускаем")
            return

        user_id = user.id
        user_name = f"@{user.username}" if user.username else user.full_name
        message_text = update.message.text or ""
        message_caption = update.message.caption or ""
        is_photo = bool(update.message.photo)

        logger.info(f"[MSG] === НАЧАЛО обработки от {user_name} ===")

        # Проверяем, не команда ли это
        if message_text and message_text.startswith('/'):
            logger.info(f"[MSG] Это команда, пропускаем")
            return

        # === ПРОВЕРКА: ОТВЕТ НА СООБЩЕНИЕ БОТА (AI ОТВЕТ) ===
        if (AI_ENABLED or GEMINI_ENABLED or DEEPSEEK_ENABLED) and update.message.reply_to_message:
            original_message = update.message.reply_to_message
            # Проверяем, что reply_to_message действительно от бота
            if original_message.from_user and original_message.from_user.id == (context.bot.id if hasattr(context.bot, 'id') else None):
                if original_message.from_user.is_bot:
                    logger.info(f"[AI] {user_name} ответил на сообщение бота: '{message_text[:30]}...'")
                    
                    # Получаем текст сообщения бота, на который ответили
                    bot_message_text = original_message.text or original_message.caption or "сообщение бота"
                    
                    # Отправляем "печатает" статус
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
                    
                    # Генерируем ИИ-ответ
                    ai_response = await generate_ai_response(message_text, bot_message_text, user_name)
                    
                    if ai_response:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=ai_response
                        )
                        logger.info(f"[AI] Ответ отправлен {user_name}")
                    else:
                        logger.warning(f"[AI] Не удалось сгенерировать ответ для {user_name}")
                    # Продолжаем выполнение для сбора статистики

        # === ПРОВЕРКА ВОЗВРАЩЕНЦА ===
        moscow_now = datetime.now(MOSCOW_TZ)
        today = moscow_now.strftime("%Y-%m-%d")
        
        if user_id in user_last_active:
            last_active_date = user_last_active[user_id]
            
            # Проверяем, прошло ли 5+ дней с последнего сообщения
            try:
                last_date_obj = datetime.strptime(last_active_date, "%Y-%m-%d")
                days_since = (moscow_now.date() - last_date_obj.date()).days
                
                if days_since >= 5:
                    # Пользователь вернулся после 5+ дней молчания
                    return_greeting = random.choice(RETURN_GREETINGS)
                    
                    # Пытаемся отправить приветствие
                    try:
                        await context.bot.send_message(
                            chat_id=CHAT_ID,
                            text=f"{user_name} {return_greeting}",
                        )
                        logger.info(f"[RETURN] Приветствие возвращенца отправлено: {user_name}, отсутствовал {days_since} дней")
                    except Exception as e:
                        logger.error(f"[RETURN] Ошибка отправки приветствия: {e}")
            except Exception as e:
                logger.error(f"[RETURN] Ошибка расчёта дней: {e}")
        
        # Обновляем дату последней активности
        user_last_active[user_id] = today
        
        # === АНОНИМНАЯ ОТПРАВКА ===
        if user_id in user_anon_state:
            state = user_anon_state[user_id]
            
            if state == "waiting_for_text" and message_text:
                # Анонимный текст
                import re
                match = re.match(r'^@(\w+)\s+(.+)', message_text)
                if match:
                    anon_text = f"📬 **Анонимное сообщение для @{match.group(1)}:**\n\n{match.group(2)}"
                else:
                    anon_text = f"📬 **Анонимное сообщение:**\n\n{message_text}"
                
                try:
                    await update.message.delete()
                except:
                    pass
                
                await context.bot.send_message(chat_id=CHAT_ID, text=anon_text, parse_mode="Markdown")
                del user_anon_state[user_id]
                logger.info(f"[ANON] Анонимное сообщение от {user_name}")
                return
            
            elif state == "waiting_for_photo" and is_photo:
                photo = update.message.photo[-1]
                try:
                    await update.message.delete()
                except:
                    pass
                
                await context.bot.send_photo(chat_id=CHAT_ID, photo=photo.file_id, caption="📬 **Анонимное фото**", parse_mode="Markdown")
                del user_anon_state[user_id]
                logger.info(f"[ANON] Анонимное фото от {user_name}")
                return
            
            else:
                del user_anon_state[user_id]
                logger.info(f"[ANON] Состояние очищено для {user_name}")
        
        # === ПРОВЕРКА ОТВЕТОВ НА /MAM ===
        if mam_message_id is not None and update.message.reply_to_message:
            if update.message.reply_to_message.message_id == mam_message_id:
                # Кто-то ответил на сообщение "/mam" - отправляем фото
                logger.info(f"[MAM] Обнаружен ответ на сообщение /mam от {user_name}")
                try:
                    with open(MAM_PHOTO_PATH, 'rb') as photo_file:
                        await context.bot.send_photo(
                            chat_id=CHAT_ID,
                            photo=photo_file,
                        )
                    logger.info(f"[MAM] Фото отправлено")
                except Exception as e:
                    logger.error(f"[MAM] Ошибка отправки фото: {e}")
                # Сбрасываем mam_message_id чтобы не реагировать на повторные ответы
                mam_message_id = None
        
        # === ОТВЕТ НА "СПОКОЙНОЙ НОЧИ" / "ДОБРОЙ НОЧИ" ===
        good_night_keywords = [
            # С "й"
            'спокойной ночи', 'спокойной ночи!', 'спокойной ночи всем', 'всем спокойной ночи',
            # Без "й" (распространённая ошибка)
            'спокойно ночи', 'спокойно ночи!', 'спокойно ночи всем', 'всем спокойно ночи',
            # Добрая ночь
            'доброй ночи', 'доброй ночи!', 'доброй ночи всем', 'всем доброй ночи',
            # Английские
            'good night', 'good night!', 'good night!', 'gn',
            # Короткие
            'спок', 'спок!', 'gn!',
        ]
        
        if any(keyword in check_text for keyword in good_night_keywords):
            good_night_responses = [
                f"🌙 {user_name}, спокойной ночи! 🌟",
                f"💤 {user_name}, сладких снов! 💫",
                f"🌙 {user_name}, пусть тебе приснятся звёзды! ✨",
                f"💫 {user_name}, доброй ночи! 🌙",
                f"🌟 {user_name}, спокойной ночи! Пусть ночь подарит тебе отдых! 💤",
                f"🌙 {user_name}, сладких снов! Завтра будет новый день! ☀️",
                f"💤 {user_name}, отличной ночи! 🌙",
                f"✨ {user_name}, спокойной ночи! Пусть сон будет крепким! 💫",
                f"🌙 {user_name}, доброй ночи! Мечтай о хорошем! 💭",
                f"💫 {user_name}, спокойной ночи! Утро будет радостным! ☀️",
                f"🌟 {user_name}, сладких снов! Ты молодец сегодня! 💪",
                f"💤 {user_name}, спокойной ночи! Завтра всё будет хорошо! 🌈",
                f"🌙 {user_name}, доброй ночи! Отдыхай! ✨",
                f"💫 {user_name}, спокойной ночи! Луна присмотрит за тобой! 🌙",
                f"🌟 {user_name}, сладких снов! До завтра! 💤",
            ]
            response = random.choice(good_night_responses)
            await context.bot.send_message(chat_id=CHAT_ID, text=response)
            logger.info(f"[GOODNIGHT] Ответил на спокойную ночь от {user_name}")
            # Не делаем return, чтобы статистика тоже считалась

        # === ОТВЕТ НА "ДОБРОЕ УТРО" С КИНО-ТЕМАТИКОЙ ===
        good_morning_keywords = [
            # Русские варианты
            'доброе утро', 'доброе утро!', 'доброе утро всем', 'всем доброе утро',
            'доброе утро!', 'доброе утро.', 'доброе утро,', 'утро доброе', 'утро!',
            'всем утро', 'утро доброе', 'доброутро', 'доброго утра',
            'всем доброго утра', 'доброго утра!', 'доброго утра всем',
            # Смайлики с утром
            '☀️ утро', '☀️доброе', 'утро ☀️',
            # Короткие
            'утра', 'всем утра', 'утречка', 'утречко', 'с утра',
            # Английские
            'good morning', 'good morning!', 'morning!', 'morning',
            # С вопросом или в предложении
            '?доброе утро', 'утро?', 'доброе утро?',
        ]
        
        # Также реагируем на слова о пробуждении
        wake_up_words = ['проснулся', 'проснулась', 'встал', 'встала', 'просыпаюсь']
        # Проверяем и текст, и подпись к фото (caption) для сообщений с фото
        check_text = (message_text + " " + message_caption).strip().lower()
        is_waking_up = any(word in check_text for word in wake_up_words)

        # === ПРОВЕРКА НА "ДОБРОЕ УТРО" ИЛИ ПРОБУЖДЕНИЕ ===
        if any(keyword in check_text for keyword in good_morning_keywords) or is_waking_up:
            # Кино-тематика для доброго утра (БЕЗ БЕГА!)
            movie_morning_responses = [
                # МАТРИЦА
                f"💊 {user_name}, проснись и пой! Зелёная таблетка выпита — доброе утро! 🟢",
                f"🔮 {user_name}, Матрица говорит: «Доброе утро, Нео!» ☀️",
                f"🕶️ {user_name}, доброе утро, чемпион! Реальность ждёт! 💫",
                f"💊 {user_name}, ты выбрал правду — и это начинается с утра! ✨",
                
                # ЗВЁЗДНЫЕ ВОЙНЫ
                f"⚔️ {user_name}, да пребудет с тобой Сила и доброе утро! 🗡️",
                f"⭐ {user_name}, да пребудет с тобой доброе утро, джедай! 🧘",
                f"🚀 {user_name}, Эскадрилья «Утренняя звезда» приветствует тебя! ✨",
                f"🌅 {user_name}, да пребудет сила в это прекрасное утро! ⚡",
                f"🪐 {user_name}, Татуин встречает рассвет — доброе утро! 🏜️",
                f"👽 {user_name}, далеко-далеко наступило доброе утро! 🌟",
                
                # НАЗАД В БУДУЩЕЕ
                f"⏰ {user_name}, 1.21 гигаватт утренней энергии — DeLorean готов! 🚗💨",
                f"🕐 {user_name}, куда ты отправишься этим утром? 🗺️",
                f"⚡ {user_name}, Эйнштейн говорит: «Доброе утро!» — ДА! 💫",
                f"🚗 {user_name}, DeLorean говорит — пора в путь! ✨",
                f"🎯 {user_name}, часы идут — утро настало! 🕰️",
                
                # ВЛАСТЕЛИН КОЛЕЦ
                f"💍 {user_name}, одно утро, чтобы править всеми! 👑",
                f"🗡️ {user_name}, Фродо проснулся — доброе утро, хоббит! 🌿",
                f"🏰 {user_name}, Шир встречает рассвет — доброе утро! 🌄",
                f"✨ {user_name}, даже хоббиты встают рано — доброе утро! 💪",
                f"🧙‍♂️ {user_name}, Гендальф говорит: «Доброе утро!» 🧙",
                f"🦶 {user_name}, путь начинается с первого шага — доброе утро! 👣",
                f"🗺️ {user_name}, приключение начинается — доброе утро! ⚔️",
                
                # ИНДИАНА ДЖОНС
                f"🎩 {user_name}, шляпа наготове — приключение начинается! 🏜️",
                f"🗺️ {user_name}, карта приключений ждёт — доброе утро! 🗺️",
                f"💎 {user_name}, священный Грааль утра — твоё время! ⚱️",
                f"🏛️ {user_name}, Храм Судьбы открыт — доброе утро! 🏛️",
                f"🐍 {user_name}, Инди говорит: «Доброе утро, искатель приключений!» 🐍",
                f"🧭 {user_name}, север зовёт — доброе утро! 🧭",
                
                # ПИРАТЫ КАРИБСКОГО МОРЯ
                f"🏴‍☠️ {user_name}, утренний бриз и паруса на ветру — доброе утро! ⚓",
                f"⚓ {user_name}, капитан говорит: «На горизонте — новый день!» 🗓️",
                f"💀 {user_name}, Дэви Джонс спит — а ты проснулся! Доброе утро! 💀",
                f"🌊 {user_name}, в море утренней свежести — доброе утро, моряк! ⛵",
                f"🗡️ {user_name}, Чёрная Жемчужина отправляется — ты на борту? ⛵",
                f"🏝️ {user_name}, Остров Сокровищ ждёт — доброе утро! 💎",
                f"🦜 {user_name}, попугай говорит: «Доброе утро, капитан!» 🦜",
            ]
            
            response = random.choice(movie_morning_responses)
            # Отправляем лично в ответ на сообщение пользователя
            await context.bot.send_message(
                chat_id=CHAT_ID, 
                text=response,
                reply_to_message_id=update.message.message_id
            )
            logger.info(f"[MORNING] Кино-ответ на утро от {user_name}")
            return  # ✅ Выходим после отправки ответа на утро
        
        # === СТАТИСТИКА ===
        
        # Считаем дату по Москве
        moscow_now = datetime.utcnow() + timedelta(hours=UTC_OFFSET)
        today = moscow_now.strftime("%Y-%m-%d")
        
        # Безопасная инициализация daily_stats
        if not isinstance(daily_stats, dict) or "date" not in daily_stats:
            daily_stats = {"date": today, "total_messages": 0, "user_messages": {}, "photos": []}
            logger.info("[MSG] daily_stats переинициализирован")
        
        logger.info(f"[MSG] today={today}, daily_stats_date={daily_stats.get('date', 'EMPTY')}")
        
        # Сбрасываем только если новый день
        if daily_stats.get("date", "") != today:
            daily_stats["date"] = today
            daily_stats["total_messages"] = 0
            daily_stats["user_messages"] = {}
            daily_stats["photos"] = []
            logger.info("[MSG] Новый день - статистика сброшена")
            logger.info(f"[MSG] Новый день! Сброшена статистика")
        
        # Увеличиваем счётчик
        daily_stats["total_messages"] += 1
        current_count = daily_stats["total_messages"]
        logger.info(f"[MSG] Сообщение #{current_count}")
        
        if user_id not in daily_stats["user_messages"]:
            daily_stats["user_messages"][user_id] = {"name": user_name, "count": 0}
        daily_stats["user_messages"][user_id]["count"] += 1
        
        if is_photo:
            photo = update.message.photo[-1]
            daily_stats["photos"].append({
                "file_id": photo.file_id,
                "user_id": user_id,
                "message_id": update.message.message_id,
            })
        
        # === РЕЙТИНГ ===
        if user_id not in user_rating_stats:
            user_rating_stats[user_id] = {"name": user_name, "messages": 0, "photos": 0, "likes": 0, "replies": 0}
            user_current_level[user_id] = "Новичок"
            logger.info(f"[MSG] Новый пользователь в рейтинге: {user_name}")
        
        old_msg_count = user_rating_stats[user_id]["messages"]
        user_rating_stats[user_id]["messages"] += 1
        new_msg_count = user_rating_stats[user_id]["messages"]
        logger.info(f"[MSG] messages: {old_msg_count} -> {new_msg_count}")
        
        if is_photo:
            user_rating_stats[user_id]["photos"] += 1
        
        # Считаем общий рейтинг
        stats = user_rating_stats[user_id]
        total_points = (stats["messages"] // 300 + stats["photos"] // 10 + stats["likes"] // 50 + stats["replies"])
        
        logger.info(f"[MSG] Рейтинг {user_name}: {total_points} баллов ({stats['messages']}msg, {stats['photos']}photo)")
        
        # === НАЧИСЛЕНИЕ БАЛЛОВ ЗА "+" ===
        reply_msg = update.message.reply_to_message
        logger.info(f"[PLUS] Проверка: reply_msg={reply_msg is not None}, text='{message_text}'")
        
        if reply_msg is not None:
            logger.info(f"[PLUS] reply_msg.from_user={reply_msg.from_user}")
            
            if reply_msg.from_user is not None:
                original_id = reply_msg.from_user.id
                is_not_self = original_id != user_id
                is_plus = message_text.strip() == "+"
                
                logger.info(f"[PLUS] original_id={original_id}, user_id={user_id}, is_not_self={is_not_self}, is_plus={is_plus}")
                
                if is_not_self and is_plus:
                    original_name = f"@{reply_msg.from_user.username}" if reply_msg.from_user.username else reply_msg.from_user.full_name
                    
                    if original_id not in user_rating_stats:
                        user_rating_stats[original_id] = {"name": original_name, "messages": 0, "photos": 0, "likes": 0, "replies": 0}
                        user_current_level[original_id] = "Новичок"
                    
                    user_rating_stats[original_id]["replies"] += 1
                    
                    orig_stats = user_rating_stats[original_id]
                    new_total = (orig_stats["messages"] // 300 + orig_stats["photos"] // 10 + orig_stats["likes"] // 50 + orig_stats["replies"])
                    
                    await send_point_notification(original_name, 1, "ответ", new_total)
                    logger.info(f"[PLUS] ✅ {user_name} дал(+) {original_name}. Всего: {new_total}")
                else:
                    if not is_not_self:
                        logger.info(f"[PLUS] ❌ Это ответ на свое сообщение")
                    if not is_plus:
                        logger.info(f"[PLUS] ❌ Текст не равен '+' (текст='{message_text}', stripped='{message_text.strip()}')")
        
        # === НОЧНОЙ РЕЖИМ ===
        utc_now = datetime.utcnow()
        utc_hour = utc_now.hour
        moscow_hour = (utc_hour + UTC_OFFSET) % 24
        
        logger.info(f"[NIGHT] Проверка: UTC={utc_hour}, Moscow={moscow_hour}, is_night={(moscow_hour >= 22 or moscow_hour < 8)}")
        
        if moscow_hour >= 22 or moscow_hour < 8:
            # Инициализируем если нет
            if user_id not in user_night_messages:
                user_night_messages[user_id] = 0
            if user_id not in user_night_warning_sent:
                user_night_warning_sent[user_id] = None
            
            # Сбрасываем только если сегодня ещё не отправляли
            if user_night_warning_sent.get(user_id) != today:
                user_night_messages[user_id] = 0
                user_night_warning_sent[user_id] = today
            
            user_night_messages[user_id] += 1
            night_count = user_night_messages[user_id]
            logger.info(f"[NIGHT] 🔥 {user_name}: {night_count}/10 ночных сообщений")
            
            if night_count == 10:
                warning = random.choice(NIGHT_WARNINGS)
                await context.bot.send_message(chat_id=CHAT_ID, text=warning)
                user_night_warning_sent[user_id] = today
                logger.info(f"[NIGHT] ⛔ ПРЕДУПРЕЖДЕНИЕ ОТПРАВЛЕНО {user_name}")
        else:
            logger.info(f"[NIGHT] ☀️ День - ночной режим не активен (Москва {moscow_hour}:00)")
        
        logger.info(f"[MSG] === КОНЕЦ обработки {user_name} ===")
    
    except Exception as e:
        logger.error(f"[MSG] 💥 КРИТИЧЕСКАЯ ОШИБКА: {e}", exc_info=True)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка callback-запросов (реакций)"""
    try:
        if update.callback_query:
            callback_data = update.callback_query.data
            logger.info(f"[CALLBACK] Получен callback: {callback_data}")
            
            # Здесь можно обрабатывать различные callback-запросы
            await update.callback_query.answer()
            
    except Exception as e:
        logger.error(f"[CALLBACK] Ошибка обработки callback: {e}")


async def handle_reactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка реакций на сообщения - подсчёт ВСЕХ реакций в реальном времени"""
    global user_rating_stats, user_current_level, daily_stats
    
    try:
        if not update.message or not update.message.reactions:
            return
        
        # Получаем информацию о реакциях
        reaction_list = update.message.reactions
        user_id = update.message.from_user.id if update.message.from_user else None
        message_id = update.message.message_id
        sender_id = update.message.from_user.id if update.message.from_user else None
        
        logger.info(f"[REACTION] Пользователь {user_id} добавил реакцию на сообщение {message_id}")
        
        # === ПРОВЕРКА: РЕАКЦИЯ НА СООБЩЕНИЕ БОТА ===
        try:
            bot_info = await context.bot.get_me()
            bot_id = bot_info.id
        except:
            bot_id = None
        
        # Если сообщение от бота — отвечаем с благодарностью!
        if sender_id == bot_id:
            reactor_name = f"@{update.message.from_user.username}" if update.message.from_user.username else update.message.from_user.full_name
            
            # Список благодарственных ответов на реакции
            reaction_thanks = [
                # Стандартные реакции
                f"❤️ {reactor_name}, спасибо за реакцию! Ты лучший!",
                f"🔥 {reactor_name}, продолжай в том же духе — лайков нам!",
                f"⭐ {reactor_name}, рад, что тебе понравилось! Ещё больше реакций!",
                f"💫 {reactor_name}, ты заметил моё сообщение — я польщён!",
                f"🎯 {reactor_name}, меткий взгляд! Ещё реакции!",
                f"👍 {reactor_name}, принимается! Давай ещё лайков!",
                f"😊 {reactor_name}, спасибо за поддержку!",
                f"🚀 {reactor_name}, реакция получена! Продолжаем!",
                f"💪 {reactor_name}, сила реакций с тобой!",
                f"🎉 {reactor_name}, ура! Ещё одна реакция!",
                f"🙌 {reactor_name}, здорово! Продолжай в том же духе!",
                f"✨ {reactor_name}, магия реакций работает!",
                f"🌟 {reactor_name}, ты зажигаешь!",
                f"💯 {reactor_name}, идеально! Ещё реакций!",
                
                # Реакции на СМЕХ (хахах, лол, 😂)
                f"😂 {reactor_name}, рассмешил! Твой смех — лучшая награда!",
                f"🤣 {reactor_name}, ржём вместе! Это того стоило!",
                f"💀 {reactor_name}, до слёз! Смейся чаще!",
                f"🤭 {reactor_name}, я тоже смеюсь!",
                f"😆 {reactor_name}, весело получилось!",
                f"🎭 {reactor_name}, комедия удалась!",
                f"🤡 {reactor_name}, ты как минимум улыбнулся — успех!",
                f"🏆 {reactor_name}, король юмора!",
                
                # Реакции на УДИВЛЕНИЕ (wow, 😮, 🤯)
                f"😮 {reactor_name}, впечатлил! Продолжай!",
                f"🤯 {reactor_name}, мозг взорван! Это успех!",
                f"😲 {reactor_name}, не ожидал такой реакции!",
                f"🎆 {reactor_name}, эффектно сработало!",
                f"🔥 {reactor_name}, ого! Зажёг!",
                
                # Реакции на ПОДДЕРЖКУ (сердечко, и т.д.)
                f"💖 {reactor_name}, твоё сердце согревает мой код!",
                f"💕 {reactor_name}, взаимная любовь к чату!",
                f"🫶 {reactor_name}, обнимашки через реакцию!",
                f"💗 {reactor_name}, ты тёплый! Продолжай!",
                f"🥰 {reactor_name}, как приятно!",
                
                # Весёлые и мотивационные
                f"🏃‍♂️ {reactor_name}, давай больше движения в чат!",
                f"💥 {reactor_name}, бабахнуло! Эпично!",
                f"🧨 {reactor_name}, взрывная реакция!",
                f"🎪 {reactor_name}, цирк начинается!",
                f"🎨 {reactor_name}, искусство реакций!",
                f"🕺 {reactor_name}, танцуют все!",
                f"💃 {reactor_name}, ритм есть!",
                f"🎵 {reactor_name}, музыка реакций!",
                f"🎶 {reactor_name}, подпеваем!",
            ]
            
            # Выбираем случайный ответ
            import random
            thanks_response = random.choice(reaction_thanks)
            
            # Отправляем ответ в чат
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=thanks_response,
                reply_to_message_id=message_id  # Отвечаем на сообщение с реакцией
            )
            
            logger.info(f"[REACTION] 🤖 Ответил на реакцию от {reactor_name} на сообщение бота")
            return  # Выходим — не нужно дальше обрабатывать реакцию бота
        
        # === ПРОВЕРКА: БОТ СОБРАЛ БОЛЬШЕ 4 ЛАЙКОВ ===
        # Считаем ВСЕ реакции (любые эмодзи)
        total_reactions = 0
        for reaction in reaction_list:
            for choice in reaction.choices:
                total_reactions += choice.count
        
        logger.info(f"[REACTION] Всего реакций на сообщение {message_id}: {total_reactions}")
        
        # Если это сообщение бота и больше 4 лайков — особая реакция!
        if sender_id == bot_id and total_reactions >= 5:
            # Список особых ответов для популярных сообщений
            popular_responses = [
                "🤩 Да, да, я самый популярный здесь! Спасибо за любовь!",
                "💪 Я чувствую вашу поддержку! Вы лучшие!",
                "🌟 Звёзды сошлись — моё сообщение взорвало чат!",
                "🎉 Ура! Меня любят! Это взаимно!",
                "🔥 Да, я король этого чата! Спасибо за лайки!",
                "💯 Популярность зашкаливает! Вы нереальные!",
                "🏆 Миссия выполнена — сердца завоёваны!",
                "💖 Ваша любовь — моё топливо! Спасибо!",
                "⭐ Звезда в чате — это я! Спасибо за признание!",
                "🎯 5+ лайков! Я сделал это! Вы сделали это!",
            ]
            
            import random
            popular_response = random.choice(popular_responses)
            
            # Отправляем особый ответ
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=popular_response,
                reply_to_message_id=message_id
            )
            
            logger.info(f"[REACTION] 🎉 Бот собрал {total_reactions} реакций и отпраздновал!")
            return
        
        # Считаем ВСЕ реакции (любые эмодзи)
        total_reactions = 0
        for reaction in reaction_list:
            for choice in reaction.choices:
                total_reactions += choice.count
        
        logger.info(f"[REACTION] Всего реакций на сообщение {message_id}: {total_reactions}")
        
        if total_reactions > 0:
            # Ищем это сообщение в daily_stats["photos"]
            if "photos" in daily_stats and daily_stats["photos"]:
                for photo_info in daily_stats["photos"]:
                    if photo_info["message_id"] == message_id:
                        photo_author_id = photo_info["user_id"]
                        
                        # Инициализируем пользователя если нужно
                        if photo_author_id not in user_rating_stats:
                            user_rating_stats[photo_author_id] = {
                                "name": "Unknown",
                                "messages": 0,
                                "photos": 0,
                                "likes": 0,
                                "replies": 0
                            }
                            user_current_level[photo_author_id] = "Новичок"
                        
                        # Обновляем общее количество лайков/реакций
                        old_likes = user_rating_stats[photo_author_id]["likes"]
                        user_rating_stats[photo_author_id]["likes"] = total_reactions
                        new_likes = user_rating_stats[photo_author_id]["likes"]
                        
                        logger.info(f"[REACTION] Реакции для пользователя {photo_author_id}: {old_likes} -> {new_likes}")
                        
                        # Проверяем, начислились ли баллы
                        POINTS_PER_LIKES = 50  # 50 реакций = 1 балл
                        old_points = old_likes // POINTS_PER_LIKES
                        new_points = new_likes // POINTS_PER_LIKES
                        points_earned = new_points - old_points
                        
                        if points_earned > 0:
                            photo_author_name = user_rating_stats[photo_author_id]["name"]
                            total = calculate_user_rating(photo_author_id)
                            await send_point_notification(photo_author_name, points_earned, "лайки", total)
                            
                            # Проверяем повышение уровня
                            new_level = get_user_level(photo_author_id)
                            old_level = user_current_level.get(photo_author_id, "Новичок")
                            if new_level != old_level and new_level != "Новичок":
                                user_current_level[photo_author_id] = new_level
                                await send_level_up_notification(photo_author_name, new_level)
                        
                        break
    
    except Exception as e:
        logger.error(f"[REACTION] Ошибка обработки реакции: {e}", exc_info=True)


# ============== ОБРАБОТЧИКИ ==============
START_MESSAGE = """🏃 **Бот для бегового чата**

**Автоматические сообщения:**
• 06:00 — Утреннее приветствие + погода + тема дня
• 11:00 — Мотивация
• 16:00 — Мотивация
• 21:00 — Мотивация
• 22:00+ — Ночной режим (после 10 сообщений напоминает спать)
• 23:59 — Ежедневная сводка
• Воскресенье 23:00 — Еженедельная сводка по уровням
• Последний день месяца 23:00 — Итоги месяца
• При возвращении после 5+ дней — приветствие от бота
• При получении баллов — публичное уведомление в чате

**Система рейтинга:**
📝 300 сообщений = 1 балл
📷 10 фото = 1 балл
❤️ 50 лайков = 1 балл
💬 Ответ на твоё сообщение = 1 балл

**Команды:**
• /start — показать это сообщение
• /morning — отправить утреннее приветствие сейчас
• /stopmorning — удалить утреннее сообщение
• /anon @никнейм текст — анонимное сообщение
• /anonphoto — анонимная отправка фото
• /remen — получить порцию смешных ругательств
• /antiremen — получить порцию смешных комплиментов
• /mam — отправить предупреждение "Не зли маму..."
• /advice — получить совет по бегу из интернета
• /summary — получить сводку за сегодня
• /rating — показать топ-10 участников по рейтингу
• /likes — показать рейтинг участников только по лайкам
• /levels — показать всех участников по уровням
• /running — показать рейтинг бегунов за месяц
• /garmin email пароль — привязать аккаунт Garmin Connect
• /garmin_stop — отключить аккаунт Garmin
• /birthday DD.MM — указать дату рождения для поздравлений
• /weekly — показать еженедельную сводку
• /monthly — показать итоги месяца"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=START_MESSAGE,
    )

    try:
        await update.message.delete()
    except Exception:
        pass


async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_morning_greeting()

    try:
        await update.message.delete()
    except Exception:
        pass


async def stopmorning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global morning_message_id

    if morning_message_id is not None:
        try:
            await application.bot.delete_message(
                chat_id=CHAT_ID,
                message_id=morning_message_id,
            )
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="☀️ Утреннее сообщение удалено!",
            )
            morning_message_id = None
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Утреннее сообщение не найдено или уже удалено!",
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Утреннее сообщение не найдено!",
        )

    try:
        await update.message.delete()
    except Exception:
        pass


async def remen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генератор смешных ругательств"""
    insult = get_random_insult()
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"😄 **{insult}**",
        parse_mode="Markdown",
    )

    try:
        await update.message.delete()
    except Exception:
        pass


async def antiremen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генератор смешных комплиментов"""
    compliment = get_random_compliment()
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💖 **{compliment}**",
        parse_mode="Markdown",
    )

    try:
        await update.message.delete()
    except Exception:
        pass


async def mam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /mam - отправить предупреждение про маму"""
    global mam_message_id
    
    try:
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не зли маму, а то сейчас как достану 😈",
        )
        mam_message_id = message.message_id
        logger.info(f"[MAM] Сообщение отправлено, message_id={mam_message_id}")
    except Exception as e:
        logger.error(f"[MAM] Ошибка отправки сообщения: {e}")

    try:
        await update.message.delete()
    except Exception:
        pass


async def advice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /advice - получить совет по бегу из интернета"""
    try:
        # Получаем категорию из аргументов
        args = context.args
        category = args[0] if args else None
        
        # Обновляем кэш советов из интернета
        await update_tips_cache()
        
        # Формируем текст совета
        advice_text = get_random_tip(category)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=advice_text,
            parse_mode="Markdown",
        )
        
        logger.info(f"[ADVICE] Совет отправлен, категория: {category or 'случайная'}")
        
    except Exception as e:
        logger.error(f"[ADVICE] Ошибка отправки совета: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="💡 Использование: /advice [категория]\n\nКатегории: running, recovery, equipment\nПример: /advice running",
        )

    try:
        await update.message.delete()
    except Exception:
        pass


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка сводки по команде"""
    # Сбрасываем флаг для принудительной отправки
    global daily_summary_sent
    was_sent = daily_summary_sent
    daily_summary_sent = False
    
    try:
        await send_daily_summary()
    except Exception as e:
        logger.error(f"Ошибка сводки: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при формировании сводки",
        )
    
    # Восстанавливаем предыдущее состояние
    daily_summary_sent = was_sent
    
    try:
        await update.message.delete()
    except Exception:
        pass


async def rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /rating — показывает топ-10 участников по очкам"""
    try:
        top_rated = await get_top_rated_users()
        
        rating_text = "⭐ **Рейтинг участников бегового чата**\n\n"
        
        if top_rated:
            medals_rating = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            for i, user in enumerate(top_rated):
                level_emoji = LEVEL_EMOJIS.get(user["level"], "")
                rating_text += f"{medals_rating[i]} {level_emoji} **{user['name']}** — **{user['points']}** очков\n"
                
                # Добавляем детализацию
                details_parts = []
                
                # Сообщения
                msg_progress = user['messages'] % POINTS_PER_MESSAGES
                details_parts.append(f"📝 {user['messages']} сообщений (+{POINTS_PER_MESSAGES - msg_progress} до сл. балла)")
                
                # Фото
                photo_progress = user['photos'] % POINTS_PER_PHOTOS
                details_parts.append(f"📷 {user['photos']} фото (+{POINTS_PER_PHOTOS - photo_progress} до сл. балла)")
                
                # Лайки
                like_progress = user['likes'] % POINTS_PER_LIKES
                details_parts.append(f"❤️ {user['likes']} лайков (+{POINTS_PER_LIKES - like_progress} до сл. балла)")
                
                # Ответы
                details_parts.append(f"💬 {user['replies']} ответов\n")
                
                # Добавляем детали с отступами
                for detail in details_parts:
                    rating_text += f"   {detail}\n"
                
                rating_text += "\n"  # Пустая строка между участниками
        else:
            rating_text += "Пока никто не набрал очков. Пишите сообщения, делитесь фото и отвечайте друг другу! 🏃‍♂️\n\n"
            rating_text += "📊 **Как получить очки:**\n"
            rating_text += f"• **{POINTS_PER_MESSAGES} сообщений** = 1 балл\n"
            rating_text += f"• **{POINTS_PER_PHOTOS} фото** = 1 балл\n"
            rating_text += f"• **{POINTS_PER_LIKES} лайков** на ваши сообщения = 1 балл\n"
            rating_text += f"• **Ответ на ваше сообщение** = 1 балл\n"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=rating_text,
            parse_mode="Markdown",
        )
        
        try:
            await update.message.delete()
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка команды rating: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при формировании рейтинга",
        )


async def levels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /levels — показывает всех участников по уровням"""
    try:
        # Группируем участников по уровням
        levels_summary = {
            "Легенда чата": [],
            "Лидер": [],
            "Активный": [],
            "Новичок": []
        }
        
        for user_id, stats in user_rating_stats.items():
            level = get_user_level(user_id)
            total_points = calculate_user_rating(user_id)
            levels_summary[level].append({
                "name": stats["name"],
                "points": total_points,
                "level": level
            })
        
        # Сортируем участников каждого уровня по очкам
        for level in levels_summary:
            levels_summary[level].sort(key=lambda x: x["points"], reverse=True)
        
        levels_text = "🌟 **Уровни участников бегового чата**\n\n"
        
        # Выводим участников по уровням (от высокого к низкому)
        level_order = ["Легенда чата", "Лидер", "Активный", "Новичок"]
        
        for level in level_order:
            users = levels_summary[level]
            if users:
                level_emoji = LEVEL_EMOJIS.get(level, "")
                levels_text += f"{level_emoji} **{level}** ({len(users)} чел.):\n"
                
                # Показываем всех участников уровня
                for user in users:
                    levels_text += f"   🏅 {user['name']} — {user['points']} очков\n"
                
                levels_text += "\n"
        
        if not any(levels_summary.values()):
            levels_text += "Пока никого нет в рейтинге. Начните активничать! 🏃‍♂️\n\n"
        
        # Информация об уровнях
        levels_text += "📊 **Уровни и требования:**\n"
        levels_text += f"🌱 **Новичок** — 0-{USER_LEVELS['Активный']-1} очков\n"
        levels_text += f"⭐ **Активный** — {USER_LEVELS['Активный']}-{USER_LEVELS['Лидер']-1} очков\n"
        levels_text += f"👑 **Лидер** — {USER_LEVELS['Лидер']}-{USER_LEVELS['Легенда чата']-1} очков\n"
        levels_text += f"🏆 **Легенда чата** — {USER_LEVELS['Легенда чата']}+ очков\n"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=levels_text,
            parse_mode="Markdown",
        )
        
        try:
            await update.message.delete()
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка команды levels: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при формировании списка уровней",
        )


async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /weekly — показывает еженедельную сводку (общая + бег)"""
    try:
        await send_weekly_summary()
    except Exception as e:
        logger.error(f"Ошибка команды weekly: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при формировании еженедельной сводки",
        )
    
    # Также отправляем сводку по бегу
    try:
        await send_weekly_running_summary()
    except Exception as e:
        logger.error(f"Ошибка команды weekly (бег): {e}")
    
    try:
        await update.message.delete()
    except Exception:
        pass


async def monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /monthly — показывает ежемесячную сводку (общая + бег)"""
    try:
        await send_monthly_summary()
    except Exception as e:
        logger.error(f"Ошибка команды monthly: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при формировании ежемесячной сводки",
        )
    
    # Также отправляем сводку по бегу
    try:
        await send_monthly_running_summary()
    except Exception as e:
        logger.error(f"Ошибка команды monthly (бег): {e}")
    
    try:
        await update.message.delete()
    except Exception:
        pass


async def running(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /running — показывает рейтинг бегунов за месяц"""
    global user_running_stats
    
    try:
        now = datetime.now(MOSCOW_TZ)
        month_name = now.strftime("%B %Y")
        
        top_runners = get_top_runners()
        
        running_text = f"🏃‍♂️ **Рейтинг бегунов за {month_name}**\n\n"
        
        if top_runners:
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            
            for i, runner in enumerate(top_runners):
                if i >= len(medals):
                    break
                
                name = runner["name"]
                activities = runner["activities"]
                distance_km = runner["distance"] / 1000  # конвертируем в км
                duration_min = runner["duration"] // 60  # конвертируем в минуты
                calories = runner["calories"]
                
                running_text += f"{medals[i]} **{name}**\n"
                running_text += f"   📍 {distance_km:.1f} км | 🏃‍♂️ {activities} тренировок\n"
                running_text += f"   ⏱️ {duration_min} мин | 🔥 {calories} ккал\n\n"
            
            # Общая статистика
            total_distance = sum(r["distance"] for r in top_runners) / 1000
            total_activities = sum(r["activities"] for r in top_runners)
            total_calories = sum(r["calories"] for r in top_runners)
            total_duration = sum(r["duration"] for r in top_runners) // 60
            
            running_text += "📊 **Общая статистика чата:**\n"
            running_text += f"📍 Всего пробежали: {total_distance:.1f} км\n"
            running_text += f"🏃‍♂️ Всего тренировок: {total_activities}\n"
            running_text += f"⏱️ Общее время: {total_duration} мин\n"
            running_text += f"🔥 Всего калорий: {total_calories} ккал\n"
        else:
            running_text += "Пока никто не зарегистрировал пробежки с Garmin.\n\n"
            running_text += "🏃‍♂️ **Как присоединиться к рейтингу:**\n"
            running_text += "• Используйте часы Garmin\n"
            running_text += "• Синхронизируйте тренировки с Garmin Connect\n"
            running_text += "• Бот автоматически отследит ваши пробежки!\n\n"
            running_text += "📱 **Команда для регистрации:** /garmin — привяжите аккаунт Garmin!"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=running_text,
            parse_mode="Markdown",
        )
        
        try:
            await update.message.delete()
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка команды running: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при формировании рейтинга бегунов",
        )


# ============== GARMIN COMMANDS ==============
async def garmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /garmin — привязка аккаунта Garmin Connect"""
    if not GARMIN_AVAILABLE:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Интеграция с Garmin недоступна.\nУстановите библиотеку: pip install garminconnect cryptography",
        )
        try:
            await update.message.delete()
        except Exception:
            pass
        return
    
    user_id = update.message.from_user.id
    user_name = f"@{update.message.from_user.username}" if update.message.from_user.username else update.message.from_user.full_name
    
    args = context.args
    
    if len(args) != 2:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🏃‍♂️ **Регистрация Garmin Connect**\n\n"
                 "📝 Использование: /garmin <email> <password>\n\n"
                 "⚠️ *После ввода сообщение будет удалено для безопасности*\n\n"
                 "📱 *Пример:* /garmin myemail@gmail.com MyPassword123\n\n"
                 "🔒 Ваш пароль хранится в зашифрованном виде",
            parse_mode="Markdown",
        )
        try:
            await update.message.delete()
        except Exception:
            pass
        return
    
    email = args[0]
    password = args[1]
    
    # Удаляем сообщение с паролем сразу
    try:
        await update.message.delete()
    except Exception:
        pass
    
    # Пытаемся войти в Garmin
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🔄 Проверяю данные Garmin для {email}...",
    )
    
    try:
        # Тестовый вход в Garmin
        client = garminconnect.Garmin(email, password)
        client.login()
        
        # Успех! Сохраняем данные
        encrypted_password = encrypt_garmin_password(password)
        
        garmin_users[user_id] = {
            "name": user_name,
            "email": email,
            "encrypted_password": encrypted_password,
            "last_activity_id": "",
            "monthly_distance": 0.0,
            "monthly_activities": 0,
            "last_activity_date": ""
        }
        
        # Сохраняем в файл
        save_garmin_users()
        
        # Подтверждение
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ *Garmin аккаунт подключён!*\n\n"
                 f"📧 Email: {email}\n"
                 f"👤 Пользователь: {user_name}\n\n"
                 f"🏃 Теперь бот будет автоматически отслеживать ваши пробежки и публиковать их в чат!",
            parse_mode="Markdown",
        )
        
        logger.info(f"[GARMIN] Пользователь {user_name} подключил аккаунт {email}")
        
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка входа для {email}: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ *Ошибка подключения Garmin*\n\n"
                 f"Проверьте правильность email и пароля.\n"
                 f"Возможно, включена двухфакторная аутентификация.\n\n"
                 f"Ошибка: {str(e)[:100]}...",
            parse_mode="Markdown",
        )


async def garmin_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /garmin_stop — отключение аккаунта Garmin"""
    user_id = update.message.from_user.id
    user_name = f"@{update.message.from_user.username}" if update.message.from_user.username else update.message.from_user.full_name
    
    if user_id not in garmin_users:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ У вас нет подключённого аккаунта Garmin.\n\n"
                 "📝 Используйте /garmin для подключения.",
        )
        try:
            await update.message.delete()
        except Exception:
            pass
        return
    
    # Удаляем данные пользователя
    email = garmin_users[user_id]["email"]
    del garmin_users[user_id]
    
    # Сохраняем изменения
    save_garmin_users()
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ *Аккаунт Garmin отключён*\n\n"
             f"📧 Email: {email}\n\n"
             f"Ваши пробежки больше не будут публиковаться в чате.",
        parse_mode="Markdown",
    )
    
    try:
        await update.message.delete()
    except Exception:
        pass
    
    logger.info(f"[GARMIN] Пользователь {user_name} отключил аккаунт")


async def garmin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /garmin_list — список зарегистрированных пользователей (только для админов)"""
    if not garmin_users:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📊 **Garmin пользователи**\n\nПока никто не подключил Garmin аккаунт.",
            parse_mode="Markdown",
        )
        try:
            await update.message.delete()
        except Exception:
            pass
        return
    
    text = f"📊 **Garmin пользователи** ({len(garmin_users)} чел.):\n\n"
    
    for user_id, data in garmin_users.items():
        text += f"• {data['name']} — {data['email']}\n"
        text += f"   📍 {data.get('monthly_distance', 0):.1f} км за месяц\n"
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="Markdown",
    )
    
    try:
        await update.message.delete()
    except Exception:
        pass


async def likes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /likes — показывает рейтинг участников только по лайкам"""
    global user_rating_stats
    
    try:
        if not user_rating_stats:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📊 **Рейтинг лайков**\n\nПока никто не получил лайков. Делитесь фото и ставьте реакции! ❤️",
                parse_mode="Markdown",
            )
            try:
                await update.message.delete()
            except Exception:
                pass
            return
        
        # Сортируем участников по количеству лайков
        sorted_by_likes = sorted(
            user_rating_stats.items(),
            key=lambda x: x[1]["likes"],
            reverse=True
        )
        
        # Фильтруем только тех, у кого есть лайки
        users_with_likes = [(uid, stats) for uid, stats in sorted_by_likes if stats["likes"] > 0]
        
        likes_text = "❤️ **Рейтинг лайков участников**\n\n"
        
        if users_with_likes:
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟", "1️⃣1️⃣", "1️⃣2️⃣", "1️⃣3️⃣", "1️⃣4️⃣", "1️⃣5️⃣", "1️⃣6️⃣", "1️⃣7️⃣", "1️⃣8️⃣", "1️⃣9️⃣", "2️⃣0️⃣"]
            
            for i, (user_id, stats) in enumerate(users_with_likes):
                if i >= len(medals):
                    break
                    
                name = stats["name"]
                likes_count = stats["likes"]
                
                # Получаем уровень пользователя
                level = get_user_level(user_id)
                level_emoji = LEVEL_EMOJIS.get(level, "")
                
                likes_text += f"{medals[i]} {level_emoji} **{name}** — **{likes_count}** лайков\n"
                
                # Добавляем информацию о фото
                photos_count = stats["photos"]
                if photos_count > 0:
                    avg_likes = likes_count / photos_count
                    likes_text += f"   📷 {photos_count} фото (среднее: {avg_likes:.1f} лайков/фото)\n"
                
                likes_text += "\n"
            
            # Общая статистика
            total_likes = sum(stats["likes"] for stats in user_rating_stats.values())
            total_photos = sum(stats["photos"] for stats in user_rating_stats.values())
            active_users = len(users_with_likes)
            
            likes_text += "📈 **Общая статистика:**\n"
            likes_text += f"❤️ Всего лайков: {total_likes}\n"
            likes_text += f"📷 Всего фото: {total_photos}\n"
            likes_text += f"👥 Участников с лайками: {active_users}\n"
            
            if total_photos > 0:
                overall_avg = total_likes / total_photos
                likes_text += f"📊 Среднее по чату: {overall_avg:.1f} лайков/фото\n"
        else:
            likes_text += "Пока никто не получил лайков. Делитесь фото! 📸\n\n"
            likes_text += "❤️ **Как получить лайки:**\n"
            likes_text += "• Выкладывайте фото с пробежек\n"
            likes_text += "• Ставьте реакции на фото других участников\n"
            likes_text += "• Чем интереснее фото — тем больше лайков! 🏃‍♂️"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=likes_text,
            parse_mode="Markdown",
        )
        
        try:
            await update.message.delete()
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка команды likes: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при формировании рейтинга лайков",
        )


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return

    try:
        bot_info = await context.bot.get_me()
        bot_id = bot_info.id
    except Exception as e:
        logger.error(f"Ошибка получения ID бота: {e}")
        bot_id = None

    for member in update.message.new_chat_members:
        if member.is_bot or (bot_id and member.id == bot_id):
            continue

        welcome = get_random_welcome()
        try:
            if member.username:
                mention = f"@{member.username}"
            else:
                mention = member.full_name

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"{mention} {welcome}",
            )
            logger.info(f"Приветствие отправлено для пользователя {member.id}")
        except Exception as e:
            logger.error(f"Ошибка отправки приветствия: {e}")


# ============== KEEP-ALIVE ==============
def keep_alive_pinger():
    """Пингование для keep-alive"""
    while bot_running:
        try:
            time.sleep(300)
            if RENDER_URL and RENDER_URL != "YOUR_RENDER_URL_HERE":
                response = httpx.get(f"{RENDER_URL}/health", timeout=30)
                if response.status_code == 200:
                    logger.info(f"Ping successful: {RENDER_URL}/health")
                else:
                    logger.warning(f"Ping returned status {response.status_code}")
        except Exception:
            pass


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    def stop_all():
        global bot_running
        bot_running = False
        if application:
            application.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask запущен на порту 10000")
    
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )
    
    logger.info(f"[INIT] application создан: {application}")
    logger.info(f"[INIT] application.bot: {application.bot}")
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", morning))
    application.add_handler(CommandHandler("stopmorning", stopmorning))
    application.add_handler(CommandHandler("remen", remen))
    application.add_handler(CommandHandler("antiremen", antiremen))
    application.add_handler(CommandHandler("mam", mam))
    application.add_handler(CommandHandler("advice", advice))
    application.add_handler(CommandHandler("summary", summary))
    application.add_handler(CommandHandler("rating", rating))
    application.add_handler(CommandHandler("likes", likes))
    application.add_handler(CommandHandler("levels", levels))
    application.add_handler(CommandHandler("running", running))
    application.add_handler(CommandHandler("garmin", garmin))
    application.add_handler(CommandHandler("garmin_stop", garmin_stop))
    application.add_handler(CommandHandler("garmin_list", garmin_list))
    application.add_handler(CommandHandler("birthday", birthday))
    application.add_handler(CommandHandler("weekly", weekly))
    application.add_handler(CommandHandler("monthly", monthly))
    application.add_handler(CommandHandler("anon", anon))
    application.add_handler(CommandHandler("anonphoto", anonphoto))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_all_messages)
    )
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member)
    )
    
    loop.create_task(morning_scheduler_task())
    loop.create_task(motivation_scheduler_task())
    loop.create_task(daily_summary_scheduler_task())
    
    # Запускаем планировщик кофе в 10:30 по будням
    coffee_thread = threading.Thread(target=lambda: asyncio.run(coffee_scheduler_task()), daemon=True)
    coffee_thread.start()
    logger.info("Планировщик кофе запущен (10:30 будни)")
    
    pinger_thread = threading.Thread(target=keep_alive_pinger, daemon=True)
    pinger_thread.start()
    
    # Инициализация Garmin
    init_garmin_on_startup()
    
    # Инициализация дней рождения
    init_birthdays_on_startup()
    
    # Запускаем планировщик проверки Garmin в отдельном потоке
    import threading
    garmin_thread = threading.Thread(target=lambda: asyncio.run(garmin_scheduler_sync()), daemon=True)
    garmin_thread.start()
    logger.info("Garmin планировщик запущен в отдельном потоке")
    
    # Запускаем планировщик дней рождения
    birthday_thread = threading.Thread(target=lambda: asyncio.run(birthday_scheduler_task()), daemon=True)
    birthday_thread.start()
    logger.info("Планировщик дней рождения запущен")
    
    logger.info("Планировщики запущены")
    
    application.run_polling(drop_pending_updates=True)
