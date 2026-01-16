#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Бот для Бегового Сообщества
Функции: Утреннее приветствие, Погода, Темы дня, Анонимная отправка, Защита от засыпания
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
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Токен бота не найден! Установите переменную окружения TELEGRAM_BOT_TOKEN")

RENDER_URL = os.environ.get("RENDER_URL", "")

CHAT_ID = os.environ.get("CHAT_ID")
if not CHAT_ID:
    raise ValueError("CHAT_ID не найден! Установите переменную окружения CHAT_ID")

try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    raise ValueError("CHAT_ID должен быть числом!")

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


# ============== ДАННЫЕ ==============
DAY_THEMES = {
    "Monday": "🎵 Музыкальный понедельник — делимся любимыми треками для бега!",
    "Tuesday": "💪 Силовой вторник — обсуждаем тренировки и упражнения!",
    "Wednesday": "🍎 Среда — правильное питание и восстановление!",
    "Thursday": "👟 Четверг — обсуждаем экипировку и кроссовки!",
    "Friday": "🏃 Пятница — планируем выходные пробежки!",
    "Saturday": "🚴 Суббота — активный отдых и кросс-тренинг!",
    "Sunday": "📸 Фото-день — делимся красивыми видами с пробежек!",
}

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

user_anon_state = {}


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


async def send_morning_greeting():
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
        try:
            await update.message.delete()
        except Exception:
            pass

        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"📬 **Анонимное сообщение:**\n\n{update.message.text}",
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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = get_random_welcome()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=welcome,
    )


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


async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Chat ID: {chat_id}")
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Debug: Chat ID = {chat_id}",
    )


# ============== KEEP-ALIVE ==============
def keep_alive_pinger():
    while bot_running:
        try:
            time.sleep(300)
            if RENDER_URL and RENDER_URL != "YOUR_RENDER_URL_HERE":
                response = httpx.get(f"{RENDER_URL}/health", timeout=10)
                if response.status_code == 200:
                    logger.info(f"Ping successful: {RENDER_URL}/health")
                else:
                    logger.warning(f"Ping returned status {response.status_code}")
        except Exception as e:
            logger.error(f"Ping failed: {e}")


# ============== ЗАПУСК ==============
def main():
    global application, bot_running

    signal.signal(signal.SIGTERM, lambda s, f: stop_all())
    signal.signal(signal.SIGINT, lambda s, f: stop_all())

    logger.info("Запуск бота...")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask запущен на порту 10000")

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", morning))
    application.add_handler(CommandHandler("stopmorning", stopmorning))
    application.add_handler(CommandHandler("anon", anon))
    application.add_handler(CommandHandler("anonphoto", anonphoto))
    application.add_handler(CommandHandler("chat_id", get_chat_id))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_anon_text)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_anon_photo)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member)
    )

    application.run_polling(drop_pending_updates=True)


def stop_all():
    global bot_running
    bot_running = False
    if application:
        application.stop()
    sys.exit(0)


if __name__ == "__main__":
    main()

