#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Бот для Бегового Сообщества
Функции: Утреннее приветствие, Погода, Темы дня, Анонимная отправка
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


def get_random_motivation() -> str:
    return random.choice(MOTIVATION_QUOTES)


def get_random_insult() -> str:
    return random.choice(FUNNY_INSULTS)


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

**Команды:**
• /start — показать это сообщение
• /morning — отправить утреннее приветствие сейчас
• /stopmorning — удалить утреннее сообщение
• /anon @никнейм текст — анонимное сообщение
• /anonphoto — анонимная отправка фото
• /remen — получить порцию смешных ругательств"""


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
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", morning))
    application.add_handler(CommandHandler("stopmorning", stopmorning))
    application.add_handler(CommandHandler("remen", remen))
    application.add_handler(CommandHandler("anon", anon))
    application.add_handler(CommandHandler("anonphoto", anonphoto))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_anon_text)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_anon_photo)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member)
    )
    
    loop.create_task(morning_scheduler_task())
    loop.create_task(motivation_scheduler_task())
    loop.create_task(delete_morning_message())
    
    pinger_thread = threading.Thread(target=keep_alive_pinger, daemon=True)
    pinger_thread.start()
    
    logger.info("Планировщики запущены")
    
    application.run_polling(drop_pending_updates=True)








