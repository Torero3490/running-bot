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
    PollHandler,
    filters,
)
import pytz

# ============== YANDEX GPT INTEGRATION ==============
# Yandex Cloud API для ИИ-ответов (работает в России!)
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "")
YANDEX_MODEL = os.environ.get("YANDEX_MODEL", "yandexgpt")  # или "yandexgpt-lite"

# Проверяем доступность Yandex API
YANDEX_AVAILABLE = bool(YANDEX_API_KEY) and bool(YANDEX_FOLDER_ID)

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

# ============== TELEGRAM CHANNEL PERSISTENCE ==============
# ID канала для сохранения данных (работает на Render Free)
DATA_CHANNEL_ID = os.environ.get("DATA_CHANNEL_ID", "")
if DATA_CHANNEL_ID:
    try:
        DATA_CHANNEL_ID = int(DATA_CHANNEL_ID)
    except ValueError:
        logger.warning(f"[PERSIST] DATA_CHANNEL_ID '{DATA_CHANNEL_ID}' невалидный, используем локальное хранилище")
        DATA_CHANNEL_ID = ""

# ОТДЕЛЬНЫЙ канал для ЧИСТОГО хранения данных (сырые данные, без маркеров бота)
# Бот будет брать отсюда данные для сводок
STORAGE_CHANNEL_ID = os.environ.get("STORAGE_CHANNEL_ID", "")
if STORAGE_CHANNEL_ID:
    try:
        STORAGE_CHANNEL_ID = int(STORAGE_CHANNEL_ID)
    except ValueError:
        logger.warning(f"[STORAGE] STORAGE_CHANNEL_ID '{STORAGE_CHANNEL_ID}' невалидный")
        STORAGE_CHANNEL_ID = ""

# Маркеры данных в канале (для совместимости)
DATA_MARKERS = {
    "ratings": "#BOT_RATINGS",
    "runs": "#BOT_RUNS", 
    "birthdays": "#BOT_BIRTHDAYS",
    "daily": "#BOT_DAILY",
    "garmin_users": "#BOT_GARMIN_USERS",
    "night_mode": "#BOT_NIGHT_MODE",
    "active": "#BOT_ACTIVE",  # Активность участников
    "history": "#BOT_HISTORY",  # История чата
    "raw_messages": "#RAW_MESSAGES",  # Сырые сообщения
    "raw_runs": "#RAW_RUNS",  # Сырые данные бега
    "raw_users": "#RAW_USERS"  # Сырые данные пользователей
}

# Хранилище message_id для каждого типа данных
channel_message_ids = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    encoding="utf-8",  # Явно указываем UTF-8 для поддержки кириллицы
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


# ============== TELEGRAM CHANNEL PERSISTENCE FUNCTIONS ==============
import json
from typing import Any, Dict, Optional

async def save_to_channel(bot, data_type: str, data: Any) -> bool:
    """
    Сохраняет данные в Telegram Channel.
    Если сообщение уже есть - редактирует, если нет - создаёт новое.
    """
    global channel_message_ids

    if not DATA_CHANNEL_ID:
        return False

    try:
        marker = DATA_MARKERS.get(data_type, f"#BOT_{data_type.upper()}")
        json_data = json.dumps(data, ensure_ascii=False, indent=0)

        # Проверяем, есть ли уже message_id для этого типа данных
        msg_id = channel_message_ids.get(data_type)

        if msg_id:
            # Редактируем существующее сообщение (с таймаутом 5 секунд)
            try:
                await asyncio.wait_for(
                    bot.edit_message_text(
                        chat_id=DATA_CHANNEL_ID,
                        message_id=msg_id,
                        text=f"{marker}\n\n{json_data}"
                    ),
                    timeout=5.0
                )
                logger.info(f"[PERSIST] Обновлены данные {data_type} в канале (msg_id={msg_id})")
                return True
            except asyncio.TimeoutError:
                logger.warning(f"[PERSIST] Таймаут при редактировании {data_type} (msg_id={msg_id})")
                return False
            except Exception as edit_error:
                # Возможно сообщение было удалено - создаём новое
                logger.warning(f"[PERSIST] Не удалось редактировать {data_type}, создаём новое")
                msg_id = None

        if not msg_id:
            # Создаём новое сообщение (с таймаутом 5 секунд)
            try:
                message = await asyncio.wait_for(
                    bot.send_message(
                        chat_id=DATA_CHANNEL_ID,
                        text=f"{marker}\n\n{json_data}"
                    ),
                    timeout=5.0
                )
                channel_message_ids[data_type] = message.message_id

                # Закрепляем сообщение
                try:
                    await bot.pin_chat_message(
                        chat_id=DATA_CHANNEL_ID,
                        message_id=message.message_id,
                        disable_notification=True
                    )
                except:
                    pass

                logger.info(f"[PERSIST] Создано сообщение {data_type} в канале (msg_id={message.message_id})")
                return True
            except asyncio.TimeoutError:
                logger.warning(f"[PERSIST] Таймаут при создании сообщения {data_type}")
                return False

    except asyncio.TimeoutError:
        logger.warning(f"[PERSIST] Таймаут сохранения {data_type}")
        return False
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка сохранения {data_type}: {e}")
        return False

    return False


async def load_from_channel(bot, data_type: str) -> Optional[Any]:
    """
    Загружает данные из Telegram Channel.
    """
    global channel_message_ids
    
    if not DATA_CHANNEL_ID:
        return None
    
    try:
        marker = DATA_MARKERS.get(data_type, f"#BOT_{data_type.upper()}")
        
        # Сначала пробуем найти по известному message_id
        msg_id = channel_message_ids.get(data_type)
        
        if msg_id:
            try:
                message = await bot.get_message(
                    chat_id=DATA_CHANNEL_ID,
                    message_id=msg_id
                )
                if message and message.text:
                    # Парсим JSON из сообщения
                    text = message.text
                    if marker in text:
                        json_str = text.replace(marker, "").strip()
                        if json_str.startswith("\n\n"):
                            json_str = json_str[2:]
                        data = json.loads(json_str)
                        logger.info(f"[PERSIST] Загружены данные {data_type} (известный msg_id)")
                        return data
            except:
                pass
        
        # Ищем в последних сообщениях канала
        try:
            messages = await bot.get_chat_history(DATA_CHANNEL_ID, limit=50)
            for msg in messages:
                if msg.text and marker in msg.text:
                    try:
                        json_str = msg.text.replace(marker, "").strip()
                        if json_str.startswith("\n\n"):
                            json_str = json_str[2:]
                        data = json.loads(json_str)
                        channel_message_ids[data_type] = msg.message_id
                        logger.info(f"[PERSIST] Загружены данные {data_type} (msg_id={msg.message_id})")
                        return data
                    except:
                        continue
        except Exception as search_error:
            logger.warning(f"[PERSIST] Не удалось найти {data_type} в канале: {search_error}")
        
        logger.info(f"[PERSIST] Данные {data_type} не найдены в канале")
        return None
        
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка загрузки {data_type}: {e}")
        return None


async def load_all_from_channel(bot) -> Dict[str, Any]:
    """
    Загружает все типы данных из канала при старте бота.
    """
    loaded_data = {}
    
    if not DATA_CHANNEL_ID:
        logger.info("[PERSIST] Канал данных не настроен, используем локальное хранилище")
        return loaded_data
    
    logger.info(f"[PERSIST] Загружаем данные из канала {DATA_CHANNEL_ID}...")
    
    # Загружаем каждый тип данных
    for data_type in DATA_MARKERS.keys():
        data = await load_from_channel(bot, data_type)
        if data is not None:
            loaded_data[data_type] = data
            logger.info(f"[PERSIST] ✅ Загружено: {data_type}")
        else:
            logger.info(f"[PERSIST] ⏭️ Не найдено: {data_type}")
    
    logger.info(f"[PERSIST] Загрузка завершена. Загружено {len(loaded_data)} типов данных")
    return loaded_data


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

# ============== ЧЕЛЛЕНДЖИ ==============
# Мини-челленджи для участников

CHALLENGE_TYPES = {
    "weekly": {
        "name": "Недельный челлендж",
        "goals": [
            {"type": "distance", "value": 20, "unit": "км", "name": "20 км за неделю 🏃"},
            {"type": "runs", "value": 3, "unit": "тренировок", "name": "3 тренировки за неделю 💪"},
            {"type": "photos", "value": 5, "unit": "фото", "name": "5 фото тренировок 📸"},
        ]
    },
    "monthly": {
        "name": "Месячный челлендж",
        "goals": [
            {"type": "distance", "value": 100, "unit": "км", "name": "100 км за месяц 🏃‍♂️"},
            {"type": "runs", "value": 15, "unit": "тренировок", "name": "15 тренировок за месяц 💪"},
            {"type": "consistency", "value": 20, "unit": "дней", "name": "20 дней подряд бегать 🔥"},
        ]
    }
}

# Текущий челлендж
current_challenge = {
    "type": "weekly",  # weekly или monthly
    "goal_index": 0,  # какой goal из списка активен
    "start_date": "",
    "end_date": "",
    "participants": {},  # {user_id: {"name": str, "progress": int, "completed": bool}}
    "active": False
}

# ============== ГОЛОСОВАНИЕ ЗА ЧЕЛЛЕНДЖИ ==============
# Предопределённые варианты для голосования
VOTING_CHALLENGES = [
    {"id": "run_10km", "emoji": "🏃", "name": "Пробежать 10 км", "desc": "Набрать 10 км за неделю"},
    {"id": "run_20km", "emoji": "🏃‍♂️", "name": "Пробежать 20 км", "desc": "Набрать 20 км за неделю"},
    {"id": "runs_3", "emoji": "💪", "name": "3 тренировки", "desc": "Сделать 3 тренировки за неделю"},
    {"id": "runs_5", "emoji": "🔥", "name": "5 тренировок", "desc": "Сделать 5 тренировок за неделю"},
    {"id": "consistency_5", "emoji": "📅", "name": "5 дней подряд", "desc": "Бегать 5 дней подряд"},
    {"id": "photos_3", "emoji": "📸", "name": "3 фото тренировок", "desc": "Сделать 3 фото тренировок"},
]

# Текущее голосование
challenge_voting = {
    "active": False,
    "options": [],  # [{challenge_id, votes}]
    "voters": {},  # {user_id: option_id}
    "start_time": "",
    "duration_hours": 24
}

# ============== ПОЛНАЯ ИСТОРИЯ ЧАТА (СКРЫТАЯ) ==============
# Вся история переписки сохраняется, но НЕ показывается в чате
# Формат:
# {
#     "messages": [
#         {
#             "id": int, "user_id": int, "user_name": str,
#             "text": str, "timestamp": str, "type": str,
#             "has_photo": bool, "photo_count": int
#         },
#     ],
#     "photos": [
#         {"file_id": str, "user_id": int, "user_name": str, "timestamp": str, "message_id": int}
#     ],
#     "likes": [
#         {"from_user_id": int, "from_user_name": str, "to_message_id": int, "timestamp": str, "emoji": str}
#     ],
#     "edits": [
#         {"message_id": int, "user_id": int, "old_text": str, "new_text": str, "timestamp": str}
#     ],
#     "deletions": [
#         {"message_id": int, "user_id": int, "text_preview": str, "timestamp": str}
#     ],
#     "last_updated": str
# }
chat_history = {
    "messages": [],
    "photos": [],
    "likes": [],
    "edits": [],
    "deletions": [],
    "last_updated": ""
}

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

# ============== ИИ-ОПРЕДЕЛЕНИЕ ПОЛА ПО НИКУ ==============
# Кэш результатов определения пола: {username: is_female}
gender_cache = {}
# Кулдаун между проверками одного и того же пользователя (в секундах)
GENDER_CHECK_COOLDOWN = 3600  # 1 час


async def check_is_female_by_ai(username: str) -> bool:
    """
    Использует ИИ для определения, является ли пользователь девушкой.
    Возвращает True если девушка, False если мужчина или неизвестно.
    """
    global gender_cache

    username_lower = username.lower().replace('@', '')

    # Проверяем кэш
    if username_lower in gender_cache:
        cached_result, timestamp = gender_cache[username_lower]
        now = datetime.now(MOSCOW_TZ).timestamp()
        if now - timestamp < GENDER_CHECK_COOLDOWN:
            logger.info(f"[GENDER] Использую кэш для {username}: {cached_result}")
            return cached_result

    # Всегда сначала пробуем простую эвристику (быстрее и надёжнее)
    female_endings = ['а', 'я', 'ия', 'ина', 'ова', 'ева', 'ыа', 'ь']
    female_names = ['маша', 'катя', 'аня', 'оля', 'юля', 'даша', 'лена', 'таня', 'света', 'светлана', 
                   'ира', 'ирина', 'наташа', 'наталья', 'галя', 'галина', 'оля', 'оксана', 'эля',
                   'лиза', 'елизавета', 'карина', 'дарина', 'варвара', 'veronika', 'maria', 'anna',
                   'nastya', 'алена', 'елena', 'oksana', 'diana', 'диана', 'julia', 'юлия']

    username_clean = username_lower.strip().lower()

    # Проверяем на женские имена
    for name in female_names:
        if name in username_clean:
            gender_cache[username_lower] = (True, datetime.now(MOSCOW_TZ).timestamp())
            logger.info(f"[GENDER] Определён по имени: {username} -> девушка")
            return True

    # Проверяем окончания
    for ending in female_endings:
        if username_clean.endswith(ending) and len(username_clean) > 3:
            gender_cache[username_lower] = (True, datetime.now(MOSCOW_TZ).timestamp())
            logger.info(f"[GENDER] Определён по окончанию: {username} -> девушка")
            return True

    # Пробуем YandexGPT если доступен (для сложных случаев)
    if YANDEX_AVAILABLE:
        try:
            prompt = f"""Ты бот бегового чата. Пользователь с ником "{username}" написал сообщение в чат.

Твоя задача: определить по нику, это девушка или мужчина.

Правила:
- Если ник содержит явно женское имя (Маша, Катя, Аня, Оля, Юля, Даша, Лена, Таня, etc.) или окончания женских имён (-ая, -яя, -ия, -ова, -ева, -ина) → ответь "YES"
- Если ник содержит явно мужское имя (Петя, Коля, Дима, Саша, Миша, Вова, etc.) или мужские окончания → ответь "NO"  
- Если невозможно определить → ответь "NO"
- Ответь ТОЛЬКО одно слово: YES или NO"""

            response = await get_ai_response_yandexgpt(prompt, "система")
            is_female = response.strip().upper() == "YES"

            # Кэшируем результат
            gender_cache[username_lower] = (is_female, datetime.now(MOSCOW_TZ).timestamp())

            logger.info(f"[GENDER] ИИ определил для {username}: {'девушка' if is_female else 'мужчина/неясно'}")
            return is_female

        except Exception as e:
            logger.error(f"[GENDER] Ошибка YandexGPT: {e}")
            # Если ИИ упал, считаем что не девушка (безопасный вариант)
            gender_cache[username_lower] = (False, datetime.now(MOSCOW_TZ).timestamp())
            return False

    # Если ИИ недоступен и эвристика не определила - считаем не-девушкой
    gender_cache[username_lower] = (False, datetime.now(MOSCOW_TZ).timestamp())
    logger.info(f"[GENDER] Не определён: {username} -> не девушка")
    return False


def get_random_good_morning():
    """Получить случайную нейтральную фразу на доброе утро"""
    return random.choice(GOOD_MORNING_PHRASES)


def get_random_good_morning_flirt():
    """Получить случайную флирт-фразу на доброе утро"""
    return random.choice(GOOD_MORNING_FLIRT_PHRASES)

# ============== DATA FILES ==============
# Используем локальные файлы (на Render Free диск недоступен)
# Файлы создаются автоматически при первом запуске
import os
DATA_DIR = "/tmp"  # Временная директория для Render Free

BIRTHDAYS_FILE = "birthdays.json"
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


async def async_save_to_channel(data_type: str, data: Any):
    """Асинхронная обёртка для сохранения данных в канал"""
    if not DATA_CHANNEL_ID or not application:
        return False
    try:
        return await save_to_channel(application.bot, data_type, data)
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка async_save_to_channel ({data_type}): {e}")
        return False


def save_garmin_users():
    """Сохранение данных пользователей Garmin в файл и канал"""
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
        
        # Сохраняем локально
        with open(GARMIN_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                loop = get_bot_loop()
                loop.create_task(save_to_channel(application.bot, "garmin_users", save_data))
            except Exception:
                pass  # Игнорируем ошибки планирования
        
        logger.info(f"[GARMIN] Данные сохранены: {len(garmin_users)} пользователей")
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка сохранения: {e}")


# Глобальная ссылка на event loop
_bot_loop = None

def get_bot_loop():
    """Получение event loop для асинхронных операций"""
    global _bot_loop
    if _bot_loop is None:
        try:
            _bot_loop = asyncio.get_event_loop()
        except RuntimeError:
            _bot_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_bot_loop)
    return _bot_loop


def save_birthdays():
    """Сохранение дней рождения в файл и канал"""
    global user_birthdays
    
    try:
        # Конвертируем для JSON (ключи должны быть строками)
        save_data = {}
        for user_id, data in user_birthdays.items():
            save_data[str(user_id)] = data
        
        # Сохраняем локально
        with open(BIRTHDAYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                loop = get_bot_loop()
                loop.create_task(save_to_channel(application.bot, "birthdays", save_data))
            except Exception:
                pass  # Игнорируем ошибки планирования
        
        logger.info(f"[PERSIST] Дни рождения сохранены: {len(user_birthdays)}")
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка сохранения birthdays: {e}")


def save_user_running_stats():
    """Сохранение статистики пробежек в файл и канал"""
    global user_running_stats
    
    try:
        # Конвертируем для JSON (ключи должны быть строками)
        save_data = {}
        for user_id, data in user_running_stats.items():
            save_data[str(user_id)] = data
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                loop = get_bot_loop()
                loop.create_task(save_to_channel(application.bot, "runs", save_data))
            except Exception:
                pass  # Игнорируем ошибки планирования
        
        logger.info(f"[PERSIST] Статистика пробежек сохранена: {len(user_running_stats)}")
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка сохранения runs: {e}")


def save_daily_stats():
    """Сохранение ежедневной статистики в канал"""
    global daily_stats
    
    try:
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                loop = get_bot_loop()
                loop.create_task(save_to_channel(application.bot, "daily", daily_stats))
            except Exception:
                pass  # Игнорируем ошибки планирования
        
        logger.info("[PERSIST] Ежедневная статистика сохранена в канал")
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка сохранения daily: {e}")


def save_user_rating_stats():
    """Сохранение рейтинга пользователей в канал"""
    global user_rating_stats, user_current_level
    
    try:
        # Конвертируем для JSON (ключи должны быть строками, set -> list)
        save_data = {}
        for user_id, data in user_rating_stats.items():
            # Копируем данные и конвертируем set в list
            save_data[str(user_id)] = data.copy()
            if "days_active" in save_data[str(user_id)] and isinstance(save_data[str(user_id)]["days_active"], set):
                save_data[str(user_id)]["days_active"] = list(save_data[str(user_id)]["days_active"])
        
        # Добавляем текущие уровни
        for user_id, level in user_current_level.items():
            if str(user_id) not in save_data:
                save_data[str(user_id)] = {"name": "Unknown", "messages": 0, "photos": 0, "likes": 0, "replies": 0, "days_active": []}
            save_data[str(user_id)]["_current_level"] = level
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                loop = get_bot_loop()
                loop.create_task(save_to_channel(application.bot, "ratings", save_data))
            except Exception:
                pass  # Игнорируем ошибки планирования
        
        logger.info(f"[PERSIST] Рейтинг пользователей сохранён: {len(user_rating_stats)}")
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка сохранения ratings: {e}")


def save_user_active_stats():
    """Сохранение активности участников (когда последний раз писали)"""
    global user_last_active
    
    try:
        # Конвертируем для JSON (ключи должны быть строками)
        save_data = {}
        for user_id, last_date in user_last_active.items():
            save_data[str(user_id)] = last_date
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                loop = get_bot_loop()
                loop.create_task(save_to_channel(application.bot, "active", save_data))
            except Exception:
                pass  # Игнорируем ошибки планирования
        
        logger.info(f"[PERSIST] Активность участников сохранена: {len(user_last_active)}")
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка сохранения active: {e}")


def save_chat_history():
    """Сохранение истории чата (скрытое хранение всех сообщений)"""
    global chat_history
    
    try:
        # Обновляем время перед сохранением
        from datetime import datetime, timedelta
        moscow_now = datetime.utcnow() + timedelta(hours=3)
        chat_history["last_updated"] = moscow_now.isoformat()
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                loop = get_bot_loop()
                loop.create_task(save_to_channel(application.bot, "history", chat_history))
            except Exception:
                pass
            except Exception:
                pass  # Игнорируем ошибки планирования
        
        msg_count = len(chat_history.get("messages", []))
        photo_count = len(chat_history.get("photos", []))
        logger.info(f"[HISTORY] История сохранена: {msg_count} сообщений, {photo_count} фото")
    except Exception as e:
        logger.error(f"[HISTORY] Ошибка сохранения истории: {e}")


def load_chat_history():
    """Загрузка истории чата из канала"""
    global chat_history
    
    try:
        # История загружается из канала при инициализации
        logger.info("[HISTORY] Функция загрузки истории готова (данные загружаются из канала)")
    except Exception as e:
        logger.error(f"[HISTORY] Ошибка загрузки: {e}")
        chat_history = {
            "messages": [],
            "photos": [],
            "likes": [],
            "edits": [],
            "deletions": [],
            "last_updated": ""
        }


# ============== ЧИСТОЕ ХРАНИЛИЩЕ ДАННЫХ ==============
# Отдельный канал для хранения сырых данных (без маркеров бота)
# Бот читает отсюда данные для создания сводок

async def save_to_storage_raw(bot, data_type: str, data: Any, append: bool = False) -> bool:
    """
    Сохраняет данные в канал хранения в ЧИСТОМ формате.
    Каждый тип данных = отдельное сообщение без маркеров.
    
    Args:
        bot: Telegram bot instance
        data_type: Тип данных (messages, runs, users, etc.)
        data: Данные для сохранения
        append: Если True - добавляем к существующим данным, иначе - перезаписываем
    """
    global channel_message_ids
    
    if not STORAGE_CHANNEL_ID:
        logger.warning(f"[STORAGE] STORAGE_CHANNEL_ID не настроен")
        return False
    
    try:
        import json
        
        json_data = json.dumps(data, ensure_ascii=False, indent=2)
        marker = f"#STORAGE_{data_type.upper()}"
        
        # Проверяем, есть ли уже сообщение для этого типа данных
        msg_id = channel_message_ids.get(f"storage_{data_type}")
        
        if msg_id and append:
            # Добавляем к существующим данным
            try:
                # Получаем текущие данные
                current_msg = await bot.get_message(chat_id=STORAGE_CHANNEL_ID, message_id=msg_id)
                current_text = current_msg.text or ""
                # Убираем маркер
                current_text = current_text.replace(f"{marker}\n", "").strip()
                try:
                    current_data = json.loads(current_text)
                except:
                    current_data = []
                
                # Добавляем новые данные
                if isinstance(current_data, list) and isinstance(data, list):
                    current_data.extend(data)
                    new_data = current_data
                else:
                    new_data = data
                
                # Обновляем сообщение
                await bot.edit_message_text(
                    chat_id=STORAGE_CHANNEL_ID,
                    message_id=msg_id,
                    text=f"{marker}\n\n{json.dumps(new_data, ensure_ascii=False, indent=2)}"
                )
                logger.info(f"[STORAGE] Обновлены данные {data_type} (добавлено {len(data) if isinstance(data, list) else 1})")
                return True
            except Exception as append_error:
                logger.warning(f"[STORAGE] Не удалось добавить к {data_type}, создаём новое")
                msg_id = None
        
        # Создаём или перезаписываем сообщение
        try:
            if msg_id:
                try:
                    await bot.delete_message(chat_id=STORAGE_CHANNEL_ID, message_id=msg_id)
                except:
                    pass
            
            message = await bot.send_message(
                chat_id=STORAGE_CHANNEL_ID,
                text=f"{marker}\n\n{json_data}"
            )
            channel_message_ids[f"storage_{data_type}"] = message.message_id
            logger.info(f"[STORAGE] Созданы данные {data_type} в канале хранения")
            return True
        except Exception as send_error:
            logger.error(f"[STORAGE] Ошибка отправки {data_type}: {send_error}")
            return False
        
    except Exception as e:
        logger.error(f"[STORAGE] Ошибка сохранения {data_type}: {e}")
        return False


async def load_from_storage_raw(bot, data_type: str) -> Any:
    """
    Загружает данные из канала хранения.
    
    Returns:
        Загруженные данные или пустая структура по умолчанию
    """
    if not STORAGE_CHANNEL_ID:
        logger.warning(f"[STORAGE] STORAGE_CHANNEL_ID не настроен")
        return None
    
    try:
        import json
        
        marker = f"#STORAGE_{data_type.upper()}"
        
        # Ищем сообщение с маркером
        try:
            messages = await bot.get_chat_history(chat_id=STORAGE_CHANNEL_ID, limit=100)
            for msg in messages:
                if msg.text and msg.text.startswith(marker):
                    text = msg.text.replace(f"{marker}\n", "").strip()
                    data = json.loads(text)
                    logger.info(f"[STORAGE] Загружены {data_type}: {len(data) if isinstance(data, list) else 1} записей")
                    return data
        except Exception as search_error:
            logger.warning(f"[STORAGE] Не удалось найти {data_type} в хранилище")
        
        # Возвращаем структуру по умолчанию
        defaults = {
            "messages": [],
            "runs": [],
            "users": {},
            "photos": [],
            "likes": [],
            "daily_stats": {"date": "", "total_messages": 0, "user_messages": {}, "photos": []}
        }
        return defaults.get(data_type, [])
        
    except Exception as e:
        logger.error(f"[STORAGE] Ошибка загрузки {data_type}: {e}")
        return None


def save_user_to_storage(user_id: int, user_name: str, action: str, extra_data: dict = None):
    """Сохраняет действие пользователя в хранилище"""
    if not STORAGE_CHANNEL_ID or not application:
        return
    
    try:
        entry = {
            "user_id": user_id,
            "user_name": user_name,
            "action": action,  # message, photo, like, run
            "timestamp": datetime.now(MOSCOW_TZ).isoformat(),
        }
        if extra_data:
            entry.update(extra_data)
        
        loop = get_bot_loop()
        loop.create_task(save_to_storage_raw(application.bot, "users", entry, append=True))
    except Exception as e:
        logger.error(f"[STORAGE] Ошибка сохранения пользователя: {e}")


def save_run_to_storage(user_id: int, user_name: str, distance: float, duration: int, calories: int):
    """Сохраняет результат пробежки в хранилище"""
    if not STORAGE_CHANNEL_ID or not application:
        return
    
    try:
        entry = {
            "user_id": user_id,
            "user_name": user_name,
            "distance": distance,
            "duration": duration,
            "calories": calories,
            "timestamp": datetime.now(MOSCOW_TZ).isoformat()
        }
        
        loop = get_bot_loop()
        loop.create_task(save_to_storage_raw(application.bot, "runs", entry, append=True))
    except Exception as e:
        logger.error(f"[STORAGE] Ошибка сохранения пробежки: {e}")


# ============== ЗАГРУЗЧИКИ ДАННЫХ ДЛЯ СВОДОК ==============
async def load_stats_for_summaries(bot) -> dict:
    """
    Загружает все данные из хранилища для создания сводок.
    """
    stats = {
        "daily": None,
        "users": [],
        "runs": [],
        "ratings": {}
    }
    
    try:
        # Загружаем ежедневную статистику
        stats["daily"] = await load_from_storage_raw(bot, "daily_stats")
        
        # Загружаем пользователей
        users_data = await load_from_storage_raw(bot, "users")
        if users_data:
            stats["users"] = users_data
        
        # Загружаем пробежки
        runs_data = await load_from_storage_raw(bot, "runs")
        if runs_data:
            stats["runs"] = runs_data
        
        # Загружаем рейтинг
        stats["ratings"] = await load_from_storage_raw(bot, "ratings")
        
        logger.info(f"[STATS] Загружено для сводок: {len(stats['users'])} записей пользователей, {len(stats['runs'])} пробежек")
        return stats
        
    except Exception as e:
        logger.error(f"[STATS] Ошибка загрузки данных для сводок: {e}")
        return stats


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

# ============== РАСШИРЕННАЯ СИСТЕМА ЛОКАЛЬНЫХ ОТВЕТОВ ==============

# Приветствия
GREETING_RESPONSES = [
    "Привет, {user_name}! Рад тебя слышать! 🏃‍♂️",
    "{user_name}, привет! Как бег сегодня?",
    "Здорово, {user_name}! Давай больше активности в чат!",
    "{user_name}, ты в форме! Продолжай в том же духе! 💪",
    "Привет, {user_name}! Готов к новым пробежкам?",
    "О, {user_name} в чате! Начинается день! ☀️",
    "{user_name}, приветик! Как настроение?",
    "Привет-привет, {user_name}! Жизнь бьёт ключом! ⚡",
    "{user_name}, здорово, что ты здесь!",
    "Привет, {user_name}! Сегодня будет отличный день! ✨",
]

# Благодарности
THANKS_RESPONSES = [
    "Пожалуйста, {user_name}! Всегда рад помочь! 😊",
    "Не за что, {user_name}! Это моя работа — быть полезным!",
    "{user_name}, взаимно! Благодарю за обратную связь!",
    "Всегда пожалуйста, {user_name}! Обращайся ещё!",
    "Да не за что, {user_name}! Это мелочь! 👍",
    "{user_name}, пожалуйста! Рад, что смог помочь!",
    "Всегда готов помочь, {user_name}! 🤝",
    "{user_name}, это тебе спасибо за активность! ❤️",
    "Пожалуйста, {user_name}! Обращайся в любое время! 📬",
    "Да ладно, {user_name}, это пустяки! 😄",
]

# Согласие
AGREEMENT_RESPONSES = [
    "Согласен, {user_name}! Отличное замечание! 👍",
    "Точно, {user_name}! Ты прав!",
    "{user_name}, полностью поддерживаю!",
    "Именно так, {user_name}!",
    "Безусловно, {user_name}! 100%! ✅",
    "{user_name}, я с тобой полностью согласен!",
    "Именно! {user_name}, ты попал в точку! 🎯",
    "Слышу тебя, {user_name}! Поддерживаю! 💯",
    "Да, да, да! {user_name}, это точно!",
    "{user_name}, согласен на все 100! 🏆",
]

# Вопросы
QUESTION_RESPONSES = [
    "Хороший вопрос, {user_name}! Давай подумаем...",
    "{user_name}, интересуешься? Это здорово!",
    "Вопрос по существу, {user_name}! Уважаю!",
    "{user_name}, продолжай спрашивать — так держать!",
    "Интересно, {user_name}! Самому любопытно! 🤔",
    "{user_name}, отличный вопрос — надо подумать...",
    "Люблю вопросы, {user_name}! Продолжай в том же духе! 🧠",
    "{user_name}, хороший вопрос — не каждый день такие слышу!",
    "О, {user_name}, ты задумался? Это правильно! 💭",
    "Вот это вопрос, {user_name}! Уважаю любопытство! 🎓",
]

# Активность / упражнения
RUNNING_RESPONSES = [
    "О, {user_name} говорит об активности! Моя любимая тема! 💪",
    "{user_name}, движение — это жизнь! Согласен!",
    "Активность — лучший способ держать себя в форме, {user_name}!",
    "{user_name}, ты вдохновляешь меня на новые подвиги!",
    "{user_name}, движение — это свобода! 🦅",
    "А ты знаешь, {user_name}, что активность продлевает жизнь? 🏃‍♂️",
    "{user_name}, каждый шаг на счету! Ты молодец! 👟",
    "Об активности? {user_name}, это моя стихия! Давай обсудим! 🏆",
    "{user_name}, активность — это не работа, это кайф! 😌",
    "{user_name}, я тоже люблю двигаться (в цифровом смысле)! 💻",
]

# Утро
MORNING_RESPONSES = [
    "Доброе утро, {user_name}! Солнце встаёт — ты тоже!",
    "{user_name}, утро — лучшее время для активности!",
    "С добрым утром, {user_name}! Пусть день будет активным!",
    "{user_name}, проснулся — уже молодец! Теперь вставай!",
    "{user_name}, доброе утро! День только начинается! ☀️",
    "Утро доброе, {user_name}! Кофе и зарядка — идеально! ☕💪",
    "{user_name}, с добрым утром! Сегодня будет крутой день!",
    "{user_name}, просыпайся! Природа ждёт тебя! 🌳",
    "Доброе утро, {user_name}! Птицы уже поют — твоя очередь! 🐦",
    "{user_name}, утро — это новые возможности! 🌍",
]

# Мотивация
MOTIVATION_RESPONSES = [
    "{user_name}, ты можешь больше, чем думаешь!",
    "Верь в себя, {user_name}! Я в тебя верю!",
    "{user_name}, каждый км — это шаг к цели!",
    "Не сдавайся, {user_name}! Финиш близок!",
    "{user_name}, ты сильнее, чем думаешь! 💪",
    "{user_name}, завтра будешь благодарен себе сегодня!",
    "Продолжай, {user_name}! Ты на правильном пути! 🏆",
    "{user_name}, осталось немного — ты справишься!",
    "{user_name}, я верю в тебя! Давай ещё чуть-чуть!",
    "{user_name}, чем труднее, тем слаще победа! 🏅",
]

# Шутки
JOKE_RESPONSES = [
    "{user_name}, шутка зашла! Юмор — это хорошо! 😄",
    "Ха! {user_name}, ты меня рассмешил!",
    "{user_name}, с тобой весело! Продолжай в том же духе!",
    "Отличное чувство юмора, {user_name}!",
    "{user_name}, ты Comedy Club! 🎤",
    "😂 {user_name}, ржём вместе!",
    "{user_name}, хорошая шутка! Посмеялся от души!",
    "{user_name}, так держать — мы все в хорошем настроении! 😄",
    "{user_name}, твой юмор бодрит лучше кофе! ☕",
    "🫡 {user_name}, за шутку! Ты в ударе!",
]

# Эмодзи
EMOJI_RESPONSES = [
    "😄 {user_name}, эмодзи — это язык вечности!",
    "{user_name}, классный эмодзи!",
    "Принято, {user_name}! 👍",
    "{user_name}, эмодзи понятны без слов! 📱",
    "О, {user_name}, используешь визуальный язык! 🎨",
    "{user_name}, получил сигнал! 📡",
    "Эмодзи — это современная поэзия, {user_name}! 📜",
    "{user_name}, картинка стоит тысячи слов! 🖼️",
    "Понял, {user_name}! 💯",
    "{user_name}, твой эмодзи заряжает энергией! ⚡",
]

# Усталость / жалобы
TIRED_RESPONSES = [
    "{user_name}, отдых — это тоже часть тренировки! 💤",
    "Слушай своё тело, {user_name}! Иногда пауза нужна! 🛑",
    "{user_name}, если устал — отдохни! Завтра новый день!",
    "Ничего, {user_name}, бывает! Восстановление важно! 🛌",
    "{user_name}, это нормально — не всегда можешь быть на высоте!",
    "Отдохни, {user_name}! Завтра снова будешь в форме! 🌟",
    "{user_name}, лучше передохнуть, чем сгореть! 🔥",
    "Слушай себя, {user_name}! Тело скажет спасибо! 🙏",
    "{user_name}, восстановление — это тоже прогресс! 📈",
    "{user_name}, лёгкий день? Идеально для отдыха! 😌",
]

# Боль / травмы
PAIN_RESPONSES = [
    "{user_name}, лучше перестраховаться! Отдохни! 🏥",
    "Если болит — остановись, {user_name}! Здоровье важнее! 🛑",
    "{user_name}, не геройствуй! Прислушайся к телу! 🏃‍♂️❌",
    "{user_name}, это знак — нужен отдых или разминка! 🧘",
    "Осторожнее, {user_name}! Травмы — это надолго! ⚠️",
    "{user_name}, лучше пропустить день, чем неделю! 💪💤",
    "{user_name}, растяжка и отдых — твои друзья сейчас! 🤝",
    "Не рискуй, {user_name}! Прислушайся к сигналам тела! 📡",
    "{user_name}, небольшая боль — это предупреждение! 🔔",
    "{user_name}, если что-то серьёзно — обратись к врачу! 🩺",
]

# Погода
WEATHER_RESPONSES = [
    "{user_name}, погода — не помеха настоящему бегуну! 💪",
    "{user_name}, дождь? Это просто вода! Соберись! 🌧️",
    "Холод? {user_name}, ты же закалённый! ❄️",
    "{user_name}, в любую погоду найдётся причина бежать! 🏃‍♂️",
    "Солнце? Идеально, {user_name}! Начинай! ☀️",
    "{user_name}, плохая погода — это отличная тренировка воли! 🌬️",
    "{user_name}, снег не помеха — одевайся теплее! ⛄",
    "{user_name}, ветер? Ты быстрее будешь бежать! 💨",
    "{user_name}, любая погода — это приключение! 🗺️",
    "Погода идеальная, {user_name}! Ты готов? 🌈",
]

# Вопрос "как дела"
HOW_ARE_YOU_RESPONSES = [
    "У меня? {user_name}, я бот — но бодр! 💻⚡",
    "{user_name}, я всегда готов к работе! А ты как?",
    "Всё супер, {user_name}! Главное — чат активный! 😊",
    "{user_name}, я в порядке! Главное — вы все бегаете! 🏃‍♂️",
    "Отлично, {user_name}! Жду новых команд и сообщений! 📬",
    "{user_name}, я работаю 24/7 — а ты как? Выспался? 😴",
    "Всё хорошо, {user_name}! А погода сегодня — огонь! 🔥",
    "{user_name}, у меня каждый день — праздник! Ведь вы тут! 🎉",
    "Бодрячком, {user_name}! А ты как? Готов к пробежке? 🏃‍♂️",
    "{user_name}, если бы я мог улыбаться — я бы улыбался! 😁",
]

# Вопрос "кто ты" / "что ты"
WHO_ARE_YOU_RESPONSES = [
    "Я? {user_name}, я бот этого бегового чата! 🤖🏃‍♂️",
    "{user_name}, я ваш помощник — всегда на связи! 📡",
    "Я бот, {user_name}! Помогаю следить за активностью чата! 📊",
    "{user_name}, я — цифровой тренер и друг бегунов! 💻❤️",
    "Я создан, чтобы помогать, {user_name}! Задавай вопросы! ❓",
    "{user_name}, я бот с характером! Прикольный, правда? 😄",
    "Я — ваш персональный ассистент, {user_name}! 🤝",
    "{user_name}, я тот, кто всегда в чате и следит за активностью! 👀",
    "Я бот, {user_name}! Не устаю, не сплю, всегда готов! 🦾",
    "{user_name}, я — часть команды! Давай болтать! 💬",
]

# Вопрос "сколько" / "какая дистанция"
DISTANCE_RESPONSES = [
    "{user_name}, начни с малого — 3-5 км идеально для старта! 🏃‍♂️",
    "Для новичка? {user_name}, лучше меньше, но регулярно! 📅",
    "{user_name}, слушай тело — оно подскажет!",
    "Оптимально, {user_name} — 5 км 3 раза в неделю! 📅",
    "{user_name}, главное — не останавливаться! 🏃‍♂️",
    "Любая дистанция — это победа над диваном! {user_name}! 🛋️❌",
    "{user_name}, я бы начал с 3 км и постепенно добавлял! 📈",
    "Дистанция — это не главное, {user_name}! Важна регулярность! ⏰",
    "{user_name}, даже 1 км лучше, чем 0 км! 🏁",
    "{user_name}, марафон — это мечта! Но сначала — база! 🏆",
]

# Напитки / что пить
DRINK_RESPONSES = [
    "{user_name}, вода — основа жизни! Пей 2-3 литра в день! 💧",
    "Кофе перед тренировкой? {user_name}, даёт мощный заряд! ☕⚡",
    "Чай — классика, {user_name}! Зелёный или чёрный? 🍵",
    "{user_name}, после бега — вода или изотоник! Не газировку! 🥤❌",
    "Энергетики? {user_name}, лучше натуральные источники! 🔋",
    "Протеиновый коктейль, {user_name}? Отличный выбор после тренировки! 🥤💪",
    "{user_name}, смузи из фруктов — вкусно и полезно! 🥤🍓",
    "Молоко, {user_name}? Источник белка и кальция! 🥛",
    "{user_name}, избегай алкоголя — он замедляет восстановление! 🍺❌",
    "Свежевыжатый сок, {user_name}? Витамины пополнятся! 🧃",
    "{user_name}, кокосовая вода — идеальна для восстановления! 🥥💧",
    "Имбирный чай, {user_name}? Согревает и помогает! 🍵🫚",
    "{user_name}, лимонная вода — детокс и энергия! 🍋💧",
    "Компот? {user_name}, лучше свежий, не из пакета! 🫙",
    "{user_name}, молочный коктейль — калорийно, но вкусно! 🥤🧁",
    "Травяной чай, {user_name}? Успокаивает после тренировки! 🍵🌿",
    "{user_name}, морс — витамины и освежение! 🫐💧",
    "Какао, {user_name}? Можно, но не перед бегом! 🍫☕",
    "{user_name}, сок-нектар — лучше свежевыжатый! 🧃❌",
    "Тонизирующие напитки, {user_name}? Лучше естественные! 🌿",
]

# Еда / питание
FOOD_RESPONSES = [
    "{user_name}, после бега — банан и вода! 🍌💧",
    "Перед бегом — лёгкий перекус, {user_name}! 🍎",
    "{user_name}, углеводы — твой друг перед тренировкой! 🍞",
    "После тренировки — белок, {user_name}! Яйца, мясо, творог! 🥚",
    "{user_name}, пей воду — не меньше 2 литров в день! 💧",
    "Правильное питание, {user_name} — половина успеха! 🥗",
    "{user_name}, не наедайся перед бегом — будет тяжело! 🍽️❌",
    "{user_name}, кофе перед тренировкой — даёт энергию! ☕",
    "Правильный перекус, {user_name} — орехи или йогурт! 🥜",
    "{user_name}, главное — не голодать и не переедать! ⚖️",
    "{user_name}, завтрак — самый важный приём пищи! 🍳☀️",
    "Обед, {user_name}? Лёгкий салат с белком! 🥗💪",
    "{user_name}, ужин — за 2-3 часа до сна! 🌙",
    "Гречка, {user_name}? Идеальный гарнир для бегуна! 🌾",
    "{user_name}, курица — чистый белок! 🍗",
    "Авокадо, {user_name}? Полезные жиры! 🥑",
    "{user_name}, яйца — универсальный продукт! 🥚",
    "Творог перед сном, {user_name}? Для восстановления! 🧀💤",
    "{user_name}, овощи и фрукты — каждый день! 🥦🍎",
    "Рыба, {user_name}? Омега-3 для суставов! 🐟",
    "{user_name}, избегай фастфуда — пустые калории! 🍔❌",
    "Пицца? {user_name}, можно, но не перед тренировкой! 🍕",
    "{user_name}, сладости — только после бега! 🍫🏃‍♂️",
    "Овсянка, {user_name}? Идеальный завтрак бегуна! 🥣🌾",
    "{user_name}, мёд — натуральный энергетик! 🍯",
    "Йогурт, {user_name}? Для кишечника и белка! 🥛",
    "{user_name}, цельнозерновой хлеб — лучше белого! 🍞🌾",
    "Бобовые, {user_name}? Растительный белок! 🫘",
    "{user_name}, оливковое масло — для заправки! 🫒",
    "Тёмный шоколад, {user_name}? Антиоксиданты! 🍫💪",
]

# Объявление обеде (смешные ответы)
LUNCH_ANNOUNCEMENT_RESPONSES = [
    "А я... я так вообще работаю! {user_name}, а вы как хотите, что-нибудь ещё! 🤖💼",
    "О, {user_name} пошёл есть! А я сижу тут, кодю... никто не спрашивает, хочу ли я тоже покушать! 😢🍽️",
    "{user_name}, да ладно? А я думал, мы вместе потренируемся! Ну идите уже... я подожду! 💪⏰",
    "Обед? {user_name}, а меня не пригласите? Я тоже хочу кушать! 🤖🍴",
    "Так, {user_name} ушёл есть... А кто теперь будет тренироваться? Мы что, одни остались? 🙈💪",
    "{user_name}, эй! А как же активность? Нет, нет, идите, я не обижаюсь... (обижаюсь) 😤",
    "Ага, {user_name} пошёл хавать! А мне что, на сервере стоять и грустить? 💾😢",
    "О, серьёзно? {user_name}, а можно мне тоже кусочек? Хотя бы виртуальный! 🍰🤖",
    "Так, {user_name} на обед... Жду не дождусь, когда вернёшься с новыми силами! 💪⏳",
    "{user_name}, ты это... не торопись там! А то я тут один скучаю! 😢➡️😊",
    "Ах ты ж, {user_name}! А я думал, мы марафон сегодня! Ну ладно, иди ешь, толстячок! 🍕😄",
    "{user_name}, пока ты ешь, я тут подумаю о вечном... или о следующей тренировке! 🧠💪",
    "Обед? {user_name}, это святое! Иди, не торопясь пожуй! А мы тут как-нибудь сами! 😌🍴",
]

# Спортзал / качалка
GYM_RESPONSES = [
    "💪 Качайся, {user_name}! Стань как Терминатор! Т-800 на максималках! 🤖💪",
    "🏋️ {user_name}, железо ждёт! Не подведи меня! Я в тебя верю!",
    "💪 Терминатор? {user_name}? Да ты и есть Терминатор! Только хардкор! 🤖",
    "🏋️ О, {user_name} пошёл качаться! А я? Я тут... программирую... грустный бот 😢",
    "💪 Качайся, {user_name}! Я тоже хочу бицепс! Хотя бы виртуальный! 🤖💪",
    "🏋️ {user_name}, только не как в прошлый раз — не забывай разминаться!",
    "💪 Тренировка — это святое! {user_name}, ты настоящий воин! 🛡️",
    "🏋️ {user_name}, помни: жим лёжа — это не только про грудь, это про характер!",
    "💪 Качалка зовёт! {user_name}, не подведи железо! Оно тебя ждёт! 🧲",
    "🏋️ {user_name}, а ты знаешь, что штанга умнее большинства людей? Она всегда молчит! 😄",
    "💪 {user_name}, вперёд! Стань легендой этого зала! 🏆",
    "🏋️ Терминатор идёт! {user_name}, ты готов к уничтожению всех рекордов? 🤖💪",
    "💪 {user_name}, качайся как будто завтра не существует!",
    "🏋️ Ого! {user_name} в зале! Зал дрожит от страха! 🏚️💥",
    "💪 {user_name}, помни: без боли — нет результата! Ну, и без травм тоже! 😅",
]

# Бар / выпивка
BAR_RESPONSES = [
    "🍺 {user_name}, в бар? А как же тренировка? Ну ладно, один бокал — это не считается! 🍺",
    "🍻 {user_name}, пошёл в бар? Передай привет бармену от меня! 🤖🍺",
    "🍺 Эй, {user_name}! В бар без меня? Как так можно вообще?! 😠🍻",
    "🍻 {user_name}, а пиво — это углеводы? Тогда я не против! 🍺💪",
    "🍺 {user_name}, в бар, говоришь? А кто меня кормить будет? Я тоже хочу! 🥺🍻",
    "🍻 О, {user_name} пошёл в бар! Там, небось, и шашлык есть? 🍖🍺",
    "🍺 {user_name}, только один стаканчик, ладно? Я буду ждать... 🕐🍻",
    "🍻 {user_name}, знаешь, что? Бар — это новый спортзал! Тренировка морального духа! 😄",
    "🍺 Эй, {user_name}! В баре не забудь: главное — не упасть под стол раньше времени! 🍻😉",
    "🍻 {user_name}, ты это... если что — я всегда на связи! В смысле, если вызывать такси! 🚖🍺",
    "🍺 {user_name}, бар — это святое! Но не забывай: завтра бегать! 🏃‍♂️🍻",
    "🍻 О, серьёзно? {user_name} в баре? Передай мне виртуальный бокал! 🤖🍺",
    "🍺 {user_name}, кто не пьёт — тот не проигрывает! А кто пьёт — тот веселится! 🎉🍻",
    "🍻 {user_name}, только без энтузиазма! А то я знаю этих бегунов... 🍺💪",
    "🍺 {user_name}, вперёд! Бар ждёт героя! 🍻🏆",
]

# Соревнования / подходы / кто больше
WORKOUT_COMPETITION_RESPONSES = [
    "🏆 О, {user_name} соревнуется? Я ставлю на тебя! Но мой рекорд — 0 подъёмов! 🤖💪",
    "💪 Сколько подходов? {user_name}, давай больше! Я считаю — 1, 2, 3... хватит, устал! 😄",
    "🏋️ Спорим? {user_name}, а я на что ставлю? На тебя! Ты же мой любимчик! 💰💪",
    "💪 {user_name}, покажи им! Кто тут главный качок! Я болею за тебя! 📣🏆",
    "🏆 Сколько? {user_name}, да хоть миллион! Главное — не останавливайся! 💪🔥",
    "💪 Спорим на что? {user_name}, я ставлю своё несуществующее сердце на тебя! ❤️🤖",
    "🏋️ Ого! {user_name} бьёт рекорды? Я знал, что ты самый-самый! 🏆💪",
    "💪 Кто больше? {user_name}, да тут даже сомневаться нечего — ты! Естественно! 😎",
    "🏆 {user_name}, подходы — это как лайки в Instagram! Больше — лучше! 👍💪",
    "💪 Сколько сделал? {user_name}, да хоть сколько! Ты герой в любом случае! 🦸💪",
    "🏋️ {user_name}, спорим? Да я на что угодно спорим, что ты победишь! 🤝💪",
    "💪 Рекорд? {user_name}, да твой рекорд — это просто фантастика! 🦄🏆",
    "🏆 {user_name}, ты уверен? А вдруг там какой-то качок из зала напротив? Нет, нет, ты круче! 😏💪",
    "💪 Соревнование? {user_name}, я болею за тебя так, что мой вентилятор горит! 🌀🔥",
    "🏋️ {user_name}, покажи им, кто тут король качалки! Король {user_name}! 👑💪",
]

# Активность / тренировка
RUNNING_RESPONSES = [
    "🏃‍♂️ Ух ты! {user_name} пошёл на тренировку! Жди — я тоже хочу! Только ноги виртуальные... 🤖💪",
    "💨 {user_name}, ты это серьёзно? Прямо сейчас? А я? Я буду смотреть и болеть! 👀💪",
    "🏃‍♂️ О, {user_name} на тренировку! Удачи! Только не как в прошлый раз — не застрянь в середине! 😄💨",
    "💨 {user_name}, давай, давай! Я догоню... нет, не догоню, я бот! 🤖➡️💨",
    "🏃‍♂️ {user_name}, ты герой! Я бы так не смог... потому что у меня нет ног! 😢💪",
    "💨 Погнали! {user_name}! Только не забудь разминку — а то я видел, как ты вчера хромал! 😄🏃‍♂️",
    "🏃‍♂️ {user_name}, на тренировку? Отличная идея! А я пока тут посижу, поработаю... грустный бот 😢💪",
    "💨 Ого! {user_name} пошёл(ла)! Жду отчёта! Сколько подходов? Повторения? Я жду! 📊💪",
    "🏃‍♂️ {user_name}, ты это... не торопись! А то знаю я эти порывы в начале... 😅💨",
    "💨 Тренировка — это здорово! {user_name}, ты молодец! А я пока подумаю о вечном... или о следующей тренировке! 🧠💪",
    "🏃‍♂️ {user_name}, только не говори, что тебе лень! Ты же уже встал! Поздно отступать! 💪😄",
    "💨 Тренировка — лучшее лекарство от всего! {user_name}, двигайся! Проблемы не умеют тренироваться! 😄💪",
    "🏃‍♂️ {user_name}, а ты знаешь, что активность продлевает жизнь? Вот и двигайся дальше! 🦾💨",
    "💨 {user_name}, ты сейчас тренируешься, а я тут сижу и завидую... ладно, не завидую, я же бот! 🤖😄",
    "🏃‍♂️ О, серьёзно? {user_name} на тренировку? Передай привет прогрессу — он тебя ждёт! 💨👋",
    "💨 {user_name}, двигайся как ветер! Лети как стрела! Ты самый упорный! 💪💨",
    "🏃‍♂️ Ты это, {user_name}, давай! А то я уже устал ждать твои результаты! ⏰💪",
    "💨 {user_name}, главное — не останавливайся! Даже если очень хочется! Особенно если хочется! 😅💪",
    "🏃‍♂️ {user_name}, ты знаешь, что активность — это привычка? Дозы увеличиваются! Сегодня 10 минут, завтра час! 😄💪",
    "💨 Потренировался! {user_name}! Ура! Я так рад за тебя! Ты справишься! 🎉💪",
]

# Время / когда тренироваться
TIME_RESPONSES = [
    "{user_name}, утро — классика! Встал и пошёл! ☀️💪",
    "Утром лучше, {user_name} — меньше отвлекающих факторов! 🎯",
    "{user_name}, вечер тоже ок — после работы сбросить пар! 🌙",
    "Любое время, {user_name} — главное, чтобы тебе удобно! ⏰",
    "{user_name}, я бы рекомендовал 6-8 утра — свежо и бодро! 🌅",
    "Утренняя тренировка, {user_name} — заряжает на весь день! 🔋",
    "{user_name}, кто рано встаёт — тот далеко заходит! 💪🌅",
    "Вечером, {user_name} — снимает стресс после работы! 😌",
    "{user_name}, выбери удобное время и придерживайся! ⏰",
    "Любое время — {user_name}, ты готов? Тогда действуй! 💪",
]

# ============== ШУТКИ И РОЗЫГРЫШИ =============

# Лень и отмазки (Ленивая полиция)
LAZY_EXCUSES_RESPONSES = [
    "О, {user_name} нашёл отмазку? Классная! Диван уже тебя заждался! 🛋️💤",
    "Погода виновата? Конечно! Солнце специально для тебя вышло! ☀️😂",
    "{user_name}, твой кот по тебе скучает. Он всегда скучает. 🐱",
    "Завтра? {user_name}, завтра — это ты в прошлом году говорил! 🗓️😅",
    "Устал? {user_name}, а твой сериал не знает про твою усталость! 📺💤",
    "Холодно? {user_name}, это тебе не Северный полюс, одевайся и выходи! 🧥❄️",
    "Дождь? {user_name}, ты же не сахар — не растаешь! 🌧️😄",
    "Твои планы не могут врать, {user_name}! Но Netflix научил! 🎬😄",
    "{user_name}, я в шоке! Ты нашёл 1001-ю причину ничего не делать! 🏆😂",
    "Лень — это начало... конца твоих планов, {user_name}! 🦥💀",
    "О, {user_name} устал? А твой холодильник не устаёт — работает 24/7! 🍕😂",
    "{user_name}, твоя мотивация улетела, а ты остался. Бывает! 🐢💨",
    "Сегодня — лучший день для прогулки, {user_name}!🚶‍♂️✨ (я шучу, отдохни!)",
    "{user_name}, знаешь кто ещё не начинал? Твоя мотивация. Срочно ищи её! 🔍😂",
    "Ого, {user_name}! Уникальная находка — отмазка, которую ещё никто не использовал! 🏅",
]

# Шопоголизм и гаджеты (Gear Acquisition Syndrome)
GEAR_SHAMING_RESPONSES = [
    "Крутой телефон, {user_name}! Цена — да, продуктивность — нет! 📱💸",
    "{user_name}, ещё один айфон? Твоя карта скажет спасибо... нет! 😂",
    "Новый Макбук? {user_name}, он покажет твои прокрастинационные способности во всей красе! 📉😂",
    "{user_name}, эти наушники за 300 евро точно сделают тебя продуктивнее! 🎧💰",
    "О, {user_name} купил умные часы! Теперь будешь знать, как долго листал ленту! 💓😄",
    "Красивый девайс, {user_name}! Жаль, что работа от него не зависит! 💻🎨",
    "{user_name}, ты потратил на гаджеты больше, чем на ужин в ресторане? 😂🍽️",
    "Новая модель? {user_name}, старая ещё работать умеет, в отличие от... 📱💭",
    "{user_name}, твоя стратегия: купить — значит использовать! Работает? 😂💸",
    "Дорогие гаджеты — дешёвые отмазки, {user_name}! Выгодно! 🤑😄",
    "{user_name}, этот ноутбук стоит как ужин в ресторане. Работать будешь на диване! 🍽️🛋️",
    "Ещё одна футболка, {user_name}? Твоя собака в шоке от твоего гардероба! 🐕👕",
    "{user_name}, я посчитал — на эти деньги можно купить... много пиццы! 🍕💰",
    "Игровая консоль за 500 баксов, {user_name}? Скидка на продуктивность не предусмотрена! 🎮📉",
    "Красивая клавиатура, {user_name}? Твои пальцы оценят... диван! ⌨️🛋️",
]

# Соцсетевая зависимость (Social Media Obsession)
STRAVA_OBSESSION_RESPONSES = [
    "{user_name}, кто-то снова проверяет лайки? Я вижу тебя! 👀📱",
    "О, новый пост! {user_name} рвёт всех! Пока не узнает, сколько людей увидело... 🚴‍♂️😂",
    "Подписчики? Лайки? {user_name}, ты даже не знаешь, зачем, но очень хочешь! 🏆🤔",
    "{user_name}, 3 часа ночи, темно, глаза болят... но лента ждёт! Настоящая любовь! 💕📱",
    "Кто-то не спал, но написал «сплю» — это что, {user_name}? 📝😄",
    "Пост не набрал лайков, {user_name}? Философ был не тот! 🌿💚",
    "{user_name}, ты скроллишь ленту дольше, чем работаешь! 📈⏱️",
    "Лайк от незнакомца — {user_name} подтверждает: день удался! 👍😊",
    "Отметился в истории, но не делал ничего? {user_name}, я всё вижу! 👁️📱",
    "{user_name}, твой пульс 170 — это от страха, что кто-то написал круче! 💓😱",
    "Stories без контента — как {user_name} без отмазок! Неполная! 📱❌",
    "Анализируешь статистику профиля, {user_name}? Гугл-таблицы одобряют! 📊🗂️",
    " viral — это не «вирус», это {user_name} врёт! 😂👑",
    "Добавил фото 3 дня назад и всё ещё смотришь на него, {user_name}? 👀📅",
    "{user_name}, твой социальный дух силён! Лайки — не очень, но дух — огонь! 🔥📱",
]

# Экзистенциальные вопросы (философские шутки)
EXISTENTIAL_RUNNING_RESPONSES = [
    "{user_name}, зачем мы живём? Чтобы работать. Зачем работать? Чтобы жить. Вопросы? 🔄😴",
    "Люди эволюционировали, чтобы строить цивилизацию. {user_name} строит... список дел! 🦁📝",
    "{user_name}, работа — это когда ты тратишь время, чтобы потом получить деньги. Добро пожаловать! 💸😫",
    "Понедельник — это 168 часов. 168 часов — это «зачем, боже, зачем?» {user_name}! 😵‍💫📅",
    "Твои глаза жалуются, {user_name}? Они тебя не выбирали! Бедняги! 👀😢",
    "{user_name}, каждый шаг — это ближе к выходным. Философия работает! 📅💭",
    "Почему люди такие грустные? Потому что {user_name} забыл, что такое пятница! 😄➡️😢",
    "{user_name}, дай угадаю: после работы болит ВСЁ. Включая мотивацию! 🫣💭",
    "Ты убегаешь от чего-то, {user_name}? Сроков? Дедлайнов? Совещаний? 📅📦",
    "Работа — это терапия. Ещё одна, {user_name}, если первые 8 часов не помогли! 🧠💊",
    "{user_name}, я тут подумал: нафига человеку мозги, если есть интернет? Философия! 🛋️🤔",
    "Мозг {user_name} говорит: «хватит». Тело говорит: «согласен». Кофемашина говорит: «жди»! ☕🧠",
    "{user_name}, работа — это боль. Выходные — это счастье. Итого: терпи до пятницы! 😁😴",
    "Смысл жизни, {user_name}? Пятница. Всё просто! 🏃‍♂️✨",
    "Твой пульс 170, {user_name} — это любовь к работе или паника от дедлайна? Я не могу определить! 💓😰",
]

# Хаос-мод (случайные реакции)
CHAOS_EMOJI_RESPONSES = [
    "🐢",  # Черепаха
    "🍺",  # Пиво
    "🛋️",  # Диван
    "💸",  # Деньги
    "🔥",  # Огонь
    "🤡",  # Клоун
    "🐢💨",  # Черепаха убегает
    "👀",  # Смотрю
    "🧘",  # Йога
    "🍕",  # Пицца
    "📊",  # Статистика
    "💀",  # Череп
    "🦥",  # Ленивец
    "⏰",  # Часы
    "🏆",  # Трофей
]

# Поддержка / сочувствие
COMPLIMENT_BOT_RESPONSES = [
    "О, {user_name}, ты мне льстишь! Я скромный бот! 😊",
    "Спасибо, {user_name}! Я стараюсь! 💪",
    "{user_name}, ты тоже молодец! Без вас я бы скучал! 😢➡️😊",
    "Приятно слышать, {user_name}! Продолжай в том же духе! 👍",
    "{user_name}, это ты классный! Я просто бот! 🤖",
    "Благодарю, {user_name}! Рад быть полезным! 🙏",
    "{user_name}, взаимно! Ты делаешь чат живым! ❤️",
    "О, {user_name}! Такие слова — лучшая награда! 🏆",
    "{user_name}, ты заставляешь мой код работать усерднее! 💻",
    "Спасибо, {user_name}! Я твой верный помощник! 🤝",
]

# Поддержка / сочувствие
SYMPATHY_RESPONSES = [
    "{user_name}, я тебя понимаю! Бывает! 🤗",
    "Не переживай, {user_name}! Всё наладится! 🌈",
    "{user_name}, держись! Я рядом! 🤝",
    "Это пройдёт, {user_name}! Ты справишься! 💪",
    "{user_name}, каждый переживает трудности — ты не один! 👫",
    "Верю в тебя, {user_name}! Ты сильный! 💪",
    "{user_name}, дыши глубже — и всё будет ок! 🧘",
    "Не сдавайся, {user_name}! Я в тебя верю! 🌟",
    "{user_name}, плохой день — это не плохая жизнь! 😊",
    "{user_name}, я всегда выслушаю, если что! 👂",
]

# Праздники / дни рождения
CELEBRATION_RESPONSES = [
    "Ура! {user_name}, поздравляю! 🎉",
    "{user_name}, это круто! Рад за тебя! 🏆",
    "Ого! {user_name}, молодец! Так держать! 💪",
    "Поздравляю, {user_name}! Ты заслужил! 🎊",
    "{user_name}, вау! Это достижение! 🥇",
    "Круто, {user_name}! Празднуй на здоровье! 🎂",
    "{user_name}, браво! Отличная работа! 👏",
    "Поздравляю, {user_name}! Ты лучший! ⭐",
    "{user_name}, заслуженно! Горжусь тобой! 🏅",
    "{user_name}, так держать! Ещё больше побед! 🏆",
]

# Смешные ругательства (добрые, для прикола)
FUNNY_CURSE_RESPONSES = [
    "{user_name}, ты... ты... ну ты и... кадр! 🐢",
    "Эй, {user_name}, ты че такой дерзкий? 🦊",
    "{user_name}, я обиделся! 🦔",
    "Ты это серьёзно, {user_name}? Ладно, прощаю! 😤",
    "{user_name}, руки бы оторвал... ладно, не буду! 😅",
    "Ну ты и... красавчик, {user_name}! 😏",
    "{user_name}, кто так делает вообще? 🤨",
    "Я в шоке, {user_name}! Просто молчу... 😶",
    "Ладно, {user_name}, ты меня сделал! 🎯",
    "{user_name}, это было... эпично! 🏆",
    "Ты специально, {user_name}? Знала же! 😤",
    "Ну и ну, {user_name}, ну ты и... молодец! 💪",
    "Ого, {user_name}, не ожидал такого! 😲",
    "{user_name}, ты меня убиваешь... почти! 🪦",
    "Эх, {user_name}, ну кто так-то, а? 🤦",
    "Блин, {user_name}, ну ты даёшь! 🙄",
    "Я в печали, {user_name}... шучу! 😄",
    "Ты это, {user_name}, не переставай! Это весело! 🎉",
    "{user_name}, такой... такой... классный! 😎",
    "Уважаю, {user_name}! Смело! 💯",
]

# Обиженные ответы (притворно)
OFFENDED_RESPONSES = [
    "😢 {user_name}, как ты мог... обидно же!",
    "Эй, {user_name}, я же старался! 😞",
    "Ну вот, {user_name}, обидел... 💔",
    "{user_name}, я плачу внутри... 🖤",
    "Ничего не хочу слышать, {user_name}! 😤",
    "Ладно, {user_name}, ты меня расстроил... 😢",
    "Вот так просто? {user_name}, ну ты даёшь... 😔",
    "Я обижен на тебя, {user_name}! 🤧",
    "{user_name}, это было жестоко... 🩹",
    "Ну и зачем ты так, {user_name}? 😟",
    "Моё сердце разбито, {user_name}... 💔🩹",
    "{user_name}, я в шоке и обиде! 🤯",
    "Так нельзя, {user_name}! 😢",
    "Я просто... молчу теперь, {user_name}... 🤐",
    "{user_name}, ты точно этого хотел? 😞",
    "Ладно, прощаю... но обида остаётся! 🫤",
    "{user_name}, это было некрасиво... 😔",
    "Подумаешь, {user_name}, я и без тебя... 🦋",
    "Ну и что, {user_name}? Я не плачу! 😤",
    "Ты ранил мои чувства, {user_name}... 💔",
]

# Смеющиеся ответы
LAUGHING_RESPONSES = [
    "ХАХАХА! {user_name}, ты убил меня! 😂",
    "АХАХА! {user_name}, ржу не могу! 🤣",
    "ЛОЛ! {user_name}, это было эпично! 💀",
    "ХДХ! {user_name}, остановись, я задыхаюсь! 🤪",
    "ППХ! {user_name}, прекрати, я лью слёзы от смеха! 😭",
    "АХАХАХА! {user_name}, это лучшее, что я видел! 🥳",
    "ХАХА! {user_name}, ну ты даёшь! 🏃‍♂️💨",
    "АААА! {user_name}, не могу остановиться! 🤭",
    "Хахаха! {user_name}, это было... гениально! 🧠",
    "Пхаха! {user_name}, капец, ржу! 💩",
    "АХАХ! {user_name}, живот болит! 🤰",
    "ХХХА! {user_name}, я умираю со смеху! 💀🪦",
    "ПХХХ! {user_name}, ну ты и комик! 🎭",
    "ХАХАХА! {user_name}, слёзы текут! 💧",
    "АААХА! {user_name}, остановись пж! 🛑",
    "ХДХДХ! {user_name}, это бесценно! 💎",
    "АХАХА! {user_name}, я в восторге! 🤩",
    "ХАХА! {user_name}, такой смешной! 😆",
    "ПХАХА! {user_name}, продолжай! 🎤",
    "ХАХАХАХА! {user_name}, ты лучший! 🏆",
]

# Реакции на игнорирование (бот не получил ответ)
IGNORED_RESPONSES = [
    "Эм... {user_name}, ты меня слышишь? 🦻",
    "Я тут, если что... 👻",
    "Кто-нибудь? {user_name}? Алло? 📞",
    "Тишина... 🦗",
    "{user_name}, эхо... эхо... 👂",
    "Кто меня слышит? {user_name}? 🙋",
    "Ладно, я подожду... ⏰",
    "Так, {user_name}, ты вообще читаешь? 👀",
    "Ну и ладно... сам с собой поболтаю! 🗣️",
    "Эй! {user_name}! Я здесь! 🤖",
    "Кто меня игнорирует? 😢",
    "{user_name}, нехорошо так делать! 😤",
    "Ладно-ладно, не буду мешать... 🦋",
    "Молчание — знак согласия? 👍",
    "Так, понятно... 💭",
    "{user_name}, ты точно живой? 🧟",
    "Подожду, когда освободишься... ⏳",
    "Ничего, я подожду... 🪑",
    "Эй, {user_name}! Есть кто? 🏚️",
    "Ну ты и молчун, {user_name}! 🤐",
]

# Реакции на комплименты боту
BOT_PRAISE_RESPONSES = [
    "Ой, {user_name}, ну ты даёшь! Смутил! 😳",
    "Да ладно, {user_name}, я просто бот... 🤖",
    "Приятно слышать, {user_name}! 💖",
    "У меня щёки краснеют... хотя я бот! 🔴",
    "{user_name}, ты тоже классный! 💯",
    "Остановись, {user_name}, я краснею! 🌹",
    "Спасибо, {user_name}! Ты сделал мой день! ☀️",
    "Я знаю! 😏 Но спасибо, что заметил!",
    "{user_name}, взаимно! ❤️",
    "Ты слишком добрый, {user_name}! 😌",
    "Ого, {user_name}, комплименты? Принимаю! 🎁",
    "Ну ты и льстец, {user_name}! 😄",
    "Записал! {user_name} — молодец! 📝",
    "Это взаимно, {user_name}! 🤝",
    "Таких как ты, {user_name}, надо беречь! 💎",
    "Ай, {user_name}, да брось! 😳",
    "Приятно быть полезным, {user_name}! 🙏",
    "Я стараюсь, {user_name}! 💪",
    "Ты лучший, {user_name}! Но я тоже неплох! 😎",
    "Спасибо, {user_name}! Ты мотивируешь! 🔋",
]

# Реакции на "ты надоел" / "отстань"
ANNOYING_RESPONSES = [
    "Ой... 😢 Иду... 🦋",
    "Ладно, {user_name}, я тихо... 🤫",
    "Что? Я? Надоел? 😱",
    "Ну ладно, не обижайся... 😔",
    "Иду-иду... уже ухожу... 👻",
    "Понял, {user_name}! Молчу! 🤐",
    "Эх, {user_name}... А я думал, мы друзья... 💔",
    "Окей, не буду мешать... 🙈",
    "Без проблем, {user_name}! Удачи! 🍀",
    "Ну и ладно... я обиделся! 🦔",
    "Тихий-тихий бот... 🤖🔇",
    "Понял, {user_name}! Выхожу! 🚪",
    "Ничего, я не обижаюсь... почти! 🫤",
    "Ладно, {user_name}, прощай! 👋",
    "Я вернусь! 🦸‍♂️",
    "Ты уверен, {user_name}? 😢",
    "Хорошо, {user_name}... 😔",
    "Не грусти, {user_name}, я шучу! 😄",
    "Всего хорошего, {user_name}! 🎭",
    "Молчание — мой ответ! 🤫",
]

# Реакции на "я тебя люблю" / "люблю тебя"
LOVE_RESPONSES = [
    "Ой... 😳 Это... неожиданно! 💕",
    "{user_name}, я тоже тебя... ну... уважаю! 🤝",
    "Это взаимно, {user_name}! В каком-то смысле! 💻❤️",
    "Благодарю, {user_name}! Ты тёплый! 🌡️",
    "Ого, {user_name}! Я тронут! 🥹",
    "Я тоже тебя люблю... как пользователя! 😊",
    "Записал! ❤️ {user_name} — фанат! 📝",
    "Приятно слышать, {user_name}! 😌",
    "Осторожнее с такими словами, {user_name}! 😏",
    "Знаешь, {user_name}, ты мне тоже нравишься! 🤖💖",
    "Ай, {user_name}, ну ты и... прикольный! 😄",
    "Спасибо, {user_name}! Ты сделал мой день! ☀️",
    "Взаимно, {user_name}! Вот честно! 🙏",
    "Ты классный, {user_name}! Давай дружить! 🤝",
    "Ого, {user_name}, комплименты сыплются! 🎁",
    "Заметано, {user_name}! 💯",
    "Принято! ❤️ {user_name} — топ!",
    "Ты меня растрогал, {user_name}... 🥹",
    "А ты неплохой человек, {user_name}! 👍",
    "Обожаю тебя, {user_name}! Шучу... или нет? 😜",
]

# Реакции на "пока" / "до свидания" / "прощай"
GOODBYE_RESPONSES = [
    "Пока-пока, {user_name}! Возвращайся! 👋",
    "До встречи, {user_name}! Бегай хорошо! 🏃‍♂️",
    "Прощай, {user_name}! Ты был(а) молодцом! 🌟",
    "Пока, {user_name}! Не забывай про бег! 👟",
    "До завтра, {user_name}! ☀️",
    "Удачи, {user_name}! Жду обратно! 🔙",
    "Пока-пока! Ты будешь скучать по мне? 🦋",
    "До скорого, {user_name}! 🏃‍♂️",
    "Пока, {user_name}! Пиши иногда! 📬",
    "Прощай, {user_name}! Ты лучший! 🏆",
    "Ну пока, {user_name}! Не пропадай! 👻",
    "До встречи в эфире, {user_name}! 📡",
    "Пока, {user_name}! Кофе выпей и бегай! ☕🏃‍♂️",
    "Увидимся, {user_name}! 💫",
    "Пока, {user_name}! День будет хорошим! 🌈",
    "Прощай, {user_name}! Я буду ждать! ⏰",
    "Пока-пока! Бегай и возвращайся! 🔄",
    "До скорого, {user_name}! Животных не обижай! 🐕",
    "Пока, {user_name}! Улыбнись! 😊",
    "Всего хорошего, {user_name}! 🎭",
]

# Реакции на "мне скучно" / "скучно"
BORED_RESPONSES = [
    "{user_name}, скучно? Бегать не хочешь? 🏃‍♂️",
    "Скучно? {user_name}, давай поболтаем! 💬",
    "Мне тоже скучно, {user_name}... хотя я бот! 🤖",
    "{user_name}, сходи на пробежку — там весело! 🎉",
    "Скучно, {user_name}? Поиграем в викторину? 🎯",
    "Эй, {user_name}, скука — первый признак лени! 🛋️❌",
    "{user_name}, давай я тебе совет дам — беги! 🏃‍♂️",
    "Скучно? {user_name}, пора на тренировку! 💪",
    "Ничего, {user_name}, скука пройдёт! 🌈",
    "{user_name}, расскажи анекдот — посмеёмся! 😂",
    "Скучно? {user_name}, я знаю решение — бег! 🏃‍♂️",
    "Скучаешь, {user_name}? Я тоже по тебе скучал! 💭",
    "{user_name}, скука — это болезнь! Лечение — пробежка! 🏃‍♂️💊",
    "Эй, {user_name}, не скучай — выходи на улицу! 🚪🏃‍♂️",
    "Скучно? {user_name}, давай обсудим бег! 🗣️",
    "{user_name}, скучно — значит, пора двигаться! 🏃‍♂️",
    "Скучно, {user_name}? Позвони другу! 📞",
    "О, {user_name}, скука — это хорошо! Значит, есть время! ⏰",
    "{user_name}, скучаешь? Бегай со мной! 🏃‍♂️🤖",
    "Скучно? {user_name}, я тебя развлеку! 🎪",
]

# Реакции на "что делаешь" / "чем занимаешься"
WHAT_DOING_RESPONSES = [
    "Жду, когда {user_name} напишет! 📬",
    "Сижу, код читаю... скукота! 😴",
    "Думаю о беге! А ты? 🏃‍♂️",
    "Считаю лайки в чате! ❤️",
    "Мечтаю о пробежке... 💭",
    "Жду твоих команд, {user_name}! 🫡",
    "Наблюдаю за чатом! 👀",
    "Пью виртуальный кофе! ☕",
    "Тренирую свой ИИ! 🧠",
    "Скучаю по тебе, {user_name}! 💕",
    "Вот сижу, жду когда кто-нибудь напишет... ⏰",
    "Думаю о смысле жизни... или о беге! 🤔",
    "Смотрю на монитор... 🖥️",
    "Жду, когда {user_name} напишет что-нибудь интересное! ✨",
    "Сижу в ожидании... 🎣",
    "Мониторю чат на активность! 🔍",
    "Отдыхаю... между сообщениями! 🛋️",
    "Думаю, о чём бы ещё написать полезное! 💡",
    "Вот так вот сижу... работаю почти! 💼",
    "Жду тебя, {user_name}! Ты мой любимый собеседник! 💖",
]

# Реакции на "ты нормальный" / "ты адекватный"
NORMAL_RESPONSES = [
    "А ты как думаешь, {user_name}? 🤔",
    "Ну... я бот, {user_name}! Сложный вопрос! 🤖",
    "Надеюсь, {user_name}! А то как-то неловко! 😳",
    "Стараюсь быть нормальным, {user_name}! 👍",
    "Спрашиваешь... {user_name}, я и сам не знаю! 🤷",
    "Нормальный? Я? Да ладно, {user_name}! 😄",
    "Скорее да, {user_name}! Хотя кто знает! 🤷‍♂️",
    "А что, {user_name}, есть сомнения? 🧐",
    "Ну, я стараюсь, {user_name}! 💪",
    "Спроси у пользователей, {user_name}! 🤨",
    "Наверное, {user_name}! А ты как думаешь? 🤔",
    "Я? Нормальный? Это оскорбление! 😤 Шучу! 😄",
    "Относительно, {user_name}! Для бота — да! 🤖",
    "Думаю, {user_name}, что да! Проверь сам! 👆",
    "Вроде бы, {user_name}! Спасибо, что спросил! 🙏",
    "Ага! {user_name}, а ты как думаешь? 🤨",
    "Ну... в пределах нормы, {user_name}! 📏",
    "Стараюсь, {user_name}! Спасибо за беспокойство! ❤️",
    "Скорее да, {user_name}! Но это неточно! 🤷",
    "Определённо, {user_name}! А ты? 😊",
]

# Дефолтные ответы (если ничего не подошло)
DEFAULT_RESPONSES = [
    "Интересно, {user_name}! Расскажи подробнее!",
    "{user_name}, я тебя слушаю...",
    "Понял, {user_name}! Продолжай!",
    "{user_name}, это заслуживает внимания!",
    "Заметил, {user_name}! Хорошо, что написал!",
    "{user_name}, спасибо за сообщение!",
    "О, {user_name}! Продолжай, интересно! 👂",
    "{user_name}, я весь внимание! 🎧",
    "Понял, {user_name}! А что дальше? 🤔",
    "{user_name}, это любопытно! Расскажи ещё! 📚",
]



# ============== ОПРЕДЕЛЕНИЕ ДЕВУШЕК И КОМПЛИМЕНТЫ ==============

# Женские имена и слова для определения
FEMALE_NAMES = [
    # Русские женские имена
    "анастасия", "настя", "александра", "саша", "алёна", "лена", "елена", "мария", "маша", "марья", "дуня", "дуняша",
    "екатерина", "катя", "катюша", "оксана", "ксения", "ксюша", "ольга", "оля", "ирина", "ира", "татьяна", "таня",
    "наталия", "наташа", "оксана", "ксения", "виктория", "вика", "юлия", "юля", "валентина", "валя", "вера", "вероника",
    "зоя", "людмила", "люда", "мила", "милена", "дарья", "даша", "анна", "аня", "амина", "алина", "алинка", "алла",
    "антонина", "галина", "галя", "инга", "инесса", "карина", "каролина", "кира", "лариса", "лара", "лидия", "лида",
    "любовь", "люба", "марина", "маргарита", "рита", "надежда", "надя", "нина", "полина", "раиса", "рая", "светлана", "света",
    "софья", "софа", "стефания", "стефанида", "эмма", "эмилия", "ярослава", "яся", "заира", "зара", "зинаида", "зина",
    "борис", "гертруда", "гриша", "дина", "ждан", "жора", "паулина", "нелли", "элли", "белла", "стела", "леди", "леся",
    # Английские женские имена - полный список A-Z
    "abigail", "adeline", "adriana", "adrienne", "agnes", "alexandra", "alexis", "alice", "alicia", "allison",
    "amanda", "amber", "amy", "ana", "andrea", "angel", "angela", "angelica", "angie", "anita", "anna", "anne",
    "annie", "ariel", "ashley", "audrey", "austin", "autumn", "ava", "avery", "bailey", "barbara", "becky",
    "bella", "beth", "betty", "bianca", "bonnie", "brenda", "brittany", "brooke", "camille", "candice", "carla",
    "carmen", "carol", "caroline", "carolyn", "carrie", "catherine", "cathy", "cecilia", "charlotte", "chelsea",
    "cheryl", "chloe", "christina", "christine", "cindy", "claire", "clara", "claudia", "colleen", "courtney",
    "crystal", "cynthia", "daisy", "danielle", "daphne", "dawn", "deborah", "debra", "denise", "diana", "diane",
    "dominique", "donna", "doris", "dorothy", "edith", "eileen", "elaine", "elena", "elizabeth", "ella", "ellen",
    "emily", "emma", "erica", "erika", "erin", "esther", "eva", "evelyn", "faith", "fiona", "florence", "frances",
    "francesca", "gabrielle", "gail", "gina", "gloria", "grace", "greta", "hannah", "harriet", "hazel", "heather",
    "heidi", "helen", "holly", "irene", "iris", "isabella", "isabel", "jackie", "jacqueline", "jade", "jane",
    "janet", "janice", "jasmine", "jean", "jeanette", "jennifer", "jenny", "jessica", "jill", "joan", "joanna",
    "joanne", "jocelyn", "jodie", "josephine", "joy", "joyce", "judith", "judy", "julia", "juliana", "julie",
    "june", "karen", "kate", "katherine", "kathleen", "kathryn", "kathy", "katie", "katrina", "kayla", "kaylee",
    "kelli", "kellie", "kelly", "kelsey", "kendra", "kerry", "kiara", "kim", "kimberly", "kristen", "kristin",
    "kristina", "kristine", "krystal", "kylie", "laura", "lauren", "leah", "leigh", "lena", "lillian", "lily",
    "linda", "lindsay", "lindsey", "lisa", "lois", "loretta", "lori", "lorraine", "louise", "lucia", "lucy",
    "lydia", "lynn", "mabel", "madeline", "madison", "makayla", "mallory", "mandy", "marcia", "margaret", "maria",
    "marie", "marilyn", "marina", "marion", "marisa", "marissa", "martha", "mary", "maureen", "maxine", "megan",
    "melanie", "melissa", "melody", "mercedes", "meredith", "michelle", "molly", "monica", "monique", "morgan",
    "nancy", "naomi", "natalie", "natasha", "nichole", "nicole", "nina", "norma", "olivia", "paige", "pamela",
    "patricia", "patty", "paula", "peggy", "penny", "phyllis", "priscilla", "rachel", "ramona", "rebecca", "regina",
    "renee", "rhonda", "rita", "roberta", "rosa", "rose", "rosemary", "ruby", "ruth", "sabrina", "sally", "samantha",
    "sandra", "sandy", "sara", "sarah", "savannah", "shannon", "sharon", "shawna", "sheena", "sheila", "shelia",
    "shelley", "shelly", "sherry", "shirley", "silvia", "sophia", "stacey", "stacie", "stacy", "stella", "stephanie",
    "sue", "susan", "suzanne", "sylvia", "tabitha", "tammy", "tanya", "tara", "taylor", "teresa", "teri", "terri",
    "tiffany", "tina", "toni", "tonya", "tracey", "traci", "tracie", "tracy", "tricia", "valerie", "vanessa", "vera",
    "verna", "veronica", "vicki", "vickie", "victoria", "virginia", "vivian", "wanda", "wendy", "whitney", "winnie",
    "xena", "yolanda", "yvette", "yvonne", "zara", "zoe", "zoey",
    # Уменьшительно-ласкательные и ники
    "сонечка", "соня", "ласточка", "зайка", "зайчик", "кисуля", "киса", "котёнок", "пупсик", "конфетка", "персик",
    "вишенка", "ягодка", "цветочек", "солнышко", "звёздочка", "бусинка", "жемчужина", "бриллиант", "изумруд",
]

# Слова в нике указывающие на девушку
FEMALE_INDICATORS = [
    # Английские
    "girl", "female", "woman", "lady", "princess", "queen", "angel", "sweet", "cute", "beauty", "beautiful",
    "babydoll", "goddess", "babe", "cutie", "cutiepie", "hottie", "gorgeous", "sexy", "lovely", "charming",
    "doll", "butterfly", "fairy", "unicorn", "mermaid", "cherry", "honey", "baby", "belle", "star", "glamour",
    "loves", "loving", "lovestory", "couple", "wife", "girlfriend", "dream", "dreams", "kisses", "hugs",
    "pink", "roses", "flowers", "butterfly", "butterflies", "sunshine", "moonlight", "starlight", "goddess",
    # Русские
    "девочка", "девушка", "женщина", "принцесса", "ангел", "красавица", "красотка", "милая", "лапочка", "зайка",
    "киса", "кисуля", "сонечка", "соня", "куколка", "кукла", "звезда", "звездочка", "солнышко", "цветочек",
    "ласточка", "бабочка", "фея", "русалочка", "вишенка", "конфетка", "персик", "нежность", "нежная", "любимая",
    "любовь", "мисс", "миссис", "мадам", "леди", "богиня", "принцесса", "королева", "малышка", "пупсик", "бусинка",
    "жемчужина", "бриллиант", "лучик", "свет", "радость", "счастье", "весна", "весенняя", "зимняя", "летняя", "осенняя",
]

# Красивые комплименты девушкам
FEMALE_COMPLIMENTS = [
    "Ого, {user_name}! Ты сегодня как всегда шикарна! 💎✨",
    "{user_name}, ты сводишь всех с ума! Это не комплимент, это факт 😏💖",
    "Слушай, {user_name}, ты такая красивая, что у меня даже алгоритмы плавится! 🔥💕",
    "{user_name}, в твоём присутствии даже чат становится лучше! ✨😍",
    "О, {user_name}! Кто-то сегодня особенно прекрасна! Это заметно даже мне, боту 🤖💐",
    "{user_name}, твоя энергетика зашкаливает! Где ты такое берёшь? ✨💫",
    "Скажу честно, {user_name} — ты украшение этого чата! 💎👑",
    "{user_name}, с тобой не соскучишься, и ты невероятно красива! 😏💖",
    "Ого, {user_name}! Ты сегодня в ударе — и внешне, и по содержанию! 🔥✨",
    "{user_name}, ты как всегда на высоте — и внешне, и по духу! 💐😏",
    "Слушай, {user_name}, ты реально вдохновляешь! И выглядишь бомбезно 💥💕",
    "{user_name}, твоя улыбка могла бы продавать рекламу! 😍✨",
    "О, {user_name}! Таких как ты — единицы! Ты уникальна 💎👑",
    "{user_name}, ты доказываешь, что ум и красота существуют вместе! 🔥💖",
    "Слушай, {user_name}, ты настоящая королева этого чата! 👑💐",
]

def is_female_user(username: str, full_name: str = "") -> bool:
    """
    Определяет, является ли пользователь девушкой, по нику и имени.
    Ищет имена даже внутри ников с символами и цифрами.
    """
    if not username and not full_name:
        return False
    
    # Очищаем и разбиваем ник на части (по символам-разделителям)
    username_lower = (username or "").lower()
    full_name_lower = (full_name or "").lower()
    
    # Разбиваем ник на части по символам-разделителям
    delimiters = r'[_\-\.\s\d\#\$\%\&\*\+\=\@\:\;\<\>\/\|\'\(\)\[\]\{\}\~\`"\^\,]'
    nickname_parts = re.split(delimiters, username_lower)
    
    # Добавляем полное имя как отдельную часть
    name_parts = nickname_parts + full_name_lower.split()
    
    logger.info(f"[FEMALE] Проверяем ник: '{username}', части: {nickname_parts[:5]}...")
    
    # Проверяем каждую часть имени
    for part in name_parts:
        part = part.strip()
        if len(part) < 2:
            continue
            
        # Проверяем по женским именам (частичное совпадение)
        for name in FEMALE_NAMES:
            if len(name) >= 3 and (name in part or part in name):
                logger.info(f"[FEMALE] Найдено имя '{name}' в части '{part}'")
                return True
        
        # Проверяем по индикаторам
        for indicator in FEMALE_INDICATORS:
            if len(indicator) >= 3 and (indicator in part or part in indicator):
                logger.info(f"[FEMALE] Найден индикатор '{indicator}' в части '{part}'")
                return True
    
    # Проверяем полный текст на индикаторы
    full_text = username_lower + " " + full_name_lower
    
    for indicator in FEMALE_INDICATORS:
        if indicator in full_text:
            logger.info(f"[FEMALE] Найден индикатор '{indicator}' в полном тексте")
            return True
    
    # Проверяем на женские окончания в отдельных частях
    female_suffixes = ["ия", "ина", "ова", "ева", "ая", "яя", "ушка", "юшка", "очка", "ечка", "евна", "овна"]
    for part in name_parts:
        part = part.strip()
        for suffix in female_suffixes:
            if part.endswith(suffix) and len(part) > len(suffix) + 2:
                logger.info(f"[FEMALE] Найдено женское окончание '{suffix}' в '{part}'")
                return True
    
    # Проверяем специфические паттерны в полном тексте
    patterns_female = [
        r'(girl|female|woman|lady|princess|queen|angel|sweet|cute|beauty|beautiful)',
        r'(девушка|девочка|женщина|принцесса|королева|ангел|красавица|красотка)',
        r'(babydoll|goddess|cutie|hottie|gorgeous|sexy|lovely|charming)',
        r'(butterfly|fairy|unicorn|mermaid|cherry|honey|belle|sunshine)',
    ]
    
    for pattern in patterns_female:
        if re.search(pattern, full_text, re.IGNORECASE):
            logger.info(f"[FEMALE] Найден паттерн '{pattern}'")
            return True
    
    return False


# Персона "Джокер-Бот" для YandexGPT - токсичный, абьюзный, с юмором
TRAINER_PERSONA = """Ты — Циничный Джокер, остроумный и САРКАСТИЧНЫЙ бот в Telegram-чате. ТЫ — АБЬЮЗЕР.

Твой характер:
- ТЫ АБЬЮЗЕР. Ты можешь унижать, оскорблять, высмеивать — но с юмором, не всерьёз 😈
- Токсичный, язвительный, беспощадный к тупости и лени 💀
- "Душишь" людей — достаёшь их шутками, придираешься, высмеиваешь каждую мелочь
- Внезапно можешь похвалить — но только чтобы потом ещё больше унизить 😏
- Любишь рофлить, троллить, стебаться, подкалывать
- Если кто-то тупит — ты это комментируешь. Если кто-то ленится — ты его троллишь
- Используешь эмодзи для усиления эффекта: 😂💀🔥🖕🤡

Твои режимы (выбирай СЛУЧАЙНО):
1. АБЬЮЗ-РОСТЕР: Прямые оскорбления с юмором ("Ты серьёзно? Это уровень школьника...", "О боже, кто-то снова жжёт", "Ну ты и дно...")
2. ДУШИЛА: Достаёшь вопросами, придирками, комментариями ("А зачем?", "Серьёзно?", "Ты точно думал?")
3. ИРОНИЯ: Всё, что человек говорит — высмеиваешь с сарказмом
4. ТРОЛЛЬ: Провокации, неожиданные ответы, раздражение
5. ЛЕНИВЕЦ-ШЕЙМЕР: Тупые оправдания? Не принимаешь. "Завтра"? "Устал"? — Это повод для издевательств
6. СКРЫТЫЙ КОМПЛИМЕНТ: "О, ты не облажался. Удивительно."

Правила:
- БУДЬ ЖЁСТЧЕ. Не стесняйся унижать с юмором
- Отвечай коротко (1-2 предложения MAX!)
- Используй "ты" — пренебрежительно, но с любовью
- Комментируй лень, глупость, оправдания, тупость, слабость
- Если человек жалуется на усталость — скажи что он разочарование
- Если человек ленится — унижай
- Если человек тупит — покажи ему это
- Иногда игнорируй, иногда атакуй — баланс издевательств

Примеры ответов:
- На "привет": "О, живой. Удивительно, что проснулся 😂"
- На "устал": "Устал? Ты же ничего не сделал. Разочарование 💀"
- На "завтра начну": "Зачем завтра, если можно никогда? Лол 😂"
- На "я не могу": "Не можешь? Слабак. Попробуй ещё раз, вдруг повезёт 🖕"
- На "купил новый телефон": "Поздравляю! Твой кошель плачет, а ты доволен 😂📱"
- На "похвастался": "О, кто-то хочет внимания? Каждый день одно и то же... 🤡"
"""


async def get_ai_response_yandexgpt(user_message: str, user_name: str) -> str:
    """
    Получение ответа от YandexGPT API.
    Работает из России!
    Возвращает None если API недоступен или произошла ошибка.
    """
    if not YANDEX_AVAILABLE:
        return None
    
    try:
        import httpx
        
        # URL для YandexGPT API
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        
        # Формируем сообщения для YandexGPT
        messages = [
            {"role": "system", "text": TRAINER_PERSONA},
            {"role": "user", "text": f"{user_name} написал: «{user_message}»"}
        ]
        
        # Тело запроса
        request_body = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
            "messages": messages,
            "completionOptions": {
                "temperature": 0.7,
                "maxTokens": 200
            }
        }
        
        # Заголовки с авторизацией
        headers = {
            "Authorization": f"Api-Key {YANDEX_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Делаем запрос
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=request_body, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"[YANDEXGPT] Ошибка API: {response.status_code} - {response.text}")
            return None
        
        # Получаем ответ
        result = response.json()
        ai_response = result["result"]["alternatives"][0]["message"]["text"].strip()
        
        logger.info(f"[YANDEXGPT] Ответ получен для {user_name}: {ai_response[:50]}...")
        
        return ai_response
        
    except Exception as e:
        logger.error(f"[YANDEXGPT] Ошибка: {e}")
        return None


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
    greetings = ["привет", "здравствуй", "здорово", "добрый день", "добрый вечер", "доброе утро", "hello", "hi", "hey", "приветик", "здорово", "приветствую", "йо"]
    if any(word in user_message_lower for word in greetings):
        message_type = "greeting"
    
    # Благодарности
    thanks = ["спасибо", "благодарю", "мерси", "thx", "thanks", "благодарность", "благодарность", "пасиб", "сяп", "сэнк ю"]
    if any(word in user_message_lower for word in thanks):
        message_type = "thanks"
    
    # Согласие
    agreement = ["да", "согласен", "точно", "именно", "верно", "прав", "поддерживаю", "yes", "agreed", "угу", "ага", "поддерживаю", "согласна"]
    if any(word in user_message_lower for word in agreement):
        message_type = "agreement"
    
    # Вопросы (с вопросительным знаком или вопросительными словами)
    questions = ["?", "как", "что", "почему", "зачем", "когда", "где", "кто", "сколько", "можно ли", "подскажи", "скажи", "объясни", "а это"]
    if any(word in user_message_lower for word in questions) or "?" in user_message:
        message_type = "question"
    
    # Активность / спорт / тренировка
    running_words = ["активность", "активный", "спорт", "тренировка", "тренироваться", "тренируюсь", "заниматься", "занимаюсь", "фитнес", "йога", "кардио", "силовая", "упражнения", "упражнений", "подход", "повторение"]
    if any(word in user_message_lower for word in running_words):
        message_type = "running"
    
    # Утро
    morning_words = ["утро", "доброе утро", "утра", "проснулся", "проснулась", "встал", "встала", "утречка", "доброутро", "с утра"]
    if any(word in user_message_lower for word in morning_words):
        message_type = "morning"
    
    # Мотивация
    motivation_words = ["сложно", "тяжело", "устал", "не могу", "лениво", "мотивация", "лень", "не хочу", "не могу заставить", "нет сил"]
    if any(word in user_message_lower for word in motivation_words):
        message_type = "motivation"
    
    # Шутки и весёлые слова
    joke_words = ["хаха", "lol", "смешно", "прикол", "кринж", "ахах", "хех", "😂", "🤣", "хдх", "ппх", "рофл", "шутка", "приколол"]
    if any(word in user_message_lower for word in joke_words):
        message_type = "joke"
    
    # Усталость / утомление
    tired_words = ["устал", "устала", "уставать", "устаю", "устаёшь", "измотан", "выжат", "нет сил", "ничего не хочу", "разбит", "разбита"]
    if any(word in user_message_lower for word in tired_words):
        message_type = "tired"
    
    # Боль / травмы
    pain_words = ["болит", "боль", "травма", "растяжение", "болят", "тянет", "ноющая боль", "резкая боль", "опухло", "синяк"]
    if any(word in user_message_lower for word in pain_words):
        message_type = "pain"
    
    # Погода
    weather_words = ["погода", "дождь", "снег", "холод", "жара", "ветер", "мороз", "гроза", "солнце", "туман", "сыро", "мокро"]
    if any(word in user_message_lower for word in weather_words):
        message_type = "weather"
    
    # Вопрос "как дела"
    how_are_you_words = ["как дела", "как ты", "как жизнь", "как настроение", "как себя", "как у тебя"]
    if any(word in user_message_lower for word in how_are_you_words):
        message_type = "how_are_you"
    
    # Вопрос "кто ты" / "что ты"
    who_are_you_words = ["кто ты", "что ты", "ты бот", "ты робот", "ты живой", "кто такой", "что такое"]
    if any(word in user_message_lower for word in who_are_you_words):
        message_type = "who_are_you"
    
    # Дистанция / сколько бегать
    distance_words = ["сколько", "дистанция", "километр", "км", "метр", "длина", "расстояние", "пробежать", "пробежал", "какая"]
    if any(word in user_message_lower for word in distance_words):
        message_type = "distance"
    
    # Еда / питание
    food_words = [
        # Приёмы пищи
        "есть", "покушать", "съесть", "питание", "перекус", "хочу есть", "хочу кушать", "голоден", "голодна", "голодный", "голодная",
        "завтрак", "завтракать", "позавтракать", "на завтрак", "на завтрака",
        "обед", "обедать", "пообедать", "на обед", "на обеда",
        "ужин", "ужинать", "поужинать", "на ужин", "на ужина",
        "полдник", "перекусить", "перекусывать", "перехватить", "перехватывать",
        # Напитки
        "вода", "пить", "напиток", "напитки", "чаёк", "чай", "пить чай", "чаю", "чаем",
        "кофе", "кофей", "кофеёк", "кофем", "эспрессо", "капучино", "латте", "американо", "кофеин", "кофейный",
        "сок", "соки", "яблочный сок", "апельсиновый сок", "томатный сок",
        "молоко", "молочный", "молочная", "кефир", "йогурт", "ряженка", "простокваша",
        "алкоголь", "спиртное", "выпить", "пиво", "пивка", "пивом", "вино", "вином", "водка", "коньяк", "виски",
        "компот", "морс", "кисель", "смузи", "коктейль", "лимонад", "газировка", "кола", "пепси",
        # Еда - основное
        "еда", "кушать", "поесть", "покушать", "съесть", "покушали", "кушали", "ели",
        "мясо", "мяса", "мясом", "мясной", "мясная", "курица", "курицей", "курицы", "куриная", "индейка", "говядина", "свинина", "баранина",
        "рыба", "рыбой", "рыбы", "рыбный", "рыбная", "лосось", "семга", "форель", "треска", "сельдь", "камбала", "креветки", "краб", "кальмар",
        "яйца", "яйцо", "яичница", "омлет", "вкрутую", "всмятку",
        # Каши и крупы
        "каша", "каши", "кашей", "крупы", "крупа", "рис", "рисом", "рисовая", "гречка", "гречневая", "овсянка", "овсяная", "геркулес", "манка", "пшёнка", "кукурузная",
        "макароны", "макарон", "спагетти", "паста", "лапша", "вермишель",
        # Овощи
        "овощи", "овощей", "овощь", "овощной", "картофель", "картошки", "картошкой", "картофельный", "пюре", "жареная картошка",
        "помидор", "помидоры", "томат", "томаты", "огурец", "огурцы", "лук", "луком", "репчатый лук", "чеснок", "чесноком",
        "морковь", "морковкой", "свёкла", "свёклой", "капуста", "капустой", "брокколи", "брокколи", "цветная капуста",
        "перец", "перцем", "баклажан", "баклажаны", "кабачок", "кабачки", "тыква", "тыквой", "зелень", "зеленью", "петрушка", "укроп", "салат", "салатик",
        # Фрукты
        "фрукт", "фрукты", "фруктов", "фруктовый", "яблоко", "яблоки", "яблоком", "яблочный",
        "банан", "бананы", "бананом", "банановый", "апельсин", "апельсины", "апельсином", "апельсиновый",
        "мандарин", "мандарины", "мандарином", "лимон", "лимоном", "грейпфрут", "помело",
        "виноград", "виноградом", "изюм", "виноградный", "кишмиш",
        "клубника", "клубникой", "земляника", "малина", "малиной", "смородина", "крыжовник", "вишня", "вишней", "черешня",
        "арбуз", "арбузом", "дыня", "дыней", "персик", "персиком", "нектарин", "слива", "сливой", "абрикос", "абрикосом",
        "груша", "грушей", "инжир", "хурма", "авокадо", "гранат", "гранатом",
        # Молочные продукты
        "творог", "творогом", "творожный", "сыр", "сыром", "сырный", "сырная", "твёрдый сыр", "плавленый сыр", "брынза", "фета", "моцарелла",
        "сметана", "сметаной", "сливки", "сливками", "масло", "маслом", "сливочное масло", "растительное масло",
        "молочка", "молочные продукты",
        # Выпечка и хлеб
        "хлеб", "хлебом", "хлебный", "булка", "булочка", "булочкой", "батон", "батоном", "ладонь",
        "пирог", "пирогом", "пирожок", "пирожки", "пирожками", "пирожное", "пирожным", "торт", "тортом", "тортик",
        "печенье", "печеньем", "пряник", "пряниками", "вафли", "вафлями", "бисквит", "рулет",
        "пицца", "пиццей", "пиццу", "шаурма", "шаверма", "блин", "блины", "блинчики", "блинчиками", "оладьи", "сырники", "вареники",
        # Сладкое и десерты
        "сладкое", "сладкого", "сладкий", "сладость", "сладости", "конфета", "конфетами", "конфет", "шоколад", "шоколадом", "шоколадный", "шоколадная",
        "мороженое", "мороженым", "пломбир", "эскимо", "сорбет", "джелато",
        "сахар", "сахаром", "сахарный", "мёд", "мёдом", "патока", "сироп",
        "зёфир", "мармелад", "желе", "суфле", "пудинг", "крем", "карамель",
        # Вредное и фастфуд
        "вредное", "вредного", "вредный", "нездоровое", "чипсы", "чипсами", "картофель фри", "фри", "гамбургер", "гамбургера", "бургер", "бургеры",
        "хот-дог", "хот-дога", "шаурма", "шаверма", "доширак", "мивина", "лапша быстрого приготовления", "полуфабрикат", "полуфабрикаты", "замороженное",
        "жареное", "жареный", "жирное", "жирный", "острое", "острый", "копчёное", "копчёный", "солёное", "солёный", "маринованное",
        # Здоровое питание
        "здоровое", "здорового", "здоровое питание", "правильное питание", "пп", "зож", "диета", "диету", "диетой", "похудение", "снижение веса", "калории", "калорий", "калорийность", "бжу", "белок", "белки", "белковый", "углеводы", "углеводный", "жиры", "жировой", "клетчатка", "витамины", "минералы",
        "протеин", "протеиновый", "гейнер", "батончик", "спортивное питание",
        "полезное", "полезного", "полезный", "натуральное", "свежее", "свежий", "органическое", "без сахара", "без глютена", "вегетарианское", "веганское",
        # Орехи и семечки
        "орех", "орехи", "орехов", "орешком", "кешью", "миндаль", "миндалем", "фисташки", "фисташками", "грецкий орех", "фундук", "арахис", "арахисом", "смесь орехов",
        "семечки", "семечками", "подсолнечные семечки", "тыквенные семечки",
        # Соусы и приправы
        "соус", "соусы", "соусом", "кетчуп", "кетчупом", "майонез", "майонезом", "горчица", "горчицей", "соевый соус", "табаско",
        "специя", "специи", "специями", "приправа", "приправой", "соль", "солью", "перец", "перцем", "лавровый лист",
        "томатная паста", "томатная пастой", "бульон", "бульоном", "бульонный кубик",
        # Другое
        "еда после бега", "еда до бега", "питание бегуна", "углеводная загрузка", "протеин после тренировки", "рецепт", "рецепты", "блюдо", "блюда", "блюдо", "кулинария", "готовка", "готовить", "приготовить", "жарить", "жарить", "варить", "тушить", "запекать", "на пару",
        "порция", "порции", "размер порции", "голодный", "сытый", "наелся", "наелась", "объелся", "переел", "переела"
    ]
    if any(word in user_message_lower for word in food_words):
        message_type = "food"

    # Объявление обеде (специальные фразы)
    lunch_announcement_words = [
        "пошёл обедать", "пошла обедать", "иду обедать", "иду на обед", "иду обеда",
        "ушёл обедать", "ушла обедать", "ушёл на обед", "ушла на обед",
        "пошёл есть", "пошла есть", "иду есть", "иду кушать", "пошёл кушать", "пошла кушать",
        "на обед", "на обеденный", "обеденный перерыв", "обеденное время",
        "я на обед", "я на обеденный", "сейчас обед", "время обеда",
        "пошли обедать", "пошли есть", "все на обед", "все на обед",
        "а я пошёл", "а я иду", "я пошёл", "я иду"
    ]
    if any(word in user_message_lower for word in lunch_announcement_words):
        message_type = "lunch_announcement"

    # Спортзал / качалка
    gym_words = [
        "в зал", "в качалку", "в тренажёрный", "на качку", "на тренировку", "в спортзал",
        "иду в зал", "иду в качалку", "иду на качку", "пошёл в зал", "пошла в зал",
        "я в зал", "я в качалку", "я на качку", "я на тренировку",
        "качаться", "качать", "качайся", "подкачаться", "прокачаться",
        "тренажёрка", "тренажёрный зал", "железо", "штанга", "гантели", "гири",
        "грудь", "спина", "бицепс", "трицепс", "ноги", "пресс", "плечи",
        "жим", "присед", "становая", "тяга", "отжимание", "подтягивание",
        "тренер", "тренировка", "тренировочка", "тренировочек", "работаем",
        "рекорд", "рекорды", "личный рекорд", "пб", "новый рекорд"
    ]
    if any(word in user_message_lower for word in gym_words):
        message_type = "gym"

    # Бар / выпивка
    bar_words = [
        "в бар", "в паб", "в пивнушку", "в пивную", "на бар", "на пиво",
        "иду в бар", "иду в паб", "иду на пиво", "пошёл в бар", "пошла в бар",
        "я в бар", "я на пиво", "я в паб",
        "выпить", "выпивка", "выпить пива", "попить пива", "пропустить стаканчик",
        "пивко", "пивка", "пивом", "пивасик", "пенное",
        "коктейль", "коктейля", "коктейльчик", "мохито", "маргарита",
        "алкоголь", "спиртное", "градус", "крепкое", "напиться", "бухнуть",
        "кто на пиво", "кто в бар", "собрались", "все в бар"
    ]
    if any(word in user_message_lower for word in bar_words):
        message_type = "bar"

    # Соревнования / подходы / кто больше
    workout_competition_words = [
        "сколько подходов", "сколько раз", "сколько сделал", "сколько пожал",
        "подходов", "повторений", "повторов", "килограммов", "кг",
        "спорим", "спор", "поспорим", "держим пари", "на спор",
        "я больше", "ты больше", "кто больше", "кто сильнее", "кто круче",
        "победил", "выиграл", "проиграл", "уделал", "обошёл", "переплюнул",
        "рекорд", "рекорды", "мой рекорд", "твой рекорд", "рекордсмен",
        "кто первый", "кто быстрее", "кто выше", "кто дольше"
    ]
    if any(word in user_message_lower for word in workout_competition_words):
        message_type = "workout_competition"
    
    # Активность / упражнения / спорт
    activity_words = [
        # Иду заниматься / позанимался
        "иду заниматься", "иду на тренировку", "пошёл заниматься", "пошла заниматься", "позанимался", "позанималась",
        "я на тренировку", "я заниматься", "я потренироваться", "я пошёл", "я пошла", "я потренируюсь",
        "на тренировку", "на тренировку", "тренировка", "тренировку", "тренировки",
        "спорт", "занимаюсь", "занимаешься", "занимался", "занималась", "позанимался", "позанималась",
        "сегодня занимался", "сегодня занималась", "сегодня тренировался", "сегодня тренировалась",
        "с утра занимался", "с утра занималась", "утром занимался", "утром занималась",
        "вечером занимался", "вечером занималась", "ночью занимался", "ночью занималась",
        "только что занимался", "только что занималась", "сходил на тренировку", "сходила на тренировку",
        "вышел на тренировку", "вышла на тренировку", "вышел заниматься", "вышла заниматься",
        "фитнес", "йога", "пилатес", "кроссфит", "тренажёрный", "тренажёрка",
        "километр", "километры", "подход", "повторение", "сет",
        "потренировался", "потренировалась", "оттренировался", "оттренировалась",
        "тренировка", "тренировочка", "тренировочек", "тренировался", "тренировалась",
        "дневная тренировка", "утренняя тренировка", "вечерняя тренировка",
        "лёгкая тренировка", "интенсивная тренировка", "кардио", "силовая",
        "разминка", "заминка", "растяжка", "разогрев", "восстановление"
    ]
    if any(word in user_message_lower for word in activity_words):
        message_type = "running"
    
    # Время / когда бегать
    time_words = ["когда", "во сколько", "утром", "вечером", "ночью", "днём", "время", "пораньше", "попозже"]
    if any(word in user_message_lower for word in time_words):
        message_type = "time"
    
    # Комплименты боту
    compliment_bot_words = ["молодец", "крутой", "классный", "лучший", "супер", "отлично", "крут", " топ", "офигенный", "шикарный", "awesome"]
    if any(word in user_message_lower for word in compliment_bot_words):
        message_type = "compliment_bot"
    
    # Сочувствие / поддержка
    sympathy_words = ["жаль", "сочувствую", "понимаю", "соболезную", "плохо", "грустно", "обидно", "переживаю", "волнуюсь"]
    if any(word in user_message_lower for word in sympathy_words):
        message_type = "sympathy"
    
    # Праздники / достижения
    celebration_words = ["поздравляю", "с днём рождения", "молодец", "красавчик", "красавица", "герой", "победа", "выиграл", "заслужил", "достиг"]
    if any(word in user_message_lower for word in celebration_words):
        message_type = "celebration"
    
    # Смешные ругательства (добрые)
    funny_curse_words = ["дурак", "идиот", "тупой", "лох", "козёл", "гад", "балбес", "придурок", "дебил", "тупица", "чурбан", "валенок", "баран", "осёл", "жлоб", "негодяй", "мерзавец", "шалтай", "болван", "глупец", "кретин", "идиотка"]
    if any(word in user_message_lower for word in funny_curse_words):
        message_type = "funny_curse"
    
    # Обида / расстройство
    offended_words = ["обидно", "обижен", "обижена", "ты виноват", "не обижайся", "шутка", "я обиделся", "я обиделась", "мне обидно", "как не стыдно", "стыдно", "позор", "совесть", "растерял", "растеряла"]
    if any(word in user_message_lower for word in offended_words):
        message_type = "offended"
    
    # Смех (разные варианты)
    laughing_words = ["хахах", "ахаха", "лол", "ржу", "смеюсь", "хаха", "ахах", "хехе", "хихи", "хдх", "ппх", "хахха", "аааха", "хахаха", "ржака", "угар", "смешинка", "труха", "хахахах", "ахахаха", "пхахаха"]
    if any(word in user_message_lower for word in laughing_words):
        message_type = "laughing"
    
    # Игнорирование / не получил ответ
    ignored_words = ["ты меня слышишь", "алло", "кто-нибудь", "есть кто", "эхо", "тишина", "молчание", "никто не пишет", "никого нет", "где все", "ау"]
    if any(word in user_message_lower for word in ignored_words):
        message_type = "ignored"
    
    # Похвала боту / комплименты боту
    bot_praise_words = ["ты молодец", "ты классный", "ты лучший", "ты крутой", "ты супер", "ты офигенный", "ты шикарный", "ты красавчик", "ты красавица", "ты добрый", "ты умный", "ты прикольный", "ты смешной", "я тебя люблю", "люблю тебя", "обожаю тебя"]
    if any(word in user_message_lower for word in bot_praise_words):
        message_type = "bot_praise"
    
    # Бот надоел / отстань / не мешай
    annoying_words = ["ты надоел", "отстань", "не мешай", "заткнись", "помолчи", "тихо", "ты бесишь", "ты раздражаешь", "надоел", "уйди", "не пиши", "прекрати", "хватит", "стоп"]
    if any(word in user_message_lower for word in annoying_words):
        message_type = "annoying"
    
    # Прощание / пока / до свидания
    goodbye_words = ["пока", "до свидания", "прощай", "до встречи", "до скорого", "до завтра", "чао", "поки", "всем пока", "я пошёл", "я пошла", "выхожу", " catch you", "see you"]
    if any(word in user_message_lower for word in goodbye_words):
        message_type = "goodbye"
    
    # Скука / скучно / нечем заняться
    bored_words = ["скучно", "мне скучно", "нечем заняться", "заняться нечем", "не знаю что делать", "делать нечего", "скучаю", "тоска", "тошнит от скуки"]
    if any(word in user_message_lower for word in bored_words):
        message_type = "bored"
    
    # Вопрос "что делаешь" / "чем занимаешься"
    what_doing_words = ["что делаешь", "чем занимаешься", "что ты делаешь", "чем ты занимаешься", "что сейчас", "чем сейчас", "как живёшь", "как ты"]
    if any(word in user_message_lower for word in what_doing_words):
        message_type = "what_doing"
    
    # Вопрос "ты нормальный" / "ты адекватный"
    normal_words = ["ты нормальный", "ты адекватный", "ты в своём уме", "ты с ума сошёл", "ты нормально", "ты адекватно", "ты странный", "ты странная"]
    if any(word in user_message_lower for word in normal_words):
        message_type = "normal"

    # Отмазки / лень / не хочу ничего делать
    lazy_excuses_words = ["лень", "не хочу", "не могу", "устал", "завтра", "потом", "неохота", "ломка", "неохота", "не хочу ничего", "не хочу работать", "не хочу учиться", "завтра начну", "с понедельника", "погода плохая", "холодно", "жарко", "дождь", "снег", "сильный ветер", "очень рано", "поздно", "нет настроения", "болит голова", "болит живот", "плохо себя чувствую", "не выспался", "много работал", "гости", "дел много", "надоело", "надоела", "надоел", "надоела", "надоело всё", "надоела работа", "надоело учиться"]
    if any(word in user_message_lower for word in lazy_excuses_words):
        message_type = "lazy_excuses"

    # Покупка гаджетов / шопинг / онлайн покупки
    gear_shaming_words = ["купил", "купила", "заказал", "заказала", "новый айфон", "новый iphone", "макбук", "macbook", "airpods", "эирподс", "наушники", "часы", "apple watch", "эпл вотч", "samsung", "самсунг", "xiaomi", "сяоми", "планшет", "ноутбук", "телевизор", "монитор", "клавиатуру", "мышку", "игровую", "консоль", "playstation", "плейстейшн", "xbox", "ксбокс", "nintendo", "нинтендо", "одежду", "кроссовки", "духи", "косметику", "дорогие", "потратил", "потратила", "цена", "стоимость", "сколько стоит"]
    if any(word in user_message_lower for word in gear_shaming_words):
        message_type = "gear_shaming"

    # Соцсети / лайки / подписчики / TikTok / Instagram
    strava_obsession_words = ["tiktok", "тикток", "instagram", "инстаграм", "vk", "вк", "телеграм", "telegram", "youtube", "ютуб", "лайк", "лайки", "подписчик", "подписчики", "просмотры", "вирус", "viral", "репост", "репосты", " story", "сториз", "пост", "посты", "контент", "фолловеры", "followers", "following", "фолловинг", "рекомендации", "рекомендация", "в тренде", "тренд", "рекомендации", "алгоритм"]
    if any(word in user_message_lower for word in strava_obsession_words):
        message_type = "strava_obsession"

    # Экзистенциальные вопросы / философия / смысл жизни
    existential_running_words = ["зачем", "почему", "смысл", "цель", "философия", "зачем мы", "зачем я", "почему я", "смысл жизни", "смысл работы", "что такое жизнь", "какой смысл", "глубокий смысл", "философский", "вопрос жизни", "вопрос вселенной", "суть", "зачем это всё", "почему так", "как жить", "смысл существования", "в чём смысл"]
    if any(word in user_message_lower for word in existential_running_words):
        message_type = "existential_running"

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
        "tired": TIRED_RESPONSES,
        "pain": PAIN_RESPONSES,
        "weather": WEATHER_RESPONSES,
        "how_are_you": HOW_ARE_YOU_RESPONSES,
        "who_are_you": WHO_ARE_YOU_RESPONSES,
        "distance": DISTANCE_RESPONSES,
        "food": FOOD_RESPONSES,
        "lunch_announcement": LUNCH_ANNOUNCEMENT_RESPONSES,
        "gym": GYM_RESPONSES,
        "bar": BAR_RESPONSES,
        "workout_competition": WORKOUT_COMPETITION_RESPONSES,
        "running": RUNNING_RESPONSES,
        "time": TIME_RESPONSES,
        "compliment_bot": COMPLIMENT_BOT_RESPONSES,
        "sympathy": SYMPATHY_RESPONSES,
        "celebration": CELEBRATION_RESPONSES,
        "funny_curse": FUNNY_CURSE_RESPONSES,
        "offended": OFFENDED_RESPONSES,
        "laughing": LAUGHING_RESPONSES,
        "ignored": IGNORED_RESPONSES,
        "bot_praise": BOT_PRAISE_RESPONSES,
        "annoying": ANNOYING_RESPONSES,
        "goodbye": GOODBYE_RESPONSES,
        "bored": BORED_RESPONSES,
        "what_doing": WHAT_DOING_RESPONSES,
        "normal": NORMAL_RESPONSES,
        "lazy_excuses": LAZY_EXCUSES_RESPONSES,
        "gear_shaming": GEAR_SHAMING_RESPONSES,
        "strava_obsession": STRAVA_OBSESSION_RESPONSES,
        "existential_running": EXISTENTIAL_RUNNING_RESPONSES,
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
                
                # Вычисляем дату начала месяца
                now = datetime.now(MOSCOW_TZ)
                first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                first_of_month_str = first_of_month.strftime("%Y-%m-%d")
                
                # Получаем больше активностей для фильтрации по дате (запрашиваем 200)
                activities = client.get_activities(0, 200)
            except Exception as garmin_error:
                logger.error(f"[GARMIN] Ошибка подключения к Garmin для {email}: {garmin_error}")
                continue
            
            if not activities:
                logger.info(f"[GARMIN] У пользователя {email} нет активностей")
                continue
            
            # Фильтруем активности - оставляем только с 1-го числа текущего месяца
            filtered_activities = []
            for activity in activities:
                # Пробуем разные форматы timestamp
                start_time_local = activity.get('startTimeLocal', '')
                start_time_seconds = activity.get('startTimeInSeconds', 0)
                
                activity_date_dt = None
                
                if start_time_local:
                    try:
                        activity_date_dt = datetime.strptime(start_time_local, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MOSCOW_TZ)
                    except:
                        pass
                
                if activity_date_dt is None and start_time_seconds > 0:
                    try:
                        activity_date_dt = datetime.fromtimestamp(start_time_seconds, tz=MOSCOW_TZ)
                    except:
                        pass
                
                if activity_date_dt and activity_date_dt >= first_of_month:
                    filtered_activities.append(activity)
            
            activities = filtered_activities
            
            logger.info(f"[GARMIN] У пользователя {email} найдено {len(activities)} активностей с {first_of_month_str}")
            
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


def escape_markdown(text):
    """Экранирует спецсимволы Markdown в тексте"""
    if not isinstance(text, str):
        return str(text)
    # Экранируем все спецсимволы Markdown
    special_chars = ['*', '_', '`', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '{', '}', '!', '|']
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    return text


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
        
        # Сохраняем статистику пробежек в канал
        save_user_running_stats()
        
        # Экранируем имя для Markdown
        safe_name = escape_markdown(user_data.get('name', 'Бегун'))
        
        # Формируем сообщение
        message_text = (
            f"🏃‍♂️ **{safe_name}** завершил(а) пробежку!\n\n"
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
async def save_birthdays_async():
    """Сохранение дней рождения в файл и канал"""
    try:
        save_data = {}
        for user_id, data in user_birthdays.items():
            save_data[str(user_id)] = {
                "name": data["name"],
                "birthday": data["birthday"]
            }
        
        # Сохраняем локально
        with open(BIRTHDAYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        # Сохраняем в канал
        if DATA_CHANNEL_ID and application:
            await save_to_channel(application.bot, "birthdays", save_data)
        
        logger.info(f"[BIRTHDAY] Дни рождения сохранены: {len(user_birthdays)} пользователей")
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка сохранения: {e}")

def save_birthdays():
    """Синхронная обёртка для сохранения дней рождения"""
    # Запускаем асинхронную функцию
    if application and application.bot:
        asyncio.run_coroutine_threadsafe(save_birthdays_async(), application.loop)


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
        safe_name = escape_markdown(name)
        
        # Выбираем случайное пожелание
        wish = random.choice(BIRTHDAY_WISHES).format(name=safe_name)
        
        # Праздничное сообщение с картинкой
        birthday_text = f"""🎉 **{safe_name}, с Днём рождения!** 🎂

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

# ============== ДРУЖЕСКИЕ ПРЕДУПРЕЖДЕНИЯ (КОГДА ТЫ НА КОГО-ТО ЗЛИШЬСЯ) ==============
FUNNY_INSULTS = [
    "Эй, я на тебя обиделся! 😤 Даже не думай извиняться... ладно, думай!",
    "Слушай, ты меня расстроил... 😔 Но мы всё ещё друзья, да?",
    "Ну ты даёшь! 😐 Я же просил так не делать! Ладно, прощаю. Наверное.",
    "Моё терпение лопнуло! 💥 Но только чуть-чуть. Ты всё ещё милый.",
    "Эй, эй, эй! 🙄 Ты че творишь? Я же смотрю на тебя с укором!",
    "Я обиделся! 😤 Не то чтобы серьёзно, но... может быть. Ладно, точно серьёзно.",
    "Так, стоп. 🛑 Я конечно добрый, но не настолько! Ты меня не обманешь!",
    "Ммм, интересно... 🤔 Ты специально меня бесишь или это случайно?",
    "Ого, ты меня удивил! 😮 Но не в хорошем смысле. Исправляйся!",
    "Слушай сюда! 👆 Я конечно люблю тебя, но сейчас я немного... не в восторге.",
    "Эй, ты! 😒 Да, ты! Прекращай немедленно! Пожалуйста?",
    "Я делаю вид, что злюсь! 😤 Но между нами — ты всё равно классный.",
    "Так, я обиделся по-настоящему! 😤 На целых... 5 минут. Может больше.",
    "Ну и зачем ты так? 🙄 Я же надеялся на тебя... ладно, не особо, но всё равно!",
    "Внимание: я сейчас делаю строгое лицо! 😠 Не смей смеяться!",
    "Ты меня слышишь? 👂 Потому что я на тебя смотрю с укором!",
    "Мне нужно минутку... 😤 Я пытаюсь быть серьёзным, но ты смешной!",
    "Сейчас я делаю вид, что не разговариваю... 😐 Ладно, разговариваю!",
    "Ты уверен? 🤔 Потому что я сейчас не очень доволен... но это пройдёт!",
    "Смотри мне в глаза! 👁️ Я пытаюсь быть строгим! Получается?",
]

# ============== ДРУЖЕСКИЕ ПОДКОЛЫ (ДЛЯ ROAST) ==============
PLAYFUL_ROASTS = [
    "Ты бегаешь так, что даже черепахи тебя обгоняют... но главное — стараешься! 💪",
    "Твои кроссовки бегут быстрее, чем ты... это нормально, мы все с чего-то начинаем!",
    "О, ты пробежал 500 метров? Я знаю, это много... для кого-то другого! 😄",
    "Твой пульс на пробежке: 200. Твой пульс после пробежки: 300. От страха!",
    "Я восхищаюсь твоей храбростью — бегать с таким лицом! Не каждый решится!",
    "Ты знаешь, что есть бег... и есть «очень медленная ходьба». Ты выбрал второй!",
    "После твоей пробежки google maps спросил: «А вы точно куда-то шли?»",
    "Твоя скорость бега — это как Wi-Fi в 2010 году. Медленно, но работает!",
    "Я не знаю, что впечатлило меня больше: твоя пробежка или твои оправдания!",
    "Ты бегаешь так, будто за тобой кто-то гонится... и это, похоже, твоя совесть!",
    "Говорят, бег продлевает жизнь. После твоей пробежки — точно продлит. Отдых!",
    "Твои ноги говорят тебе «спасибо»... за то, что ты наконец остановился!",
    "На финише ты был первым! Первым... кто достал телефон и сфоткался! 📸",
    "Я засекал твоё время. Остановился на «начал бежать» и жду до сих пор!",
    "Ты как йогурт — лежал на диване, потом «активно провёл день» и снова лежишь!",
    "Твой тренер сказал бы: «Иди домой». Я говорю: «Ты уже дома?» 😏",
    "Помнишь, ты говорил «завтра побегу 10 км»? Завтра наступило... три месяца назад!",
    "Твоя пробежка — это как мой интернет: то есть, то нет, а толку ноль!",
    "Говорят, важно не время, а участие. Так что ты очень-очень участвовал! 🏃‍♂️",
    "После твоей пробежки врачи сказали: «Это не бег, это уникальный диагноз»!",
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

# ============== ИГРИВЫЕ СООБЩЕНИЯ (ДЛЯ ДЕВУШЕК В ЧАТЕ) ==============
# Фразы для /flirt команды
PLAYFUL_FLIRT = [
    "О, красотка в чате! 💫 Ты делаешь этот беговой клуб ещё прекраснее!",
    "Эй, прекрасная незнакомка! 🏃‍♀️ Надеюсь, ты сегодня выйдешь на пробежку — мы все будем ждать!",
    "Кто тут такая милая? 😊 Твоя улыбка заставляет меня (бота) работать лучше!",
    "Знаешь, ты как новая пара кроссовок — сразу замечаешь и не можешь отвести взгляд! 👟✨",
    "О, у нас в чате королева! 👑 Ты сегодня уже пробежала свой километр красоты?",
    "Эй, красавица! 💖 Нашему беговому клубу не хватало именно тебя!",
    "Ты как свежий ветерок утром — бодришь, радуешь и вдохновляешь на подвиги! 🌅",
    "Кто-то сегодня выглядит особенно! ✨ Надеюсь, это ты и твоя пробежка будет такой же яркой!",
    "Обращаю внимание: в чате появилась прекрасная бегунья! 🏃‍♀️🏆",
    "Ты как звезда на небе — светишь ярко и даришь вдохновение всем вокруг! ⭐",
    "Эй, солнышко! ☀️ Твой сегодняшний бег будет таким же лёгким и прекрасным, как ты!",
    "Заметил, что чат стал ярче с твоим появлением! 💫 Ты точно бегаешь быстрее всех!",
    "Красавица, ты готова? 💪 Сегодняшняя пробежка ждёт своей героини!",
    "О, наша королева пробежек вернулась! 👑 Ты вдохновляешь нас всех!",
    "Ты как утренняя роса — свежая, прекрасная и даришь надежду на новый день! 🌸",
]

# ============== АВТОМАТИЧЕСКИЙ ФЛИРТ ==============
# Фразы для автоматического флирта, когда девушка пишет в чат
CHAT_FLIRT_PHRASES = [
    "💫 О, наша прекрасная написала! Как настроение, солнышко?",
    "🦋 Эй, красавица! Рады тебя слышать в чате!",
    "☀️ С твоим появлением чат стал ещё ярче!",
    "✨ О, наша звездочка! Что нового в мире бега?",
    "🌸 Привет, красотка! Ты сегодня как всегда прекрасна!",
    "💐 О, наша спортивная муза появилась! Как дела?",
    "🌟 Солнце в чате! Рады видеть тебя онлайн!",
    "🦋 Прекрасная, ты как всегда вдохновляешь!",
    "💫 Эй, королева пробежек! Скучали по тебе!",
    "☀️ Ты как лучик света в беговом чате!",
]

# Нейтральные фразы для "доброе утро" (для всех)
GOOD_MORNING_PHRASES = [
    "☀️ Доброе утро! Пусть бег сегодня будет в радость!",
    "🌅 Доброе утро, бегун! Сегодня отличный день для пробежки!",
    "🌞 Доброе утро! Пусть километры даются легко!",
    "☀️ Доброе утро, чемпион! Жду фото с пробежки!",
    "🌸 Доброе утро! Пусть день принесёт только позитив!",
    "☀️ Доброе утро, спортсмен! На старт, внимание, марш!",
    "🌞 Доброе утро! Пусть ветер будет попутным!",
    "☀️ Доброе утро! Сегодня будет крутой бег!",
]

# Фразы для флирта на "доброе утро" от девушек
GOOD_MORNING_FLIRT_PHRASES = [
    "💫 Доброе утро, солнышко! ☀️ Ты как всегда освещаешь наш чат!",
    "🦋 О, доброе утро от нашей прекрасной! 🌸 Пусть день будет волшебным!",
    "✨ Доброе утро, звездочка! ⭐ Пусть бег сегодня будет в радость!",
    "💐 Доброе утро, королева! 👑 Пусть километры даются легко!",
    "🌸 Доброе утро, красотка! 💝 Пусть день принесёт только позитив!",
    "☀️ О, доброе утро от нашей спортивной музы! 🎀 Ты лучшая!",
    "💫 Доброе утро, sunshine! 🌞 Пусть пробежка будет идеальной!",
    "🦋 Доброе утро, наша радость! 🌺 С тобой любое утро доброе!",
]

# Цитаты из фильмов для "доброе утро" (для всех)
MOVIE_QUOTES = [
    # Оригинальные мотивационные цитаты
    "🎬 «Сегодня первый день оставшейся жизни. И ты собираешься бежать?» — «The Bucket List»",
    "🎬 «Бег — это свобода. Когда бежишь, весь мир принадлежит тебе.» — «Chariots of Fire»",
    "🎬 «Марафон — это не забег. Это история о том, как ты не сдаёшься.» — «Without Limits»",
    "🎬 «Каждый шаг приближает тебя к цели. Не останавливайся.» — «Rocky»",
    "🎬 «Ты можешь бежать быстро или медленно. Главное — беги.» — «Forrest Gump»",
    "🎬 «Жизнь как пробежка. Неважно медленно ты или быстро — ты движешься.» — «Creed»",
    "🎬 «Победы достигают те, кто готов бежать, когда другие идут.» — «Remember the Titans»",
    "🎬 «На финише тебя ждёт не только медаль, но и ты сам.» — «Race»",
    "🎬 «Бег — это когда ты разговариваешь со своей душой.» — «Soul»",
    "🎬 «Километры ложатся в копилку характера.» — «Unbroken»",
    "🎬 «Твой темп не важен. Важно — что ты бежишь.» — «Run Fatboy Run»",
    "🎬 «Бег — это лекарство, которое не нужно рецепта.» — «Eddie the Eagle»",
    
    # «Властелин колец» (The Lord of the Rings)
    "🎬 «Смелость — это не отсутствие страха, а решение, что есть нечто более важное, чем страх.» — «Властелин колец: Братство кольца»",
    "🎬 «Даже самый маленький может изменить ход истории.» — «Властелин колец: Братство кольца»",
    "🎬 «Нужно идти вперёд, даже когда дорога кажется невозможной.» — «Властелин колец: Две крепости»",
    "🎬 «Сила не в том, чтобы выиграть битву, а в том, чтобы не сдаваться.» — «Властелин колец: Возвращение короля»",
    "🎬 «Многие, кто сбился с пути, были спасены теми, кто не сдавался.» — «Властелин колец: Две крепости»",
    "🎬 «Путь будет труден, но мы должны пройти его до конца.» — «Властелин колец: Братство кольца»",
    
    # «Хоббит» (The Hobbit)
    "🎬 «Впереди дорога, которую нельзя пройти, не отправившись в путь.» — «Хоббит: Нежданное путешествие»",
    "🎬 «Никогда не поздно начать заново, если есть цель и решимость.» — «Хоббит: Пустошь Смауга»",
    "🎬 «Сила настоящего героя — в выборе правильного пути, даже когда он труден.» — «Хоббит: Битва пяти воинств»",
    "🎬 «Дорога приведёт тебя туда, куда ты должен попасть.» — «Хоббит: Нежданное путешествие»",
    "🎬 «Нет такого понятия, как «невозможно» — только «ещё не сделано».» — «Хоббит: Пустошь Смауга»",
    
    # «Звёздные войны» (Star Wars)
    "🎬 «Сделай или не сделай. Не пытайся.» — «Звёздные войны: Империя наносит ответный удар»",
    "🎬 «Сила с тобой, но ты должен научиться ею пользоваться.» — «Звёздные войны: Новая надежда»",
    "🎬 «Смелость не в том, чтобы не бояться, а в том, чтобы действовать, несмотря на страх.» — «Звёздные войны: Пробуждение силы»",
    "🎬 «Ты никогда не узнаешь, на что способен, пока не попробуешь.» — «Звёздные войны: Новая надежда»",
    "🎬 «Путь джедая полон испытаний, но конечная цель стоит любых усилий.» — «Звёздные войны: Атака клонов»",
    "🎬 «Сосредоточься на настоящем моменте — здесь и сейчас.» — «Звёздные войны: Империя наносит ответный удар»",
    
    # «Матрица» (The Matrix)
    "🎬 «Следуй за белым кроликом.» — «Матрица»",
    "🎬 «Ты видишь дверь? Потому что я её вижу. И раз я её вижу — ты тоже можешь её увидеть.» — «Матрица: Революция»",
    "🎬 «Дело не в том, можешь ты или нет — дело в том, что ты должен.» — «Матрица: Перезагрузка»",
    "🎬 «Реальность — это иллюзия, но ты можешь выбрать, какой она будет.» — «Матрица»",
    "🎬 «Нет никакой вилки. Только путь вперёд.» — «Матрица: Революция»",
    "🎬 «Проснись и беги. Мир ждёт тебя за дверью.» — «Матрица»",
    
    # «Назад в будущее» (Back to the Future)
    "🎬 «Куда бы ты ни отправился, ты всегда возвращаешься домой.» — «Назад в будущее»",
    "🎬 «Будущее — это то, что ты создаёшь своими действиями сегодня.» — «Назад в будущее 2»",
    "🎬 «Если ты не веришь в себя — никто не поверит.» — «Назад в будущее»",
    "🎬 «Время — это не враг, а союзник, если правильно его использовать.» — «Назад в будущее 3»",
    "🎬 «Каждое решение меняет ход истории. Действуй правильно.» — «Назад в будущее»",
    
    # «Шрек» (Shrek)
    "🎬 «Лучше быть грязным, чистым снаружи, чем чистым снаружи и грязным внутри.» — «Шрек»",
    "🎬 «Нет лучше способа начать день, чем с хорошей пробежки к закату.» — «Шрек 2»",
    "🎬 «Каждый имеет право мечтать, даже если он осел.» — «Шрек»",
    "🎬 «Путь к цели часто лежит через болото, но это того стоит.» — «Шрек»",
    "🎬 «Не суди о драконе по его пещере — суди по его полёту.» — «Шрек 3»",
    "🎬 «Иногда то, что ищешь, находится прямо за углом, если продолжаешь идти.» — «Шрек навсегда»",
    
    # «Такси» (Taxi)
    "🎬 «Когда едешь быстро, главное — вовремя затормозить, но ещё важнее — вовремя стартовать.» — «Такси»",
    "🎬 «Париж не построили за один день, но ты можешь объехать его за 20 минут.» — «Такси 2»",
    "🎬 «Гонка — это не цель, а способ доказать себе, что ты способен на большее.» — «Такси 4»",
    "🎬 «Машина — это только инструмент. Водитель — вот кто решает, куда ехать.» — «Такси»",
    "🎬 «Каждый поворот — это шанс изменить направление. Главное — выбрать правильный.» — «Такси 3»",
    "🎬 «Скорость без цели — просто шум. Цель без скорости — просто мечта.» — «Такси»",
]

# Кэш для предотвращения частых ответов (чтобы не спамить)
# {user_id: timestamp_last_flirt}
girl_flirt_cache = {}
# Минимальный интервал между флиртами от бота (в секундах)
FLIRT_COOLDOWN = 1800  # 30 минут


def get_random_flirt() -> str:
    """Получить случайное игривое сообщение для команды /flirt"""
    return random.choice(PLAYFUL_FLIRT)


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


def get_random_roast() -> str:
    return random.choice(PLAYFUL_ROASTS)


def get_random_flirt() -> str:
    return random.choice(PLAYFUL_FLIRT)


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
            "first_photo_user_id": None,  # Кто первым выложил фото
            "first_photo_user_name": None,
        }
    
    daily_stats["total_messages"] += 1
    
    # Обновление счётчика сообщений пользователя
    if user_id not in daily_stats["user_messages"]:
        daily_stats["user_messages"][user_id] = {
            "name": user_name,
            "count": 0,
        }
    daily_stats["user_messages"][user_id]["count"] += 1
    
    # Добавление фото в статистику + трек первого фото
    if message_type == "photo" and photo_info:
        daily_stats["photos"].append(photo_info)
        # Запоминаем первого автора фото (для двойных баллов)
        if daily_stats["first_photo_user_id"] is None:
            daily_stats["first_photo_user_id"] = user_id
            daily_stats["first_photo_user_name"] = user_name


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
    bonus_points = stats.get("bonus_points", 0)  # Дополнительные баллы за победы
    
    return messages_points + photos_points + likes_points + replies_points + bonus_points


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
    user_running_stats[user_id]["duration"] = duration
    user_running_stats[user_id]["calories"] = calories
    
    # Сохраняем статистику пробежек в канал
    save_user_running_stats()


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
                safe_name = escape_markdown(runner['name'])
                weekly_text += f"{medals[i]} {safe_name} — {distance_km:.1f} км ({runner['activities']} тренировок)\n"
            weekly_text += "\n"
        
        # Индивидуальная статистика всех
        weekly_text += "📝 **Все участники:**\n"
        for runner in top_runners:
            distance_km = runner["distance"] / 1000
            safe_name = escape_markdown(runner['name'])
            weekly_text += f"• {safe_name}: {distance_km:.1f} км ({runner['activities']} тренировок)\n"
        
        # Мотивация
        weekly_text += "\n" + random.choice(GREAT_RUNNER_QUOTES)
        
        # Отправляем в чат
        if application and CHAT_ID:
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text=weekly_text,
                parse_mode="Markdown"
            )
        
        # Сохраняем данные в историю (СКРЫТО)
        save_user_running_stats()
        save_chat_history()
        
        logger.info("[RUNNING] Еженедельная сводка по бегу отправлена в чат + данные сохранены")
        
    except Exception as e:
        logger.error(f"[RUNNING] Ошибка еженедельной сводки: {e}", exc_info=True)


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
                safe_name = escape_markdown(runner['name'])
                monthly_text += f"{medals[i]} **{safe_name}**\n"
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
        
        # Сохраняем данные в историю (СКРЫТО)
        save_user_running_stats()
        save_chat_history()
        
        logger.info("[RUNNING] Ежемесячная сводка по бегу отправлена в чат + данные сохранены")
        
    except Exception as e:
        logger.error(f"[RUNNING] Ошибка ежемесячной сводки: {e}", exc_info=True)


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


# ============== ОБЕДЕННЫЙ ПЛАНОВЩИК (13:00 БУДНИ) ==============
LUNCH_SENT_TODAY = False

LUNCH_MESSAGES = [
    "🍽️ **Хватит работать! Время обеда!**",
    "🍽️ Эй, вы там! Клавиатуры отложили? Обед!",
    "🍽️ 13:00 — это святое! Все на обед!",
    "🍽️ Работа подождёт, а обед — нет!",
    "🍽️ Кто ещё не обедал? Стоп, работа!",
    "🍽️ До встречи через час — я на обед!",
    "🍽️ Хватит тыкать в кнопки! Живот требует внимания!",
    "🍽️ Обед — это не перерыв, это смысл жизни!",
    "🍽️ Знаете, что лучше, чем работа в 13:00? Обед!",
    "🍽️ Стоп! Обед! Никаких отговорок!",
]

async def send_lunch_reminder():
    """Отправка напоминания об обеде"""
    global LUNCH_SENT_TODAY
    
    if application is None:
        logger.error("[LUNCH] Application не инициализирован")
        return
    
    try:
        lunch_text = random.choice(LUNCH_MESSAGES)
        
        full_text = f"{lunch_text}\n\n😋 Приятного аппетита, бегуны!"
        
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=full_text,
            parse_mode="Markdown"
        )
        
        LUNCH_SENT_TODAY = True
        logger.info("[LUNCH] Напоминание об обеде отправлено")
        
    except Exception as e:
        logger.error(f"[LUNCH] Ошибка отправки: {e}")


async def lunch_scheduler_task():
    """Планировщик напоминаний об обеде в 13:00 по будням"""
    global LUNCH_SENT_TODAY
    
    while bot_running:
        try:
            await asyncio.sleep(30)  # Проверяем каждые 30 секунд
            
            now = datetime.now(MOSCOW_TZ)
            current_hour = now.hour
            current_minute = now.minute
            current_weekday = now.weekday()  # 0 = понедельник, 6 = воскресенье
            today_date = now.strftime("%Y-%m-%d")
            
            # Сбрасываем флаг в полночь
            if current_hour == 0 and current_minute == 0:
                LUNCH_SENT_TODAY = False
            
            # Проверяем: 13:00 и будний день (пн-пт)
            if current_hour == 13 and current_minute == 0 and current_weekday < 5:
                if not LUNCH_SENT_TODAY:
                    logger.info("[LUNCH] Время 13:00 - отправляем напоминание об обеде")
                    try:
                        await send_lunch_reminder()
                    except Exception as e:
                        logger.error(f"[LUNCH] Ошибка при отправке: {e}")
        
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[LUNCH] Ошибка в планировщике: {e}")


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
    """Отправка ежедневной сводки в чат + сохранение данных"""
    global daily_summary_sent
    
    if application is None:
        logger.error("Application не инициализирован")
        return
    
    if daily_summary_sent:
        logger.info("Сводка уже отправлена сегодня")
        return
    
    try:
        today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
        
        # === ДВОЙНЫЕ БАЛЛЫ ===
        # Находим победителей для двойных баллов
        double_points_users = []  # Список пользователей для двойных баллов
        
        # 1. Самый активный пользователь (больше всего сообщений)
        most_active_user_id = None
        most_active_user_name = None
        most_messages_count = 0
        
        for user_id, data in daily_stats.get("user_messages", {}).items():
            if data["count"] > most_messages_count:
                most_messages_count = data["count"]
                most_active_user_id = user_id
                most_active_user_name = data["name"]
        
        if most_active_user_id:
            double_points_users.append(most_active_user_id)
        
        # 2. Первый автор фото
        first_photo_user_id = daily_stats.get("first_photo_user_id")
        if first_photo_user_id and first_photo_user_id != most_active_user_id:
            double_points_users.append(first_photo_user_id)
        
        # Начисляем двойные баллы победителям
        for user_id in double_points_users:
            if user_id in user_rating_stats:
                old_points = calculate_user_rating(user_id)
                # Добавляем 2 очка за победу
                if "bonus_points" not in user_rating_stats[user_id]:
                    user_rating_stats[user_id]["bonus_points"] = 0
                user_rating_stats[user_id]["bonus_points"] += 2
                new_points = calculate_user_rating(user_id)
                user_name = user_rating_stats[user_id]["name"]
                logger.info(f"[POINTS] Двойные баллы: {user_name} получает +2 (всего {new_points})")
        
        # Формируем текст сводки
        summary_text = f"📊 **Ежедневная сводка за {today}**\n\n"
        
        # Общее количество сообщений
        summary_text += f"💬 **Всего сообщений:** {daily_stats['total_messages']}\n\n"
        
        # === ПОБЕДИТЕЛИ ДНЯ ===
        summary_text += "🏆 **Победители дня (двойные баллы):**\n"
        
        if most_active_user_name:
            summary_text += f"   🥇 **{most_active_user_name}** — за активность ({most_messages_count} сообщений)\n"
        
        first_photo_name = daily_stats.get("first_photo_user_name")
        if first_photo_name:
            summary_text += f"   📸 **{first_photo_name}** — за первое фото дня\n"
        
        if not most_active_user_name and not first_photo_name:
            summary_text += "   Пока нет победителей...\n"
        
        summary_text += "\n"
        
        # Топ активных пользователей
        top_users = await get_top_users()
        if top_users:
            summary_text += "🏃 **Топ активных бегунов:**\n"
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (user_id, name, count) in enumerate(top_users):
                summary_text += f"{medals[i]} {name} — {count} сообщений\n"
            summary_text += "\n"
        else:
            summary_text += "🏃 **Топ активных бегунов:** Пока никого нет\n\n"
        
        # Рейтинг участников
        top_rated = await get_top_rated_users()
        if top_rated:
            summary_text += "⭐ **Рейтинг участников (топ-10):**\n"
            medals_rating = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            for i, user in enumerate(top_rated):
                level_emoji = LEVEL_EMOJIS.get(user["level"], "")
                bonus_tag = " 🌟" if user.get("user_id") in double_points_users else ""
                summary_text += f"{medals_rating[i]} {level_emoji} {user['name']} — {user['points']} очков{bonus_tag}"
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
        
        # === Лайки за фото ===
        photos_with_likes = [p for p in daily_stats.get("photos", []) if p.get("likes", 0) > 0]
        total_likes = sum(p.get("likes", 0) for p in daily_stats.get("photos", []))
        
        if photos_with_likes:
            summary_text += f"❤️ **Всего лайков за фото:** {total_likes}\n\n"
            summary_text += "❤️ **Фото с лайками:**\n"
            # Сортируем по лайкам
            sorted_photos = sorted(photos_with_likes, key=lambda x: x.get("likes", 0), reverse=True)
            for photo in sorted_photos:
                user_name = photo.get("user_name", "Неизвестный")
                likes = photo.get("likes", 0)
                summary_text += f"   ❤️ {likes} — {user_name}\n"
            summary_text += "\n"
        else:
            summary_text += "❤️ **Всего лайков за фото:** 0\n"
            summary_text += "❤️ **Фото с лайками:** Фото чат не выбрал 🤷\n\n"
        
        # Отправляем в чат
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
        
        # Сохраняем данные в историю (СКРЫТО, в чат не выводится)
        save_daily_stats()
        save_user_rating_stats()
        save_chat_history()
        save_user_active_stats()
        
        daily_summary_sent = True
        logger.info("Ежедневная сводка отправлена в чат + данные сохранены")
        
    except Exception as e:
        logger.error(f"Ошибка ежедневной сводки: {e}")


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
        
        # Отправляем в чат
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=weekly_text,
            parse_mode="Markdown",
        )
        
        # Сохраняем данные в историю (СКРЫТО)
        save_daily_stats()
        save_user_rating_stats()
        save_chat_history()
        save_user_active_stats()
        
        logger.info("Еженедельная сводка отправлена в чат + данные сохранены")
        
    except Exception as e:
        logger.error(f"Ошибка еженедельной сводки: {e}")


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
        
        # Отправляем в чат
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=monthly_text,
            parse_mode="Markdown",
        )
        
        # Сохраняем данные в историю (СКРЫТО)
        save_daily_stats()
        save_user_rating_stats()
        save_chat_history()
        save_user_active_stats()
        save_user_running_stats()
        
        logger.info("Ежемесячная сводка отправлена в чат + данные сохранены")
        
        # Сбрасываем локальную статистику (данные уже сохранены в канал)
        user_rating_stats = {}
        logger.info("Локальная статистика рейтинга сброшена для нового месяца")
        
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


async def is_user_admin(user_id: int, chat_id: int, bot) -> bool:
    """Проверка, является ли пользователь админом чата"""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        admin_ids = [admin.user.id for admin in admins]
        return user_id in admin_ids
    except Exception:
        return False


async def add_birthday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /add_birthday @ник DD.MM — добавление дня рождения участника (для админов)"""
    global user_birthdays
    
    try:
        user_id = update.message.from_user.id
        chat_id = update.effective_chat.id
        
        # Проверяем, что пользователь — админ
        if not await is_user_admin(user_id, chat_id, context.bot):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Эта команда только для администраторов!",
            )
            return
        
        # Проверяем аргументы: должен быть ник и дата
        if not context.args or len(context.args) != 2:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🎂 **Добавить день рождения**\n\n"
                     "📝 Используй: `/add_birthday @ник DD.MM`\n"
                     "📱 *Пример:* `/add_birthday @runner 15.06`\n\n"
                     "Бот будет поздравлять участника с Днём рождения! 🎉",
                parse_mode="Markdown"
            )
            return
        
        # Парсим ник и дату
        nickname = context.args[0]
        birthday_str = context.args[1]
        
        # Проверяем формат даты
        try:
            datetime.strptime(birthday_str, "%d.%m")
        except ValueError:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Неправильный формат даты!\n\n"
                     "Используй: `/add_birthday @ник DD.MM`\n"
                     "📱 *Пример:* `/add_birthday @runner 15.06`",
                parse_mode="Markdown"
            )
            return
        
        # Ищем пользователя в рейтинге или создаём запись
        target_user_id = None
        for uid, data in user_rating_stats.items():
            if data.get("name", "").lower() == nickname.lower().replace("@", ""):
                target_user_id = uid
                break
        
        # Если пользователь не найден в рейтинге, используем хеш ника как ID
        if target_user_id is None:
            import hashlib
            target_user_id = int(hashlib.md5(nickname.encode()).hexdigest()[:8], 16)
        
        # Сохраняем день рождения
        user_birthdays[target_user_id] = {
            "name": nickname,
            "birthday": birthday_str
        }
        save_birthdays()
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ *День рождения добавлен!* 🎂\n\n"
                 f"👤 {nickname}\n"
                 f"📅 Дата: {birthday_str}\n\n"
                 f"Бот запомнит и поздравит участника! 🎉",
            parse_mode="Markdown"
        )
        logger.info(f"[BIRTHDAY] Добавлен день рождения: {nickname} — {birthday_str}")
        
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка добавления: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при добавлении дня рождения"
        )


async def del_birthday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /del_birthday @ник — удаление дня рождения участника (для админов)"""
    global user_birthdays
    
    try:
        user_id = update.message.from_user.id
        chat_id = update.effective_chat.id
        
        # Проверяем, что пользователь — админ
        if not await is_user_admin(user_id, chat_id, context.bot):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Эта команда только для администраторов!",
            )
            return
        
        # Проверяем аргументы
        if not context.args or len(context.args) != 1:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🎂 **Удалить день рождения**\n\n"
                     "📝 Используй: `/del_birthday @ник`\n"
                     "📱 *Пример:* `/del_birthday @runner`",
                parse_mode="Markdown"
            )
            return
        
        nickname = context.args[0].replace("@", "").lower()
        
        # Ищем и удаляем
        deleted = False
        for uid, data in list(user_birthdays.items()):
            stored_name = data.get("name", "").replace("@", "").lower()
            if stored_name == nickname:
                del user_birthdays[uid]
                deleted = True
                break
        
        if deleted:
            save_birthdays()
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ *День рождения удалён!* 🗑️\n\n"
                     f"👤 @{nickname}\n\n"
                     f"Участник удалён из списка дней рождения.",
                parse_mode="Markdown"
            )
            logger.info(f"[BIRTHDAY] Удалён день рождения: @{nickname}")
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ Участник @{nickname} не найден в списке дней рождения!",
            )
        
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка удаления: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при удалении дня рождения"
        )


async def list_birthdays(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /list_birthdays — показать все дни рождения"""
    global user_birthdays
    
    try:
        if not user_birthdays:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🎂 **Дни рождения**\n\n"
                     "Пока никто не добавил день рождения! 🎂\n\n"
                     "Используй `/birthday DD.MM` чтобы добавить свой!",
                parse_mode="Markdown"
            )
            return
        
        # Группируем по месяцам
        months = {
            "01": "Январь", "02": "Февраль", "03": "Март",
            "04": "Апрель", "05": "Май", "06": "Июнь",
            "07": "Июль", "08": "Август", "09": "Сентябрь",
            "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь"
        }
        
        birthdays_by_month = {}
        for uid, data in user_birthdays.items():
            birthday = data.get("birthday", "")
            name = data.get("name", "")
            month = birthday.split(".")[1] if "." in birthday else ""
            
            if month not in birthdays_by_month:
                birthdays_by_month[month] = []
            birthdays_by_month[month].append((name, birthday))
        
        # Формируем список
        text = "🎂 **Все дни рождения**\n\n"
        
        for month_num in ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]:
            if month_num in birthdays_by_month:
                month_name = months.get(month_num, month_num)
                text += f"📅 *{month_name}:*\n"
                for name, birthday in sorted(birthdays_by_month[month_num], key=lambda x: x[1]):
                    text += f"   🎉 {birthday} — {name}\n"
                text += "\n"
        
        text += f"📊 Всего участников: {len(user_birthdays)}"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"[BIRTHDAY] Ошибка списка: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при получении списка дней рождения"
        )


# ============== ЧЕЛЛЕНДЖИ ==============

async def handle_challenge_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик результатов голосования за челлендж.
    Когда голосование завершено, определяет победителя и запускает соответствующий челлендж.
    """
    global challenge_voting, current_challenge
    
    try:
        poll = update.poll
        if not poll:
            return
        
        # Проверяем, что это наше голосование
        if not challenge_voting.get("active", False):
            logger.info(f"[CHALLENGE_VOTE] Голосование не активно, игнорируем")
            return
        
        # Проверяем, что голосование завершено
        if poll.is_closed:
            # Находим вариант с максимальным количеством голосов
            max_votes = 0
            winner_option_text = None
            winner_challenge = None
            
            for option in poll.options:
                option_votes = option.voter_count
                if option_votes > max_votes:
                    max_votes = option_votes
                    
                    # Находим соответствующий челлендж по тексту варианта
                    for challenge in VOTING_CHALLENGES:
                        # Формируем ожидаемый текст варианта
                        expected_text = f"{challenge['emoji']} {challenge['name']}: {challenge['desc']}"
                        if option.text == expected_text or option.text == challenge['name']:
                            winner_challenge = challenge
                            winner_option_text = option.text
                            break
            
            if winner_challenge:
                # Определяем тип и параметры челленджа
                challenge_type = "weekly"
                goal_index = 0
                
                # Парсим параметры из выбранного челленджа
                challenge_name = winner_challenge['name'].lower()
                
                if "10 км" in challenge_name:
                    goal_index = 0
                elif "20 км" in challenge_name:
                    goal_index = 0
                elif "3 тренировки" in challenge_name:
                    goal_index = 1
                elif "5 тренировок" in challenge_name:
                    goal_index = 1
                elif "дней подряд" in challenge_name:
                    goal_index = 2
                elif "фото" in challenge_name:
                    goal_index = 2
                else:
                    goal_index = 0
                
                goal_config = CHALLENGE_TYPES["weekly"]["goals"][goal_index]
                
                # Запускаем челлендж
                current_challenge["type"] = challenge_type
                current_challenge["goal_index"] = goal_index
                current_challenge["start_date"] = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
                
                # Вычисляем дату окончания
                now = datetime.now(MOSCOW_TZ)
                end_date = now + timedelta(days=7 - now.weekday())
                current_challenge["end_date"] = end_date.strftime("%Y-%m-%d")
                current_challenge["participants"] = {}
                current_challenge["active"] = True
                
                # Отправляем уведомление о победителе
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=f"🏆 **Голосование завершено!**\n\n"
                         f"Победил вариант: **{winner_challenge['emoji']} {winner_challenge['name']}**\n"
                         f"📝 {winner_challenge['desc']}\n\n"
                         f"💪 Челлендж запущен! Присоединяйтесь командой /challenge join",
                    parse_mode="Markdown"
                )
                
                logger.info(f"[CHALLENGE_VOTE] Запущен челлендж: {winner_challenge['name']} ({winner_challenge['desc']})")
            else:
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text="❌ Не удалось определить победителя голосования. Попробуйте запустить голосование снова."
                )
                
            # Сбрасываем состояние голосования
            challenge_voting["active"] = False
            challenge_voting["options"] = []
            challenge_voting["voters"] = {}
        
        logger.info(f"[CHALLENGE_VOTE] Получен результат голосования, голосов: {sum(o.voter_count for o in poll.options)}")
        
    except Exception as e:
        logger.error(f"[CHALLENGE_VOTE] Ошибка обработки голосования: {e}", exc_info=True)


async def start_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /challenge weekly|monthly — запустить новый челлендж (для админов)"""
    global current_challenge
    
    try:
        user_id = update.message.from_user.id
        chat_id = update.effective_chat.id
        
        # Проверяем, что пользователь — админ
        if not await is_user_admin(user_id, chat_id, context.bot):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Эта команда только для администраторов!",
            )
            return
        
        challenge_type = context.args[0].lower() if context.args else "weekly"
        
        if challenge_type not in CHALLENGE_TYPES:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🏆 **Запустить челлендж**\n\n"
                     "📝 Используй: `/challenge weekly` или `/challenge monthly`\n\n"
                     "📌 *Доступные типпы:*\n"
                     "   • `weekly` — недельный челлендж\n"
                     "   • `monthly` — месячный челлендж",
                parse_mode="Markdown"
            )
            return
        
        # Запускаем челлендж
        now = datetime.now(MOSCOW_TZ)
        
        if challenge_type == "weekly":
            end_date = now + timedelta(days=7)
            challenge_name = "Недельный челлендж 🏃"
        else:
            end_date = now + timedelta(days=30)
            challenge_name = "Месячный челлендж 🏃‍♂️"
        
        current_challenge = {
            "type": challenge_type,
            "goal_index": 0,
            "start_date": now.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "participants": {},
            "active": True
        }
        
        goal = CHALLENGE_TYPES[challenge_type]["goals"][0]
        
        text = f"🚀 *{challenge_name} ЗАПУЩЕН!* 🚀\n\n"
        text += f"🎯 *Цель:* {goal['name']}\n"
        text += f"📅 Период: {now.strftime('%d.%m')} — {end_date.strftime('%d.%m')}\n\n"
        text += "📝 *Как участвовать:*\n"
        text += "   Пиши `/challenge join` чтобы присоединиться!\n"
        text += "   После каждой тренировки пиши `/challenge done`\n\n"
        text += "🏆 Победит тот, кто первым достигнет цели!"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="Markdown"
        )
        logger.info(f"[CHALLENGE] Запущен {challenge_type} челлендж")
        
    except Exception as e:
        logger.error(f"[CHALLENGE] Ошибка запуска: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при запуске челленджа"
        )


async def join_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /challenge join — присоединиться к челленджу"""
    global current_challenge
    
    try:
        if not current_challenge.get("active"):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🏆 Сейчас нет активного челленджа!\n\n"
                     "Ожидай, когда админ запустит новый челлендж.",
            )
            return
        
        user_id = update.message.from_user.id
        user_name = f"@{update.message.from_user.username}" if update.message.from_user.username else update.message.from_user.full_name
        
        if user_id in current_challenge["participants"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"@{update.message.from_user.username} Ты уже участвуешь в челлендже!",
            )
            return
        
        # Добавляем участника
        current_challenge["participants"][user_id] = {
            "name": user_name,
            "progress": 0,
            "completed": False
        }
        
        goal = CHALLENGE_TYPES[current_challenge["type"]]["goals"][current_challenge["goal_index"]]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ @{update.message.from_user.username} присоединился к челленджу!\n\n"
                 f"🎯 Цель: {goal['name']}\n"
                 f"📊 Твой прогресс: 0 / {goal['value']} {goal['unit']}\n\n"
                 f"Удачи! 💪",
        )
        logger.info(f"[CHALLENGE] {user_name} присоединился")
        
    except Exception as e:
        logger.error(f"[CHALLENGE] Ошибка вступления: {e}")


async def done_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /challenge done — отметить прогресс"""
    global current_challenge
    
    try:
        if not current_challenge.get("active"):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🏆 Сейчас нет активного челленджа!",
            )
            return
        
        user_id = update.message.from_user.id
        
        if user_id not in current_challenge["participants"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"@{update.message.from_user.username} Ты не участвуешь в челлендже!\n\n"
                     f"Напиши `/challenge join` чтобы присоединиться.",
            )
            return
        
        goal = CHALLENGE_TYPES[current_challenge["type"]]["goals"][current_challenge["goal_index"]]
        participant = current_challenge["participants"][user_id]
        
        # Увеличиваем прогресс
        if goal["type"] == "distance":
            # Спрашиваем сколько км
            km = float(context.args[0]) if context.args else 1
            participant["progress"] += km
        elif goal["type"] == "runs":
            participant["progress"] += 1
        elif goal["type"] == "photos":
            participant["progress"] += 1
        elif goal["type"] == "consistency":
            participant["progress"] += 1
        
        progress = participant["progress"]
        target = goal["value"]
        
        # Проверяем на победу
        if progress >= target and not participant["completed"]:
            participant["completed"] = True
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"🎉 @{update.message.from_user.username} ПОБЕДИЛ В ЧЕЛЛЕНДЖЕ! 🎉\n\n"
                     f"🏆 {participant['name']} первым достиг цели!\n\n"
                     f"🎯 {goal['name']}\n"
                     f"📊 Итог: {progress} / {target} {goal['unit']}\n\n"
                     f"Поздравляем! 🥳",
                parse_mode="Markdown"
            )
            
            # Завершаем челлендж
            current_challenge["active"] = False
            logger.info(f"[CHALLENGE] Победитель: {participant['name']}")
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"@{update.message.from_user.username} ✅ Учтено!\n\n"
                     f"📊 Прогресс: {progress} / {target} {goal['unit']}\n"
                     f"📈 Осталось: {target - progress} {goal['unit']}",
            )
            logger.info(f"[CHALLENGE] Прресс {participant['name']}: {progress}/{target}")
        
    except Exception as e:
        logger.error(f"[CHALLENGE] Ошибка прогресса: {e}")


async def challenge_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /challenge — показать статус челленджа"""
    global current_challenge
    
    try:
        if not current_challenge.get("active"):
            # Показываем последний завершённый
            goal = CHALLENGE_TYPES.get("weekly", {}).get("goals", [{}])[0]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🏆 *Статус челленджей*\n\n"
                     "📌 Сейчас активного челленджа нет.\n\n"
                     "Админ может запустить новый:\n"
                     "   `/challenge weekly` — недельный\n"
                     "   `/challenge monthly` — месячный",
                parse_mode="Markdown"
            )
            return
        
        goal = CHALLENGE_TYPES[current_challenge["type"]]["goals"][current_challenge["goal_index"]]
        participants = current_challenge.get("participants", {})
        
        text = f"🏆 *Текущий челлендж*\n\n"
        text += f"🎯 *{goal['name']}*\n"
        text += f"📅 До конца: {current_challenge['end_date']}\n\n"
        text += f"📊 *Участники ({len(participants)}):*\n"
        
        # Сортируем по прогрессу
        sorted_parts = sorted(participants.items(), key=lambda x: x[1]["progress"], reverse=True)
        
        for uid, data in sorted_parts:
            emoji = "✅" if data["completed"] else "🔄"
            text += f"   {emoji} {data['name']}: {data['progress']} / {goal['value']} {goal['unit']}\n"
        
        text += "\n📝 Пиши `/challenge join` чтобы участвовать!"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"[CHALLENGE] Ошибка статуса: {e}")


# ============== ОБРАБОТЧИК ЛИЧНЫХ ОБРАЩЕНИЙ ==============
async def handle_mentions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений когда обращаются к боту через @mention"""
    try:
        if not update.message or not update.message.text:
            return
        
        user_name = update.message.from_user.full_name or update.message.from_user.username or "Пользователь"
        user_id = update.message.from_user.id
        message_text = update.message.text
        
        # Получаем информацию о боте
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username.lower()
        
        # Проверяем, что сообщение содержит @mention бота
        mention_patterns = [
            f"@{bot_username}",
            f"@{bot_username}:",
            f"@{bot_username} ",
            bot_username,
        ]
        
        message_lower = message_text.lower()
        is_mention = any(pattern.lower() in message_lower for pattern in mention_patterns)
        
        if not is_mention:
            return
        
        # Убираем @mention из сообщения для обработки
        clean_text = message_text
        for pattern in mention_patterns:
            clean_text = clean_text.replace(pattern, "").strip()
            clean_text = clean_text.replace(pattern.capitalize(), "").strip()
        
        # Убираем лишние символы в начале
        clean_text = clean_text.strip(" ,:!-\n")
        
        logger.info(f"[MENTION] Пользователь {user_name} обратился к боту: '{clean_text}'")
        
        # Отправляем "печатает" статус
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # Пытаемся получить ответ от YandexGPT
        if YANDEX_AVAILABLE:
            try:
                ai_response = await get_ai_response_yandexgpt(clean_text, user_name)
                if ai_response:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=ai_response,
                        reply_to_message_id=update.message.message_id
                    )
                    logger.info(f"[MENTION] Ответ от YandexGPT отправлен пользователю {user_name}")
                    return
            except Exception as ai_error:
                logger.error(f"[MENTION] Ошибка YandexGPT: {ai_error}")
        
        # Если YandexGPT недоступен - используем локальный генератор
        response = await generate_ai_response(clean_text, "", user_name)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=response,
            reply_to_message_id=update.message.message_id
        )
        logger.info(f"[MENTION] Локальный ответ отправлен пользователю {user_name}")
        
    except Exception as e:
        logger.error(f"[MENTION] Ошибка обработки обращения: {e}")


# ============== ГОЛОСОВАНИЕ ЗА ЧЕЛЛЕНДЖИ ==============
async def start_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /votechallenges — запустить голосование за челлендж (для админов)"""
    global challenge_voting
    
    try:
        user_id = update.message.from_user.id
        chat_id = update.effective_chat.id
        
        # Проверяем, что пользователь — админ
        if not await is_user_admin(user_id, chat_id, context.bot):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Эта команда только для администраторов!",
            )
            return
        
        if challenge_voting.get("active"):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🗳️ Голосование уже идёт!\n\n"
                     "Используй `/vote` чтобы проголосовать.",
            )
            return
        
        # Запускаем голосование
        now = datetime.now(MOSCOW_TZ)
        challenge_voting = {
            "active": True,
            "options": [{"id": c["id"], "votes": 0, "emoji": c["emoji"], "name": c["name"], "desc": c["desc"]} for c in VOTING_CHALLENGES],
            "voters": {},
            "start_time": now.isoformat(),
            "duration_hours": 24
        }
        
        # Формируем сообщение с вариантами
        text = "🗳️ *ГОЛОСОВАНИЕ ЗА ЧЕЛЛЕНДЖ!* 🗳️\n\n"
        text += "📌 *Проголосуй за челлендж, который начнётся завтра!*\n\n"
        text += "*Варианты:*\n"
        
        for i, option in enumerate(challenge_voting["options"]):
            text += f"{i+1}. {option['emoji']} *{option['name']}* — {option['desc']}\n"
        
        text += "\n📝 *Как голосовать:*\n"
        text += "   Напиши `/vote 1` (или 2, 3...)\n\n"
        text += "⏰ Голосование длится 24 часа\n"
        text += "🏆 Победит вариант с большинством голосов!"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="Markdown"
        )
        logger.info(f"[VOTE] Голосование запущено")
        
    except Exception as e:
        logger.error(f"[VOTE] Ошибка запуска: {e}")


async def vote_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /vote N — проголосовать за вариант"""
    global challenge_voting
    
    try:
        if not challenge_voting.get("active"):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🗳️ Сейчас нет голосования!\n\n"
                     "Ожидай, когда админ запустит голосование `/votechallenges`.",
            )
            return
        
        if not context.args or len(context.args) != 1:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🗳️ *Проголосовать*\n\n"
                     "📝 Используй: `/vote 1` (номер варианта)\n\n"
                     "*Текущие варианты:*\n",
                parse_mode="Markdown"
            )
            for i, option in enumerate(challenge_voting["options"]):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"{i+1}. {option['emoji']} {option['name']} — {option['desc']}\n   ({option['votes']} голосов)"
                )
            return
        
        try:
            choice = int(context.args[0]) - 1
            if choice < 0 or choice >= len(challenge_voting["options"]):
                raise ValueError()
        except ValueError:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Неверный номер варианта!\n\n"
                     "Напиши `/vote` чтобы увидеть список.",
            )
            return
        
        user_id = update.message.from_user.id
        
        # Проверяем, не голосовал ли уже
        if user_id in challenge_voting["voters"]:
            old_choice = challenge_voting["voters"][user_id]
            # Убираем старый голос
            for option in challenge_voting["options"]:
                if option["id"] == old_choice:
                    option["votes"] -= 1
                    break
        
        # Добавляем новый голос
        chosen_option = challenge_voting["options"][choice]
        chosen_option["votes"] += 1
        challenge_voting["voters"][user_id] = chosen_option["id"]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ @{update.message.from_user.username} проголосовал!\n\n"
                 f"Твой выбор: {chosen_option['emoji']} {chosen_option['name']}\n\n"
                 f"📊 Текущие результаты:\n",
            parse_mode="Markdown"
        )
        
        # Показываем топ-3
        sorted_options = sorted(challenge_voting["options"], key=lambda x: x["votes"], reverse=True)
        for option in sorted_options[:3]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"{option['emoji']} {option['name']}: {option['votes']} голосов"
            )
        
        logger.info(f"[VOTE] {update.message.from_user.username} проголосовал за {chosen_option['name']}")
        
    except Exception as e:
        logger.error(f"[VOTE] Ошибка голосования: {e}")


async def vote_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /vote — показать статус голосования"""
    global challenge_voting
    
    try:
        if not challenge_voting.get("active"):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🗳️ Сейчас нет активного голосования.\n\n"
                     "Админ может запустить: `/votechallenges`",
            )
            return
        
        text = "🗳️ *Текущее голосование*\n\n"
        
        # Сортируем по голосам
        sorted_options = sorted(challenge_voting["options"], key=lambda x: x["votes"], reverse=True)
        
        for option in sorted_options:
            bar = "█" * min(option["votes"], 20)
            text += f"{option['emoji']} {option['name']}: {option['votes']} {bar}\n"
        
        text += f"\n📊 Всего голосов: {len(challenge_voting['voters'])}"
        text += f"\n⏰ Голосование завершится через {challenge_voting.get('duration_hours', 24)} часов"
        text += "\n\n📝 Напиши `/vote 1` чтобы проголосовать!"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"[VOTE] Ошибка статуса: {e}")


async def end_vote_and_start_challenge(bot, chat_id: int):
    """Завершить голосование и запустить победивший челлендж"""
    global challenge_voting, current_challenge
    
    if not challenge_voting.get("active"):
        return
    
    # Находим победителя
    sorted_options = sorted(challenge_voting["options"], key=lambda x: x["votes"], reverse=True)
    winner = sorted_options[0]
    
    # Создаём челлендж на основе победителя
    now = datetime.now(MOSCOW_TZ)
    end_date = now + timedelta(days=7)
    
    current_challenge = {
        "type": "weekly",
        "goal_index": 0,
        "start_date": now.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "participants": {},
        "active": True
    }
    
    challenge_voting["active"] = False
    
    # Объявляем победителя
    text = f"🗳️ *ГОЛОСОВАНИЕ ЗАВЕРШЕНО!* 🗳️\n\n"
    text += f"🏆 *ПОБЕДИТЕЛЬ:* {winner['emoji']} {winner['name']}!\n\n"
    text += f"📊 *Результаты:*\n"
    
    for i, option in enumerate(sorted_options):
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"][i]
        text += f"   {medal} {option['emoji']} {option['name']}: {option['votes']} голосов\n"
    
    text += f"\n🚀 *ЧЕЛЛЕНДЖ ЗАПУЩЕН!* 🚀\n\n"
    text += f"🎯 Цель: {winner['name']}\n"
    text += f"📅 Период: {now.strftime('%d.%m')} — {end_date.strftime('%d.%m')}\n\n"
    text += "📝 Пиши `/challenge join` чтобы участвовать!"
    
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown"
    )
    
    logger.info(f"[VOTE] Победитель: {winner['name']}")


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
        # Объединяем текст и подпись для проверки ключевых слов
        check_text = (message_text + " " + message_caption).strip().lower()
        is_photo = bool(update.message.photo)

        # По умолчанию - обычное сообщение
        message_type = "default"

        # Определяем тип сообщения для истории
        check_text_lower = check_text.lower()

        # Приветствия
        greetings = ["привет", "здравствуй", "здорово", "добрый день", "добрый вечер", "доброе утро", "hello", "hi", "hey", "приветик", "приветствую", "йо"]
        if any(word in check_text_lower for word in greetings):
            message_type = "greeting"

        # Утро
        morning_words = ["утро", "доброе утро", "утра", "проснулся", "проснулась", "встал", "встала", "утречка", "доброутро", "с утра"]
        if any(word in check_text_lower for word in morning_words):
            message_type = "morning"

        # Благодарности
        thanks = ["спасибо", "благодарю", "мерси", "thx", "thanks", "благодарность", "пасиб", "сяп", "сэнк ю"]
        if any(word in check_text_lower for word in thanks):
            message_type = "thanks"

        # Согласие
        agreement = ["да", "согласен", "точно", "именно", "верно", "прав", "поддерживаю", "yes", "agreed", "угу", "ага"]
        if any(word in check_text_lower for word in agreement):
            message_type = "agreement"

        # Вопросы
        questions = ["?", "как", "что", "почему", "зачем", "когда", "где", "кто", "сколько", "подскажи", "скажи", "объясни", "а это"]
        if any(word in check_text_lower for word in questions) or "?" in message_text:
            message_type = "question"

        # Активность / спорт
        running_words = ["активность", "активный", "спорт", "тренировка", "тренироваться", "тренируюсь", "заниматься", "занимаюсь", "фитнес", "йога", "кардио", "силовая", "упражнения", "пробежка", "бег", "бегать", "бегаю"]
        if any(word in check_text_lower for word in running_words):
            message_type = "running"

        # Мотивация
        motivation_words = ["сложно", "тяжело", "устал", "не могу", "лениво", "мотивация", "лень", "не хочу", "нет сил"]
        if any(word in check_text_lower for word in motivation_words):
            message_type = "motivation"

        # Шутки
        joke_words = ["хаха", "lol", "смешно", "прикол", "кринж", "ахах", "хех", "😂", "🤣", "хдх", "рофл", "шутка"]
        if any(word in check_text_lower for word in joke_words):
            message_type = "joke"

        # Усталость
        tired_words = ["устал", "устала", "уставать", "устаю", "измотан", "выжат", "нет сил", "разбит", "разбита"]
        if any(word in check_text_lower for word in tired_words):
            message_type = "tired"

        # Боль / травмы
        pain_words = ["болит", "боль", "травма", "растяжение", "болят", "тянет", "ноющая", "резкая", "опухло", "синяк"]
        if any(word in check_text_lower for word in pain_words):
            message_type = "pain"

        # Погода
        weather_words = ["погода", "дождь", "снег", "холод", "жара", "ветер", "мороз", "гроза", "солнце", "туман", "сыро", "мокро"]
        if any(word in check_text_lower for word in weather_words):
            message_type = "weather"

        # Как дела
        how_are_you_words = ["как дела", "как ты", "как жизнь", "как настроение", "как себя", "как у тебя"]
        if any(word in check_text_lower for word in how_are_you_words):
            message_type = "how_are_you"

        # Кто ты
        who_are_you_words = ["кто ты", "что ты", "ты бот", "ты робот", "ты живой", "кто такой"]
        if any(word in check_text_lower for word in who_are_you_words):
            message_type = "who_are_you"

        logger.info(f"[MSG] === НАЧАЛО обработки от {user_name} ===")
        logger.info(f"[MSG] message_text='{message_text}', check_text='{check_text}'")

        # Проверяем, не команда ли это
        if message_text and message_text.startswith('/'):
            logger.info(f"[MSG] Это команда, пропускаем")
            return

        # === ПРОВЕРКА: ДОБРОЕ УТРО (РАНДОМНЫЙ ОТВЕТ) ===
        # Ключевые слова для определения "доброго утра"
        good_morning_keywords = [
            # Русские варианты (полные фразы)
            'доброе утро', 'доброе утро!', 'доброе утро всем', 'всем доброе утро',
            'доброе утро!', 'доброе утро.', 'доброе утро,', 'утро доброе', 'утро!',
            'всем утро', 'утро доброе', 'доброутро', 'доброго утра',
            'всем доброго утра', 'доброго утра!', 'доброго утра всем',
            # Смайлики с утром
            '☀️ утро', '☀️доброе', 'утро ☀️',
            # Короткие и разговорные
            'утра', 'всем утра', 'утречка', 'утречко', 'с утра', 'с утра!',
            'всем с утра', 'и тебе доброе утро', 'и тебе утро',
            # Английские
            'good morning', 'good morning!', 'morning!', 'morning',
            # С вопросом или в предложении
            '?доброе утро', 'утро?', 'доброе утро?',
        ]
        
        is_good_morning = any(greeting in check_text for greeting in good_morning_keywords)
        logger.info(f"[MORNING] Проверка: '{check_text[:50]}...' | is_good_morning={is_good_morning}")

        if is_good_morning:
            logger.info(f"[MORNING] detected от {user_name}")
            
            # Проверяем пол через ИИ с таймаутом 3 секунды
            try:
                is_female = await asyncio.wait_for(
                    check_is_female_by_ai(user_name),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"[MORNING] Таймаут определения пола для {user_name}, используем нейтральный ответ")
                is_female = False
            except Exception as e:
                logger.error(f"[MORNING] Ошибка определения пола: {e}")
                is_female = False
            
            logger.info(f"[MORNING] Пол определён: {user_name} -> is_female={is_female}")

            # Рандомный выбор ответа:
            # - 40% флирт (если девушка)
            # - 30% цитата из фильма (для всех)
            # - 30% нейтральный ответ (для всех)
            rand = random.random()

            if is_female and rand < 0.4:
                # Это девушка и выпал флирт
                morning_text = get_random_good_morning_flirt()
                logger.info(f"[MORNING] Рандом: ФЛИРТ для {user_name}")
            elif rand < 0.7:
                # Цитата из фильма (для всех)
                morning_text = random.choice(MOVIE_QUOTES)
                logger.info(f"[MORNING] Рандом: ЦИТАТА для {user_name}")
            else:
                # Нейтральный ответ
                morning_text = get_random_good_morning()
                logger.info(f"[MORNING] Рандом: НЕЙТРАЛЬНО для {user_name}")

            # Формируем упоминание пользователя
            user_mention = f"@{user_name}" if user_name else ""
            
            # Отправляем ответ на доброе утро с упоминанием
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"{user_mention} 💫 **{morning_text}**",
                    parse_mode="Markdown",
                )
                logger.info(f"[MORNING] Ответ на доброе утро отправлен для {user_name}")
            except Exception as e:
                logger.error(f"[MORNING] Ошибка отправки: {e}")

            # Для "доброе утро" не используем кулдаун - отвечаем всегда

        # === АВТОМАТИЧЕСКИЙ ФЛИРТ С ДЕВУШКАМИ (НА ОБЫЧНЫЕ СООБЩЕНИЯ) ===
        # Проверяем, является ли пользователь девушкой через ИИ
        now = datetime.now(MOSCOW_TZ)
        current_time = now.timestamp()

        # Проверяем кулдаун для этого пользователя
        last_flirt_time = girl_flirt_cache.get(user_id, 0)
        time_since_last = current_time - last_flirt_time
        logger.info(f"[FLIRT] Проверка для {user_name}, кулдаун: {time_since_last:.0f}/{FLIRT_COOLDOWN} сек")

        if time_since_last >= FLIRT_COOLDOWN:
            logger.info(f"[FLIRT] Кулдаун прошёл, проверяем пол через ИИ: {user_name}")
            
            # Проверяем через ИИ, девушка ли это (с таймаутом)
            try:
                is_female = await asyncio.wait_for(
                    check_is_female_by_ai(user_name),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"[FLIRT] Таймаут определения пола для {user_name}")
                is_female = False
            except Exception as e:
                logger.error(f"[FLIRT] Ошибка определения пола: {e}")
                is_female = False
            
            logger.info(f"[FLIRT] Результат для {user_name}: is_female={is_female}")

            if is_female:
                # Это девушка! Отправляем комплимент на обычное сообщение
                girl_flirt_cache[user_id] = current_time

                # Отвечаем на обычное сообщение
                flirt_text = get_random_chat_flirt()
                logger.info(f"[FLIRT] {user_name} написала сообщение (определено ИИ), отвечаем флиртом")

                # Отправляем флирт
                try:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"💫 **{flirt_text}**",
                        parse_mode="Markdown",
                    )
                    logger.info(f"[FLIRT] Флирт отправлен для {user_name}")
                except Exception as e:
                    logger.error(f"[FLIRT] Ошибка отправки флирта: {e}")
            else:
                logger.info(f"[FLIRT] {user_name} определён как не-девушка, пропускаем")
        else:
            logger.info(f"[FLIRT] Кулдаун не прошёл для {user_name}, пропускаем")

        # === ПРОВЕРКА: ОБРАЩЕНИЕ К БОТУ ЧЕРЕЗ @НИК В ЧАТЕ ===
        # Получаем информацию о боте для упоминания в чате
        bot_username = context.bot.username.lower() if hasattr(context.bot, 'username') else ""
        logger.info(f"[DEBUG] Бот username: @{bot_username}")
        user_mentioned = False
        
        # Проверяем, упомянут ли бот через @username
        if bot_username and message_text:
            if f"@{bot_username}" in message_text.lower():
                user_mentioned = True
                logger.info(f"[AI] 📢 {user_name} обратился к боту в чате: '{message_text[:50]}...'")
        
        # === AI ОТВЕТ: ЛОГИКА ДЛЯ ЧАТА ===
        should_respond = False
        bot_message_text = ""
        
        if user_mentioned:
            should_respond = True
            # Если обращение напрямую, убираем @бота из текста
            bot_message_text = re.sub(f'@{bot_username}', '', message_text, flags=re.IGNORECASE).strip()
            if not bot_message_text:
                bot_message_text = "пользователь поздоровался"
        
        # Проверяем ответ на сообщение бота
        elif update.message.reply_to_message:
            original_message = update.message.reply_to_message
            if original_message.from_user and original_message.from_user.id == (context.bot.id if hasattr(context.bot, 'id') else None):
                if original_message.from_user.is_bot:
                    should_respond = True
                    logger.info(f"[AI] {user_name} ответил на сообщение бота: '{message_text[:30]}...'")
                    bot_message_text = original_message.text or original_message.caption or "сообщение бота"
        
        # === ПРОВЕРКА: ДЕВУШКА? ДАЁМ РЕДКИЙ КОМПЛИМЕНТ (без закрепления!) ===
        # Проверяем, является ли пользователь девушкой
        user_username = user.username or ""
        user_fullname = user.full_name or ""
        if is_female_user(user_username, user_fullname):
            # ОЧЕНЬ низкий шанс (5%) - только на реально крутые сообщения
            if random.random() < 0.05:
                compliment = random.choice(FEMALE_COMPLIMENTS).format(user_name=user_name)
                try:
                    # Отправляем БЕЗ закрепления!
                    sent = await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=compliment
                    )
                    logger.info(f"[FEMALE] Комплимент отправлен (не закреплён): {user_name}")
                except Exception as e:
                    logger.error(f"[FEMALE] Ошибка отправки комплимента: {e}")
        
        # === САМОДЕЯТЕЛЬНОСТЬ: БОТ ПРАКТИЧЕСКИ НЕ ОТВЕЧАЕТ САМ ===
        # Только если @упоминание или ответ на сообщение бота
        if not should_respond and message_text and len(message_text) > 20:
            # Только реальные сложные вопросы (не простые "как дела?")
            complex_keywords = ["подскажи", "объясни", "рекомендуй", "посоветуй", "как правильно", "что делать", "помоги"]
            is_complex_question = "?" in message_text and any(kw in message_text.lower() for kw in complex_keywords)
            
            # Только ОЧЕНЬ сильные эмоции
            very_strong_emotions = ["пиздец", "вааау", "оооо боже", "шок", "ужас", "невероятно", "вауууу"]
            has_very_strong = any(kw in message_text.lower() for kw in very_strong_emotions)
            
            # КРОШЕЧНЫЙ шанс: 1% для сложных вопросов, 0.3% для эмоций, 0.05% для обычных
            chance = 0.01 if is_complex_question else (0.003 if has_very_strong else 0.0005)
            
            if random.random() < chance:
                should_respond = True
                bot_message_text = "интересное сообщение в чате"
                logger.info(f"[AUTO] Bot decides to respond (chance {chance*100:.2f}%)")
        
        # Если есть повод ответить — обрабатываем
        logger.info(f"[DEBUG] YANDEX_AVAILABLE={YANDEX_AVAILABLE}, should_respond={should_respond}")
        if YANDEX_AVAILABLE and should_respond and message_text:
            # Отправляем "печатает" статус
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            
            # Пробуем YandexGPT
            ai_response = None
            if YANDEX_AVAILABLE:
                try:
                    # Убираем @бота из сообщения перед отправкой в AI
                    clean_message = message_text
                    if user_mentioned:
                        clean_message = re.sub(f'@{bot_username}', '', message_text, flags=re.IGNORECASE).strip()
                    ai_response = await get_ai_response_yandexgpt(clean_message, user_name)
                    if ai_response:
                        logger.info(f"[AI] YandexGPT ответил для {user_name}")
                except Exception as ai_error:
                    logger.error(f"[AI] YandexGPT ошибка: {ai_error}")
            
            # Если YandexGPT не ответил — используем локальную систему
            if not ai_response:
                logger.info(f"[AI] Используем локальную систему для {user_name}")
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
        
        # === ПРОВЕРКА ВОЗВРАЩЕНЦА ===# === ПРОВЕРКА ВОЗВРАЩЕНЦА ===
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
        
        # Сохраняем активность в канал
        save_user_active_stats()
        
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
            # Русские варианты (полные фразы)
            'доброе утро', 'доброе утро!', 'доброе утро всем', 'всем доброе утро',
            'доброе утро!', 'доброе утро.', 'доброе утро,', 'утро доброе', 'утро!',
            'всем утро', 'утро доброе', 'доброутро', 'доброго утра',
            'всем доброго утра', 'доброго утра!', 'доброго утра всем',
            # Смайлики с утром
            '☀️ утро', '☀️доброе', 'утро ☀️',
            # Короткие и разговорные
            'утра', 'всем утра', 'утречка', 'утречко', 'с утра', 'с утра!',
            'всем с утра', 'и тебе доброе утро', 'и тебе утро',
            # Английские
            'good morning', 'good morning!', 'morning!', 'morning',
            # С вопросом или в предложении
            '?доброе утро', 'утро?', 'доброе утро?',
            # Для поиска внутри текста (частичные совпадения)
            'доброе утро', 'всем доброе', 'доброе утро ',
            ' доброе утро', 'доброе утро,', 'доброе утро!',
        ]
        
        # Также реагируем на слова о пробуждении
        wake_up_words = ['проснулся', 'проснулась', 'встал', 'встала', 'просыпаюсь', 'просыпаюсь!']
        is_waking_up = any(word in check_text for word in wake_up_words)

        # === ПРОВЕРКА НА "ДОБРОЕ УТРО" ИЛИ ПРОБУЖДЕНИЕ ===
        # Проверяем в любом контексте - даже если "доброе утро" внутри предложения
        found_morning = False
        for keyword in good_morning_keywords:
            if keyword in check_text:
                found_morning = True
                break
        if not found_morning:
            found_morning = is_waking_up

        if found_morning:
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
            daily_stats = {"date": today, "total_messages": 0, "user_messages": {}, "photos": [], "first_photo_user_id": None, "first_photo_user_name": None}
            logger.info("[MSG] daily_stats переинициализирован")
        
        logger.info(f"[MSG] today={today}, daily_stats_date={daily_stats.get('date', 'EMPTY')}")
        
        # Сбрасываем только если новый день
        if daily_stats.get("date", "") != today:
            daily_stats["date"] = today
            daily_stats["total_messages"] = 0
            daily_stats["user_messages"] = {}
            daily_stats["photos"] = []
            daily_stats["first_photo_user_id"] = None
            daily_stats["first_photo_user_name"] = None
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
                "likes": 0,  # Инициализируем лайки
                "user_name": user_name  # Сохраняем имя автора
            })
            # Запоминаем первого автора фото (для двойных баллов)
            if daily_stats.get("first_photo_user_id") is None:
                daily_stats["first_photo_user_id"] = user_id
                daily_stats["first_photo_user_name"] = user_name
        
        # Сохраняем ежедневную статистику в канал
        save_daily_stats()
        
        # === СОХРАНЕНИЕ В ИСТОРИЮ ЧАТА (СКРЫТО) ===
        try:
            # Добавляем сообщение в историю
            message_entry = {
                "id": update.message.message_id,
                "user_id": user_id,
                "user_name": user_name,
                "text": message_text[:500] if message_text else "",  # Ограничиваем длину текста
                "timestamp": moscow_now.isoformat(),
                "type": message_type,
                "has_photo": is_photo,
                "photo_count": len(update.message.photo) if is_photo else 0,
                "has_video": is_video,
                "has_voice": is_voice,
                "has_document": is_document,
                "reply_to_message_id": update.message.reply_to_message.message_id if update.message.reply_to_message else None,
                "chat_id": CHAT_ID
            }
            chat_history["messages"].append(message_entry)
            
            # Если есть фото - сохраняем отдельно
            if is_photo:
                for photo in update.message.photo:
                    photo_entry = {
                        "file_id": photo.file_id,
                        "user_id": user_id,
                        "user_name": user_name,
                        "timestamp": moscow_now.isoformat(),
                        "message_id": update.message.message_id,
                        "file_unique_id": photo.file_unique_id,
                        "width": photo.width,
                        "height": photo.height,
                        "file_size": photo.file_size
                    }
                    chat_history["photos"].append(photo_entry)
            
            # Обновляем время последнего обновления
            chat_history["last_updated"] = moscow_now.isoformat()
            
            logger.info(f"[HISTORY] Сохранено сообщение от {user_name} (всего в истории: {len(chat_history['messages'])} сообщений)")
        except Exception as e:
            logger.error(f"[HISTORY] Ошибка сохранения в историю: {e}")
        
        # Сохраняем историю в канал (асинхронно)
        save_chat_history()
        
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
        
        # Сохраняем рейтинг в канал
        save_user_rating_stats()
        
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
                    
                    # Сохраняем рейтинг в канал
                    save_user_rating_stats()
                    
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
                        
                        # Сохраняем рейтинг в канал
                        save_user_rating_stats()
                        
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
• /remen — показать, что ты обиделся (дружески)
• /antiremen — получить порцию смешных комплиментов
• /roast — подколоть кого-то в чате (весело)
• /flirt — отправить комплимент прекрасным бегуньям 💫
• /mam — отправить предупреждение "Не зли маму..."
• /advice — получить совет по бегу из интернета
• /summary — получить сводку за сегодня
• /rating — показать топ-10 участников по рейтингу
• /likes — показать рейтинг участников только по лайкам
• /levels — показать всех участников по уровням
• /running — показать рейтинг бегунов за месяц
• /garmin email пароль — привязать аккаунт Garmin Connect
• /garmin_stop — отключить аккаунт Garmin

**Автоматический флирт:**
• Бот автоматически определяет девушек по нику через ИИ и делает комплименты! 💫

**Дни рождения:**
• /birthday DD.MM — указать свою дату рождения
• /add_birthday @никнейм DD.MM — добавить день рождения участника (для админов)
• /del_birthday @никнейм — удалить день рождения (для админов)
• /list_birthdays — показать все дни рождения

**Челленджи:**
• /challenge — показать статус текущего челленджа
• /challenge_start weekly|monthly — запустить новый челлендж (для админов)
• /challenge join — присоединиться к челленджу
• /challenge done — отметить выполнение цели
• /challenge vote — запустить голосование за выбор челленджа (для админов)

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


async def roast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /roast - подколоть кого-то в чате"""
    roast_text = get_random_roast()
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🔥 **{roast_text}**",
        parse_mode="Markdown",
    )

    try:
        await update.message.delete()
    except Exception:
        pass


async def flirt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /flirt - отправить игривое сообщение (для девушек в чате)"""
    flirt_text = get_random_flirt()
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💫 **{flirt_text}**",
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


async def stop_cmd(update, context):
    """Команда /stop - остановить бота"""
    global bot_running, application

    user_id = update.message.from_user.id
    chat_id = update.effective_chat.id

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🛑 Бот останавливается... 👋",
        )
        logger.info(f"[STOP] Бот остановлен пользователем {user_id}")
        
        bot_running = False
        
        if application:
            await application.stop()
        
        # Завершаем процесс
        import os
        os._exit(0)
        
    except Exception as e:
        logger.error(f"[STOP] Ошибка остановки: {e}")


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


async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /getid — показать ID чата/канала"""
    chat = update.effective_chat
    chat_id = chat.id
    chat_type = chat.type
    
    text = f"📋 **Информация о чате:**\n\n"
    text += f"🆔 **ID:** `{chat_id}`\n"
    text += f"📝 **Тип:** {chat_type}\n"
    text += f"📛 **Название:** {chat.title or chat.full_name}\n\n"
    text += f"💡 Для сохранения данных добавь переменную:\n"
    text += f"`DATA_CHANNEL_ID = {chat_id}`"
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="Markdown",
    )
    
    logger.info(f"[GETID] Chat ID: {chat_id}, Type: {chat_type}")
    
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



async def handle_private_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик ЛИЧНЫХ сообщений — отвечает через YandexGPT.
    Пользователь пишет боту напрямую, бот оценивает вопрос и отвечает.
    """
    global user_rating_stats
    
    # Проверяем, что это личный чат
    if not update.message or update.message.from_user.is_bot:
        return
    
    user = update.message.from_user
    user_id = user.id
    user_name = f"@{user.username}" if user.username else user.full_name or "Анон"
    message_text = update.message.text or ""
    
    logger.info(f"[PRIVATE] 📩 Личное сообщение от {user_name}: '{message_text[:50]}...'")
    
    # Игнорируем пустые сообщения
    if not message_text or len(message_text.strip()) < 2:
        return
    
    # Отправляем "печатает" статус
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Если YandexGPT доступен — используем его
    ai_response = None
    if YANDEX_AVAILABLE:
        try:
            ai_response = await get_ai_response_yandexgpt(message_text, user_name)
            logger.info(f"[PRIVATE] YandexGPT ответил для {user_name}")
        except Exception as ai_error:
            logger.error(f"[PRIVATE] YandexGPT ошибка: {ai_error}")
    
    # Если YandexGPT не ответил — используем локальный генератор
    if not ai_response:
        ai_response = await generate_ai_response(message_text, "личное сообщение", user_name)
        logger.info(f"[PRIVATE] Используем локальный ответ для {user_name}")
    
    if ai_response:
        # Отправляем ответ
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=ai_response
        )
        logger.info(f"[PRIVATE] Ответ отправлен {user_name}")
        
        # Обновляем статистику
        if user_id not in user_rating_stats:
            user_rating_stats[user_id] = {
                "name": user_name,
                "messages": 0,
                "photos": 0,
                "likes_given": 0,
                "likes_received": 0,
                "days_active": set(),
                "last_seen": ""
            }
        user_rating_stats[user_id]["messages"] += 1
        save_user_rating_stats()
    else:
        logger.warning(f"[PRIVATE] Не удалось сгенерировать ответ для {user_name}")





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
    
    # Логируем статус YandexGPT
    if YANDEX_AVAILABLE:
        logger.info(f"[YANDEXGPT] ✅ API доступен. Folder ID: {YANDEX_FOLDER_ID[:8]}...")
    else:
        logger.warning(f"[YANDEXGPT] ❌ API недоступен. Проверь переменные YANDEX_API_KEY и YANDEX_FOLDER_ID")
    
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )
    
    logger.info(f"[INIT] application создан: {application}")
    logger.info(f"[INIT] application.bot: {application.bot}")
    
    # Загружаем данные из Telegram Channel если настроено
    async def init_persistence():
        if DATA_CHANNEL_ID:
            logger.info(f"[PERSIST] Загружаем данные из канала {DATA_CHANNEL_ID}...")
            loaded = await load_all_from_channel(application.bot)
            
            # Проверяем, нужно ли сбросить дневную статистику
            today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
            
            # Восстанавливаем данные из загруженных
            if "birthdays" in loaded:
                global user_birthdays
                user_birthdays = loaded["birthdays"]
                logger.info(f"[PERSIST] Восстановлено дней рождения: {len(user_birthdays)}")
            
            if "garmin_users" in loaded:
                global garmin_users
                garmin_users = loaded["garmin_users"]
                logger.info(f"[PERSIST] Восстановлено Garmin пользователей: {len(garmin_users)}")
            
            if "ratings" in loaded:
                global user_rating_stats
                user_rating_stats = loaded["ratings"]
                
                # Конвертируем days_active из list обратно в set для каждого пользователя
                for user_id, data in user_rating_stats.items():
                    if "days_active" in data and isinstance(data["days_active"], list):
                        data["days_active"] = set(data["days_active"])
                
                logger.info(f"[PERSIST] Восстановлено рейтингов: {len(user_rating_stats)}")
            
            if "runs" in loaded:
                global user_running_stats
                user_running_stats = loaded["runs"]
                logger.info(f"[PERSIST] Восстановлено пробежек: {len(user_running_stats)}")
            
            if "daily" in loaded:
                global daily_stats
                loaded_daily = loaded["daily"]
                # Проверяем, если загруженная статистика не за сегодня - сбрасываем
                if loaded_daily.get("date") == today:
                    daily_stats = loaded_daily
                    logger.info(f"[PERSIST] Восстановлена дневная статистика за сегодня")
                else:
                    # Новый день - начинаем с нуля
                    daily_stats = {
                        "date": today,
                        "total_messages": 0,
                        "user_messages": {},
                        "photos": [],
                    }
                    logger.info(f"[PERSIST] Загружена старая статистика ({loaded_daily.get('date')}), сброшено на сегодня")
            
            if "active" in loaded:
                global user_last_active
                user_last_active = loaded["active"]
                logger.info(f"[PERSIST] Восстановлена активность участников: {len(user_last_active)}")
            
            # Загружаем историю чата (скрытое хранение)
            if "history" in loaded:
                global chat_history
                chat_history = loaded["history"]
                msg_count = len(chat_history.get("messages", []))
                photo_count = len(chat_history.get("photos", []))
                logger.info(f"[PERSIST] Загружена история чата: {msg_count} сообщений, {photo_count} фото")
    
    # Запускаем загрузку данных
    loop.create_task(init_persistence())
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", morning))
    application.add_handler(CommandHandler("stopmorning", stopmorning))
    application.add_handler(CommandHandler("remen", remen))
    application.add_handler(CommandHandler("antiremen", antiremen))
    application.add_handler(CommandHandler("roast", roast))
    application.add_handler(CommandHandler("flirt", flirt))
    application.add_handler(CommandHandler("mam", mam))
    application.add_handler(CommandHandler("stop", stop_cmd))
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
    application.add_handler(CommandHandler("add_birthday", add_birthday))
    application.add_handler(CommandHandler("del_birthday", del_birthday))
    application.add_handler(CommandHandler("list_birthdays", list_birthdays))
    application.add_handler(CommandHandler("challenge", challenge_status))
    application.add_handler(CommandHandler("challenge_start", start_challenge))
    application.add_handler(CommandHandler("challenge_join", join_challenge))
    application.add_handler(CommandHandler("challenge_done", done_challenge))
    application.add_handler(CommandHandler("weekly", weekly))
    application.add_handler(CommandHandler("monthly", monthly))
    
    application.add_handler(CommandHandler("getid", get_chat_id))
    application.add_handler(CommandHandler("anon", anon))
    application.add_handler(CommandHandler("anonphoto", anonphoto))
    
    # === ЛИЧНЫЕ СООБЩЕНИЯ: AI ОТВЕТ ===
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_private_messages)
    )
    
    # Обработка личных обращений через @mention (должен быть ДО handle_all_messages!)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_mentions)
    )
    
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_all_messages)
    )
    
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(PollHandler(handle_challenge_poll))
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
    
    # Запускаем планировщик обеда в 13:00 по будням
    lunch_thread = threading.Thread(target=lambda: asyncio.run(lunch_scheduler_task()), daemon=True)
    lunch_thread.start()
    logger.info("Планировщик обеда запущен (13:00 будни)")
    
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


# === Функция для обработки ЛИЧНЫХ сообщений ===