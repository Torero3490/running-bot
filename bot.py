#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Бот для Бегового Сообщества
Функции: Утреннее приветствие, Погода, Темы дня, Анонимная отправка, Защита от засыпания
"""

import asyncio
import logging
import threading
import time
import random
import httpx  # ИСПРАВЛЕНО: Добавлен отсутствующий импорт
from datetime import datetime
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
# Токен вашего бота от BotFather
BOT_TOKEN = "8130314650:AAFoQAz0xe9dZQ5PekSD2Oo1AEJUkdHsesE"

# URL вашего сервиса на Render (например: https://your-app.onrender.com)
RENDER_URL = "YOUR_RENDER_URL_HERE"

# ID чата (можно узнать командой /chat, когда бот запущен локально)
CHAT_ID = "YOUR_CHAT_ID_HERE"

# Москва - основной часовой пояс
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============== FLASK ДЛЯ PORT BINDING ==============
app = Flask(__name__)


@app.route("/")
def home():
    """Главная страница для проверки работы сервиса"""
    return "Bot is running!"


@app.route("/health")
def health():
    """Эндпоинт для проверки здоровья сервиса"""
    return "OK"


def run_flask():
    """Запуск Flask сервера в отдельном потоке"""
    app.run(host="0.0.0.0", port=10000)


# ============== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==============
application = None
morning_message_id = None
morning_scheduled_date = ""


# ============== ДАННЫЕ ДЛЯ БОТА ==============
# Темы дней недели
DAY_THEMES = {
    "Monday": "🎵 Музыкальный понедельник — делимся любимыми треками для бега!",
    "Tuesday": "💪 Силовой вторник — обсуждаем тренировки и упражнения!",
    "Wednesday": "🍎 Среда — правильное питание и восстановление!",
    "Thursday": "👟 Четверг — обсуждаем экипировку и кроссовки!",
    "Friday": "🏃 Пятница — планируем выходные пробежки!",
    "Saturday": "🚴 Суббота — активный отдых и кросс-тренинг!",
    "Sunday": "📸 Фото-день — делимся красивыми видами с пробежек!",
}

# Приветствия для новых участников (10 вариантов)
WELCOME_MESSAGES = [
    "🏃 Привет, новый бегун! Теперь твои ноги не знают покоя!",
    "👋 Добро пожаловать в клуб тех, кто бежит от дивана!",
    "🚀 Отлично! Теперь ты будешь бегать быстрее, чем заказываешь пиццу!",
    "🌟 Приветствуем! Диван по тебе уже не плачет!",
    "🎉 Ура! Ещё один человек, который выбрал здоровый сон!",
    "💨 Добро пожаловать! Теперь ты бегаешь, а не просто так стоишь!",
    "🏅 Привет, новый чемпион! До марафона осталось... ну, очень много времени!",
    "🎯 Отлично! Ты нашёл правильное место для правильного дела!",
    "🔥 Добро пожаловать! Теперь твой будильник — твой главный враг!",
    "⭐ Приветствуем! Добро пожаловать в клуб любителей утренней зарядки!",
]

# Хранилище состояний для анонимной отправки
user_anon_state = {}


# ============== ФУНКЦИИ ПОГОДЫ ==============
async def get_weather() -> str:
    """Получение погоды для Москвы и Санкт-Петербурга через Open-Meteo"""
    try:
        async with httpx.AsyncClient() as client:
            # Москва
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
    except httpx.RequestError as e:
        logger.error(f"Ошибка HTTP-запроса при получении погоды: {e}")
        return "🌤 Погода временно недоступна"
    except (KeyError, ValueError) as e:
        logger.error(f"Ошибка парсинга данных о погоде: {e}")
        return "🌤 Погода временно недоступна"
    except Exception as e:
        logger.error(f"Неожиданная ошибка при получении погоды: {e}")
        return "🌤 Погода временно недоступна"


# ============== ФУНКЦИИ УТРЕННЕГО ПРИВЕТСТВИЯ ==============
def get_day_theme() -> str:
    """Получение темы дня недели на русском языке"""
    now = datetime.now(MOSCOW_TZ)
    day_name_en = now.strftime("%A")
    return DAY_THEMES.get(day_name_en, "🌟 Отличный день для пробежки!")


def get_random_welcome() -> str:
    """Получение случайного приветствия"""
    return random.choice(WELCOME_MESSAGES)


async def send_morning_greeting():
    """Отправка утреннего приветствия"""
    global morning_message_id

    if application is None:
        logger.error("Application не инициализирован")
        return

    try:
        weather = await get_weather()
        theme = get_day_theme()

        greeting_text = (
            f"🌅 **Доброе утро, бегуны!** 🏃‍♂️\n\n"
            f"{weather}\n\n"
            f"{theme}\n\n"
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
    """Асинхронный планировщик утренних сообщений на 06:00 по Москве"""
    global morning_scheduled_date

    while True:
        now = datetime.now(MOSCOW_TZ)
        current_hour = now.hour
        current_minute = now.minute
        today_date = now.strftime("%Y-%m-%d")

        # Если сейчас 6:00 и сообщение ещё не отправляли сегодня
        if current_hour == 6 and current_minute == 0:
            if morning_scheduled_date != today_date:
                logger.info("Время 6:00 - отправляем утреннее сообщение")
                try:
                    await send_morning_greeting()
                    morning_scheduled_date = today_date
                    logger.info("Утреннее сообщение успешно отправлено")
                except Exception as e:
                    logger.error(f"Ошибка при отправке: {e}")

        # Проверка каждую минуту
        await asyncio.sleep(60)


async def delete_morning_message():
    """Удаление утреннего сообщения через 5 часов"""
    global morning_message_id

    while True:
        await asyncio.sleep(300)  # Проверка каждые 5 минут

        if morning_message_id is None:
            continue

        now = datetime.now(MOSCOW_TZ)
        # Если текущее время больше 11:00 (6:00 + 5 часов)
        if now.hour >= 11:
            try:
                if application:
                    await application.bot.delete_message(
                        chat_id=CHAT_ID,
                        message_id=morning_message_id,
                    )
                    logger.info(f"Утреннее сообщение {morning_message_id} удалено")
                    morning_message_id = None
                    break
            except Exception as e:
                logger.error(f"Ошибка удаления утреннего сообщения: {e}")
                break


# ============== ФУНКЦИИ АНОНИМНОЙ ОТПРАВКИ ==============
async def anon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /anon для анонимной отправки текста"""
    user_id = update.message.from_user.id
    user_anon_state[user_id] = "waiting_for_text"

    # Удаляем команду пользователя
    try:
        await update.message.delete()
    except Exception:
        pass


async def anonphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /anonphoto для анонимной отправки фото"""
    user_id = update.message.from_user.id
    user_anon_state[user_id] = "waiting_for_photo"

    # Удаляем команду пользователя
    try:
        await update.message.delete()
    except Exception:
        pass


async def handle_anon_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений для анонимной отправки"""
    user_id = update.message.from_user.id

    if user_id not in user_anon_state:
        return

    if user_anon_state[user_id] == "waiting_for_text":
        # Удаляем сообщение пользователя
        try:
            await update.message.delete()
        except Exception:
            pass

        # Отправляем анонимное сообщение
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"📬 **Анонимное сообщение:**\n\n{update.message.text}",
            parse_mode="Markdown",
        )

        # Удаляем состояние
        del user_anon_state[user_id]


async def handle_anon_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фото для анонимной отправки"""
    user_id = update.message.from_user.id

    if user_id not in user_anon_state:
        return

    if user_anon_state[user_id] == "waiting_for_photo":
        # Получаем фото
        photo = update.message.photo[-1]

        # Удаляем сообщение пользователя
        try:
            await update.message.delete()
        except Exception:
            pass

        # Отправляем анонимное фото
        await context.bot.send_photo(
            chat_id=CHAT_ID,
            photo=photo.file_id,
            caption="📬 **Анонимное фото**",
            parse_mode="Markdown",
        )

        # Удаляем состояние
        del user_anon_state[user_id]


# ============== ОСНОВНЫЕ ОБРАБОТЧИКИ ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome = get_random_welcome()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=welcome,
    )


async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /morning для ручной отправки утреннего приветствия"""
    await send_morning_greeting()

    # Удаляем команду пользователя
    try:
        await update.message.delete()
    except Exception:
        pass


async def stopmorning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stopmorning"""
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

    # Удаляем команду пользователя
    try:
        await update.message.delete()
    except Exception:
        pass


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик новых участников чата"""
    # Проверяем, есть ли новые участники
    if not update.message or not update.message.new_chat_members:
        return

    try:
        # Получаем информацию о боте
        bot_info = await context.bot.get_me()
        bot_id = bot_info.id
    except Exception as e:
        logger.error(f"Ошибка получения ID бота: {e}")
        bot_id = None

    for member in update.message.new_chat_members:
        # Пропускаем, если это сам бот
        if bot_id and member.id == bot_id:
            continue

        welcome = get_random_welcome()
        try:
            # Формируем текст с упоминанием пользователя
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


# ============== KEEP-ALIVE PINGER ==============
def keep_alive_pinger():
    """Пингование собственного URL для предотвращения засыпания на Render"""
    while True:
        try:
            # Пингуем каждые 5 минут
            time.sleep(300)
            if RENDER_URL and RENDER_URL != "YOUR_RENDER_URL_HERE":
                response = httpx.get(f"{RENDER_URL}/health", timeout=10)  # ИСПРАВЛЕНО: используем httpx вместо requests
                if response.status_code == 200:
                    logger.info(f"Ping successful: {RENDER_URL}/health")
                else:
                    logger.warning(
                        f"Ping returned status {response.status_code}: {RENDER_URL}/health"
                    )
        except Exception as e:
            logger.error(f"Ping failed: {e}")


# ============== ГЛАВНЫЙ ЗАПУСК ==============
async def main():
    global application

    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask сервер запущен на порту 10000")

    # Создаём приложение бота
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # Инициализируем приложение (ОБЯЗАТЕЛЬНО для python-telegram-bot 20+!)
    await application.initialize()

    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", morning))
    application.add_handler(CommandHandler("stopmorning", stopmorning))
    application.add_handler(CommandHandler("anon", anon))
    application.add_handler(CommandHandler("anonphoto", anonphoto))

    # Регистрируем обработчики сообщений
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_anon_text)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_anon_photo)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member)
    )

    # Запускаем polling
    await application.start()
    await application.updater.start_polling()
    logger.info("Бот запущен и ожидает сообщений...")

    # Запускаем асинхронный планировщик утренних сообщений
    asyncio.create_task(morning_scheduler_task())
    logger.info("Планировщик утренних сообщений запущен")

    # Запускаем удаление утреннего сообщения через 5 часов
    asyncio.create_task(delete_morning_message())

    # Запускаем keep-alive пinger в отдельном потоке
    pinger_thread = threading.Thread(target=keep_alive_pinger, daemon=True)
    pinger_thread.start()
    logger.info("Keep-alive пингер запущен")


if __name__ == "__main__":
    # Запуск в асинхронном режиме
    asyncio.run(main())

