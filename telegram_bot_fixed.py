#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Бот для Бегового Сообщества
Функции: Утреннее приветствие, Погода, Темы дня, Анонимная отправка, Garmin Connect
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
from datetime import datetime
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import pytz
from flask import Flask

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

# Ключ шифрования для Garmin данных (сгенерируй свой и сохрани в переменных окружения)
GARMIN_ENCRYPTION_KEY = os.environ.get("GARMIN_ENCRYPTION_KEY", "")

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

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
    app.run(host="0.0.0.0", port=10000)


# ============== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==============
application = None
morning_message_id = None
morning_scheduled_date = ""
bot_running = True
motivation_sent_times = []

# Файл для хранения зашифрованных данных Garmin
CREDENTIALS_FILE = "garmin_credentials.json"


# ============== ШИФРОВАНИЕ ДАННЫХ ==============
class CryptoManager:
    """Менеджер для шифрования/дешифрования данных Garmin"""
    
    def __init__(self, key: str):
        from cryptography.fernet import Fernet
        if not key:
            # Генерируем новый ключ если не задан (только для первого запуска)
            self.fernet = None
            logger.warning("GARMIN_ENCRYPTION_KEY не установлен! Данные будут храниться без шифрования!")
        else:
            self.fernet = Fernet(key.encode())
            logger.info("CryptoManager инициализирован с ключом")
    
    def encrypt(self, data: str) -> str:
        """Шифрует строку и возвращает base64 строку"""
        if not self.fernet:
            return data  # Без шифрования если нет ключа
        return self.fernet.encrypt(data.encode()).decode()
    
    def decrypt(self, encrypted_data: str) -> str:
        """Дешифрует строку"""
        if not self.fernet:
            return encrypted_data  # Без дешифрования если нет ключа
        return self.fernet.decrypt(encrypted_data.encode()).decode()


# Инициализируем крипто-менеджер
crypto_manager = CryptoManager(GARMIN_ENCRYPTION_KEY)


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

user_anon_state = {}


# ============== GARMIN INTEGRATION ==============
def load_credentials() -> dict:
    """Загружает данные пользователей из файла"""
    try:
        if Path(CREDENTIALS_FILE).exists():
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки credentials: {e}")
    return {}


def save_credentials(data: dict):
    """Сохраняет данные пользователей в файл"""
    try:
        with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения credentials: {e}")


async def garmin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /garmin add email password"""
    user_id = str(update.message.from_user.id)
    user_name = update.message.from_user.full_name or update.message.from_user.username or "Бегун"
    
    # Проверяем аргументы
    if not context.args or len(context.args) < 2:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ **Ошибка!** Используйте: `/garmin add email@example.com ваш_пароль`\n\nПример: `/garmin add example@gmail.com MyPassword123`",
            parse_mode="Markdown",
        )
        return
    
    email = context.args[0]
    password = " ".join(context.args[1:])  # Пароль может содержать спецсимволы
    
    # Удаляем сообщение пользователя для безопасности
    try:
        await update.message.delete()
    except Exception:
        pass
    
    # Отправляем сообщение о проверке
    status_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🔐 **Проверяю данные Garmin...**",
        parse_mode="Markdown",
    )
    
    # Проверяем логин через Garmin API
    try:
        import garminconnect
        
        client = garminconnect.Garmin(email, password)
        client.login()
        
        # Если успешно — сохраняем зашифрованные данные
        credentials = load_credentials()
        
        credentials[user_id] = {
            "email": crypto_manager.encrypt(email),
            "password": crypto_manager.encrypt(password),
            "user_name": user_name,
            "last_activity_id": 0
        }
        
        save_credentials(credentials)
        
        await status_msg.edit_text(
            text=f"✅ **{user_name}**, Garmin аккаунт успешно привязан!\n\nТеперь бот будет автоматически публиковать твои тренировки в чат.",
            parse_mode="Markdown",
        )
        logger.info(f"Garmin аккаунт привязан для пользователя {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка Garmin авторизации: {e}")
        await status_msg.edit_text(
            text="❌ **Ошибка авторизации!**\n\nПроверь email и пароль. Убедись, что в аккаунте Garmin нет двухфакторной аутентификации.",
            parse_mode="Markdown",
        )


async def garmin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса привязки Garmin"""
    user_id = str(update.message.from_user.id)
    
    credentials = load_credentials()
    
    if user_id in credentials:
        user_name = credentials[user_id].get("user_name", "Бегун")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🟢 **{user_name}**, Garmin подключён!\n\nТвои тренировки будут автоматически публиковаться в чате.",
            parse_mode="Markdown",
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚪ Garmin не подключён.\n\nИспользуй: `/garmin add email@example.com пароль`",
            parse_mode="Markdown",
        )
    
    try:
        await update.message.delete()
    except Exception:
        pass


async def garmin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвязка Garmin аккаунта"""
    user_id = str(update.message.from_user.id)
    
    credentials = load_credentials()
    
    if user_id in credentials:
        del credentials[user_id]
        save_credentials(credentials)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Garmin аккаунт отвязан.",
            parse_mode="Markdown",
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚪ Garmin не был привязан.",
            parse_mode="Markdown",
        )
    
    try:
        await update.message.delete()
    except Exception:
        pass


def format_activity_message(activity: dict, user_name: str) -> str:
    """Форматирует сообщение о тренировке"""
    
    # Дистанция в км
    distance_km = round(activity.get("distanceInMeters", 0) / 1000, 2)
    
    # Время в формате ЧЧ:ММ:СС
    duration_seconds = activity.get("durationInSeconds", 0)
    hours = duration_seconds // 3600
    minutes = (duration_seconds % 3600) // 60
    seconds = duration_seconds % 60
    if hours > 0:
        time_str = f"{hours}ч {minutes}м"
    else:
        time_str = f"{minutes}:{seconds:02d}"
    
    # Пульс
    avg_heart_rate = activity.get("averageHeartRateInBeatsPerMinute", "—")
    max_heart_rate = activity.get("maxHeartRateInBeatsPerMinute", "—")
    
    # Темп (мин/км)
    avg_speed_mps = activity.get("averageSpeedInMetersPerSecond", 0)
    if avg_speed_mps > 0:
        pace_seconds_per_km = 1000 / avg_speed_mps
        pace_minutes = int(pace_seconds_per_km // 60)
        pace_seconds = int(pace_seconds_per_km % 60)
        pace_str = f"{pace_minutes}:{pace_seconds:02d}"
    else:
        pace_str = "—"
    
    # Калории
    calories = activity.get("calories", "—")
    
    # Тип активности
    activity_type = activity.get("activityType", "Бег")
    
    # Формируем сообщение
    message = (
        f"🏃‍♂️ **{user_name}** завершил тренировку!\n\n"
        f"📏 **Дистанция:** {distance_km} км\n"
        f"⏱️ **Время:** {time_str}\n"
        f"❤️ **Пульс:** {avg_heart_rate}/{max_heart_rate} (сред/макс)\n"
        f"⚡ **Темп:** {pace_str} мин/км\n"
        f"🔥 **Калории:** {calories}\n"
        f"\n#{activity_type.replace(' ', '')} #тренировка"
    )
    
    return message


async def check_garmin_activities():
    """Проверка новых тренировок у всех пользователей"""
    global application
    
    if application is None:
        return
    
    try:
        credentials = load_credentials()
        
        if not credentials:
            return
        
        import garminconnect
        
        for user_id, user_data in credentials.items():
            try:
                # Дешифруем данные
                email = crypto_manager.decrypt(user_data["email"])
                password = crypto_manager.decrypt(user_data["password"])
                user_name = user_data.get("user_name", "Бегун")
                last_activity_id = user_data.get("last_activity_id", 0)
                
                # Авторизуемся в Garmin
                client = garminconnect.Garmin(email, password)
                client.login()
                
                # Получаем последние 3 активности
                activities = client.get_activities(limit=3)
                
                if not activities:
                    continue
                
                # Ищем новую тренировку
                for activity in activities:
                    activity_id = activity.get("activityId", 0)
                    
                    if activity_id > last_activity_id:
                        # Новая тренировка!
                        logger.info(f"Новая тренировка для {user_name}: {activity_id}")
                        
                        # Форматируем и отправляем сообщение
                        message = format_activity_message(activity, user_name)
                        
                        await application.bot.send_message(
                            chat_id=CHAT_ID,
                            text=message,
                            parse_mode="Markdown",
                        )
                        
                        # Обновляем last_activity_id
                        user_data["last_activity_id"] = activity_id
                        save_credentials(credentials)
                        
                        # Обновляем локальные данные
                        credentials[user_id]["last_activity_id"] = activity_id
                        
            except Exception as e:
                logger.error(f"Ошибка проверки Garmin для пользователя {user_id}: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Ошибка в check_garmin_activities: {e}")


async def garmin_scheduler_task():
    """Планировщик проверки Garmin активностей каждые 15 минут"""
    while bot_running:
        try:
            await check_garmin_activities()
        except Exception as e:
            logger.error(f"Ошибка в garmin_scheduler_task: {e}")
        
        await asyncio.sleep(900)  # 15 минут


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


async def delete_morning_message():
    global morning_message_id

    if morning_message_id is not None and application is not None:
        try:
            now = datetime.now(MOSCOW_TZ)
            if now.hour >= 11:
                await application.bot.delete_message(
                    chat_id=CHAT_ID,
                    message_id=morning_message_id,
                )
                logger.info(f"Утреннее сообщение {morning_message_id} удалено при старте")
                morning_message_id = None
                return
        except Exception as e:
            logger.error(f"Ошибка удаления при старте: {e}")

    while bot_running:
        await asyncio.sleep(300)

        if morning_message_id is None:
            continue

        try:
            now = datetime.now(MOSCOW_TZ)
            if now.hour >= 11 and application:
                await application.bot.delete_message(
                    chat_id=CHAT_ID,
                    message_id=morning_message_id,
                )
                logger.info(f"Утреннее сообщение {morning_message_id} удалено")
                morning_message_id = None
        except Exception as e:
            logger.error(f"Ошибка удаления утреннего сообщения: {e}")
            break


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


async def handle_anon_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in user_anon_state:
        return

    if user_anon_state[user_id] == "waiting_for_text":
        text = update.message.text
        target_mention = ""
        
        # Проверяем, есть ли упоминание @никнейм в начале
        import re
        match = re.match(r'^@(\w+)\s+(.+)', text)
        
        if match:
            target_username = match.group(1)
            message_text = match.group(2)
            target_mention = f"@{target_username}"
        else:
            message_text = text
        
        try:
            await update.message.delete()
        except Exception:
            pass

        # Формируем сообщение
        if target_mention:
            anon_text = f"📬 **Анонимное сообщение для {target_mention}:**\n\n{message_text}"
        else:
            anon_text = f"📬 **Анонимное сообщение:**\n\n{message_text}"
        
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=anon_text,
            parse_mode="Markdown",
        )

        del user_anon_state[user_id]


async def handle_anon_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in user_anon_state:
        return

    if user_anon_state[user_id] == "waiting_for_photo":
        photo = update.message.photo[-1]

        try:
            await update.message.delete()
        except Exception:
            pass

        await context.bot.send_photo(
            chat_id=CHAT_ID,
            photo=photo.file_id,
            caption="📬 **Анонимное фото**",
            parse_mode="Markdown",
        )

        del user_anon_state[user_id]


# ============== ОБРАБОТЧИКИ ==============
START_MESSAGE = """🏃 **Бот для бегового чата**

**Автоматические сообщения:**
• 06:00 — Утреннее приветствие + погода + тема дня
• 11:00 — Мотивация
• 16:00 — Мотивация
• 21:00 — Мотивация
• Каждые 15 мин — Проверка Garmin тренировок

**Команды:**
• /start — показать это сообщение
• /morning — отправить утреннее приветствие сейчас
• /stopmorning — удалить утреннее сообщение
• /anon @никнейм текст — анонимное сообщение
• /anonphoto — анонимная отправка фото

**Garmin команды:**
• /garmin add email пароль — привязать Garmin
• /garmin status — проверить статус
• /garmin remove — отвязать аккаунт"""


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
        except Exception as e:
            # Время от времени пинг может не доходить — это нормально
            pass


if __name__ == "__main__":
    # Создаём цикл событий и запускаем всё
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Обработчики сигналов
    def stop_all():
        global bot_running
        bot_running = False
        if application:
            application.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask запущен на порту 10000")
    
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )
    
    # Основные команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", morning))
    application.add_handler(CommandHandler("stopmorning", stopmorning))
    application.add_handler(CommandHandler("anon", anon))
    application.add_handler(CommandHandler("anonphoto", anonphoto))
    
    # Garmin команды
    application.add_handler(CommandHandler("garmin", garmin_add))
    application.add_handler(CommandHandler("garmin_add", garmin_add))
    application.add_handler(CommandHandler("garmin_status", garmin_status))
    application.add_handler(CommandHandler("garmin_remove", garmin_remove))
    
    # Обработчики сообщений
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_anon_text)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_anon_photo)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member)
    )
    
    # Запускаем фоновые задачи
    loop.create_task(morning_scheduler_task())
    loop.create_task(motivation_scheduler_task())
    loop.create_task(delete_morning_message())
    loop.create_task(garmin_scheduler_task())
    
    # Запускаем keep-alive пингер
    pinger_thread = threading.Thread(target=keep_alive_pinger, daemon=True)
    pinger_thread.start()
    
    logger.info("Планировщики запущены")
    
    # Запускаем polling
    application.run_polling(drop_pending_updates=True)







