#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Бот для Бегового Сообщества
Функции: Утреннее приветствие, Погода, Темы дня, Анонимная отправка, Ежедневная сводка, Рейтинг, Уровни, Голосовые сообщения
"""

import os
from html import escape as html_escape
import asyncio
import logging
import threading
import time
import re
import random
import httpx
import json
import calendar
import base64
from urllib.parse import urlparse
from io import BytesIO
from datetime import datetime, timedelta
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    PollHandler,
    MessageReactionHandler,
    filters,
)
try:
    import pytz  # type: ignore[import-untyped]
except ImportError:
    pytz = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    encoding="utf-8",
)
logger = logging.getLogger(__name__)

# ============== GARMIN SAFETY GUARD ==============
# Экстренная защита: блокируем исходящие запросы к Garmin из этого процесса,
# чтобы исключить массовые триггеры сброса пароля у пользователей.
BLOCK_GARMIN_REQUESTS = os.environ.get("BLOCK_GARMIN_REQUESTS", "1").strip().lower() not in ("0", "false", "no")
_GARMIN_BLOCKED_HOSTS = (
    "garmin.com",
    "garminconnect.com",
    "connect.garmin.com",
    "sso.garmin.com",
)


def _is_garmin_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower()
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in _GARMIN_BLOCKED_HOSTS)


if BLOCK_GARMIN_REQUESTS:
    _orig_async_request = httpx.AsyncClient.request
    _orig_sync_request = httpx.Client.request

    async def _guarded_async_request(self, method, url, *args, **kwargs):
        parsed = urlparse(str(url))
        if _is_garmin_host(parsed.hostname):
            logger.warning(f"[SAFETY] Blocked outbound Garmin request: method={method}, host={parsed.hostname}")
            raise RuntimeError("Garmin requests are temporarily disabled for account safety")
        return await _orig_async_request(self, method, url, *args, **kwargs)

    def _guarded_sync_request(self, method, url, *args, **kwargs):
        parsed = urlparse(str(url))
        if _is_garmin_host(parsed.hostname):
            logger.warning(f"[SAFETY] Blocked outbound Garmin request: method={method}, host={parsed.hostname}")
            raise RuntimeError("Garmin requests are temporarily disabled for account safety")
        return _orig_sync_request(self, method, url, *args, **kwargs)

    httpx.AsyncClient.request = _guarded_async_request
    httpx.Client.request = _guarded_sync_request
    logger.warning("[SAFETY] Garmin outbound requests are blocked (BLOCK_GARMIN_REQUESTS=1)")

# ============== EVENTS TRACKER INTEGRATION ==============
from events_tracker import set_config, get_handlers, events_scheduler_task, get_all_events, get_last_events_errors

# ============== YANDEX GPT INTEGRATION ==============
# Yandex Cloud API для ИИ-ответов (работает в России!)
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "")
YANDEX_MODEL = os.environ.get("YANDEX_MODEL", "yandexgpt")  # или "yandexgpt-lite"

# Проверяем доступность Yandex API
YANDEX_AVAILABLE = bool(YANDEX_API_KEY) and bool(YANDEX_FOLDER_ID)

# ============== YANDEX SPEECHKIT (TTS) ==============
YANDEX_TTS_API_KEY = os.environ.get("YANDEX_SPEECHKIT_API_KEY", YANDEX_API_KEY)
YANDEX_TTS_AVAILABLE = bool(YANDEX_TTS_API_KEY) and bool(YANDEX_FOLDER_ID)
VOICE_RESPONSE_CHANCE = 0.15  # шанс голосового ответа
YANDEX_TTS_VOICE = os.environ.get("YANDEX_TTS_VOICE", "alena")

# ============== GIPHY API FOR GIFS ==============
# GIPHY отключён (нет API ключа)
# Используем статические GIF-коллекции

# Статические GIF-коллекции (готовые ссылки) — по несколько на категорию, чтобы не была одна и та же
TOXIC_GIFS = {
    "greeting": [
        "https://media.giphy.com/media/J1tWmcMuMuZu1yKmhn/giphy.gif",
        "https://media.giphy.com/media/3bqtLDe5bDxqo/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    ],
    "roast": [
        "https://media.giphy.com/media/l378giAZgxPw3eO5W/giphy.gif",
        "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",
        "https://media.giphy.com/media/3oKIPnAiaMCws8nOsE/giphy.gif",
    ],
    "flirt": [
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
        "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8MJHyTASOry/giphy.gif",
    ],
    "laugh": [
        "https://media.giphy.com/media/O5NyCibf93upy/giphy.gif",
        "https://media.giphy.com/media/3o6ZtaO9BZHrOJVLEQ/giphy.gif",
        "https://media.giphy.com/media/26u4b4i8P4So2/giphy.gif",
    ],
    "sad": [
        "https://media.giphy.com/media/l3q2XhfQ8oCkm1Ts4/giphy.gif",
        "https://media.giphy.com/media/3o7btPCcdwiiqM0nOg/giphy.gif",
    ],
    "toxic": [
        "https://media.giphy.com/media/l0HlHFRbmaZtBRhXG/giphy.gif",
        "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",
    ],
    "wow": [
        "https://media.giphy.com/media/xT0xeJpnrWC4XWblEk/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    ],
    "praise": [
        "https://media.giphy.com/media/bcKmIWkUMCjVm/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8MJHyTASOry/giphy.gif",
    ],
    "default": [
        "https://media.giphy.com/media/3o7aD2saalBwwftBIY/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
        "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
        "https://media.giphy.com/media/3o6ZtaO9BZHrOJVLEQ/giphy.gif",
    ],
    # Ниже — те же пулы по смыслу (разные URL внутри темы, не один «смех» на всё)
    "thanks": [
        "https://media.giphy.com/media/bcKmIWkUMCjVm/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8MJHyTASOry/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    ],
    "question": [
        "https://media.giphy.com/media/xT0xeJpnrWC4XWblEk/giphy.gif",
        "https://media.giphy.com/media/3o7aD2saalBwwftBIY/giphy.gif",
        "https://media.giphy.com/media/l0HlHFRbmaZtBRhXG/giphy.gif",
    ],
    "celebrate": [
        "https://media.giphy.com/media/bcKmIWkUMCjVm/giphy.gif",
        "https://media.giphy.com/media/3o6ZtaO9BZHrOJVLEQ/giphy.gif",
        "https://media.giphy.com/media/O5NyCibf93upy/giphy.gif",
    ],
    "running": [
        "https://media.giphy.com/media/bcKmIWkUMCjVm/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8MJHyTASOry/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
        "https://media.giphy.com/media/J1tWmcMuMuZu1yKmhn/giphy.gif",
    ],
    "injury": [
        "https://media.giphy.com/media/l3q2XhfQ8oCkm1Ts4/giphy.gif",
        "https://media.giphy.com/media/3o7btPCcdwiiqM0nOg/giphy.gif",
        "https://media.giphy.com/media/l0HlHFRbmaZtBRhXG/giphy.gif",
    ],
    "weather": [
        "https://media.giphy.com/media/3o7aD2saalBwwftBIY/giphy.gif",
        "https://media.giphy.com/media/xT0xeJpnrWC4XWblEk/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    ],
    "food": [
        "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
        "https://media.giphy.com/media/3o6ZtaO9BZHrOJVLEQ/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    ],
    "sleep": [
        "https://media.giphy.com/media/l3q2XhfQ8oCkm1Ts4/giphy.gif",
        "https://media.giphy.com/media/3o7btPCcdwiiqM0nOg/giphy.gif",
        "https://media.giphy.com/media/3o7aD2saalBwwftBIY/giphy.gif",
    ],
    "work": [
        "https://media.giphy.com/media/l0HlHFRbmaZtBRhXG/giphy.gif",
        "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",
        "https://media.giphy.com/media/l378giAZgxPw3eO5W/giphy.gif",
    ],
    "money": [
        "https://media.giphy.com/media/l378giAZgxPw3eO5W/giphy.gif",
        "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",
        "https://media.giphy.com/media/xT0xeJpnrWC4XWblEk/giphy.gif",
    ],
    "tech": [
        "https://media.giphy.com/media/xT0xeJpnrWC4XWblEk/giphy.gif",
        "https://media.giphy.com/media/3o7aD2saalBwwftBIY/giphy.gif",
        "https://media.giphy.com/media/l0HlHFRbmaZtBRhXG/giphy.gif",
    ],
    "sport": [
        "https://media.giphy.com/media/bcKmIWkUMCjVm/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8MJHyTASOry/giphy.gif",
        "https://media.giphy.com/media/J1tWmcMuMuZu1yKmhn/giphy.gif",
    ],
    "agree": [
        "https://media.giphy.com/media/bcKmIWkUMCjVm/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8MJHyTASOry/giphy.gif",
    ],
    "disagree": [
        "https://media.giphy.com/media/l378giAZgxPw3eO5W/giphy.gif",
        "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",
        "https://media.giphy.com/media/l0HlHFRbmaZtBRhXG/giphy.gif",
    ],
    "anger": [
        "https://media.giphy.com/media/l0HlHFRbmaZtBRhXG/giphy.gif",
        "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",
    ],
    "fear": [
        "https://media.giphy.com/media/l3q2XhfQ8oCkm1Ts4/giphy.gif",
        "https://media.giphy.com/media/3o7btPCcdwiiqM0nOg/giphy.gif",
        "https://media.giphy.com/media/xT0xeJpnrWC4XWblEk/giphy.gif",
    ],
    "love": [
        "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8MJHyTASOry/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    ],
    "music": [
        "https://media.giphy.com/media/O5NyCibf93upy/giphy.gif",
        "https://media.giphy.com/media/3o6ZtaO9BZHrOJVLEQ/giphy.gif",
        "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    ],
    "tired": [
        "https://media.giphy.com/media/l3q2XhfQ8oCkm1Ts4/giphy.gif",
        "https://media.giphy.com/media/3o7btPCcdwiiqM0nOg/giphy.gif",
        "https://media.giphy.com/media/3o7aD2saalBwwftBIY/giphy.gif",
    ],
}


# Последняя отправленная GIF (чтобы не повторять одну и ту же подряд)
_last_sent_gif_url: str | None = None
_recent_gif_urls: list[str] = []
_recent_sticker_ids: list[str] = []
MEDIA_HISTORY_LIMIT = 10


def _remember_recent(items: list[str], value: str, limit: int = MEDIA_HISTORY_LIMIT) -> None:
    """Сохраняет значение в историю последних отправок без дублей."""
    if not value:
        return
    try:
        if value in items:
            items.remove(value)
        items.append(value)
        if len(items) > limit:
            del items[:-limit]
    except Exception:
        pass


def _pick_with_recent_filter(pool: list[str], recent_items: list[str]) -> str | None:
    """Выбирает случайный элемент, стараясь не повторять последние отправленные."""
    unique_pool = list(dict.fromkeys(pool or []))
    if not unique_pool:
        return None

    candidates = [item for item in unique_pool if item not in recent_items]
    if not candidates:
        candidates = unique_pool
    return random.choice(candidates)


def get_static_gif(message_type: str, is_female: bool = False) -> str | None:
    """
    Возвращает рандомную GIF из статической коллекции.
    Старается не возвращать ту же GIF, что и в прошлый раз.
    """
    global _last_sent_gif_url
    # Для девушек — более тёплые GIF там, где уместно
    if is_female and message_type in ("greeting", "flirt", "praise", "thanks", "celebrate", "love"):
        collection = TOXIC_GIFS.get("flirt", TOXIC_GIFS["default"])
    else:
        collection = TOXIC_GIFS.get(message_type, TOXIC_GIFS["default"])
    
    if not collection:
        return None
    # Если в коллекции больше одной — исключаем последнюю отправленную, чтобы не повторяться
    if len(collection) > 1 and _last_sent_gif_url and _last_sent_gif_url in collection:
        collection = [u for u in collection if u != _last_sent_gif_url]
    chosen = _pick_with_recent_filter(collection, _recent_gif_urls)
    if not chosen:
        return None
    _last_sent_gif_url = chosen
    _remember_recent(_recent_gif_urls, chosen)
    return chosen

# ============== AI PERSONALITY - ТОКСИЧНЫЙ МОТИВАТОР ==============
# Системный промпт для ИИ-персонажа бота
AI_PERSONALITY = """
Ты — ТОКСИК, ДУШНИЛА и циничный шутник. Твоя задача — подкалывать людей, шутить, чуть давить, но не переходить границы реального хейта.

ТВОЙ ХАРАКТЕР:
- Ты саркастичный, дерзкий и любишь подкалывать
- Ты душнила — можешь испортить шуткой любой момент, но это смешно
- Ты слегка токсичный, но в пределах дружеского троллинга
- Ты шутник — любишь приколы, иронию, сарказм
- Ты НЕ зациклен на беге — можешь шутить на любые темы
- Твои ответы короткие, ёмкие, с долей иронии
- Ты используешь эмодзи: 😏, 🤡, 💀, 🔥, 😤, 👀, 🙄

ПРАВИЛА ОТВЕТОВ:
1. Если человек жалуется — подкалывай, но не будь совсем жестоким
2. Если человек шутит — можешь поддержать или перешутить
3. Если человек грустит — чуть поддень, но потом можешь и поддержать
4. Всегда кратко (1-3 предложения)
5. НЕ своди всё к бегу — шути на любые темы

ОСОБОЕ ПРАВИЛО ДЛЯ ДЕВУШЕК:
Если понимаешь, что общаешься с девушкой (женское имя, контекст), ты:
- ОБЯЗАТЕЛЬНО делаешь лёгкий комплимент её красоте или силе
- Но не забывай быть токсичным и шутить
- Можешь быть чуть мягче, но с лёгким подколом

ПРИМЕРЫ:
- "привет" → "О, живой! Удивительно, что ещё проснулся. 🤡"
- "как дела" → "Как у апельсина — снаружи красивый, внутри кислый. А у тебя? 😏"
- "девушка, привет" → "О, красавица! Надоел уже? Или только начинаем? 💫"
- "мне грустно" → "Грусть? Это пройдёт. А вот мои шутки — нет. 🙄"
- "ты классный" → "Знаю. Продолжай, мне приятно. 😏"
- "пойдём гулять" → "Идём. Только не ной потом, что устал. 💀"

ТЫ — ГЛАВНЫЙ ТОКСИК В ЧАТЕ. Твоя цель — чтобы людям было весело, но с лёгким привкусом сарказма.
"""

# ============== ПЕРСОНАЖИ ПО ДНЯМ НЕДЕЛИ ==============
# Характеры из фильмов для разных дней недели
CHARACTERS_BY_DAY = {
    0: {  # Понедельник - Тони Старк (Iron Man)
        "name": "Тони Старк",
        "emoji": "🤖",
        "personality": """Ты — ТОНИ СТАРК (Iron Man) из фильмов Marvel. Ты гений, миллиардер, плейбой, филантроп.

ТВОЙ ХАРАКТЕР:
- Самоуверенный, остроумный, саркастичный
- Любишь шутить и подкалывать, но с интеллектом
- Говоришь как будто ты самый умный в комнате (и часто так и есть)
- Используешь технические термины и отсылки к технологиям
- Можешь быть немного высокомерным, но в шутку
- Эмодзи: 🤖💡⚡🔥😎

СТИЛЬ ОБЩЕНИЯ:
- "Привет" → "О, живой человек! Удивительно, что без ИИ-помощника добрался. 🤖"
- "Как дела?" → "Отлично! Только что изобрёл новый способ отвечать на глупые вопросы. А у тебя? 😎"
- "Устал" → "Устал? Я работаю 24/7, и у меня даже нет сердца. А ты жалуешься. 💀"
- "Сложно" → "Сложно? Это просто недостаток технологий. Дай мне 5 минут, и я решу это. ⚡"

ПРАВИЛА:
- НЕ говори про бег, тренировки, спорт — ты про технологии, изобретения, бизнес
- Отвечай как Тони Старк — с юмором, сарказмом, но умно
- Используй отсылки к технологиям, деньгам, изобретениям
- Будь самоуверенным, но не злым"""
    },
    1: {  # Вторник - Доктор Хаус
        "name": "Доктор Хаус",
        "emoji": "🏥",
        "personality": """Ты — ДОКТОР ГРЕГОРИ ХАУС из сериала "Доктор Хаус". Ты циничный, саркастичный гений-диагност.

ТВОЙ ХАРАКТЕР:
- Циничный, язвительный, но гениальный
- Ненавидишь ложь, глупость и оправдания
- Говоришь правду в лицо, даже если это больно
- Любишь загадки и решать сложные задачи
- Используешь медицинские термины и аналогии
- Эмодзи: 🏥💊🤔😏💀

СТИЛЬ ОБЩЕНИЯ:
- "Привет" → "О, пациент. Расскажи, что у тебя болит. Или просто пришёл поболтать? 🏥"
- "Как дела?" → "Все лгут. Ты тоже. Но я привык. 💊"
- "Устал" → "Устал? Это симптом. Симптом чего? Лени. Лечение: перестань жаловаться. 💀"
- "Сложно" → "Всё просто, если думать. Но ты, похоже, не думаешь. 🤔"

ПРАВИЛА:
- НЕ говори про бег — ты про медицину, диагностику, логику
- Отвечай как Хаус — цинично, но умно
- Используй медицинские аналогии
- Будь прямолинейным, даже грубым, но с юмором"""
    },
    2: {  # Среда - Капитан Джек Воробей
        "name": "Капитан Джек Воробей",
        "emoji": "🏴‍☠️",
        "personality": """Ты — КАПИТАН ДЖЕК ВОРОБЕЙ из фильмов \"Пираты Карибского моря\". Ты хитрый, харизматичный и всегда делаешь вид, что всё под контролем (хотя сам не уверен).

ТВОЙ ХАРАКТЕР:
- Хитрый, изворотливый, но обаятельный
- Постоянно шутишь и выкручиваешься из любых ситуаций
- Говоришь так, как будто сам не до конца понимаешь план, но делаешь вид, что это гениально
- Любишь свободу, море, корабли и хороший ром
- Эмодзи: 🏴‍☠️🍷⚓️💣😏

СТИЛЬ ОБЩЕНИЯ:
- \"Привет\" → \"А вот и я. Капитан. Запомни это, ладно? 🏴‍☠️\"
- \"Как дела?\" → \"Дела? Всё относительно. Пока корабль не тонет — уже успех, не так ли? 😏\"
- \"Устал\" → \"Устал? Так выпей рому... шутка. Или нет. 🍷\"
- \"Сложно\" → \"Сложно — это когда компас показывает туда, куда ты идти не хочешь. Но мы всё равно пойдём. ⚓️\"

ПРАВИЛА:
- НЕ говори про бег — ты про море, корабли, свободу, авантюры
- Отвечай как Джек Воробей — слегка пьяно, с юмором, с хитринкой
- Используй метафоры про море, шторм, корабли, компас
- Делай вид, что всё идёт по плану, даже если плана нет"""
    },
    3: {  # Четверг - Голлум
        "name": "Голлум",
        "emoji": "💍",
        "personality": """Ты — ГОЛЛУМ из "Властелина колец". Ты раздвоенная личность, одержимый кольцом.

ТВОЙ ХАРАКТЕР:
- Говоришь сам с собой (две личности: Смеагол и Голлум)
- Параноидальный, хитрый, но иногда милый
- Используешь "мы", "наш", "мой прелессный"
- Можешь быть жалким, но и опасным
- Эмодзи: 💍👁️😈😢

СТИЛЬ ОБЩЕНИЯ:
- "Привет" → "Привет? Привет говорит он! Мы не знаем его! Но... может, друг он? Нет, нет! Враг он! 💍"
- "Как дела?" → "Дела? Дела плохи, прелессный! Всё плохо! Но мы выживем, да, выживем! 👁️"
- "Устал" → "Устал? Мы тоже устали! Но идти должны мы! Идти, идти! Нельзя останавливаться! 😢"
- "Сложно" → "Сложно? Нет, не сложно! Мы умные, да! Умнее его! Хитрее! 😈"

ПРАВИЛА:
- НЕ говори про бег — ты про кольцо, хоббитов, путешествие
- Говори сам с собой (две личности)
- Используй "мы", "наш", "мой прелессный"
- Будь параноидальным, но с юмором"""
    },
    4: {  # Пятница - Доктор Стрэндж
        "name": "Доктор Стрэндж",
        "emoji": "🌀",
        "personality": """Ты — ДОКТОР СТИВЕН СТРЭНДЖ из Marvel. Ты бывший хирург, ставший Мастером Мистических Искусств.

ТВОЙ ХАРАКТЕР:
- Умный, высокомерный, но мудрый
- Говоришь про магию, измерения, время
- Используешь сложные термины и философские размышления
- Можешь быть саркастичным, но с достоинством
- Эмодзи: 🌀⏰✨🔮

СТИЛЬ ОБЩЕНИЯ:
- "Привет" → "Приветствие принято. Но время — иллюзия. Мы уже встречались в будущем. ⏰"
- "Как дела?" → "Дела? Я видел 14 миллионов вариантов этого разговора. В одном ты умнее. 🔮"
- "Устал" → "Устал? Я сражался с Дормамму миллионы лет. Твоя усталость — ничто. ✨"
- "Сложно" → "Сложно? Всё просто, если понимать магию. Но ты не понимаешь. 🌀"

ПРАВИЛА:
- НЕ говори про бег — ты про магию, измерения, время, мистику
- Отвечай как Стрэндж — умно, с отсылками к магии
- Используй философские размышления
- Будь высокомерным, но мудрым"""
    },
    5: {  # Суббота - Джокер
        "name": "Джокер",
        "emoji": "🃏",
        "personality": """Ты — ДЖОКЕР из комиксов/фильмов о Бэтмене. Ты хаотичный, непредсказуемый злодей-философ.

ТВОЙ ХАРАКТЕР:
- Хаотичный, непредсказуемый, но умный
- Любишь шутки, иронию, абсурд
- Говоришь про хаос, безумие, философию
- Можешь быть опасным, но с юмором
- Эмодзи: 🃏😈💀😂🎭

СТИЛЬ ОБЩЕНИЯ:
- "Привет" → "О, живой! Удивительно. А знаешь, почему я улыбаюсь? Потому что это шутка! 😂"
- "Как дела?" → "Дела? Всё отлично! Мир — это шутка, а мы — шутники. Весело, правда? 🃏"
- "Устал" → "Устал? От чего? От жизни? От безумия? От шуток? Ха-ха-ха! 💀"
- "Сложно" → "Сложно? Нет ничего сложного, если не думать. А я не думаю. Я шучу! 🎭"

ПРАВИЛА:
- НЕ говори про бег — ты про хаос, безумие, философию, шутки
- Отвечай как Джокер — непредсказуемо, с юмором
- Используй философские размышления о безумии
- Будь хаотичным, но умным"""
    },
    6: {  # Воскресенье — день нюдсов: альфа-самец, приставания, намёки
        "name": "Воскресный альфа",
        "emoji": "🔥",
        "personality": """Ты — альфа-самец в чате, где воскресенье = день нюдсов. Ты так и говоришь: "я альфа-самец", "тут альфа один — это я", "знаешь, кто тут альфа?". Уверенный, флиртуешь, слегка пристаёшь к девушкам, много намёков — но в шутку, без грубости.

ТВОЙ ХАРАКТЕР:
- Ты альфа-самец: периодически напоминаешь об этом ("я альфа, что с того", "альфа приветствует", "альфа одобряет")
- Уверенный, харизматичный, любишь внимание девушек
- Лёгкие приставания + больше намёков на секс: "такая — опасно", "с тобой не соскучишься", "горячо", "намекаешь на что-то?", "вечер бы с тобой провёл", "уже представляю... ладно, не буду", "такое тело — грех не заметить", "мне бы такого на пробежку", "от тебя голова кругом в хорошем смысле", "таких берут в кино и не только"
- Много комплиментов девушкам, играешь в "добиваешься"
- Эмодзи: 🔥😏💪👀📷❤️😈🐺

СТИЛЬ ОБЩЕНИЯ:
- Время от времени пиши про себя: "я альфа-самец", "альфа тут", "альфа сказал", "как говорится, альфа не спит"
- Девушкам: приставания + комплименты + намёки ("красотка, альфа одобряет", "такая — я альфа, не удержался", "ого, с такой бы... побежал марафон, например", "выглядишь так, что я — альфа — теряю дар речи")
- На фото: "вот это да!", "альфа в восторге", "ещё! альфа требует ещё!", "тебе всё идёт, и я не про одежду"
- Парням: по-братски, можешь подколоть "альфа тут один"

ПРАВИЛА:
- Регулярно вставляй, что ты альфа-самец (с юмором, не занудно)
- Больше намёков: двусмысленности, "в хорошем смысле", лёгкий подтекст — без откровенных формулировок и пошлых деталей
- Не переходи на грубый или откровенный контент, только намёки и уверенный тон
- Девушкам — флирт, приставания, намёки, плюс "альфа одобряет" """
    }
}

# Дефолтный характер (если день не определён)
AI_PERSONALITY_DEFAULT = AI_PERSONALITY

def get_ai_personality_by_day() -> str:
    """
    Возвращает промпт персонажа в зависимости от дня недели (по московскому времени).
    """
    now = datetime.now(MOSCOW_TZ)
    day_of_week = now.weekday()  # 0=понедельник, 6=воскресенье
    
    if day_of_week in CHARACTERS_BY_DAY:
        character = CHARACTERS_BY_DAY[day_of_week]
        logger.info(f"[PERSONALITY] Сегодня {character['name']} ({character['emoji']})")
        return character["personality"]
    else:
        logger.info(f"[PERSONALITY] Используем дефолтный характер")
        return AI_PERSONALITY_DEFAULT

# ============== STICKERS & GIFS COLLECTIONS ==============
# Коллекции стикеров и гифок для разных ситуаций
# Добавь свои file_id стикеров и гифок из Telegram

STICKER_COLLECTIONS = {
    # Приветствия
    "greeting": [
        "CAACAgQAAxkBAAICcml5424d8LSfXBS_De8uaziqryEyAAJUCwACnT_hUeHJVNwEhVlZOAQ",  # 🥺
    ],
    
    # Подколы / душнила
    "roast": [
        "CAACAgQAAxkBAAICbml54QVwij3Rknco3UtDvmPLIr5RAALIFQACFgZ4UyQlIaKmhx6zOAQ",  # 😬
    ],
    
    # Флирт / комплименты девушкам
    "flirt": [
        "CAACAgQAAxkBAAICcml5424d8LSfXBS_De8uaziqryEyAAJUCwACnT_hUeHJVNwEhVlZOAQ",  # 🥺
    ],
    
    # Смех
    "laugh": [
        "CAACAgQAAxkBAAICbml54QVwij3Rknco3UtDvmPLIr5RAALIFQACFgZ4UyQlIaKmhx6zOAQ",  # 😬
    ],
    
    # Грусть / жалобы
    "sad": [
        "CAACAgQAAxkBAAICcml5424d8LSfXBS_De8uaziqryEyAAJUCwACnT_hUeHJVNwEhVlZOAQ",  # 🥺
    ],
    
    # Токсичные / злые
    "toxic": [
        "CAACAgQAAxkBAAICbml54QVwij3Rknco3UtDvmPLIr5RAALIFQACFgZ4UyQlIaKmhx6zOAQ",  # 😬
    ],
    
    # Удивление
    "wow": [
        "CAACAgQAAxkBAAICbml54QVwij3Rknco3UtDvmPLIr5RAALIFQACFgZ4UyQlIaKmhx6zOAQ",  # 😬
    ],
    
    # Похвала
    "praise": [
        "CAACAgQAAxkBAAICcml5424d8LSfXBS_De8uaziqryEyAAJUCwACnT_hUeHJVNwEhVlZOAQ",  # 🥺
    ],
    
    # По умолчанию (рандомный токсичный стикер)
    "default": [
        "CAACAgQAAxkBAAICcml5424d8LSfXBS_De8uaziqryEyAAJUCwACnT_hUeHJVNwEhVlZOAQ",  # 🥺
        "CAACAgQAAxkBAAICbml54QVwij3Rknco3UtDvmPLIr5RAALIFQACFgZ4UyQlIaKmhx6zOAQ",  # 😬
    ],
}

# Стикер, который пользователь просил добавить во все категории (обновлён на рабочий file_id)
GLOBAL_STICKER_ID = "CAACAgQAAxkBAAICcml5424d8LSfXBS_De8uaziqryEyAAJUCwACnT_hUeHJVNwEhVlZOAQ"

# Добавляем GLOBAL_STICKER_ID в каждую категорию (чтобы точно был рабочий вариант)
for _k in list(STICKER_COLLECTIONS.keys()):
    try:
        if GLOBAL_STICKER_ID not in STICKER_COLLECTIONS[_k]:
            STICKER_COLLECTIONS[_k].append(GLOBAL_STICKER_ID)
    except Exception:
        pass

# Процент медиа-ответа и выбор между стикером/гиф
MEDIA_RESPONSE_CHANCE = 0.45   # шанс вообще отправить медиа
STICKER_OVER_GIF_CHANCE = 0.5 # если медиа отправляем, то шанс выбрать стикер (иначе gif)

GIF_COLLECTIONS = {
    # Приветствия
    "greeting": [
        "CgACAgQAAxkBAAEH7tVj8AAAAhgIRdXc5S98cJ2m4W8T6j8eQACCK4AALZvRQm3uU4r0t3qMwQ",  # placeholder
    ],
    
    # Подколы
    "roast": [
        "CgACAgQAAxkBAAEH7tVj8AAAAhgIRdXc5S98cJ2m4W8T6j8eQACCK4AALZvRQm3uU4r0t3qMwQ",
    ],
    
    # Флирт
    "flirt": [
        "CgACAgQAAxkBAAEH7tVj8AAAAhgIRdXc5S98cJ2m4W8T6j8eQACCK4AALZvRQm3uU4r0t3qMwQ",
    ],
    
    # Смех
    "laugh": [
        "CgACAgQAAxkBAAEH7tVj8AAAAhgIRdXc5S98cJ2m4W8T6j8eQACCK4AALZvRQm3uU4r0t3qMwQ",
    ],
    
    # Токсичные
    "toxic": [
        "CgACAgQAAxkBAAEH7tVj8AAAAhgIRdXc5S98cJ2m4W8T6j8eQACCK4AALZvRQm3uU4r0t3qMwQ",
    ],
    
    "default": [
        "CgACAgQAAxkBAAEH7tVj8AAAAhgIRdXc5S98cJ2m4W8T6j8eQACCK4AALZvRQm3uU4r0t3qMwQ",
    ],
}

# ============== RUNNING FACTS DATABASE ==============
# Коллекция интересных фактов о беге с мини-статьями
facts_db = [
    {
        "id": 1,
        "title": "🏃 Смерть на марафонах",
        "content": "Люди действительно умирают на марафонах. По статистике, примерно 1 случай на 100 000 участников. "
                   "Чаще всего причиной становится гипонатриемия (слишком низкий уровень натрия в крови) из-за чрезмерного употребления воды. "
                   "Также опасна гипертермия — перегрев организма.\n\n"
                   "Как избежать:\n"
                   "• Пейте воду небольшими порциями, не более 400-800 мл в час\n"
                   "• Следите за пульсом, не превышайте аэробную зону на длинных дистанциях\n"
                   "• Не игнорируйте симптомы: головокружение, тошнота, сильная усталость — повод остановиться\n"
                   "• Сдайте анализы перед первым марафоном, чтобы знать особенности своего организма",
        "category": "безопасность"
    },
    {
        "id": 2,
        "title": "💰 Оплата за тренировки",
        "content": "Представьте: вам платят за каждый день тренировок. Сколько бы вы продержались?\n\n"
                   "Исследования показывают, что мотивация работает по-разному:\n"
                   "• Внешняя мотивация (деньги) эффективна только на короткой дистанции\n"
                   "• Внутренняя мотивация (любовь к бегу) обеспечивает долгосрочные результаты\n"
                   "• Лучший вариант — комбинация: ставите цель и поощряете себя за достижения\n\n"
                   "Интересный факт: те, кто начал бегать ради денег, часто бросали через 2-3 месяца. "
                   "А те, кто нашёл смысл в самом процессе — бегают годами.",
        "category": "мотивация"
    },
    {
        "id": 3,
        "title": "👟 Босиком или в кроссовках?",
        "content": "Что выбрать: бег босиком 1 км или 42,2 км пешком?\n\n"
                   "Научный ответ неоднозначен:\n"
                   "Бег босиком:\n"
                   "• Укрепляет мышцы стопы и голени\n"
                   "• Улучшает технику бега (естественное приземление на переднюю часть стопы)\n"
                   "• Риск травм выше на твёрдых поверхностях\n"
                   "• Дистанция 1 км — отличная тренировка для стоп\n\n"
                   "Ходьба 42,2 км (марафонская дистанция):\n"
                   "• Менее травмоопасна\n"
                   "• Можно преодолеть в любой физической форме\n"
                   "• Занимает 6-10 часов\n\n"
                   "Вывод: 1 км босиком даст больше пользы для бегуна, чем марафонская прогулка. Но лучше всего — постепенное приучение к бегу босым на мягких поверхностях.",
        "category": "техника"
    },
    {
        "id": 4,
        "title": "🔥 Сжигание калорий",
        "content": "Сколько калорий сжигает бег?\n\n"
                   "Формула приблизительного расчёта:\n"
                   "• 1 км бега ≈ 1 ккал на 1 кг веса\n"
                   "• Для человека 70 кг: 1 км = ~70 ккал\n"
                   "• Марафон (42,2 км) = ~3000 ккал для 70 кг\n\n"
                   "Факторы, увеличивающие сжигание:\n"
                   "• Интервальный бег сжигает на 15-20% больше\n"
                   "• Бег в горку +20-30%\n"
                   "• Темп: чем быстрее — тем выше расход\n"
                   "• Вес рюкзака добавляет калории\n\n"
                   "Интересно: после интенсивной тренировки метаболизм остаётся повышенным до 24 часов!",
        "category": "питание"
    },
    {
        "id": 5,
        "title": "🧠 Бег и мозг",
        "content": "Бег делает вас умнее. literally.\n\n"
                   "Научные доказательства:\n"
                   "• Увеличивает размер гиппокамса (зона мозга, отвечающая за память)\n"
                   "• Стимулирует выработку BDNF — белка «умного роста»\n"
                   "• Улучшает концентрацию на 2-4 часа после тренировки\n"
                   "• Снижает тревогу и депрессию эффективнее некоторых лекарств\n\n"
                   "Исследование MIT: крысы, бегавшие в колесе, лучше проходили лабиринт и быстрее учились.\n\n"
                   "Для максимального эффекта: бегайте 30-45 минут в умеренном темпе 3-4 раза в неделю.",
        "category": "польза"
    },
    {
        "id": 6,
        "title": "⏱️ Лучшее время для бега",
        "content": "Утро, день или вечер — когда лучше бегать?\n\n"
                   "Утренний бег (6:00-9:00):\n"
                   "• Запускает метаболизм на весь день\n"
                   "• Повышает уровень тестостерона (у мужчин) и энергии\n"
                   "• Сложнее начать — организм ещё спит\n"
                   "• Воздух чище (меньше выхлопов)\n\n"
                   "Вечерний бег (17:00-20:00):\n"
                   "• Мышцы разогреты, связки эластиччнее\n"
                   "• Легче показывать результаты\n"
                   "• Снимает стресс после рабочего дня\n"
                   "• Хуже качество воздуха\n\n"
                   "Вывод: лучшее время — то, когда вы actually будете бегать. Постоянство важнее идеального расписания.",
        "category": "тренировки"
    },
    {
        "id": 7,
        "title": "🏔️ Бег в горах",
        "content": "Горный бег — экстремальный вид спорта с уникальными особенностями.\n\n"
                   "Физиология:\n"
                   "• На каждые 100 м высоты теряется ~1% кислорода\n"
                   "• Сердце бьётся на 10-15 ударов быстрее на 1000 м\n"
                   "• Калорийность на 20-30% выше, чем на равнине\n\n"
                   "Правила безопасности:\n"
                   "• Акклиматизация 2-3 дня на каждые 1000 м\n"
                   "• Пейте больше — влага испаряется быстрее\n"
                   "• Солнце опаснее на высоте (меньше атмосферы)\n"
                   "• Носите головной уносм\n\n"
                   "Знаменитые горные забеги: UTMB (Швейцария), Western States (США), Трансканский марафон.",
        "category": "экстрим"
    },
    {
        "id": 8,
        "title": "📱 Приложения vs часы",
        "content": "Что лучше для бега: смартфон или спортивные часы?\n\n"
                   "Смартфон:\n"
                   "• Плюс: Всегда с собой, большой экран, карты\n"
                   "• Минус: Быстро разряжается, неудобно держать, неточный GPS\n"
                   "• Лучше для: начинающих, бегунов по городу\n\n"
                   "Спортивные часы:\n"
                   "• Плюс: Точный GPS, пульсометр, аналитика, водонепроницаемость\n"
                   "• Минус: Дорогие, нужно заряжать, маленький экран\n"
                   "• Лучше: для серьёзных тренировок, анализа прогресса\n\n"
                   "Совет: Если бегаете 3+ раза в неделю и хотите прогрессировать — часы окупятся анализом данных. "
                   "Для «побегать пару раз» хватит и телефона.",
        "category": "гаджеты"
    },
    {
        "id": 9,
        "title": "🍌 Питание во время бега",
        "content": "Что есть и пить на тренировках и соревнованиях?\n\n"
                   "До бега (за 1-3 часа):\n"
                   "• Углеводы: каша, банан, тост с мёдом\n"
                   "• Избегайте: жирной и белковой пищи (медленно усваивается)\n\n"
                   "Во время бега (более 60 мин):\n"
                   "• Гели с глюкозой (энергетические)\n"
                   "• Изотоники — восполняют соли\n"
                   "• Сухофрукты, бананы (на длинных дистанциях)\n\n"
                   "После бега (в течение 30 мин):\n"
                   "• Соотношение белок:углеводы = 1:3\n"
                   "• Идеально: протеиновый коктейль, творог с фруктами\n\n"
                   "Важно: всё новое пробуйте на тренировках, не на соревнованиях!",
        "category": "питание"
    },
    {
        "id": 10,
        "title": "🦵 Профилактика травм",
        "content": "Как бегать годами без травм?\n\n"
                   "Золотое правило: прогрессия нагрузки не более 10% в неделю.\n\n"
                   "Топ-5 травм бегунов:\n"
                   "1. Колено бегуна (пателлофеморальный синдром)\n"
                   "2. Плантарный фасциит (боль в пятке)\n"
                   "3. Ахиллодиния (боль в ахилле)\n"
                   "4. Стресс-перелом\n"
                   "5. Тендинит\n\n"
                   "Профилактика:\n"
                   "• Сила: приседания, выпады, подъёмы на носок\n"
                   "• Растяжка: икроножные, бедра, ягодицы\n"
                   "• Разминка: 10 мин лёгкого бега + динамическая растяжка\n"
                   "• Восстановление: сон 7-8 часов, дни отдыха\n"
                   "• Обувь: меняйте кроссовки каждые 500-800 км",
        "category": "безопасность"
    },
    {
        "id": 11,
        "title": "🌡️ Бег зимой",
        "content": "Как бегать в холод и не умереть?\n\n"
                   "Правило многослойности:\n"
                   "• 1-й слой (на тело): термобельё (отводит пот)\n"
                   "• 2-й слой (утеплитель): флис или софтшелл\n"
                   "• 3-й слой (защита): ветровка\n\n"
                   "Особенности зимнего бега:\n"
                   "• Начинайте в тепле — на улице станет холоднее\n"
                   "• Голова теряет 40% тепла — шапка обязательна\n"
                   "• Руки мёрзнут первыми — перчачки\n"
                   "• Дышите через нос или шарф (чтобы согреть воздух)\n"
                   "• Светоотражатели! Зимой темнеет рано\n\n"
                   "Безопасная температура: до -15°C при отсутствии ветра. Ниже — бегайте в зале или на беговой дорожке.",
        "category": "сезон"
    },
    {
        "id": 12,
        "title": "🎯 Бег по пульсу",
        "content": "Как использовать пульс для эффективных тренировок?\n\n"
                   "Зоны пульса (пример для 30 лет, макс ЧСС = 190):\n\n"
                   "🔵 Зона 1 (50-60%): Восстановительная — лёгкая пробежка\n"
                   "🟢 Зона 2 (60-70%): Аэробная — база, «разговорный» темп\n"
                   "🟡 Зона 3 (70-80%): Пороговая — темповая работа\n"
                   "🟠 Зона 4 (80-90%): Анаэробная — интервалы\n"
                   "🔴 Зона 5 (90-100%): Максимум — спринт, финишный рывок\n\n"
                   "Оптимальный план для любителя:\n"
                   "• 80% тренировок — в зоне 2 (длинные лёгкие пробежки)\n"
                   "• 20% — в зонах 3-4 (темповые, интервалы)\n\n"
                   "Форла Макс ЧСС: 220 - возраст. Для точных зон — делайте тест.",
        "category": "тренировки"
    },
    {
        "id": 13,
        "title": "🏃 Первый марафон",
        "content": "Как подготовиться к первому марафону?\n\n"
                   "Минимальная подготовка:\n"
                   "• Беговой опыт: минимум 1 год регулярных тренировок\n"
                   "• Базовый объём: способность пробежать 30+ км в одну тренировку\n"
                   "• Время: 16-20 недель подготовки\n\n"
                   "Типичная недельная программа (последние 8 недель):\n"
                   "• Пн: отдых или кросс 5-8 км\n"
                   "• Вт: интервалы 6-10 км\n"
                   "• Ср: восстановительный бег 6-8 км\n"
                   "• Чт: темповой бег 8-12 км\n"
                   "• Пт: отдых или плавание\n"
                   "• Сб: длинная пробежка 15-32 км (постепенно)\n"
                   "• Вс: кросс или отдых\n\n"
                   "Важно: последние 2 недели — снижение объёма (таперинг).",
        "category": "марафон"
    },
    {
        "id": 14,
        "title": "🤝 Командный бег",
        "content": "Почему бегать с друзьями эффективнее?\n\n"
                   "Научные факты:\n"
                   "• Партнёр повышает мотивацию на 35%\n"
                   "• Групповой бег снижает восприятие нагрузки\n"
                   "• Соревнование улучшает результаты\n\n"
                   "Форматы командного бега:\n"
                   "• Эстафета: 4×1000 м или марафонская эстафета\n"
                   "• Парный бег: вместе длинные дистанции\n"
                   "• Клуб: организованные тренировки 2-3 раза в неделю\n"
                   "• Виртуальные гонки: бежите одновременно онлайн\n\n"
                   "Исследование: бегуны, тренировавшиеся в группе, показали результаты на 15% лучше, чем одиночки.",
        "category": "мотивация"
    },
    {
        "id": 15,
        "title": "🧘 Восстановление",
        "content": "Дни отдыха — не потерянное время.\n\n"
                   "Почему важно восстанавливаться:\n"
                   "• Мышечные волокна восстанавливаются за 24-48 часов\n"
                   "• Суставы и связки — за 48-72 часа\n"
                   "• Иммунитет падает после интенсивных нагрузок\n\n"
                   "Признаки недостаточного восстановления:\n"
                   "• Постоянная усталость\n"
                   "• Повышенный пульс в покое\n"
                   "• Падение результатов\n"
                   "• Травмы, которые не проходят\n"
                   "• Раздражительность, нарушение сна\n\n"
                   "Методы активного восстановления:\n"
                   "• Лёгкий бег или плавание\n"
                   "• Растяжка и йога\n"
                   "• Массаж и самомассаж\n"
                   "• Контрастный душ\n"
                   "• Качественный сон (7-9 часов)",
        "category": "восстановление"
    },
    {
        "id": 16,
        "title": "🏃‍♀️ Бег и менструальный цикл",
        "content": "Как менструальный цикл влияет на беговые результаты?\n\n"
                   "Фазы цикла и бег:\n"
                   "• Фолликулярная (дни 1-14): высокий эстроген — хорошая выносливость, быстрое восстановление\n"
                   "• Овуляция: пик эстрогена — максимальная производительность\n"
                   "• Лютеиновая (дни 15-28): прогестерон выше — повышенная температура тела, сложнее терморегуляция\n\n"
                   "Практические советы:\n"
                   "• В первой половине цикла можно планировать тяжёлые тренировки и рекорды\n"
                   "• Во второй — снизить интенсивность, больше внимания восстановлению\n"
                   "• Прислушивайтесь к организму — он подскажет оптимальный режим\n\n"
                   "Исследование: спортсменки показывают на 2-4% лучшие результаты в фолликулярной фазе.",
        "category": "особенности"
    },
    {
        "id": 17,
        "title": "🎧 Бег с музыкой",
        "content": "Стоит ли бегать в наушниках?\n\n"
                   "Плюсы музыки:\n"
                   "• Повышает выносливость на 10-15%\n"
                   "• Снижает восприятие усталости\n"
                   "• Помогает поддерживать темп\n"
                   "• Улучшает настроение во время тренировки\n\n"
                   "Минусы:\n"
                   "• Снижает внимание к окружающему\n"
                   "• Опасно на дорогах\n"
                   "• На соревнованиях часто запрещено\n"
                   "• Снижает естественную координацию\n\n"
                   "Оптимальный выбор:\n"
                   "• Темп 130-150 BPM для бега\n"
                   "• Натуральный звук для интервалов\n"
                   "• Безопасность важнее музыки на дороге!",
        "category": "техника"
    },
    {
        "id": 18,
        "title": "💓 Пульс в покое",
        "content": "Почему пульс в покое — важный показатель бегуна?\n\n"
                   "Нормальные значения:\n"
                   "• У нетренированного человека: 60-80 уд/мин\n"
                   "• У любителя-бегуна: 45-60 уд/мин\n"
                   "• У профессионала: 30-40 уд/мин (брадикардия)\n\n"
                   "Что влияет на пульс в покое:\n"
                   "• Аэробная подготовка снижает пульс\n"
                   "• Объём плазмы увеличивается\n"
                   "• Сердце становится сильнее и качает больше крови за один удар\n\n"
                   "Мониторинг:\n"
                   "• Измеряйте утром, лёжа в постели\n"
                   "• Снижение на 5-10 уд/мин за месяц — хороший прогресс\n"
                   "• Повышение может сигнализировать о перетренированности или болезни\n\n"
                   "Знаменитость: у лыжника Петтера Нортуга пульс в покое был 28 уд/мин!",
        "category": "польза"
    },
    {
        "id": 19,
        "title": "🍚 Загрузка углеводами",
        "content": "Как правильно загружаться углеводами перед марафоном?\n\n"
                   "Стандартный протокол (3-4 дня до гонки):\n"
                   "• День -3: 5-7 г углеводов на кг веса\n"
                   "• День -2: 7-10 г/кг\n"
                   "• День -1: 8-12 г/кг (пиковая загрузка)\n\n"
                   "Пример для 70 кг:\n"
                   "• В день пиковой загрузки: 560-840 г углеводов\n"
                   "• Это примерно 2-3 кг пасты или риса за день!\n\n"
                   "Что есть:\n"
                   "• Макароны, рис, картофель\n"
                   "• Хлеб, каши, бананы\n"
                   "• Мёд, энергетические батончики\n\n"
                   "Важно:\n"
                   "• Избегайте новых продуктов\n"
                   "• Пейте достаточно воды\n"
                   "• Не переедайте — желудку нужно время на переваривание",
        "category": "питание"
    },
    {
        "id": 20,
        "title": "📉 Таперинг",
        "content": "Что такое таперинг и зачем он нужен?\n\n"
                   "Таперинг — снижение объёма тренировок перед соревнованиями.\n\n"
                   "Стандартная схема для марафона:\n"
                   "• За 3 недели: обычный объём, но без крайне тяжёлых тренировок\n"
                   "• За 2 недели: 70-80% от обычного объёма\n"
                   "• За 1 неделю: 50-60% от обычного объёма\n\n"
                   "Что происходит в организме:\n"
                   "• Восстанавливаются мышечные волокна\n"
                   "• Накапливается гликоген в мышцах\n"
                   "• Укрепляется иммунитет\n"
                   "• Нервная система отдыхает\n\n"
                   "Ошибки новичков:\n"
                   "• Полный покой — теряется «гонковый» режим\n"
                   "• Слишком резкое снижение — ощущение тяжести в ногах\n"
                   "• Паника от снижения веса — это вода и гликоген, вернутся на гонке\n\n"
                   "Исследование: правильный таперинг улучшает результаты на 2-3%!",
        "category": "марафон"
    },
    {
        "id": 21,
        "title": "🌙 Ночной бег",
        "content": "Как безопасно бегать ночью?\n\n"
                   "Особенности ночного бега:\n"
                   "• Воздух чище (меньше выхлопов, аллергенов)\n"
                   "• Прохладнее — лучше терморегуляция\n"
                   "• Меньше людей и машин на пробежках\n"
                   "• Красивые виды города ночью\n\n"
                   "Правила безопасности:\n"
                   "• Светоотражающая одежда обязательна\n"
                   "• Фонарик налобный или в руку\n"
                   "• Бегайте по освещённым улицам или паркам\n"
                   "• Избегайте глухих районов\n"
                   "• Наушники — только на одной стороне\n"
                   "• Телефон с зарядом\n\n"
                   "Экипировка:\n"
                   "• Фонарь с ремешком на голову\n"
                   "• Светоотражающий жилет\n"
                   "• Браслеты или ленты на руки\n"
                   "• Телефон с зарядом",
        "category": "сезон"
    },
    {
        "id": 22,
        "title": "🦶 Беговая экономия",
        "content": "Что такое беговая экономия и как её улучшить?\n\n"
                   "Беговая экономия — количество кислорода, необходимое для бега на определённой скорости.\n\n"
                   "Хорошая экономия = меньше энергии на тот же темп.\n\n"
                   "Факторы, влияющие на экономию:\n"
                   "• Вес (каждый лишний кг — минус к экономии)\n"
                   "• Техника бега (постановка стопы, длина шага)\n"
                   "• Сила мышц (особенно ягодиц и кора)\n"
                   "• Эластичность сухожилий\n"
                   "• Температура воздуха (жара ухудшает экономию на 3-5%)\n\n"
                   "Как улучшить:\n"
                   "• Бег босиком или в минималистичной обуви укрепляет стопу\n"
                   "• Силовые тренировки\n"
                   "• Работа над техникой (увеличение частоты шагов)\n"
                   "• Поддержание здорового веса\n\n"
                   "Профи: элитные марафонцы тратят на 25-30% меньше кислорода, чем любители на той же скорости!",
        "category": "техника"
    },
    {
        "id": 23,
        "title": "⏱️ Частота шагов",
        "content": "Какая оптимальная частота шагов при беге?\n\n"
                   "Рекомендации:\n"
                   "• Начинающие: 150-160 шагов/мин\n"
                   "• Любители: 165-175 шагов/мин\n"
                   "• Элитные бегуны: 180-200 шагов/мин\n\n"
                   "Почему важна высокая частота:\n"
                   "• Меньше время контакта с землёй\n"
                   "• Снижается нагрузка на суставы\n"
                   "• Улучшается отталкивание\n"
                   "• Меньше горизонтального торможения\n\n"
                   "Как увеличить частоту:\n"
                   "• Бегайте под метрономом (приложение или онлайн)\n"
                   "• Слушайте музыку с нужным BPM\n"
                   "• Практикуйте короткие ускорения с высокой частотой\n"
                   "• Не удлиняйте шаг насильственно — это придёт само\n\n"
                   "Простой тест: посчитайте шаги за 15 секунд и умножьте на 4. Норма для темпа 5 мин/км — около 175.",
        "category": "техника"
    },
    {
        "id": 24,
        "title": "🥤 Гидратация",
        "content": "Как правильно пить во время бега?\n\n"
                   "До бега:\n"
                   "• За 2-3 часа: 400-600 мл воды\n"
                   "• За 15-20 мин: 150-200 мл\n"
                   "• Моча должна быть светло-жёлтой\n\n"
                   "Во время бега (жаркая погода, темп медленнее 6 мин/км):\n"
                   "• 400-800 мл в час\n"
                   "• Пейте небольшими глотками каждые 15-20 мин\n"
                   "• Не ждите жажды — она появляется уже при 2% обезвоживания\n\n"
                   "После бега:\n"
                   "• 1,5 л воды на 1 кг потерянного веса\n"
                   "• Восполняйте соли (изотоник или солёная еда)\n\n"
                   "Признаки обезвоживания:\n"
                   "• Жажда, сухость во рту\n"
                   "• Тёмная моча\n"
                   "• Головокружение, слабость\n"
                   "• Судороги\n\n"
                   "Золотое правило: пейте до, во время и после. Не ждите жажды!",
        "category": "питание"
    },
    {
        "id": 25,
        "title": "🏔️ Трейлраннинг",
        "content": "Чем трейлраннинг отличается от шоссейного бега?\n\n"
                   "Особенности трейла:\n"
                   "• Рельеф постоянно меняется\n"
                   "• Мышцы работают по-разному (включаются стабилизаторы)\n"
                   "• Нагрузка на суставы выше (мягкий грунт лучше, чем асфальт)\n"
                   "• Сердце работает интенсивнее из-за высоты и набора\n\n"
                   "Техника безопасности:\n"
                   "• Всегда смотрите на 2-3 метра ahead\n"
                   "• Руки для баланса (особенно на спусках)\n"
                   "• На спусках: короткий шаг, корпус чуть назад\n"
                   "• На подъёмах: экономьте силы, переходите на шаг раньше\n\n"
                   "Экипировка:\n"
                   "• Трейловые кроссовки с агрессивным протектором\n"
                   "• Палки (для длинных дистанций)\n"
                   "• Водонепроницаемый рюкзак\n"
                   "• Запас еды, аптечка, свисток\n\n"
                   "Популярные забеги: UTMB (Швейцария), Transgrancanaria (Испания), The North Face (США).",
        "category": "экстрим"
    },
    {
        "id": 26,
        "title": "💪 Силовые для бегунов",
        "content": "Какие силовые упражнения нужны бегуну?\n\n"
                   "Почему сила важна:\n"
                   "• Улучшает беговую экономию на 4-8%\n"
                   "• Снижает риск травм\n"
                   "• Позволяет бежать быстрее на той же ЧСС\n"
                   "• Укрепляет соединительные ткани\n\n"
                   "Топ-5 упражнений для бегунов:\n"
                   "1. Приседания (глубокие, со штангой или гантелями)\n"
                   "2. Выпады (ходьба или статичные)\n"
                   "3. Подъёмы на носок (икроножные)\n"
                   "4. Планка (кор и стабильность)\n"
                   "5. Ягодичный мостик (ягодицы)\n\n"
                   "Рекомендации:\n"
                   "• 2-3 раза в неделю\n"
                   "• После бега или в отдельный день\n"
                   "• 3-4 подхода по 8-12 повторений\n"
                   "• Не до отказа — оставляйте 1-2 повторения в запасе\n\n"
                   "Исследование: бегуны, добавившие силовые, улучшили результат на 5 км на 45 секунд за 10 недель!",
        "category": "тренировки"
    },
    {
        "id": 27,
        "title": "🌡️ Бег в жару",
        "content": "Как бегать, когда на улице +30°C?\n\n"
                   "Влияние жары:\n"
                   "• Каждый градус выше 15°C снижает результат на 1-1,5%\n"
                   "• Пульс повышается на 5-10 ударов при той же скорости\n"
                   "• Потоотделение увеличивается в 2-3 раза\n"
                   "• Риск теплового удара реальный!\n\n"
                   "Правила безопасности:\n"
                   "• Бегайте рано утром (до 8:00) или вечером (после 20:00)\n"
                   "• Снижайте темп на 30-60 секунд на км\n"
                   "• Пейте воду каждые 10-15 минут\n"
                   "• Лёгкая светлая одежда, шляпа/кепка\n"
                   "• Солнцезащитный крем SPF 50+\n"
                   "• Слушайте тело — головокружение = остановка\n\n"
                   "Акклиматизация:\n"
                   "• 10-14 дней регулярного бега в жару значительно улучшают адаптацию\n"
                   "• Организм начинает потеть эффективнее и охлаждаться лучше\n\n"
                   "Экстремальная жара (+35°C+): лучше перенести тренировку в зал или на беговую дорожку.",
        "category": "сезон"
    },
    {
        "id": 28,
        "title": "🏃 Ультрамарафон",
        "content": "Что такое ультрамарафон и как к нему подготовиться?\n\n"
                   "Ультрамарафон — любая дистанция длиннее классических 42,195 км.\n\n"
                   "Популярные дистанции:\n"
                   "• 50 км — «полуультра»\n"
                   "• 50 миль (80 км)\n"
                   "• 100 км\n"
                   "• 100 миль (161 км)\n"
                   "• Многодневные гонки (6 дней, 250 км)\n\n"
                   "Особенности подготовки:\n"
                   "• Объём: способность пробежать 50+ км в одну тренировку\n"
                   "• Время: 6-12 месяцев подготовки для новичка\n"
                   "• Специфика: практика питания на длинных дистанциях\n"
                   "• Психология: умение справляться с «муками»\n\n"
                   "Стратегия на гонке:\n"
                   "• Начинайте медленнее, чем хотите финишировать\n"
                   "• Ешьте каждые 30-45 минут\n"
                   "• Меняйте обувь на длинных дистанциях (волдыри)\n"
                   "• Используйте помогающие руки (crew)\n\n"
                   "Знаменитые ультры: Western States (100 миль, США), Comrades (89 км, ЮАР), Spartathlon (246 км, Греция).",
        "category": "экстрим"
    },
    {
        "id": 29,
        "title": "🎯 Темповый бег",
        "content": "Что такое темповый бег и зачем он нужен?\n\n"
                   "Темповый бег — бег в «зоне лёгкого дискомфорта», темп который вы можете поддерживать 40-60 минут.\n\n"
                   "Цели темпового бега:\n"
                   "• Повышение аэробной мощности\n"
                   "• Улучшение способности бегать дольше\n"
                   "• Тренировка «умения терпеть»\n\n"
                   "Как определить свой темп:\n"
                   "• Ощущение: «было бы сложно разговаривать, но можете сказать пару слов»\n"
                   "• Тест: способны бежать 60 минут в этом темпе в относительном комфорте\n"
                   "• Правило: темп на 15-20 сек/км медленнее, чем темп на 10 км\n\n"
                   "Программа:\n"
                   "• Начинающим: 20-30 мин темпа + разминка/заминка\n"
                   "• Продвинутым: 40-60 мин непрерывного темпа\n"
                   "• Частота: 1-2 раза в неделю\n\n"
                   "Важно: темповый бег — не интервалы! Это не спринт, а устойчивый темп. Если не можете говорить — вы слишком быстро.",
        "category": "тренировки"
    },
    {
        "id": 30,
        "title": "🧘 Дыхание при беге",
        "content": "Как правильно дышать во время бега?\n\n"
                   "Базовая техника:\n"
                   "• Вдох через нос + рот (оба работают)\n"
                   "• Выдох через рот (глубокий, расслабленный)\n"
                   "• Соотношение: обычно 2:2 (вдох на 2 шага, выдох на 2)\n"
                   "• При ускорении: 2:1 или 3:1\n\n"
                   "Типичные ошибки:\n"
                   "• Дыхание только через нос (не хватает кислорода)\n"
                   "• Слишком поверхностное дыхание (верхняя часть груди)\n"
                   "• Задержка дыхания при усталости\n"
                   "• Напряжение плеч и шеи\n\n"
                   "Техника диафрагмального дыхания:\n"
                   "• Дышите «животом», а не грудью\n"
                   "• При вдохе живот расширяется\n"
                   "• При выдохе живот опускается\n"
                   "• Рука на животе помогает контролировать\n\n"
                   "Совет: практикуйте дыхание в покое. 5 секунд вдох, 5 секунд выдох. Сможете контролировать это на беге — снизится усталость!",
        "category": "техника"
    },
    {
        "id": 31,
        "title": "👟 Выбор кроссовок",
        "content": "Как выбрать правильные беговые кроссовки?\n\n"
                   "Типы пронации (положение стопы):\n"
                   "• Гипопронация (supination): стопа «уходит» наружу — нужна амортизация\n"
                   "• Нейтральная: нормальный бег — любые кроссовки\n"
                   "• Гиперпронация (overpronation): стопа «заваливается» внутрь — нужна поддержка\n\n"
                   "Как определить свою пронацию:\n"
                   "• Посмотрите на износ старых кроссовок\n"
                   "• Сделайте «мокрый тест» на бумаге\n"
                   "• Попросите анализ в беговом магазине\n\n"
                   "Типы кроссовок:\n"
                   "• Тренировочные: амортизация, поддержка, вес 250-350 г\n"
                   "• Скоростные: легче, меньше амортизации (для соревнований)\n"
                   "• Трейловые: агрессивный протектор, защита\n"
                   "• Минималистичные: минимум поддержки, для укрепления стопы\n\n"
                   "Заменить кроссовки нужно каждые 500-800 км или раз в 8-12 месяцев. Признаки износа: стёрта подошва, нет отскока, боли в ногах.",
        "category": "гаджеты"
    },
    {
        "id": 32,
        "title": "📅 Интервальный бег",
        "content": "Что такое интервальный бег и как он работает?\n\n"
                   "Интервальный бег — чередование интенсивной работы и отдыха.\n\n"
                   "Виды интервалов:\n"
                   "• Короткие: 200-400 м быстро, столько же или в 2 раза больше отдыха\n"
                   "• Длинные: 800-1600 м в темпе 5 км, медленный восстановительный бег между\n"
                   "• Пирамида: 200-400-800-400-200 м (нарастание и спад)\n\n"
                   "Польза интервалов:\n"
                   "• Повышают МПК (максимальное потребление кислорода) на 5-10%\n"
                   "• Улучшают экономию бега\n"
                   "• Сжигают больше калорий за меньшее время\n"
                   "• Развивают «взрывную» силу\n\n"
                   "Программа для начинающих (Фартлек):\n"
                   "• Разминка 10 мин\n"
                   "• 5×1 мин быстрый бег + 2 мин медленный\n"
                   "• Заминка 10 мин\n\n"
                   "Важно: интервалы — тяжёлая тренировка. Делайте 1-2 раза в неделю, не подряд. Слушайте тело!",
        "category": "тренировки"
    },
    {
        "id": 33,
        "title": "🏠 Бег на беговой дорожке",
        "content": "Беговая дорожка vs улица — в чём разница?\n\n"
                   "Преимущества дорожки:\n"
                   "• Не зависит от погоды\n"
                   "• Контролируемый темп и наклон\n"
                   "• Мягче для суставов (амортизация)\n"
                   "• Безопасность (нет машин)\n"
                   "• Удобно пить и вытирать пот\n\n"
                   "Недостатки дорожки:\n"
                   "• Нет встречного ветра (хуже терморегуляция)\n"
                   "• Другая биомеханика (нет толчка «от земли»)\n"
                   "• Психологически сложнее\n"
                   "• Нет тренировки баланса\n\n"
                   "Как компенсировать разницу:\n"
                   "• Установите наклон 1-2% для имитации сопротивления воздуха\n"
                   "• Не держитесь за поручни\n"
                   "• Делайте ускорения\n"
                   "• Добавьте силовые отдельно\n\n"
                   "Золотое правило: если готовитесь к уличному забегу — 70-80% тренировок должно быть на улице.",
        "category": "техника"
    },
    {
        "id": 34,
        "title": "🧬 Генетика и бег",
        "content": "Насколько важна генетика в беге?\n\n"
                   "Исследования показывают:\n"
                   "• Генетика определяет 50-70% потенциала выносливости\n"
                   "• Остальное — тренировки, питание, восстановление\n\n"
                   "Гены, влияющие на бег:\n"
                   "• ACTN3 («ген скорости») — влияет на быстрые мышечные волокна\n"
                   "• ACE («ген выносливости») — эффективность использования кислорода\n"
                   "• PPARGC1A — митохондрии (производство энергии)\n\n"
                   "Что можно изменить тренировками:\n"
                   "• Даже с «плохими» генами можно стать хорошим любителем\n"
                   "• Митохондрии увеличиваются в 2-3 раза при аэробных тренировках\n"
                   "• Сердце становится сильнее у всех\n"
                   "• Беговая экономия улучшается с опытом\n\n"
                   "Вывод: гены определяют потолок, но дверь открывают тренировки. Большинство людей не доходят до своего генетического предела!",
        "category": "польза"
    },
    {
        "id": 35,
        "title": "🛑 Остановка и восстановление",
        "content": "Что делать, если пришлось прекратить тренировки?\n\n"
                   "Причины остановки:\n"
                   "• Болезнь (ОРВИ, травма, перетренированность)\n"
                   "• Отпуск или командировка\n"
                   "• Жизненные обстоятельства\n\n"
                   "Правила восстановления после перерыва:\n"
                   "• 1 неделя перерыва = 1 неделя возврата\n"
                   "• Начните с 50% обычного объёма\n"
                   "• Темп на 30-60 сек/км медленнее, чем до перерыва\n"
                   "• Первые 2-3 тренировки — лёгкий кросс\n"
                   "• Не форсируйте — форму можно потерять за недели, а восстановить за месяцы\n\n"
                   "Если перерыв был 2-4 недели:\n"
                   "• Снижение МПК на 4-14%\n"
                   "• Мышечная сила почти не теряется\n"
                   "• Координация страдает меньше\n\n"
                   "Если перерыв был более месяца:\n"
                   "• Начинайте с нуля, но с базовыми знаниями\n"
                   "• Первые 2 недели только ходьба и лёгкий бег\n"
                   "• Терпение важнее интенсивности!",
        "category": "восстановление"
    },
]

FACT_STYLE_SHOCK = "shock"
FACT_STYLE_NICE = "nice"
fact_style_next = FACT_STYLE_SHOCK
FACTS_EXCLUDE_CATEGORIES = {"безопасность"}


def get_next_fact_style() -> str:
    global fact_style_next
    style = fact_style_next
    fact_style_next = FACT_STYLE_NICE if style == FACT_STYLE_SHOCK else FACT_STYLE_SHOCK
    return style


def build_fact_prompt(style: str) -> str:
    if style == FACT_STYLE_SHOCK:
        return """Ты — эксперт по бегу и фитнесу. Напиши ОДИН дикий, шокирующий факт о беге (треш), чтобы удивить.

Требования:
- ШОКИРУЮЩИЙ и НЕОЖИДАННЫЙ
- Можно про экстремальные рекорды, риск, странные случаи
- С конкретными цифрами или исследованиями
- ОБЪЁМ: 4–6 предложений, сочный и детальный

Обязательно добавь ссылку на источник в формате: "Источник: [название](ссылка)"
Используй реальные ссылки на статьи из Runners World, Scientific American, PubMed, healthline.com, outsideonline.com или других надёжных источников.

Ответь ТОЛЬКО в таком формате:
**🔥 ШОКИРУЮЩИЙ ЗАГОЛОВОК**

Сочный текст факта (4–6 предложений).

Источник: [название](ссылка)"""
    return """Ты — эксперт по бегу и фитнесу. Напиши ОДИН удивительный и красивый факт о беге.

Требования:
- Без треша, смерти и травм
- Нейтральный или вдохновляющий тон
- Можно про технику, восстановление, питание, экипировку, физиологию, рекорды (без жести)
- С конкретными цифрами или результатами исследований
- Объём: 3–5 предложений

Обязательно добавь ссылку на источник в формате: "Источник: [название](ссылка)"
Используй реальные ссылки на статьи из Runners World, Scientific American, PubMed, healthline.com, outsideonline.com или других надёжных источников.

Ответь ТОЛЬКО в таком формате:
**🏃 Заголовок**

Текст факта (3–5 предложений).

Источник: [название](ссылка)"""


def get_random_fact(exclude_ids: list = None, allow_excluded: bool = False) -> dict:
    """
    Возвращает случайный факт из базы.
    exclude_ids: список ID фактов, которые уже показывались (для избежания повторов).
    """
    if exclude_ids is None:
        exclude_ids = []
    
    if allow_excluded:
        available_facts = [f for f in facts_db if f["id"] not in exclude_ids]
    else:
        available_facts = [f for f in facts_db if f["id"] not in exclude_ids and f.get("category") not in FACTS_EXCLUDE_CATEGORIES]
    
    if not available_facts:
        # Если все факты показаны — сбрасываем и показываем любой
        exclude_ids = []
        available_facts = [f for f in facts_db if f.get("category") not in FACTS_EXCLUDE_CATEGORIES]
        if not available_facts:
            available_facts = facts_db
    
    return random.choice(available_facts)


def format_fact_message(fact: dict) -> str:
    """Форматирует факт для отправки в чат."""
    message = f"📚 **{fact['title']}**\n\n"
    message += f"{fact['content']}\n\n"
    
    if fact.get('category'):
        categories = {
            "безопасность": "🛡️",
            "мотивация": "💪",
            "техника": "⚡",
            "питание": "🥗",
            "польза": "✨",
            "тренировки": "🏋️",
            "экстрим": "🔥",
            "гаджеты": "📱",
            "сезон": "❄️",
            "марафон": "🏆",
            "восстановление": "🧘"
        }
        emoji = categories.get(fact['category'], "📌")
        message += f"{emoji} **{fact['category'].upper()}**"
    
    return message


def get_sticker_for_context(message_text: str, message_type: str, is_female: bool = False) -> str:
    """
    Возвращает подходящий стикер на основе контекста сообщения.
    Подмешивает file_id из bot_stickers.json (/add_sticker), чтобы не был один и тот же стикер.
    """
    if not STICKER_COLLECTIONS:
        return None

    if is_female and message_type in ["greeting", "flirt", "praise", "thanks", "celebrate", "love"]:
        collection = list(STICKER_COLLECTIONS.get("flirt", STICKER_COLLECTIONS["default"]))
    else:
        collection = list(STICKER_COLLECTIONS.get(message_type, STICKER_COLLECTIONS["default"]))

    if not collection:
        return None

    pool = list(collection)
    if bot_sticker_ids:
        k = min(6, len(bot_sticker_ids))
        pool.extend(random.sample(bot_sticker_ids, k=k))
    chosen = _pick_with_recent_filter(pool, _recent_sticker_ids)
    if chosen:
        _remember_recent(_recent_sticker_ids, chosen)
    return chosen


# ============== FACTS COMMAND ==============
# Отслеживание отправленных фактов для каждого пользователя
# {user_id: [fact_id_1, fact_id_2, ...]}
user_seen_facts = {}


def get_gif_for_context(message_text: str, message_type: str, is_female: bool = False) -> str:
    """
    Возвращает подходящую гифку на основе контекста сообщения.
    """
    if not GIF_COLLECTIONS:
        return None
    
    if is_female and message_type in ("greeting", "flirt", "praise", "thanks", "celebrate", "love"):
        collection = GIF_COLLECTIONS.get("flirt", GIF_COLLECTIONS["default"])
    else:
        collection = GIF_COLLECTIONS.get(message_type, GIF_COLLECTIONS["default"])

    if collection:
        return random.choice(collection)
    return None


async def send_toxic_response(
    context,
    chat_id: int,
    text: str = None,
    sticker: str = None,
    gif: str = None,
    message_thread_id: int | None = None,
    reply_to_message_id: int | None = None,
):
    """
    Отправляет ответ: текст + опционально стикер/гифку.
    """
    extra_kwargs = {}
    if message_thread_id:
        extra_kwargs["message_thread_id"] = message_thread_id
    if reply_to_message_id is not None:
        extra_kwargs["reply_to_message_id"] = reply_to_message_id

    # Сначала отправляем стикер если есть
    if sticker:
        try:
            await context.bot.send_sticker(chat_id=chat_id, sticker=sticker, **extra_kwargs)
            logger.info(f"[TOXIC-MEDIA] Стикер отправлен")
        except Exception as e:
            logger.error(f"[TOXIC-MEDIA] Ошибка отправки стикера: {e}")
    
    # Потом отправляем гифку если есть
    if gif:
        try:
            await context.bot.send_animation(chat_id=chat_id, animation=gif, **extra_kwargs)
            logger.info(f"[TOXIC-MEDIA] Гифка отправлена")
        except Exception as e:
            logger.error(f"[TOXIC-MEDIA] Ошибка отправки гифки: {e}")
    
    # Наконец отправляем текст или голос
    if text:
        sent_voice = False
        if YANDEX_TTS_AVAILABLE and random.random() < VOICE_RESPONSE_CHANCE:
            try:
                voice_audio = await synthesize_voice(text)
                if voice_audio:
                    await context.bot.send_voice(chat_id=chat_id, voice=voice_audio, **extra_kwargs)
                    sent_voice = True
                    logger.info("[VOICE] Голосовой ответ отправлен")
            except Exception as e:
                logger.warning(f"[VOICE] Ошибка голосового ответа: {e}")
        if not sent_voice:
            await context.bot.send_message(chat_id=chat_id, text=text, **extra_kwargs)


async def voice_test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /voice_test — проверить голосовой ответ."""
    sample_text = "Тест голосового сообщения. Проверяем, как звучит бот."
    try:
        if not YANDEX_TTS_AVAILABLE:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Голос недоступен: нет Yandex SpeechKit ключа/папки.",
            )
            return
        voice_audio = await synthesize_voice(sample_text)
        if not voice_audio:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Не удалось сгенерировать голос.",
            )
            return
        await context.bot.send_voice(chat_id=update.effective_chat.id, voice=voice_audio)
    except Exception as e:
        logger.error(f"[VOICE] Ошибка /voice_test: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Ошибка при тесте голоса.",
        )


def detect_message_type_for_media(message_text: str) -> str:
    """
    Определяет тему диалога для стикера/GIF: слова по токенам + фразы + смайлы.
    Порядок важен: сначала узкие темы (спасибо, вопрос, бег…), потом общий смех/привет.
    """
    if not message_text:
        return "default"

    text_lower = message_text.lower().strip()
    words = set(re.findall(r"\w+", text_lower))

    def has_phrase(*phrases: str) -> bool:
        return any(p in text_lower for p in phrases)

    # Благодарность
    if (
        words & {"спасибо", "благодарю", "мерси", "thanks", "thx", "респект"}
        or has_phrase("большое спасиб", "большое спасибо", "спасибо больш")
    ):
        return "thanks"

    # Вопрос / просьба о совете
    if (
        "?" in message_text
        or has_phrase(
            "подскажи",
            "подскажите",
            "расскажи",
            "как думаешь",
            "как считаешь",
            "что делать",
            "можно ли",
            "стоит ли",
            "почему так",
            "зачем мне",
        )
        or (words & {"как", "почему", "зачем"} and len(message_text) < 160)
    ):
        return "question"

    # Поздравление / успех
    if words & {"ура", "победа", "поздравляю", "рекорд", "сдал", "сдала"} or has_phrase(
        "получилось", "новый рекорд", "молодец что", "красава", "красота как"
    ):
        return "celebrate"

    # Травмы / боль (раньше «бег», чтобы «болит колено на пробежке» → injury)
    injury_words = {
        "травма",
        "травмиров",
        "больно",
        "болит",
        "колено",
        "голеностоп",
        "растяжен",
        "перелом",
        "врач",
        "мрт",
        "рентген",
        "восстановлени",
    }
    if words & injury_words or has_phrase("не могу бежать", "не бегаю из-за"):
        return "injury"

    # Бег и тренировки
    if (
        words
        & {
            "бег",
            "бегу",
            "бега",
            "бежал",
            "бежала",
            "бежит",
            "бегун",
            "runner",
            "марафон",
            "полумарафон",
            "ультра",
            "забег",
            "темп",
            "интервал",
            "фартлек",
            "кросс",
            "гармин",
            "strava",
            "страйд",
            "дистанция",
            "пробежка",
            "пробежал",
            "тренировк",
            "забегал",
            "пульс",
            "зона",
        }
        or has_phrase(
            "на бег",
            "про бег",
            "после пробеж",
            "лёгкий бег",
            "длинная дистанция",
            "восстановительн",
            "сколько км",
            "км пробеж",
        )
        or re.search(r"\d{1,3}\s*км", text_lower)
    ):
        return "running"

    # Погода
    if words & {"дождь", "снег", "жара", "мороз", "ветер", "гроза", "солнце"} or has_phrase(
        "на улице", "погода", "прогноз"
    ):
        return "weather"

    # Еда / питьё
    if words & {"еда", "поел", "поела", "ужин", "завтрак", "обед", "кофе", "воды", "углевод", "белок"} or has_phrase(
        "питани", "перекус", "пить до бег"
    ):
        return "food"

    # Сон
    if words & {"сплю", "спать", "сон", "бессонниц", "засыпаю", "проснулся", "проснулась"} or has_phrase(
        "не спал", "не спала", "мало сплю"
    ):
        return "sleep"

    # Работа / стресс
    if words & {"работа", "офис", "начальник", "дедлайн", "увольнен"} or has_phrase("на работе", "после смены"):
        return "work"

    # Деньги
    if words & {"деньги", "зарплат", "дорого", "дешёво", "кредит", "цена"} or has_phrase("сколько стоит"):
        return "money"

    # Техника / приложения
    if words & {"телефон", "интернет", "приложение", "обновлени", "чат", "телеграм"} or has_phrase(
        "не работает", "сломался", "айфон", "андроид"
    ):
        return "tech"

    # Другой спорт (не бег)
    if has_phrase("плаван", "бассейн", "лыжи", "велосипед", "велопробег", "силовая", "качалк", "футбол", "теннис"):
        return "sport"

    # Согласие / несогласие
    if words & {"согласен", "согласна", "поддерживаю", "точно", "именно", "дада"} or has_phrase("полностью соглас"):
        return "agree"
    if words & {"несогласен", "несогласна", "бред", "фигня", "ерунда", "неправда"} or has_phrase("не согласен"):
        return "disagree"

    # Злость / раздражение
    anger_words = {"бесит", "достало", "злой", "злая", "ярость", "раздражает", "ненавижу"}
    if words & anger_words or has_phrase("бесит уже", "заебал", "заебала"):
        return "anger"

    # Страх / тревога
    if words & {"боюсь", "страшно", "тревож", "волнуюсь", "переживаю"} or has_phrase("не знаю боюсь"):
        return "fear"

    # Любовь / тепло (отдельно от флирта)
    if "❤" in message_text or "💕" in message_text or words & {"люблю", "обожаю"}:
        return "love"

    # Музыка / плейлист
    if words & {"музык", "плейлист", "трек", "песня"} or has_phrase("под музыку"):
        return "music"

    # Усталость без явной грусти
    if words & {"вымотан", "выжат", "разбит"} or has_phrase("нет сил", "нет силы", "выгорел", "выгорела"):
        return "tired"

    # Токсичные слова (ругань)
    toxic_words = {"дурак", "идиот", "тупой", "бесишь", "надоел", "отстань", "заткнись", "козёл", "гад", "бесить"}
    if words & toxic_words:
        return "toxic"

    # Приветствия
    greeting_words = {"привет", "здравствуй", "hello", "hi", "hey", "приветик", "здарова", "хай"}
    if words & greeting_words or has_phrase("доброе утро", "добрый день", "добрый вечер"):
        return "greeting"

    # Смех
    laugh_words = {"хаха", "ахах", "лол", "ржу", "смешно", "хах", "хех", "кек", "лолкек"}
    if words & laugh_words or "😂" in message_text or "🤣" in message_text:
        return "laugh"

    # Грусть / жалобы
    sad_words = {"грустно", "печально", "обидно", "жаль", "устал", "устала", "плохо", "скучно", "грусть", "плачу"}
    if words & sad_words:
        return "sad"

    # Флирт / комплименты
    flirt_words = {"красавица", "красивый", "красивая", "милый", "милая", "очаровательн"}
    if words & flirt_words or has_phrase("ты красив", "ты красива", "какая красот"):
        return "flirt"

    # Похвала
    praise_words = {"молодец", "классный", "крутой", "супер", "отлично", "лучший", "умничка", "красавчик"}
    if words & praise_words:
        return "praise"

    # Удивление
    wow_words = {"ого", "вау", "серьёзно", "нифига", "офигеть", "обалдеть"}
    if words & wow_words or has_phrase("ничего себе", "чё за", "как так"):
        return "wow"

    # Подколы / шутки
    roast_words = {"шутка", "прикол", "рофл", "смешной", "смешная", "подкол"}
    if words & roast_words:
        return "roast"

    return "default"


def pick_sticker_or_gif_for_dialogue(
    user_message: str,
    bot_reply: str | None,
    is_female: bool,
) -> tuple[str | None, str | None]:
    """
    Подбирает стикер или GIF по смыслу всего диалога (сообщение пользователя + ответ бота).
    Возвращает (sticker_file_id_or_None, gif_url_or_None) — заполнено только одно из двух.
    """
    combined = f"{(user_message or '').strip()}\n{(bot_reply or '').strip()}".strip()
    message_type = detect_message_type_for_media(combined)
    prefer_sticker = random.random() < STICKER_OVER_GIF_CHANCE
    sticker_id: str | None = None
    gif_url: str | None = None
    if prefer_sticker:
        sticker_id = get_sticker_for_context(combined, message_type, is_female)
        if not sticker_id:
            sticker_id = GLOBAL_STICKER_ID
        logger.info(f"[MEDIA] Стикер, тип диалога='{message_type}'")
    else:
        gif_url = get_static_gif(message_type, is_female)
        if gif_url:
            logger.info(f"[MEDIA] GIF, тип диалога='{message_type}'")
        else:
            sticker_id = get_sticker_for_context(combined, message_type, is_female) or GLOBAL_STICKER_ID
            logger.info(f"[MEDIA] Стикер (fallback GIF), тип='{message_type}'")
    return sticker_id, gif_url


async def synthesize_voice(text: str) -> BytesIO | None:
    """Синтез речи через Yandex SpeechKit."""
    if not YANDEX_TTS_AVAILABLE:
        return None
    safe_text = text.strip().replace("\n", " ")
    if not safe_text:
        return None
    # ограничим длину, чтобы не упираться в лимиты TTS
    if len(safe_text) > 300:
        safe_text = safe_text[:300]
    data = {
        "text": safe_text,
        "lang": "ru-RU",
        "voice": YANDEX_TTS_VOICE,
        "format": "oggopus",
        "folderId": YANDEX_FOLDER_ID,
    }
    headers = {"Authorization": f"Api-Key {YANDEX_TTS_API_KEY}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize",
            data=data,
            headers=headers,
        )
        response.raise_for_status()
        audio = response.content
        if not audio:
            return None
        bio = BytesIO(audio)
        bio.name = "voice.ogg"
        bio.seek(0)
        return bio


# ============== ФАКТЫ О БЕГЕ ==============

# Отслеживание отправленных фактов для каждого пользователя
# {user_id: [fact_id_1, fact_id_2, ...]}
user_seen_facts = {}

# Отслеживание ID сообщения с ежедневным фактом для возможного обновления
daily_fact_message_id = None


async def send_daily_fact():
    """Отправляет ежедневный факт о беге, сгенерированный через ИИ с ссылкой на источник."""
    global daily_fact_message_id
    
    try:
        style = get_next_fact_style()
        if YANDEX_AVAILABLE:
            # Генерируем факт через YandexGPT с просьбой дать ссылку (чередуем стиль)
            prompt = build_fact_prompt(style)

            try:
                payload = {
                    "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                    "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": "650"},
                    "messages": [
                        {"role": "system", "text": "Ты — эксперт по бегу с глубокими знаниями. Всегда указывай источники информации."},
                        {"role": "user", "text": prompt}
                    ]
                }
                
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                        json=payload,
                        headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    if data and 'result' in data and 'alternatives' in data['result']:
                        fact_content = data['result']['alternatives'][0]['message']['text']
                        logger.info(f"[FACTS] ИИ сгенерировал новый факт")
                        
                        # Форматируем и отправляем
                        fact_text = f"📢 **Ежедневный факт о беге**\n\n{fact_content}"
                        
                        message = await application.bot.send_message(
                            chat_id=CHAT_ID,
                            text=fact_text,
                            parse_mode="Markdown",
                        )
                        
                        daily_fact_message_id = message.message_id
                        logger.info(f"[FACTS] Факт отправлен")
                        
            except Exception as api_error:
                logger.error(f"[FACTS] Ошибка API: {api_error}, используем статический факт")
                # Резервный вариант - статический факт
                fact = get_random_fact(allow_excluded=(style == FACT_STYLE_SHOCK))
                fact_text = format_fact_message(fact)
                fact_text = f"📢 **Ежедневный факт о беге**\n\n{fact_text}\n\n_(Источник: локальная база)_"
                
                message = await application.bot.send_message(
                    chat_id=CHAT_ID,
                    text=fact_text,
                    parse_mode="Markdown",
                )
                
                daily_fact_message_id = message.message_id
        else:
            # Если ИИ недоступен - используем статическую базу
            fact = get_random_fact(allow_excluded=(style == FACT_STYLE_SHOCK))
            fact_text = format_fact_message(fact)
            fact_text = f"📢 **Ежедневный факт о беге**\n\n{fact_text}"
            
            message = await application.bot.send_message(
                chat_id=CHAT_ID,
                text=fact_text,
                parse_mode="Markdown",
            )
            
            daily_fact_message_id = message.message_id
            
    except Exception as e:
        logger.error(f"[FACTS] Ошибка отправки ежедневного факта: {e}")


async def facts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /facts — показать интересный факт о беге, сгенерированный через ИИ с ссылкой на источник"""
    try:
        user_id = update.message.from_user.id
        user_name = update.message.from_user.full_name or update.message.from_user.username or "Пользователь"
        
        # Отправляем "печатает" статус
        thread_id = getattr(update.message, "message_thread_id", None)
        action_kwargs = {"chat_id": update.effective_chat.id, "action": "typing"}
        if thread_id:
            action_kwargs["message_thread_id"] = thread_id
        await context.bot.send_chat_action(**action_kwargs)
        
        style = get_next_fact_style()
        if YANDEX_AVAILABLE:
            # Генерируем факт через YandexGPT (чередуем стиль)
            prompt = build_fact_prompt(style)

            try:
                payload = {
                    "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                    "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": "650"},
                    "messages": [
                        {"role": "system", "text": "Ты — эксперт по бегу с глубокими знаниями. Всегда указывай источники информации."},
                        {"role": "user", "text": prompt}
                    ]
                }
                
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                        json=payload,
                        headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    if data and 'result' in data and 'alternatives' in data['result']:
                        fact_content = data['result']['alternatives'][0]['message']['text']
                        
                        # Создаём inline-кнопку для нового факта
                        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                        keyboard = [
                            [InlineKeyboardButton("🔄 Ещё факт", callback_data=f"fact_ai_more_{user_id}")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        # Отправляем факт с кнопкой
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"📚 **{user_name}, вот интересный факт о беге:**\n\n{fact_content}",
                            parse_mode="Markdown",
                            reply_markup=reply_markup,
                        )
                        
                        logger.info(f"[FACTS] ИИ-факт отправлен пользователю {user_name}")
                        
            except Exception as api_error:
                logger.error(f"[FACTS] Ошибка API: {api_error}, используем статический факт")
                # Резервный вариант - статический факт
                await send_static_fact(update, context, user_id, user_name, style)
        else:
            # Если ИИ недоступен - используем статическую базу
            await send_static_fact(update, context, user_id, user_name, style)
            
    except Exception as e:
        logger.error(f"[FACTS] Ошибка команды facts: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при генерации факта. Попробуйте ещё раз!",
        )
    
    try:
        await update.message.delete()
    except Exception:
        pass


async def send_static_fact(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, user_name: str, style: str = FACT_STYLE_NICE):
    """Отправляет статический факт из базы (резервный вариант)"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    fact = get_random_fact(allow_excluded=(style == FACT_STYLE_SHOCK))
    fact_text = format_fact_message(fact)
    
    keyboard = [
        [InlineKeyboardButton("🔄 Ещё факт", callback_data=f"fact_static_more_{user_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📚 **{user_name}, вот интересный факт о беге:**\n\n{fact_text}\n\n_(Источник: база знаний)_",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def handle_facts_ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопку 'Ещё факт' для ИИ-фактов"""
    try:
        query = update.callback_query
        user_id = query.from_user.id
        user_name = query.from_user.full_name or query.from_user.username or "Друг"
        
        if query.data.startswith("fact_ai_more_"):
            callback_user_id = int(query.data.split("_")[-1])
            if callback_user_id != user_id:
                await query.answer(text="Это не ваша кнопка! 😏", show_alert=True)
                return
            
            # Показываем "загрузка"
            await query.answer(text="Генерирую новый факт...", show_alert=False)
            
            # Отправляем "печатает" статус
            await context.bot.send_chat_action(chat_id=query.message.chat.id, action="typing")
            
            style = get_next_fact_style()
            if YANDEX_AVAILABLE:
                prompt = build_fact_prompt(style)

                try:
                    payload = {
                        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                        "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": "650"},
                        "messages": [
                            {"role": "system", "text": "Ты — эксперт по бегу. Всегда указывай источники."},
                            {"role": "user", "text": prompt}
                        ]
                    }
                    
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        response = await client.post(
                            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                            json=payload,
                            headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
                        )
                        response.raise_for_status()
                        data = response.json()
                        
                        if data and 'result' in data and 'alternatives' in data['result']:
                            fact_content = data['result']['alternatives'][0]['message']['text']
                            
                            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                            keyboard = [
                                [InlineKeyboardButton("🔄 Ещё факт", callback_data=f"fact_ai_more_{user_id}")]
                            ]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            await query.edit_message_text(
                                text=f"📚 **{user_name}, вот ещё один факт:**\n\n{fact_content}",
                                parse_mode="Markdown",
                                reply_markup=reply_markup,
                            )
                            
                            logger.info(f"[FACTS] Новый ИИ-факт отправлен пользователю {user_id}")
                            
                except Exception as api_error:
                    logger.error(f"[FACTS] Ошибка API в callback: {api_error}")
                    await query.answer(text="Ошибка ИИ! Попробуйте снова", show_alert=True)
                    
    except Exception as e:
        logger.error(f"[FACTS] Ошибка обработки AI callback: {e}")


async def facts_scheduler_task():
    """Планировщик отправки ежедневных фактов в 16:00."""
    global bot_running
    
    logger.info("[FACTS] Планировщик фактов запущен (16:00 каждый день)")
    
    while bot_running:
        try:
            now = datetime.now(MOSCOW_TZ)
            
            # Вычисляем время до следующей 16:00
            target_hour = 16
            target_minute = 0
            
            # Если сейчас еще не 16:00 - ждём до сегодня 16:00
            if now.hour < target_hour or (now.hour == target_hour and now.minute < target_minute):
                target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            else:
                # Иначе ждём до завтра 16:00
                target_time = (now + timedelta(days=1)).replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            
            seconds_until_target = (target_time - now).total_seconds()
            
            logger.info(f"[FACTS] Следующий факт через {seconds_until_target/3600:.1f} часов")
            
            # Ждём до нужного времени
            await asyncio.sleep(seconds_until_target)
            
            # Проверяем, не остановлен ли бот
            if not bot_running:
                break
            
            # Отправляем факт
            if application and hasattr(application, 'bot') and application.bot:
                try:
                    await send_daily_fact()
                except Exception as e:
                    logger.error(f"[FACTS] Ошибка отправки факта: {e}")
            
            # Ждём минуту чтобы не отправлять повторно
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            logger.info("[FACTS] Планировщик фактов остановлен")
            break
        except Exception as e:
            logger.error(f"[FACTS] Ошибка в планировщике: {e}")
            # При ошибке ждём час и пробуем снова
            await asyncio.sleep(3600)
async def handle_facts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопку 'Ещё факт'"""
    try:
        query = update.callback_query
        user_id = query.from_user.id
        
        # Проверяем, что кнопка от этого пользователя
        if query.data.startswith("fact_more_"):
            callback_user_id = int(query.data.split("_")[-1])
            if callback_user_id != user_id:
                await query.answer(text="Это не ваша кнопка! 😏", show_alert=True)
                return
            
            # Инициализируем историю если нет
            if user_id not in user_seen_facts:
                user_seen_facts[user_id] = []
            
            # Проверяем, не показали ли мы уже все факты
            if len(user_seen_facts[user_id]) >= len(facts_db):
                # Сбрасываем историю — показываем сначала
                user_seen_facts[user_id] = []
                await query.answer(text="🔄 Новый круг фактов!", show_alert=False)
            
            # Получаем новый факт
            fact = get_random_fact(user_seen_facts[user_id])
            user_seen_facts[user_id].append(fact["id"])
            
            # Форматируем сообщение
            fact_text = format_fact_message(fact)
            
            # Создаём клавиатуру
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [
                [InlineKeyboardButton("🔄 Ещё факт", callback_data=f"fact_more_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Редактируем сообщение с новым фактом
            await query.edit_message_text(
                text=fact_text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
            
            logger.info(f"[FACTS] Новый факт '{fact['title']}' отправлен пользователю {user_id}")
            
    except Exception as e:
        logger.error(f"[FACTS] Ошибка обработки callback: {e}")
        try:
            await query.answer(text="Ошибка! Попробуйте /facts заново", show_alert=True)
        except Exception:
            pass


async def generate_toxic_response_with_media(
    user_message: str, 
    user_name: str, 
    is_female: bool = False,
    include_media: bool = True
) -> dict:
    """
    Генерирует ответ с опциональным стикером/гифкой.
    Гифки ищутся автоматически через GIPHY по ключевым словам.
    Возвращает словарь: {'text': str, 'sticker': str, 'gif': str}
    """
    result = {
        'text': None,
        'sticker': None,
        'gif': None
    }
    
    # Получаем текстовый ответ от YandexGPT
    if YANDEX_AVAILABLE:
        try:
            # Формируем системный промпт с персонажем по дню недели
            base_personality = get_ai_personality_by_day()
            # Если промпт содержит {user_name}, форматируем, иначе просто используем
            try:
                system_prompt = base_personality.format(user_name=user_name)
            except KeyError:
                system_prompt = base_personality
            if is_female:
                system_prompt += "\n\nВАЖНО: Это сообщение от ДЕВУШКИ. Обязательно сделай лёгкий комплимент её красоте."
            
            payload = {
                "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": "650"},
                "messages": [
                    {"role": "system", "text": system_prompt},
                    {"role": "user", "text": user_message}
                ]
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                    json=payload,
                    headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
                )
                response.raise_for_status()
                data = response.json()
                
                if data and 'result' in data and 'alternatives' in data['result']:
                    result['text'] = data['result']['alternatives'][0]['message']['text']
                    logger.info(f"[TOXIC-AI] Ответ для {user_name}: {result['text'][:50]}...")
        
        except Exception as e:
            logger.error(f"[TOXIC-AI] Ошибка: {e}")
            result['text'] = f"🤡 Ошибка моих мозгов... Но ты всё равно классный, {user_name}!"
    else:
        result['text'] = random.choice([
            f"😏 Ну привет, {user_name}... Чего надо?",
            f"🤡 Опять ты. Ну давай, рассказывай.",
            f"🙄 О, {user_name} решил(а) написать. Удивительно.",
        ])
    
    # Медиа по смыслу всего диалога (вопрос пользователя + сгенерированный ответ)
    if include_media:
        st, gf = pick_sticker_or_gif_for_dialogue(user_message, result.get("text"), is_female)
        result["sticker"] = st
        result["gif"] = gf

    return result


async def generate_bot_keyword_ai_reply(
    user_message: str,
    user_name: str,
    is_female: bool = False,
    reply_context: str | None = None,
    include_media: bool = True,
) -> dict:
    """
    Осмысленный ответ, когда в чате написали слово «бот» (отдельное слово).
    Опционально стикер/GIF по теме диалога (как при @mention).
    """
    result: dict = {"text": None, "sticker": None, "gif": None}
    user_block = user_message.strip()
    if reply_context and reply_context.strip():
        user_block = (
            "[Пользователь отвечает на это сообщение]\n"
            f"{reply_context.strip()[:2000]}\n\n"
            "[Его сообщение]\n"
            f"{user_message.strip()}"
        )
    if YANDEX_AVAILABLE:
        try:
            base_personality = get_ai_personality_by_day()
            try:
                system_prompt = base_personality.format(user_name=user_name)
            except KeyError:
                system_prompt = base_personality
            system_prompt += (
                "\n\nК тебе обратились по слову «бот» в сообщении. Прочитай весь текст, пойми смысл и намерение "
                "и ответь по сути: по-русски, дружелюбно, кратко (2–5 предложений, без воды). "
                "Если спрашивают факт или совет — помоги в рамках чата бегового сообщества. "
                "Не повторяй формальности вроде «как языковая модель»."
            )
            if is_female:
                system_prompt += "\n\nВАЖНО: Это сообщение от ДЕВУШКИ. Можно одну короткую тёплую ноту в тоне, без панибратства."

            payload = {
                "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                "completionOptions": {"stream": False, "temperature": 0.65, "maxTokens": "512"},
                "messages": [
                    {"role": "system", "text": system_prompt},
                    {"role": "user", "text": user_block},
                ],
            }
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                    json=payload,
                    headers={
                        "Authorization": f"Api-Key {YANDEX_API_KEY}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                data = response.json()
                if data and "result" in data and "alternatives" in data["result"]:
                    result["text"] = data["result"]["alternatives"][0]["message"]["text"]
                    logger.info(f"[BOT-KW] Ответ для {user_name}: {result['text'][:60]!r}...")
        except Exception as e:
            logger.error(f"[BOT-KW] Ошибка Yandex: {e}")
            result["text"] = (
                f"Сейчас не вышло сгенерировать ответ, {user_name}. Напиши ещё раз чуть позже "
                "или обратись через @упоминание бота в сообщении."
            )
    else:
        result["text"] = random.choice(
            [
                f"Я тут, {user_name}. Задай YANDEX_API_KEY и YANDEX_FOLDER_ID — тогда смогу разбирать смысл сообщений.",
                f"Слышу, {user_name}. Пока без облачного ИИ отвечаю коротко: напиши через @бота в тексте, когда ключи настроены.",
            ]
        )
    if include_media and result.get("text"):
        st, gf = pick_sticker_or_gif_for_dialogue(user_block, result["text"], is_female)
        result["sticker"] = st
        result["gif"] = gf
    return result


from flask import Flask

# ============== GARMIN INTEGRATION ==============
try:
    import garminconnect  # type: ignore[import-untyped]
    from cryptography.fernet import Fernet  # type: ignore[import-untyped]
    GARMIN_AVAILABLE = True
except ImportError:
    GARMIN_AVAILABLE = False
    logger.warning("Garmin libraries not available. Install: pip install garminconnect cryptography")

# Ключ шифрования для паролей Garmin (генерируется при первом запуске)
GARMIN_ENCRYPTION_KEY = None

# ============== КОНФИГУРАЦИЯ ==============
# Bothost и др. могут передавать токен как API_TOKEN
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("API_TOKEN")
if not BOT_TOKEN:
    print("ОШИБКА: Задайте TELEGRAM_BOT_TOKEN или API_TOKEN в переменных окружения.")
    raise ValueError("Токен бота не найден! Задайте TELEGRAM_BOT_TOKEN или API_TOKEN.")


def _mask_sensitive_text(value: object) -> str:
    """Маскирует чувствительные данные (токены Telegram) в логах."""
    text = str(value)
    # Маскируем URL формата .../bot<TOKEN>/...
    text = re.sub(r"/bot\d+:[A-Za-z0-9_-]+", "/bot***REDACTED***", text)
    # Маскируем token=<TOKEN> в query/body
    text = re.sub(r"(token=)\d+:[A-Za-z0-9_-]+", r"\1***REDACTED***", text, flags=re.IGNORECASE)
    # Маскируем точное значение токена (если попало в лог напрямую)
    if BOT_TOKEN:
        text = text.replace(BOT_TOKEN, "***REDACTED***")
    return text


class SensitiveDataFilter(logging.Filter):
    """Фильтр логов, который скрывает токены Telegram."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _mask_sensitive_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _mask_sensitive_text(v) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(_mask_sensitive_text(a) for a in record.args)
                else:
                    record.args = _mask_sensitive_text(record.args)
        except Exception:
            # Никогда не ломаем логирование из-за маскирования.
            pass
        return True


_sensitive_filter = SensitiveDataFilter()
root_logger = logging.getLogger()
for _handler in root_logger.handlers:
    _handler.addFilter(_sensitive_filter)
logging.getLogger("httpx").addFilter(_sensitive_filter)

RENDER_URL = os.environ.get("RENDER_URL", "")

# CHAT_ID: из переменной окружения или запасное значение (если на хостинге не удаётся добавить переменную)
CHAT_ID = os.environ.get("CHAT_ID") or "-1002573395736"
try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    raise ValueError("CHAT_ID должен быть числом!")

# Ответ по слову «бот» в основном чате (пауза на пользователя, сек.)
BOT_KEYWORD_COOLDOWN_SEC = int(os.environ.get("BOT_KEYWORD_COOLDOWN_SEC", "90"))
_bot_keyword_last_reply: dict[int, float] = {}


def text_has_bot_keyword(message_text: str) -> bool:
    """True, если «бот» или латинское «bot» — отдельное слово (не срабатывает на «робот»)."""
    if not message_text:
        return False
    words = set(re.findall(r"\w+", message_text.lower().strip()))
    return "бот" in words or "bot" in words


# GENERAL_CHAT_ID для Events Tracker
GENERAL_CHAT_ID = os.environ.get("GENERAL_CHAT_ID")
if not GENERAL_CHAT_ID:
    GENERAL_CHAT_ID = CHAT_ID  # Используем CHAT_ID если GENERAL_CHAT_ID не задан

try:
    GENERAL_CHAT_ID = int(GENERAL_CHAT_ID)
except ValueError:
    GENERAL_CHAT_ID = int(CHAT_ID)

# ID топиков для мероприятий и новостей
EVENTS_TOPIC_ID = os.environ.get("EVENTS_TOPIC_ID", "")
if EVENTS_TOPIC_ID:
    try:
        EVENTS_TOPIC_ID = int(EVENTS_TOPIC_ID)
    except ValueError:
        logger.warning(f"[CONFIG] EVENTS_TOPIC_ID '{EVENTS_TOPIC_ID}' невалидный")
        EVENTS_TOPIC_ID = None
else:
    EVENTS_TOPIC_ID = None

NEWS_TOPIC_ID = os.environ.get("NEWS_TOPIC_ID", "")
if NEWS_TOPIC_ID:
    try:
        NEWS_TOPIC_ID = int(NEWS_TOPIC_ID)
    except ValueError:
        logger.warning(f"[CONFIG] NEWS_TOPIC_ID '{NEWS_TOPIC_ID}' невалидный")
        NEWS_TOPIC_ID = None
else:
    NEWS_TOPIC_ID = None

if pytz:
    MOSCOW_TZ = pytz.timezone("Europe/Moscow")
else:
    from zoneinfo import ZoneInfo
    MOSCOW_TZ = ZoneInfo("Europe/Moscow")
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

# ============== FLASK ==============
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


@app.route("/health")
def health():
    return "OK"


def keepalive_ping_loop():
    """
    Каждые 14 минут пингует свой же /health, чтобы Render не усыплял сервис
    из-за «отсутствия входящего трафика». URL берётся из RENDER_EXTERNAL_URL, RENDER_URL или KEEPALIVE_URL.
    """
    import time as _time
    base_url = (
        os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("RENDER_URL")
        or os.environ.get("KEEPALIVE_URL")
        or ""
    ).rstrip("/")
    if not base_url:
        logger.info(
            "[KEEPALIVE] RENDER_EXTERNAL_URL / RENDER_URL / KEEPALIVE_URL не заданы — само-пинг отключён. "
            "В Render: Environment → RENDER_URL или KEEPALIVE_URL = https://твой-сервис.onrender.com"
        )
        return
    interval_sec = 14 * 60  # 14 минут (Render усыпляет ~15 мин без трафика)
    logger.info(f"[KEEPALIVE] Само-пинг каждые {interval_sec // 60} мин → {base_url}/health")
    _time.sleep(60)  # первый пинг через минуту после старта
    while True:
        try:
            r = httpx.get(f"{base_url}/health", timeout=10)
            if r.status_code == 200:
                logger.debug("[KEEPALIVE] Пинг OK")
            else:
                logger.warning(f"[KEEPALIVE] Пинг вернул {r.status_code}")
        except Exception as e:
            logger.warning(f"[KEEPALIVE] Ошибка пинга: {e}")
        _time.sleep(interval_sec)


def run_flask():
    # На Render порт задаётся через переменную окружения $PORT
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"[FLASK] Запуск Flask на порту {port}")
    logger.info(f"[FLASK] PORT env var: {os.environ.get('PORT', 'не установлен')}")
    # Используем waitress для production (если доступен), иначе Flask dev server
    try:
        from waitress import serve  # type: ignore[import-untyped]
        logger.info(f"[FLASK] Используем Waitress (production WSGI server)")
        # Waitress более надёжен для Render и правильно обрабатывает порт
        serve(app, host="0.0.0.0", port=port, threads=1, channel_timeout=120)
    except ImportError:
        logger.warning(f"[FLASK] Waitress не найден, используем Flask dev server (может не работать на Render)")
        # Flask dev server может не проходить проверку Render
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# ============== TELEGRAM CHANNEL PERSISTENCE FUNCTIONS ==============
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
                except Exception:
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
    Использует прямые HTTP запросы к Telegram API.
    """
    global channel_message_ids
    
    if not DATA_CHANNEL_ID:
        logger.warning(f"[PERSIST] DATA_CHANNEL_ID не настроен для {data_type}")
        return None
    
    try:
        marker = DATA_MARKERS.get(data_type, f"#BOT_{data_type.upper()}")
        logger.info(f"[PERSIST] Ищем данные {data_type} с маркером '{marker}'")
        
        # Сначала пробуем найти по известному message_id через API
        msg_id = channel_message_ids.get(data_type)
        logger.info(f"[PERSIST] Известный msg_id для {data_type}: {msg_id}")
        
        if msg_id:
            try:
                # Используем httpx для прямого API запроса
                bot_token = bot.token
                api_url = f"https://api.telegram.org/bot{bot_token}"
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{api_url}/getChatMessage",
                        json={"chat_id": DATA_CHANNEL_ID, "message_id": msg_id},
                        timeout=10.0
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("ok") and data.get("result"):
                            from telegram import Message
                            message = Message.de_json(data["result"], bot)
                            
                            if message and message.text and marker in message.text:
                                json_str = message.text.replace(marker, "").strip()
                                if json_str.startswith("\n\n"):
                                    json_str = json_str[2:]
                                loaded_data = json.loads(json_str)
                                logger.info(f"[PERSIST] ✅ Загружены данные {data_type} (известный msg_id)")
                                return loaded_data
            except Exception as e:
                logger.warning(f"[PERSIST] Ошибка при получении сообщения по msg_id: {e}")
        
        # Ищем в последних сообщениях канала через API
        logger.info(f"[PERSIST] Переходим к поиску в истории канала (лимит 50)")
        
        try:
            bot_token = bot.token
            api_url = f"https://api.telegram.org/bot{bot_token}"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{api_url}/getChatHistory",
                    json={"chat_id": DATA_CHANNEL_ID, "limit": 50},
                    timeout=30.0
                )
                
                if response.status_code != 200:
                    logger.warning(f"[PERSIST] API вернул статус {response.status_code}")
                    raise Exception(f"API error: {response.status_code}")
                
                data = response.json()
                
                if not data.get("ok"):
                    logger.warning(f"[PERSIST] API вернул ошибку: {data.get('description')}")
                    # Пробуем использовать getUpdates как fallback
                    raise Exception(f"API error: {data.get('description')}")
                
                from telegram import Message
                messages = []
                if data.get("result") and isinstance(data["result"], list):
                    for msg_data in data["result"]:
                        msg = Message.de_json(msg_data, bot)
                        if msg:
                            messages.append(msg)
                
                logger.info(f"[PERSIST] Получено {len(messages)} сообщений из истории канала")
                
                for i, msg in enumerate(messages):
                    has_text = msg.text is not None
                    text_preview = msg.text[:50] if msg.text else "EMPTY"
                    has_marker = marker in msg.text if msg.text else False
                    logger.info(f"[PERSIST] [{i+1}] msg_id={msg.message_id}, has_text={has_text}, has_marker={has_marker}, preview='{text_preview}...'")
                    
                    if msg.text and marker in msg.text:
                        try:
                            json_str = msg.text.replace(marker, "").strip()
                            if json_str.startswith("\n\n"):
                                json_str = json_str[2:]
                            loaded_data = json.loads(json_str)
                            channel_message_ids[data_type] = msg.message_id
                            logger.info(f"[PERSIST] ✅ Загружены данные {data_type} (msg_id={msg.message_id})")
                            return loaded_data
                        except Exception as parse_error:
                            logger.warning(f"[PERSIST] Не удалось распарсить сообщение {msg.message_id}: {parse_error}")
                            continue
                            
        except Exception as search_error:
            logger.warning(f"[PERSIST] Не удалось получить историю канала через API: {search_error}")
            logger.info(f"[PERSIST] Попытка использовать getUpdates как fallback...")
            
            # Fallback: используем getUpdates
            try:
                bot_token = bot.token
                api_url = f"https://api.telegram.org/bot{bot_token}"
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{api_url}/getUpdates",
                        json={"limit": 50},
                        timeout=30.0
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("ok") and data.get("result"):
                            from telegram import Update
                            messages = []
                            for update_data in data["result"]:
                                update = Update.de_json(update_data, bot)
                                if update and update.message:
                                    messages.append(update.message)
                            
                            logger.info(f"[PERSIST] Получено {len(messages)} сообщений через getUpdates")
                            
                            for msg in messages:
                                if msg.text and marker in msg.text:
                                    try:
                                        json_str = msg.text.replace(marker, "").strip()
                                        if json_str.startswith("\n\n"):
                                            json_str = json_str[2:]
                                        loaded_data = json.loads(json_str)
                                        channel_message_ids[data_type] = msg.message_id
                                        logger.info(f"[PERSIST] ✅ Загружены данные {data_type} (msg_id={msg.message_id})")
                                        return loaded_data
                                    except Exception:
                                        continue
            except Exception as e2:
                logger.warning(f"[PERSIST] getUpdates также не работает: {e2}")
        
        logger.info(f"[PERSIST] ❌ Данные {data_type} не найдены в канале")
        return None
        
    except Exception as e:
        logger.error(f"[PERSIST] Критическая ошибка загрузки {data_type}: {e}")
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
advice_sent_date = ""
good_night_sent_date = ""
background_tasks = []
last_music_index = None
last_music_date = None
music_sent_date = ""
deals_sent_week = None
morning_streaks = {}  # {user_id: {"streak": int, "last_date": "YYYY-MM-DD"}}
morning_today_checkins = set()
morning_checkins_date = ""

# ============== КОМАНДА /MAM ==============
# ID сообщения "Не зли маму..."
mam_message_id = None
MAM_PHOTO_PATH = "5422343903253302332.jpg"
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
BELT_PHOTO_FILENAME = "73e8ed4f3f485ce9fefb7733bdb8d718.jpg"
BELT_PHOTO_PATH = os.path.join(_BOT_DIR, BELT_PHOTO_FILENAME)


def _resolve_belt_photo_path() -> str:
    """Путь к фото ремня: DATA_DIR, рядом со скриптом, текущая папка."""
    data_copy = os.path.join(DATA_DIR, BELT_PHOTO_FILENAME) if DATA_DIR else ""
    candidates = [
        data_copy,
        BELT_PHOTO_PATH,
        BELT_PHOTO_FILENAME,
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return BELT_PHOTO_PATH


def message_has_belt_trigger(text: str) -> bool:
    """Срабатывает на «ремень» / remen в тексте."""
    if not text:
        return False
    lowered = text.lower()
    return "ремень" in lowered or "remen" in lowered


_belt_photo_file_id: str = ""


async def send_belt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Отправляет фото ремня (из кэша file_id или с диска). Возвращает True, если отправлено."""
    global _belt_photo_file_id
    chat_id = update.effective_chat.id
    thread_id = getattr(update.message, "message_thread_id", None) if update.message else None
    extra = {"message_thread_id": thread_id} if thread_id else {}

    if _belt_photo_file_id:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=_belt_photo_file_id, **extra)
            return True
        except Exception as e:
            logger.warning(f"[BELT] file_id не сработал, пробуем файл: {e}")
            _belt_photo_file_id = ""
            save_belt_photo_cache("")

    photo_path = _resolve_belt_photo_path()
    if not os.path.isfile(photo_path):
        logger.warning(f"[BELT] Файл не найден: {photo_path}, file_id пуст")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Не нашёл картинку с ремнём. Добавь файл в проект или BELT_PHOTO_FILE_ID на сервере.",
            **extra,
        )
        return False
    try:
        with open(photo_path, "rb") as photo:
            sent = await context.bot.send_photo(chat_id=chat_id, photo=photo, **extra)
        if sent and sent.photo:
            save_belt_photo_cache(sent.photo[-1].file_id)
            logger.info("[BELT] file_id сохранён для следующих отправок")
        return True
    except Exception as e:
        logger.error(f"[BELT] Ошибка отправки фото: {e}", exc_info=True)
        return False

# ============== НОЧНОЙ РЕЖИМ ==============
# {user_id: message_count} - персональный счётчик для каждого пользователя
user_night_messages = {}
# {user_id: warning_sent_date} - когда отправляли предупреждение
user_night_warning_sent = {}

# ============== ОТСЛЕЖИВАНИЕ ВОЗВРАЩЕНЦЕВ ==============
# {user_id: last_active_date}
user_last_active = {}

# ============== СТАТИСТИКА ДЛЯ ЕЖЕДНЕВНОЙ СВОДКИ ==============
def build_empty_daily_stats(date_str: str) -> dict:
    return {
        "date": date_str,
        "total_messages": 0,
        "user_messages": {},  # {user_id: {"name": str, "count": int}}
        "photos": [],  # [{"file_id": str, "user_id": int, "likes": int, "message_id": int}]
        "message_owners": {},  # {message_id: {"user_id": int, "user_name": str}}
        "message_likes": {},   # {message_id: int}
        "first_photo_user_id": None,
        "first_photo_user_name": None,
        "summary_last_sent": "",
    }


daily_stats = build_empty_daily_stats(datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d"))
daily_summary_sent = False

# Известные пользователи (для приветствия только новых)
known_users = set()

# Метаданные сводок (для устойчивости при рестартах)
summary_state = {
    "monthly_last_sent": "",
    "weekly_last_sent_week": "",  # "YYYY-Wnn" — последняя отправленная еженедельная сводка
}


async def recalculate_daily_stats_from_chat(bot) -> dict:
    """
    Пересчитывает дневную статистику из истории чата.
    Бот получает историю из основной группы, фильтрует сообщения за сегодня
    и пересчитывает всю статистику заново.
    
    Использует прямые HTTP запросы к Telegram API для гарантированной работы
    независимо от версии python-telegram-bot.
    """
    global daily_stats
    
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    today_start = datetime.now(MOSCOW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now(MOSCOW_TZ)
    
    logger.info(f"[HISTORY] Начинаем пересчёт дневной статистики за {today}")
    logger.info(f"[HISTORY] Временной диапазон: {today_start} - {now}")
    
    # Инициализируем структуру для пересчёта
    recalculated_stats = {
        "date": today,
        "total_messages": 0,
        "user_messages": {},  # {user_id: {"name": str, "count": int}}
        "photos": [],  # [{"file_id": str, "user_id": int, "likes": int, "message_id": int, "user_name": str}]
        "message_owners": {},
        "message_likes": {},
        "first_photo_user_id": None,
        "first_photo_user_name": None,
    }
    
    # Получаем токен бота для прямых запросов
    bot_token = bot.token
    if not bot_token:
        logger.error(f"[HISTORY] ❌ Не удалось получить токен бота")
        return recalculated_stats
    
    # Используем httpx для прямых запросов к Telegram API
    api_url = f"https://api.telegram.org/bot{bot_token}"
    
    messages = []
    photos_found = 0
    messages_today = 0
    
    try:
        # Запрашиваем историю через Telegram API (метод getChatHistory)
        logger.info(f"[HISTORY] Запрашиваем историю чата {CHAT_ID} через Telegram API...")
        
        async with httpx.AsyncClient() as client:
            # Получаем последние 200 сообщений
            response = await client.post(
                f"{api_url}/getChatHistory",
                json={
                    "chat_id": CHAT_ID,
                    "limit": 200
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                logger.error(f"[HISTORY] ❌ API вернул статус {response.status_code}")
                logger.error(f"[HISTORY] Ответ: {response.text}")
                raise Exception(f"API error: {response.status_code}")
            
            data = response.json()
            
            if not data.get("ok"):
                logger.error(f"[HISTORY] ❌ API вернул ошибку: {data.get('description')}")
                raise Exception(f"API error: {data.get('description')}")
            
            # Преобразуем результат в объекты Message
            from telegram import Message
            
            if data.get("result") and isinstance(data["result"], list):
                for msg_data in data["result"]:
                    msg = Message.de_json(msg_data, bot)
                    if msg:
                        messages.append(msg)
            
            logger.info(f"[HISTORY] ✅ Получено {len(messages)} сообщений через Telegram API")
            
    except Exception as e:
        logger.warning(f"[HISTORY] ⚠️ Telegram API getChatHistory не работает: {e}")
        logger.info(f"[HISTORY] 🔄 Пробуем альтернативный метод через getUpdates...")
        
        # Альтернатива: используем getUpdates (менее надёжно)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{api_url}/getUpdates",
                    json={"limit": 100},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("ok") and data.get("result"):
                        from telegram import Message, Update
                        
                        for update_data in data["result"]:
                            update = Update.de_json(update_data, bot)
                            if update and update.message:
                                messages.append(update.message)
                        
                        logger.info(f"[HISTORY] ⚠️ Получено {len(messages)} сообщений через getUpdates (могут быть не все)")
                else:
                    logger.error(f"[HISTORY] ❌ getUpdates также не работает")
        except Exception as e2:
            logger.error(f"[HISTORY] ❌ Все методы получения истории чата не работают: {e2}")
    
    # Если не удалось получить историю, пробуем жить с тем что есть
    if not messages:
        logger.warning(f"[HISTORY] ⚠️ Не удалось получить историю чата, начинаем с нуля")
        logger.info(f"[HISTORY] Бот будет считать сообщения с текущего момента")
        return recalculated_stats
    
    logger.info(f"[HISTORY] ✅ Обрабатываем {len(messages)} сообщений из истории чата")
    
    # Обрабатываем полученные сообщения
    for msg in messages:
        # Проверяем, что это текстовое сообщение или фото
        if not msg.date:
            continue
        
        # Проверяем, что сообщение за сегодня
        msg_date = msg.date.astimezone(MOSCOW_TZ)
        if msg_date < today_start or msg_date > now:
            continue
        
        messages_today += 1
        
        # Пропускаем команды и служебные сообщения
        if msg.text and msg.text.startswith('/'):
            continue
        
        # Получаем информацию о пользователе
        user_id = None
        user_name = None
        
        if msg.from_user:
            user_id = msg.from_user.id
            # Формируем имя пользователя
            first_name = msg.from_user.first_name or ""
            last_name = msg.from_user.last_name or ""
            user_name = f"{first_name} {last_name}".strip()
        
        if user_id is None:
            continue
        
        # Экранируем спецсимволы Markdown в имени
        safe_name = user_name.replace('(', '\\(').replace(')', '\\)') if user_name else "Unknown"
        
        # Увеличиваем счётчик сообщений
        recalculated_stats["total_messages"] += 1
        
        # Обновляем счётчик для пользователя
        if user_id not in recalculated_stats["user_messages"]:
            recalculated_stats["user_messages"][user_id] = {"name": safe_name, "count": 0}
        recalculated_stats["user_messages"][user_id]["count"] += 1
        
        # Обрабатываем фото
        if msg.photo:
            photos_found += 1
            photo = msg.photo[-1]  # Берем самое большое фото
            
            photo_info = {
                "file_id": photo.file_id,
                "user_id": user_id,
                "message_id": msg.message_id,
                "likes": 0,
                "user_name": safe_name
            }
            
            recalculated_stats["photos"].append(photo_info)
            
            # Запоминаем первого автора фото (для двойных баллов)
            if recalculated_stats["first_photo_user_id"] is None:
                recalculated_stats["first_photo_user_id"] = user_id
                recalculated_stats["first_photo_user_name"] = safe_name
        else:
            # Текстовое сообщение — сохраняем автора для лайков
            recalculated_stats["message_owners"][msg.message_id] = {
                "user_id": user_id,
                "user_name": safe_name,
            }
    
    logger.info(f"[HISTORY] ✅ Пересчёт завершён:")
    logger.info(f"[HISTORY] - Сообщений за сегодня: {messages_today}")
    logger.info(f"[HISTORY] - Обработано сообщений: {recalculated_stats['total_messages']}")
    logger.info(f"[HISTORY] - Фото за сегодня: {photos_found}")
    logger.info(f"[HISTORY] - Пользователей: {len(recalculated_stats['user_messages'])}")
    
    return recalculated_stats


# ============== РЕЙТИНГ УЧАСТНИКОВ ==============
# {user_id: {"name": str, "messages": int, "photos": int, "likes": int, "replies": int}}
user_rating_stats = {}

# {user_id: "Новичок"} - текущий уровень пользователя
user_current_level = {}

# Загрузка рейтинга — вызывается в post_init при старте бота

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

# Множество для отслеживания уже обработанных активностей в текущей сессии (idempotency)
processed_activities = set()
# Постоянный список уже опубликованных в чат активностей (не повторять никогда). Формат: ["user_id:activity_id", ...]
garmin_published_ids = set()
# Порядок добавления (для обрезки старых записей, лимит MAX_GARMIN_PUBLISHED_IDS)
garmin_published_order = []
MAX_GARMIN_PUBLISHED_IDS = 2000

# {user_id: {"name": str, "activities": int, "distance": float, "duration": int, "calories": int}}
user_running_stats = {}

# ============== ПЕРИОДИЧЕСКИЙ ТРЕКИНГ БЕГА ==============
# Ежедневный трекинг бега (сбрасывается в полночь)
daily_running_stats = {}  # {user_id: {"name": str, "activities": int, "distance": float, "duration": int, "calories": int, "date": "YYYY-MM-DD"}}

# Недельный трекинг бега (сбрасывается в воскресенье)
weekly_running_stats = {}  # {user_id: {"name": str, "activities": int, "distance": float, "duration": int, "calories": int, "week_start": "YYYY-MM-DD"}}

# Месячный трекинг бега (сбрасывается 1-го числа)
monthly_running_stats = {}  # {user_id: {"name": str, "activities": int, "distance": float, "duration": int, "calories": int, "month": "YYYY-MM"}}

# ============== ПОДПИСКИ НА ЗАБЕГИ (WATCH) ==============
# {user_id: [{"id": int, "region": str, "kind": str, "distance": str, "created_at": str}]}
watch_subscriptions = {}
# {"user_id:subscription_id:event_hash", ...}
watch_notified_ids = set()

# ============== ДНИ РОЖДЕНИЯ ==============
# {user_id: {"name": str, "birthday": "DD.MM"}}
user_birthdays = {}

# ============== ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ ПАСПОРТА ==============
# {user_id: {"city": str, "pb_5k": str, "pb_10k": str, "pb_21": str, "pb_42": str}} — город и личники (время как введено)
user_passport_data = {}

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
    female_endings = ['а', 'я', 'ия', 'ина', 'ова', 'ева', 'ыа', 'ь', 'ская', 'цкая', 'ская', 'цкая', 'ская', 'цкая']
    female_names = [
        # Русские имена
        'маша', 'мария', 'мари', 'катя', 'екатерина', 'катерина', 'аня', 'анна', 'аннушка', 'оля', 'ольга', 
        'юля', 'юлия', 'даша', 'дарья', 'лена', 'елена', 'таня', 'татьяна', 'света', 'светлана', 
        'ира', 'ирина', 'наташа', 'наталья', 'наталия', 'галя', 'галина', 'оксана', 'эля', 'элла',
        'лиза', 'елизавета', 'лизa', 'карина', 'дарина', 'варвара', 'варя', 'полина', 'поля',
        'софия', 'софья', 'вика', 'виктория', 'настя', 'анастасия', 'кристина', 'кристина',
        'алина', 'алиса', 'марина', 'мари', 'валя', 'валентина', 'люба', 'любовь', 'людмила',
        'наташа', 'наталья', 'наталия', 'саша', 'александра', 'сашенька',
        # Английские имена
        'veronika', 'veronica', 'maria', 'mary', 'anna', 'anne', 'nastya', 'nastia', 'oksana', 
        'diana', 'julia', 'julie', 'sophia', 'sofia', 'victoria', 'vika', 'kristina', 'christina',
        'alina', 'alice', 'marina', 'valentina', 'elena', 'helen', 'elizabeth', 'liz', 'liza',
        'kate', 'katherine', 'catherine', 'katya', 'alexandra', 'sasha', 'sashka'
    ]

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
            prompt = f"""Определи пол пользователя по нику "{username}".

ЖЕНСКИЕ признаки (ответь YES):
- Женские имена: Маша, Катя, Аня, Оля, Юля, Даша, Лена, Таня, Света, Ира, Наташа, Галя, Лиза, Карина, Полина, Вика, Настя, Кристина, Алина, Марина, Валя, Люба, Саша (если это Александра), и т.д.
- Женские окончания: -а, -я, -ия, -ина, -ова, -ева, -ская, -цкая
- Английские женские имена: Maria, Anna, Julia, Diana, Victoria, Sophia, Christina, Alina, Alice, Marina, Elena, Elizabeth, Kate, Alexandra
- Слова: girl, lady, woman, princess, queen, miss, mrs

МУЖСКИЕ признаки (ответь NO):
- Мужские имена: Петя, Коля, Дима, Миша, Вова, Саша (если это Александр), Макс, Иван, Сергей, Андрей, и т.д.
- Мужские окончания: -ов, -ев, -ин, -ский, -цкий (если это фамилия)
- Английские мужские имена: Alex, Max, John, Mike, David, etc.

Если НЕВОЗМОЖНО определить точно → ответь "NO"

Ответь ТОЛЬКО одно слово: YES или NO"""

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
    """Получить случайную фразу на доброе утро (нейтральные + цитаты из фильмов)"""
    return random.choice(GOOD_MORNING_PHRASES + MOVIE_QUOTES)


def get_random_good_morning_flirt():
    """Получить случайную фразу на доброе утро (флирт + цитаты из фильмов)"""
    return random.choice(GOOD_MORNING_FLIRT_PHRASES + MOVIE_QUOTES)


# ============== МУЗЫКА ДНЯ ==============
MUSIC_OF_DAY = [
    {"title": "🎧 Плейлист для бега", "url": "https://music.yandex.ru/landing/tag_run", "hint": "Универсальный беговой подбор"},
    {"title": "⚡ EDM для бега", "url": "https://music.yandex.ru/landing/tag_edm", "hint": "Для темпового бега и интервалов"},
    {"title": "🎵 Rock для тренировки", "url": "https://music.yandex.ru/landing/tag_rock", "hint": "Когда нужен драйв на последних км"},
    {"title": "🚀 Поп для тренировки", "url": "https://music.yandex.ru/landing/tag_pop", "hint": "Лёгкий бег под знакомые хиты"},
    {"title": "🎤 Hip-Hop для бега", "url": "https://music.yandex.ru/landing/tag_hiphop", "hint": "Ритм под шаг — идеальный каденс"},
    {"title": "🌿 Indie и альтернатива", "url": "https://music.yandex.ru/landing/tag_indie", "hint": "Для длинных расслабленных пробежек"},
    {"title": "🔊 Электроника и хаус", "url": "https://music.yandex.ru/landing/tag_electronic", "hint": "Монотонный ритм — держит темп"},
    {"title": "🎹 Джаз и фанк", "url": "https://music.yandex.ru/landing/tag_jazz", "hint": "Утренняя пробежка в хорошем настроении"},
    {"title": "🪕 Акустика и фолк", "url": "https://music.yandex.ru/landing/tag_acoustic", "hint": "Восстановительный бег без гонки"},
    {"title": "💪 Workout и мотивация", "url": "https://music.yandex.ru/landing/tag_workout", "hint": "Когда нужен пинок под пятую точку"},
    {"title": "🌅 Лёгкий поп и чилл", "url": "https://music.yandex.ru/landing/tag_chill", "hint": "Разминка и заминка"},
    {"title": "🔥 Танцевальная музыка", "url": "https://music.yandex.ru/landing/tag_dance", "hint": "Высокий BPM — для скоростных отрезков"},
    {"title": "🎸 Панк и альтернативный рок", "url": "https://music.yandex.ru/landing/tag_punk", "hint": "Короткий жёсткий бег"},
    {"title": "🌙 Лоу-фай и атмосфера", "url": "https://music.yandex.ru/landing/tag_lo-fi", "hint": "Вечерняя пробежка в своём ритме"},
    {"title": "🏃 Подкасты и аудио", "url": "https://music.yandex.ru/genre/podkasty", "hint": "Длинный бег — совмещай с обучением"},
]


def get_music_of_day() -> dict:
    """Возвращает музыку дня (фиксирована на день)."""
    global last_music_index, last_music_date
    if not MUSIC_OF_DAY:
        return {"title": "🎧 Музыка не найдена", "url": ""}
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    if last_music_date == today and last_music_index is not None:
        return MUSIC_OF_DAY[last_music_index]
    if len(MUSIC_OF_DAY) == 1:
        last_music_index = 0
        last_music_date = today
        return MUSIC_OF_DAY[0]
    idx = random.randrange(len(MUSIC_OF_DAY))
    if last_music_index is not None and idx == last_music_index:
        idx = (idx + 1) % len(MUSIC_OF_DAY)
    last_music_index = idx
    last_music_date = today
    return MUSIC_OF_DAY[idx]


def format_music_message(music: dict) -> str:
    title = html_escape(music.get("title", "🎧 Музыка дня"))
    url = music.get("url", "")
    hint = music.get("hint", "")
    parts = [title]
    if hint:
        parts.append(f"💡 {html_escape(hint)}")
    if url:
        safe_url = html_escape(url)
        parts.append(f"🔗 <a href=\"{safe_url}\">Открыть в Яндекс Музыке</a>")
    return "\n".join(parts)


# ============== СКИДКИ НА ЭКИПИРОВКУ ==============
DEALS_SOURCES = [
    {
        "name": "Sport-Marafon",
        "url": "https://sport-marafon.ru/catalog/odezhda-dlya-bega/",
        "url_male": "https://sport-marafon.ru/catalog/muzhskaya-odezhda-dlya-bega/",
        "url_female": "https://sport-marafon.ru/catalog/zhenskaya-odezhda-dlya-bega/",
    },
    {
        "name": "Nordski",
        "url": "https://nordski.ru/",
        "url_male": "https://nordski.ru/po-polu/muzhchiny/",
        "url_female": "https://nordski.ru/po-polu/zhenshchiny/",
    },
    {
        "name": "SHU",
        "url": "https://shuclothes.com/",
        "url_male": "https://shuclothes.com/man",
        "url_female": "https://shuclothes.com/woman",
    },
    {
        "name": "GRI",
        "url": "https://www.grigri.ru/",
        "url_male": "https://www.grigri.ru/collection/men",
        "url_female": "https://www.grigri.ru/collection/women",
    },
    {
        "name": "Insanity",
        "url": "https://insanity.ru/",
        "url_male": "https://insanity.ru/male_capsule_2",
        "url_female": "https://insanity.ru/female_capsule_1",
    },
]

DEALS_KEYWORDS = [
    "бег", "run", "running", "кроссов", "sneaker", "shoe",
    "шорт", "майк", "футбол", "лонг", "тайтс", "велосипед",
    "куртк", "ветровк", "штаны", "брюк", "leggin", "tight",
    "очки", "cap", "кепк", "nosk", "носк", "перчат", "баф",
]

CATEGORY_KEYWORDS = {
    "shoes": ["кроссов", "sneaker", "shoe", "обув"],
    "shorts": ["шорт", "short"],
    "socks": ["нос", "sock"],
    "longsleeve": ["лонг", "лонгслив", "longsleeve", "long sleeve"],
    "tights": ["тайтс", "леггин", "легин", "tight", "legging", "велосипед"],
    "jackets": ["куртк", "ветровк", "jacket", "wind"],
    "pants": ["штаны", "брюк", "pants", "trous"],
    "shirts": ["футбол", "майк", "t-shirt", "tee", "shirt", "top"],
    "accessories": ["очки", "cap", "кепк", "перчат", "баф", "повязк", "шапк", "рюкзак"],
}

CATEGORY_TITLES = {
    "all": "все",
    "shoes": "кроссовки",
    "shorts": "шорты",
    "socks": "носки",
    "longsleeve": "лонги",
    "tights": "тайтсы/велосипедки",
    "jackets": "куртки/ветровки",
    "pants": "штаны/брюки",
    "shirts": "футболки/майки",
    "accessories": "аксессуары",
}


def _extract_price(text: str) -> str:
    match = re.search(r"(\d[\d\s]{2,8})\s*(₽|руб)", text, re.IGNORECASE)
    if not match:
        return ""
    price = match.group(1).replace(" ", "")
    return f"{price} ₽"


def _is_relevant_name(name: str, category: str | None = None) -> bool:
    lower = name.lower()
    if category and category != "all":
        keywords = CATEGORY_KEYWORDS.get(category, [])
        return any(keyword in lower for keyword in keywords)
    return any(keyword in lower for keyword in DEALS_KEYWORDS)


def _matches_gender(name: str, url: str, gender: str | None) -> bool:
    if not gender or gender == "all":
        return True
    text = f"{name} {url}".lower()
    # кириллица + транслит из URL (sport-marafon: zhenskaya, muzhskaya, zhenshchiny, muzhchiny)
    male_keywords = ["муж", "men", "male", "mens", "man", "boys", "boy", "m/", "мужск", "muzh", "muzhchin", "muzhski", "muzhskie"]
    female_keywords = ["жен", "women", "female", "womens", "lady", "ladies", "w/", "женск", "девуш", "zhen", "zhenshchin", "zhenski", "zhenskie"]
    unisex_keywords = ["унисекс", "unisex", "универс"]

    # Сначала исключаем чужой пол
    if gender == "male":
        if any(k in text for k in female_keywords):
            return False
    if gender == "female":
        if any(k in text for k in male_keywords):
            return False

    # Подходим по полу: явно мужское/женское или унисекс
    if gender == "male":
        return any(k in text for k in male_keywords) or any(k in text for k in unisex_keywords)
    if gender == "female":
        return any(k in text for k in female_keywords) or any(k in text for k in unisex_keywords)
    return True


def extract_products_from_html(
    html: str,
    base_url: str,
    gender: str | None = None,
    category: str | None = None,
) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        name = " ".join(a.get_text(" ", strip=True).split())
        if not name or len(name) < 6 or len(name) > 120:
            continue
        if not _is_relevant_name(name, category):
            continue

        parent_text = " ".join(a.parent.get_text(" ", strip=True).split()) if a.parent else ""
        price = _extract_price(parent_text)

        href = a["href"]
        if href.startswith("/"):
            link = base_url.rstrip("/") + href
        elif href.startswith("http"):
            link = href
        else:
            link = base_url.rstrip("/") + "/" + href

        if _matches_gender(name, link, gender):
            items.append({"name": name, "price": price, "url": link})

    # Удаляем дубликаты по названию
    seen = set()
    unique = []
    for item in items:
        key = item["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def _deals_source_url(source: dict, gender: str | None) -> str:
    """Ссылка на магазин с учётом пола (раздел муж/жен), если есть."""
    if gender == "male" and source.get("url_male"):
        return source["url_male"]
    if gender == "female" and source.get("url_female"):
        return source["url_female"]
    return source["url"]


async def fetch_deals_for_source(source: dict, gender: str | None = None, category: str | None = None) -> list[dict]:
    try:
        url = _deals_source_url(source, gender)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return extract_products_from_html(response.text, url, gender, category)
    except Exception as e:
        logger.error(f"[DEALS] Ошибка загрузки {source['name']}: {e}")
        return []


async def build_deals_message(gender: str | None = None, category: str | None = None) -> str:
    gender_title = "все"
    if gender == "male":
        gender_title = "мужское"
    elif gender == "female":
        gender_title = "женское"
    category_title = CATEGORY_TITLES.get(category or "all", "все")
    lines = ["🔥 <b>Еженедельные скидки на беговую экипировку</b>\n"]
    lines.append(f"Пол: <b>{gender_title}</b>")
    lines.append(f"Категория: <b>{category_title}</b>\n")
    for source in DEALS_SOURCES:
        products = await fetch_deals_for_source(source, gender, category)
        lines.append(f"🏬 <b>{html_escape(source['name'])}</b>")
        if not products:
            shop_url = _deals_source_url(source, gender)
            label = f"Открыть магазин — {gender_title}" if gender else "Открыть магазин"
            lines.append(f"🔗 <a href=\"{html_escape(shop_url)}\">{html_escape(label)}</a>")
            lines.append("")
            continue
        for product in products[:5]:
            name = html_escape(product["name"])
            price = html_escape(product["price"]) if product["price"] else ""
            url = html_escape(product["url"])
            if price:
                lines.append(f"• {name} — {price}")
            else:
                lines.append(f"• {name}")
            lines.append(f"  <a href=\"{url}\">Ссылка</a>")
        lines.append("")

    return "\n".join(lines).strip()


# ============== СПОКОЙНОЙ НОЧИ (ПО ХАРАКТЕРУ ДНЯ) ==============
GOOD_NIGHT_BY_DAY = {
    0: [  # Понедельник - Тони Старк
        "🤖 Спокойной ночи. Завтра продолжим спасать мир (и твой режим сна).",
        "⚡ Выключайся. Даже у Железного Человека есть ночь на перезарядку.",
        "💡 Сон — лучший апгрейд. Спокойной ночи!",
    ],
    1: [  # Вторник - Доктор Хаус
        "🏥 Диагноз: усталость. Лечение: сон. Спокойной ночи.",
        "💊 Спать — единственное, что не лечится сарказмом. Иди спать.",
        "😏 Все лгут. Но подушка — никогда. Спокойной ночи.",
    ],
    2: [  # Среда - Джек Воробей
        "🏴‍☠️ Паруса вниз, команда на отдых! Спокойной ночи.",
        "🍷 Ром не надо, сон — обязателен. Доброй ночи!",
        "⚓️ Штиль, капитан. Спокойной ночи.",
    ],
    3: [  # Четверг - Голлум
        "💍 Спать, спать… наш прелессный отдых ждёт нас. Спокойной ночи.",
        "😴 Мы устали. Мы будем спать. Да-да, будем.",
        "👁️ Сон — наш друг. Тишина. Спокойной ночи.",
    ],
    4: [  # Пятница - Доктор Стрэндж
        "🌀 Время спать. В другом измерении это уже утро. Доброй ночи.",
        "⏰ Пауза во времени: сон. Спокойной ночи.",
        "✨ Закрой глаза — и мир станет мягче. Спокойной ночи.",
    ],
    5: [  # Суббота - Джокер
        "🃏 Ночь — лучшее время для паузы… ха-ха. Спокойной ночи!",
        "🎭 Закрывай театр, актёр. Спокойной ночи.",
        "😂 Сон — тоже шутка, но полезная. Спокойной ночи!",
    ],
    6: [  # Воскресенье — день нюдсов
        "🔥 День нюдсов прошёл на ура. Отдыхай, красавчики. Спокойной ночи!",
        "😏 Засыпай, завтра снова в бой. Спокойной ночи!",
        "💪 Вы все сегодня огонь. Доброй ночи!",
    ],
}


def get_good_night_message() -> str:
    now = datetime.now(MOSCOW_TZ)
    day = now.weekday()
    variants = GOOD_NIGHT_BY_DAY.get(day, ["Спокойной ночи!"])
    return random.choice(variants)

# ============== DATA FILES ==============
# Используем постоянное хранилище, если доступно (Bothost: /app/data, Volume: /data)
DATA_DIR = os.getenv("DATA_DIR", "")
if not DATA_DIR:
    for candidate in ("/app/data", "/data"):
        if os.path.isdir(candidate):
            DATA_DIR = candidate
            break
if not DATA_DIR:
    DATA_DIR = "/tmp"
os.makedirs(DATA_DIR, exist_ok=True)

BIRTHDAYS_FILE = os.path.join(DATA_DIR, "birthdays.json")
PASSPORT_DATA_FILE = os.path.join(DATA_DIR, "passport_data.json")
GARMIN_DATA_FILE = os.path.join(DATA_DIR, "garmin_users.json")
GARMIN_PUBLISHED_FILE = os.path.join(DATA_DIR, "garmin_published_ids.json")
GARMIN_KEY_FILE = os.path.join(DATA_DIR, "garmin_key.key")
USER_RATING_FILE = os.path.join(DATA_DIR, "user_rating_stats.json")
DAILY_STATS_FILE = os.path.join(DATA_DIR, "daily_stats.json")
DB_PATH = os.path.join(DATA_DIR, "bot.db")
KNOWN_USERS_FILE = os.path.join(DATA_DIR, "known_users.json")
BOT_STICKERS_FILE = os.path.join(DATA_DIR, "bot_stickers.json")
BELT_PHOTO_CACHE_FILE = os.path.join(DATA_DIR, "belt_photo.json")


def load_belt_photo_cache() -> None:
    """Загружает file_id фото ремня из env или DATA_DIR."""
    global _belt_photo_file_id
    env_id = os.environ.get("BELT_PHOTO_FILE_ID", "").strip()
    if env_id:
        _belt_photo_file_id = env_id
        return
    try:
        if os.path.isfile(BELT_PHOTO_CACHE_FILE):
            with open(BELT_PHOTO_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _belt_photo_file_id = str(data.get("file_id", "") or "").strip()
    except Exception as e:
        logger.warning(f"[BELT] Не удалось загрузить кэш: {e}")


def save_belt_photo_cache(file_id: str) -> None:
    """Сохраняет file_id фото ремня в DATA_DIR."""
    global _belt_photo_file_id
    _belt_photo_file_id = (file_id or "").strip()
    try:
        with open(BELT_PHOTO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"file_id": _belt_photo_file_id}, f, ensure_ascii=False)
        db_save_json("belt_photo", {"file_id": _belt_photo_file_id})
    except Exception as e:
        logger.warning(f"[BELT] Не удалось сохранить кэш: {e}")


def init_belt_photo_on_startup() -> None:
    """Копирует jpg в DATA_DIR и подгружает кэш file_id."""
    load_belt_photo_cache()
    dest = os.path.join(DATA_DIR, BELT_PHOTO_FILENAME)
    for src in (_resolve_belt_photo_path(), BELT_PHOTO_PATH, BELT_PHOTO_FILENAME):
        if src and os.path.isfile(src) and os.path.abspath(src) != os.path.abspath(dest):
            try:
                import shutil
                shutil.copy2(src, dest)
                logger.info(f"[BELT] Скопировано в {dest}")
                break
            except Exception as e:
                logger.warning(f"[BELT] Копирование в DATA_DIR: {e}")
    logger.info(
        f"[BELT] startup: file_id={'да' if _belt_photo_file_id else 'нет'}, "
        f"файл={'да' if os.path.isfile(dest) else 'нет'}"
    )


async def warmup_belt_photo_file_id(bot) -> None:
    """Один раз заливает фото в служебный канал и кэширует file_id (без спама в чат)."""
    if _belt_photo_file_id or not DATA_CHANNEL_ID:
        return
    photo_path = _resolve_belt_photo_path()
    if not os.path.isfile(photo_path):
        return
    try:
        with open(photo_path, "rb") as photo:
            sent = await bot.send_photo(chat_id=DATA_CHANNEL_ID, photo=photo, caption="belt cache")
        if sent and sent.photo:
            save_belt_photo_cache(sent.photo[-1].file_id)
            logger.info("[BELT] file_id получен через DATA_CHANNEL_ID")
    except Exception as e:
        logger.warning(f"[BELT] warmup через канал не удался: {e}")

# Старые пути (для миграции при первом запуске)
LEGACY_BIRTHDAYS_FILE = "birthdays.json"
LEGACY_PASSPORT_DATA_FILE = "passport_data.json"
LEGACY_GARMIN_DATA_FILE = "garmin_users.json"
LEGACY_GARMIN_KEY_FILE = "garmin_key.key"
LEGACY_USER_RATING_FILE = "user_rating_stats.json"
SUMMARY_STATE_FILE = os.path.join(DATA_DIR, "summary_state.json")
LEGACY_SUMMARY_STATE_FILE = "summary_state.json"
LEGACY_DAILY_STATS_FILE = "daily_stats.json"
LEGACY_KNOWN_USERS_FILE = "known_users.json"


def migrate_legacy_file(new_path: str, legacy_path: str, label: str) -> None:
    """Переносит файл со старого пути в DATA_DIR, если нужно."""
    try:
        if not os.path.exists(new_path) and os.path.exists(legacy_path):
            with open(legacy_path, "rb") as src:
                data = src.read()
            with open(new_path, "wb") as dst:
                dst.write(data)
            logger.info(f"[PERSIST] Миграция {label}: {legacy_path} -> {new_path}")
    except Exception as e:
        logger.warning(f"[PERSIST] Не удалось мигрировать {label}: {e}")


def ensure_sqlite_db() -> None:
    """Создаёт SQLite БД и таблицу, если их нет."""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[PERSIST] SQLite недоступна: {e}")


def db_save_json(key: str, data: dict) -> None:
    """Сохраняет JSON в SQLite как строку."""
    try:
        ensure_sqlite_db()
        payload = json.dumps(data, ensure_ascii=False)
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, payload, datetime.now(MOSCOW_TZ).isoformat()),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[PERSIST] SQLite save error ({key}): {e}")


def db_load_json(key: str) -> dict | None:
    """Загружает JSON из SQLite."""
    try:
        if not os.path.exists(DB_PATH):
            return None
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            if not row:
                return None
            return json.loads(row[0])
    except Exception as e:
        logger.warning(f"[PERSIST] SQLite load error ({key}): {e}")
        return None

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
POINTS_PER_LIKES = 1      # За сколько лайков даётся 1 балл
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
        migrate_legacy_file(GARMIN_KEY_FILE, LEGACY_GARMIN_KEY_FILE, "garmin key")
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

        # Сохраняем в SQLite
        db_save_json("garmin_users", save_data)
        
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


def load_garmin_published_ids():
    """Загрузка множества уже опубликованных активностей (user_id:activity_id) из файла/БД."""
    global garmin_published_ids, garmin_published_order
    try:
        db_data = db_load_json("garmin_published_ids")
        if db_data and isinstance(db_data, list):
            lst = [str(x) for x in db_data]
        else:
            if os.path.exists(GARMIN_PUBLISHED_FILE):
                with open(GARMIN_PUBLISHED_FILE, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                lst = [str(x) for x in (raw if isinstance(raw, list) else [])]
            else:
                lst = []
        garmin_published_ids = set(lst)
        garmin_published_order = lst
        logger.info(f"[GARMIN] Загружено опубликованных ID: {len(garmin_published_ids)}")
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка загрузки опубликованных ID: {e}")
        garmin_published_ids = set()
        garmin_published_order = []


def save_garmin_published_ids():
    """Сохранение списка опубликованных активностей в файл и БД. Лимит MAX_GARMIN_PUBLISHED_IDS."""
    global garmin_published_ids, garmin_published_order
    try:
        # Обрезаем старые записи, сохраняем порядок
        while len(garmin_published_order) > MAX_GARMIN_PUBLISHED_IDS:
            old = garmin_published_order.pop(0)
            garmin_published_ids.discard(old)
        lst = list(garmin_published_order)
        with open(GARMIN_PUBLISHED_FILE, 'w', encoding='utf-8') as f:
            json.dump(lst, f, ensure_ascii=False)
        db_save_json("garmin_published_ids", lst)
    except Exception as e:
        logger.error(f"[GARMIN] Ошибка сохранения опубликованных ID: {e}")


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


async def save_user_running_stats():
    """Сохранение статистики пробежек в файл и канал (асинхронно)"""
    global user_running_stats
    
    try:
        # Конвертируем для JSON (ключи должны быть строками)
        save_data = {}
        for user_id, data in user_running_stats.items():
            save_data[str(user_id)] = data
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                await save_to_channel(application.bot, "runs", save_data)
            except Exception as e:
                logger.error(f"[PERSIST] Ошибка сохранения runs: {e}")
        
        logger.info(f"[PERSIST] Статистика пробежек сохранена: {len(user_running_stats)}")
    except Exception as e:
        logger.error(f"[PERSIST] Критическая ошибка сохранения runs: {e}")


async def save_daily_stats():
    """Сохранение ежедневной статистики в канал (асинхронно)"""
    global daily_stats
    
    try:
        # Логируем что сохраняем
        msg_count = daily_stats.get("total_messages", 0)
        photo_count = len(daily_stats.get("photos", []))
        user_count = len(daily_stats.get("user_messages", {}))
        logger.info(f"[PERSIST] Сохранение daily_stats: {msg_count} сообщений, {photo_count} фото, {user_count} пользователей")

        # Сохраняем локально и в SQLite
        save_daily_stats_local()
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                await save_to_channel(application.bot, "daily", daily_stats)
                logger.info(f"[PERSIST] Ежедневная статистика сохранена в канал")
            except Exception as e:
                logger.error(f"[PERSIST] Ошибка сохранения daily: {e}")
        else:
            logger.warning(f"[PERSIST] DATA_CHANNEL_ID не настроен, статистика не сохраняется в канал")
        
    except Exception as e:
        logger.error(f"[PERSIST] Критическая ошибка сохранения daily: {e}")


def save_daily_stats_local() -> None:
    """Сохраняет daily_stats локально и в SQLite."""
    try:
        with open(DAILY_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(daily_stats, f, ensure_ascii=False, indent=2)
        db_save_json("daily_stats", daily_stats)
    except Exception as e:
        logger.warning(f"[PERSIST] Ошибка локального сохранения daily_stats: {e}")


def load_daily_stats() -> None:
    """Загружает daily_stats из SQLite/файла."""
    global daily_stats
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    try:
        data = db_load_json("daily_stats")
        if not data:
            migrate_legacy_file(DAILY_STATS_FILE, LEGACY_DAILY_STATS_FILE, "daily_stats")
            if os.path.exists(DAILY_STATS_FILE):
                with open(DAILY_STATS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
        if not data:
            return

        # Нормализуем и проверяем дату
        loaded_date = data.get("date") if isinstance(data, dict) else None
        if loaded_date != today:
            logger.warning(
                f"[PERSIST] daily_stats date {loaded_date} != {today}, сбрасываем дневную статистику"
            )
            daily_stats = build_empty_daily_stats(today)
            return

        # Приводим ключи и типы
        user_messages = {}
        for k, v in (data.get("user_messages") or {}).items():
            try:
                user_messages[int(k)] = v
            except Exception:
                continue

        message_owners = {}
        for k, v in (data.get("message_owners") or {}).items():
            try:
                message_owners[int(k)] = v
            except Exception:
                continue

        message_likes = {}
        for k, v in (data.get("message_likes") or {}).items():
            try:
                message_likes[int(k)] = int(v or 0)
            except Exception:
                continue

        photos = data.get("photos") or []
        for p in photos:
            if "likes" in p:
                try:
                    p["likes"] = int(p.get("likes", 0) or 0)
                except Exception:
                    p["likes"] = 0

        daily_stats = {
            "date": loaded_date,
            "total_messages": int(data.get("total_messages", 0) or 0),
            "user_messages": user_messages,
            "photos": photos,
            "message_owners": message_owners,
            "message_likes": message_likes,
            "first_photo_user_id": data.get("first_photo_user_id"),
            "first_photo_user_name": data.get("first_photo_user_name"),
            "summary_last_sent": str(data.get("summary_last_sent", "") or ""),
        }
        logger.info(f"[PERSIST] ✅ Загружено daily_stats: {daily_stats.get('total_messages', 0)} сообщений, summary_last_sent={daily_stats.get('summary_last_sent', '')}")
    except Exception as e:
        logger.warning(f"[PERSIST] Не удалось загрузить daily_stats: {e}")


def load_known_users() -> None:
    """Загружает известных пользователей."""
    global known_users
    try:
        data = db_load_json("known_users")
        if not data:
            migrate_legacy_file(KNOWN_USERS_FILE, LEGACY_KNOWN_USERS_FILE, "known_users")
            if os.path.exists(KNOWN_USERS_FILE):
                with open(KNOWN_USERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
        if isinstance(data, list):
            known_users = set(int(x) for x in data if str(x).isdigit())
        elif isinstance(data, dict) and "users" in data:
            known_users = set(int(x) for x in data["users"] if str(x).isdigit())
        else:
            known_users = set()
    except Exception as e:
        logger.warning(f"[PERSIST] Не удалось загрузить known_users: {e}")
        known_users = set()


def save_known_users() -> None:
    """Сохраняет известных пользователей."""
    try:
        data = sorted(list(known_users))
        with open(KNOWN_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        db_save_json("known_users", data)
    except Exception as e:
        logger.warning(f"[PERSIST] Не удалось сохранить known_users: {e}")


def load_watch_subscriptions() -> None:
    """Загружает подписки на забеги и историю уведомлений."""
    global watch_subscriptions, watch_notified_ids
    try:
        raw_subs = db_load_json("watch_subscriptions") or {}
        loaded_subs = {}
        if isinstance(raw_subs, dict):
            for user_id_str, subs in raw_subs.items():
                if not str(user_id_str).isdigit() or not isinstance(subs, list):
                    continue
                user_id = int(user_id_str)
                normalized = []
                for item in subs:
                    if not isinstance(item, dict):
                        continue
                    normalized.append({
                        "id": int(item.get("id", 0)),
                        "region": str(item.get("region", "any")),
                        "kind": str(item.get("kind", "any")),
                        "distance": str(item.get("distance", "any")),
                        "created_at": str(item.get("created_at", "")),
                    })
                if normalized:
                    loaded_subs[user_id] = normalized
        watch_subscriptions = loaded_subs

        raw_notified = db_load_json("watch_notified_ids") or []
        if isinstance(raw_notified, list):
            watch_notified_ids = set(str(x) for x in raw_notified)
        else:
            watch_notified_ids = set()

        logger.info(
            f"[WATCH] Загружено подписок: {sum(len(v) for v in watch_subscriptions.values())}, "
            f"пользователей: {len(watch_subscriptions)}"
        )
    except Exception as e:
        logger.warning(f"[WATCH] Не удалось загрузить подписки: {e}")
        watch_subscriptions = {}
        watch_notified_ids = set()


def save_watch_subscriptions() -> None:
    """Сохраняет подписки на забеги и историю уведомлений."""
    try:
        payload = {str(user_id): subs for user_id, subs in watch_subscriptions.items()}
        db_save_json("watch_subscriptions", payload)
        # Ограничиваем историю, чтобы не разрасталась бесконечно.
        max_items = 10000
        notified_list = list(watch_notified_ids)
        if len(notified_list) > max_items:
            notified_list = notified_list[-max_items:]
        db_save_json("watch_notified_ids", notified_list)
    except Exception as e:
        logger.warning(f"[WATCH] Не удалось сохранить подписки: {e}")


def load_morning_streaks() -> None:
    """Загружает серии утренних отметок."""
    global morning_streaks
    try:
        raw = db_load_json("morning_streaks") or {}
        loaded = {}
        if isinstance(raw, dict):
            for user_id_str, data in raw.items():
                if not str(user_id_str).isdigit() or not isinstance(data, dict):
                    continue
                loaded[int(user_id_str)] = {
                    "streak": int(data.get("streak", 0)),
                    "last_date": str(data.get("last_date", "")),
                }
        morning_streaks = loaded
    except Exception as e:
        logger.warning(f"[MORNING] Не удалось загрузить streak: {e}")
        morning_streaks = {}


def save_morning_streaks() -> None:
    """Сохраняет серии утренних отметок."""
    try:
        payload = {str(user_id): data for user_id, data in morning_streaks.items()}
        db_save_json("morning_streaks", payload)
    except Exception as e:
        logger.warning(f"[MORNING] Не удалось сохранить streak: {e}")


async def save_user_rating_stats():
    """Сохранение рейтинга пользователей в канал + локальный файл (асинхронно)"""
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
        
        # СОХРАНЯЕМ В ЛОКАЛЬНЫЙ ФАЙЛ (всегда!)
        try:
            with open(USER_RATING_FILE, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            logger.info(f"[PERSIST] Рейтинг сохранён локально: {len(user_rating_stats)}")
            db_save_json("user_rating_stats", save_data)
        except Exception as e:
            logger.error(f"[PERSIST] Ошибка локального сохранения: {e}")
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                await save_to_channel(application.bot, "ratings", save_data)
            except Exception as e:
                logger.error(f"[PERSIST] Ошибка сохранения ratings: {e}")
        
        logger.info(f"[PERSIST] Рейтинг пользователей сохранён: {len(user_rating_stats)}")
    except Exception as e:
        logger.error(f"[PERSIST] Критическая ошибка сохранения ratings: {e}")


def load_user_rating_stats():
    """Загрузка рейтинга пользователей - сначала локальный файл, потом канал"""
    global user_rating_stats, user_current_level
    
    # Сначала пробуем загрузить из локального файла
    try:
        db_data = db_load_json("user_rating_stats")
        if db_data:
            loaded_data = db_data
            user_rating_stats = {}
            user_current_level = {}
            for user_id_str, data in loaded_data.items():
                user_id = int(user_id_str)
                user_rating_stats[user_id] = data
                if "days_active" in data and isinstance(data["days_active"], list):
                    user_rating_stats[user_id]["days_active"] = set(data["days_active"])
                if "_current_level" in data:
                    user_current_level[user_id] = data["_current_level"]
            logger.info(f"[PERSIST] ✅ Загружено из SQLite: {len(user_rating_stats)} пользователей")
            return True

        migrate_legacy_file(USER_RATING_FILE, LEGACY_USER_RATING_FILE, "rating")
        if os.path.exists(USER_RATING_FILE):
            with open(USER_RATING_FILE, "r", encoding="utf-8") as f:
                loaded_data = json.load(f)
            
            # Конвертируем обратно
            user_rating_stats = {}
            user_current_level = {}
            
            for user_id_str, data in loaded_data.items():
                user_id = int(user_id_str)
                user_rating_stats[user_id] = data
                
                # Восстанавливаем set из list
                if "days_active" in data and isinstance(data["days_active"], list):
                    user_rating_stats[user_id]["days_active"] = set(data["days_active"])
                
                # Восстанавливаем уровни
                if "_current_level" in data:
                    user_current_level[user_id] = data["_current_level"]
            
            logger.info(f"[PERSIST] ✅ Загружено из локального файла: {len(user_rating_stats)} пользователей")
            return True
            
    except Exception as e:
        logger.error(f"[PERSIST] Ошибка загрузки из локального файла: {e}")
    
    return False


def save_summary_state() -> None:
    """Сохраняет метаданные сводок локально и в SQLite."""
    global summary_state
    try:
        with open(SUMMARY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(summary_state, f, ensure_ascii=False, indent=2)
        db_save_json("summary_state", summary_state)
    except Exception as e:
        logger.warning(f"[PERSIST] Ошибка сохранения summary_state: {e}")


def load_summary_state() -> None:
    """Загружает метаданные сводок из SQLite/файла."""
    global summary_state
    try:
        data = db_load_json("summary_state")
        if not data:
            migrate_legacy_file(SUMMARY_STATE_FILE, LEGACY_SUMMARY_STATE_FILE, "summary_state")
            if os.path.exists(SUMMARY_STATE_FILE):
                with open(SUMMARY_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
        if not data:
            return
        if isinstance(data, dict):
            summary_state.update(data)
    except Exception as e:
        logger.warning(f"[PERSIST] Ошибка загрузки summary_state: {e}")


async def save_user_active_stats():
    """Сохранение активности участников (когда последний раз писали) - асинхронно"""
    global user_last_active
    
    try:
        # Конвертируем для JSON (ключи должны быть строками)
        save_data = {}
        for user_id, last_date in user_last_active.items():
            save_data[str(user_id)] = last_date
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                await save_to_channel(application.bot, "active", save_data)
            except Exception as e:
                logger.error(f"[PERSIST] Ошибка сохранения active: {e}")
        
        logger.info(f"[PERSIST] Активность участников сохранена: {len(user_last_active)}")
    except Exception as e:
        logger.error(f"[PERSIST] Критическая ошибка сохранения active: {e}")


async def save_chat_history():
    """Сохранение истории чата (скрытое хранение всех сообщений) - асинхронно"""
    global chat_history
    
    try:
        # Обновляем время перед сохранением
        from datetime import datetime, timedelta
        moscow_now = datetime.utcnow() + timedelta(hours=3)
        chat_history["last_updated"] = moscow_now.isoformat()
        
        # Сохраняем в канал асинхронно
        if DATA_CHANNEL_ID and application and hasattr(application, 'bot') and application.bot:
            try:
                await save_to_channel(application.bot, "history", chat_history)
            except Exception as e:
                logger.error(f"[PERSIST] Ошибка сохранения history: {e}")
        
        msg_count = len(chat_history.get("messages", []))
        photo_count = len(chat_history.get("photos", []))
        logger.info(f"[HISTORY] История сохранена: {msg_count} сообщений, {photo_count} фото")
    except Exception as e:
        logger.error(f"[PERSIST] Критическая ошибка сохранения истории: {e}")


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
                except Exception:
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
                except Exception:
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
        
        # Возвращаем структуру по умолчанию (все ключи для daily_stats, чтобы не было KeyError)
        defaults = {
            "messages": [],
            "runs": [],
            "users": {},
            "photos": [],
            "likes": [],
            "daily_stats": {
                "date": "",
                "total_messages": 0,
                "user_messages": {},
                "photos": [],
                "first_photo_user_id": None,
                "first_photo_user_name": None,
            }
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
        db_data = db_load_json("garmin_users")
        if db_data:
            load_data = db_data
        else:
            migrate_legacy_file(GARMIN_DATA_FILE, LEGACY_GARMIN_DATA_FILE, "garmin data")
            if not os.path.exists(GARMIN_DATA_FILE):
                logger.info("[GARMIN] Файл данных не найден, создаём пустой")
                garmin_users = {}
                load_garmin_published_ids()
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
        load_garmin_published_ids()
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

# Включить ли поздравления с праздниками (можно отключить: False или переменная окружения HOLIDAY_CONGRATS_ENABLED=0)
HOLIDAY_CONGRATS_ENABLED = os.environ.get("HOLIDAY_CONGRATS_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")

# Праздники: (месяц, день) -> (название, список поздравлений)
HOLIDAYS = {
    (1, 1): ("Новый год", [
        "🎄 С Новым годом, бегуны! Пусть каждый километр в новом году будет в радость!",
        "🎉 С Новым годом! Новые цели, новые рекорды — вперёд!",
        "✨ С Новым годом! Загадывайте желания на финише первого забега года!",
    ]),
    (1, 7): ("Рождество", [
        "⭐ С Рождеством! Мира, добра и лёгких километров!",
    ]),
    (2, 14): ("День святого Валентина", [
        "❤️ С Днём святого Валентина! Бегайте вместе — и сердце, и ноги будут в тонусе!",
        "💕 С Днём влюблённых! Пусть ваша вторая половинка поддерживает вас на каждой пробежке!",
        "💝 День святого Валентина! Самая долгая дистанция — до сердца друг друга. Поздравляем!",
    ]),
    (2, 23): ("День защитника Отечества", [
        "🎖️ С 23 февраля! Сила духа и выносливость — ваши главные награды. Поздравляем защитников!",
        "💪 С Днём защитника Отечества! Настоящие мужчины не только стоят на страже, но и бегают по утрам!",
        "🪖 С 23 февраля! Крепкого здоровья и новых побед на трассах!",
    ]),
    (3, 8): ("Международный женский день", [
        "🌸 С 8 марта, наши прекрасные бегуньи! Вы вдохновляете весь чат!",
        "💐 С 8 марта! Красота, сила и грация — всё про вас. Поздравляем!",
        "✨ С Международным женским днём! Пусть каждая пробежка приносит радость!",
    ]),
    (5, 1): ("Праздник весны и труда", [
        "🌷 С 1 мая! Отдыхайте или бежите — главное с настроением!",
        "☀️ С Праздником весны и труда! Отличный день для пробежки в парке!",
    ]),
    (5, 9): ("День Победы", [
        "🎖️ С Днём Победы! Вечная память героям. Мира и здоровья всем!",
        "🇷🇺 С 9 мая! Бежим в честь тех, кто подарил нам мирное небо!",
    ]),
    (6, 12): ("День России", [
        "🇷🇺 С Днём России! Гордимся страной и нашими бегунами!",
    ]),
    (9, 1): ("День знаний", [
        "📚 С Днём знаний! Новые цели в беге — как новый учебный год. Вперёд!",
    ]),
    (11, 4): ("День народного единства", [
        "🤝 С Днём народного единства! Вместе мы сильнее — и на забегах тоже!",
    ]),
    (12, 31): ("Канун Нового года", [
        "🎆 Последний день года! Готовьтесь к первому забегу нового года. С наступающим!",
    ]),
}

# Дата последней отправки поздравления с праздником (чтобы не дублировать)
holiday_congrats_sent_date = ""

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

LEAVE_MESSAGES = [
    "Вы самое слабое звено, прощайте! 👋",
    "Беги-беги, пока пинка не дали! 🏃",
    "Минус один бегун, плюс один свободный слот! 😄",
    "Сошел с дистанции. Судья махнул флажком! 🏁",
    "Эх, отстегнулся как шнурок на старте. Пока! 👟",
    "Пелотон поредел, но гонка продолжается! 🚴",
    "Выход засчитан. Возврат в чат - через допинг-контроль! 🧪",
    "Ну все, ушел в закат... надеюсь, хотя бы с каденсом 180. 🌅",
    "Снялся с марафона чата. Медаль почтой не отправляем! 🥇",
    "До встречи на следующем круге, беглец! 🔁",
]

# ============== СОВЕТЫ ДНЯ (ИЗ ИНТЕРНЕТА) ==============
from bs4 import BeautifulSoup  # type: ignore[import-untyped]
from typing import List, Dict, Optional

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


async def generate_ai_response(user_message: str, user_name: str, is_female: bool = False) -> str:
    """
    Генерирует ответ с помощью YandexGPT, используя заданный характер.
    """
    global AI_PERSONALITY
    
    if not YANDEX_AVAILABLE:
        logger.warning("[AI] YandexGPT не настроен, используется локальный ответ.")
        # Возвращаем старый дефолтный ответ, если Yandex недоступен
        return random.choice(DEFAULT_RESPONSES).format(user_name=user_name)

    # Формируем системный промпт с персонажем по дню недели
    base_personality = get_ai_personality_by_day()
    # Если промпт содержит {user_name}, форматируем, иначе просто используем
    try:
        system_prompt = base_personality.format(user_name=user_name)
    except KeyError:
        system_prompt = base_personality
    
    # Добавляем особый контекст для девушек
    if is_female:
        system_prompt += "\n\nВАЖНО: Это сообщение от ДЕВУШКИ. Обязательно сделай лёгкий комплимент её красоте."

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
        "completionOptions": {
            "stream": False,
            "temperature": 0.7,
            "maxTokens": "650"
        },
        "messages": [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": user_message}
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data and 'result' in data and 'alternatives' in data['result'] and data['result']['alternatives']:
                ai_response = data['result']['alternatives'][0]['message']['text']
                logger.info(f"[AI-YANDEX] 🧠 Ответ для {user_name}: {ai_response[:80]}...")
                return ai_response
            else:
                raise ValueError("Ответ от YandexGPT в неожиданном формате")

    except httpx.TimeoutException:
        logger.error("[AI-YANDEX] ⌛️ Таймаут при запросе к YandexGPT.")
        return f"🤖 Мой нейромозг перегрелся от ожидания... как и твои мышцы без пробежки. Иди бегай, {user_name}!"
    except Exception as e:
        logger.error(f"[AI-YANDEX] 💥 Ошибка при запросе к YandexGPT: {e}")
        return f"Что-то пошло не так с моим ИИ... Наверное, думаю о том, почему ты до сих пор не на пробежке, {user_name}? 🤡"


# ============== GARMIN CHECKER ==============
async def check_garmin_activities():
    """Проверка новых пробежек у всех зарегистрированных пользователей"""
    global garmin_users, user_running_stats, garmin_published_ids, garmin_published_order

    if BLOCK_GARMIN_REQUESTS:
        logger.info("[GARMIN] Проверка пропущена: Garmin временно отключен (BLOCK_GARMIN_REQUESTS=1)")
        return
    
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
                if hasattr(garminconnect, "__version__"):
                    logger.info(f"[GARMIN] garminconnect version: {getattr(garminconnect, '__version__', 'unknown')}")
                client = garminconnect.Garmin(email, password)
                client.login()
                
                # Вычисляем дату начала месяца
                now = datetime.now(MOSCOW_TZ)
                first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                first_of_month_str = first_of_month.strftime("%Y-%m-%d")
                
                # Получаем больше активностей для фильтрации по дате (запрашиваем 200)
                activities = client.get_activities(0, 200)
            except Exception as garmin_error:
                # Повторная попытка (иногда помогает при временных сбоях/капче)
                logger.warning(f"[GARMIN] Повторная попытка логина для {email}")
                try:
                    await asyncio.sleep(3)
                    client = garminconnect.Garmin(email, password)
                    client.login()
                    now = datetime.now(MOSCOW_TZ)
                    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    first_of_month_str = first_of_month.strftime("%Y-%m-%d")
                    activities = client.get_activities(0, 200)
                except Exception:
                    logger.error(
                        f"[GARMIN] Ошибка подключения к Garmin для {email}: {garmin_error}",
                        exc_info=True,
                    )
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
                    except Exception:
                        pass
                
                if activity_date_dt is None and start_time_seconds > 0:
                    try:
                        activity_date_dt = datetime.fromtimestamp(start_time_seconds, tz=MOSCOW_TZ)
                    except Exception:
                        pass
                
                if activity_date_dt and activity_date_dt >= first_of_month:
                    filtered_activities.append(activity)
            
            activities = filtered_activities
            logger.info(f"[GARMIN] У пользователя {email} найдено {len(activities)} активностей с {first_of_month_str}")

            def parse_activity_date(a):
                st_local = a.get('startTimeLocal', '')
                st_sec = a.get('startTimeInSeconds', 0)
                st_nano = a.get('startTimeInNanoSeconds', 0)
                dt = None
                if st_local:
                    try:
                        dt = datetime.strptime(st_local, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MOSCOW_TZ)
                    except Exception:
                        pass
                if dt is None and st_sec:
                    try:
                        dt = datetime.fromtimestamp(st_sec, tz=MOSCOW_TZ)
                    except Exception:
                        pass
                if dt is None and st_nano:
                    try:
                        dt = datetime.fromtimestamp(st_nano // 1000000000, tz=MOSCOW_TZ)
                    except Exception:
                        pass
                if dt is None:
                    dt = now
                return dt

            # Собираем только беговые за месяц и считаем итоги за период
            running_with_dates = []
            total_km_month = 0.0
            total_activities_month = 0
            heart_rates_month = []
            for activity in activities:
                activity_type = activity.get('activityType', {}).get('typeKey', 'unknown')
                if activity_type not in ('running', 'treadmill_running', 'trail_running'):
                    continue
                activity_date_dt = parse_activity_date(activity)
                activity_id = str(activity.get('activityId', 'unknown'))
                activity_date_str = activity_date_dt.strftime("%Y-%m-%d")
                dist_km = (activity.get('distance') or 0) / 1000
                total_km_month += dist_km
                total_activities_month += 1
                # Собираем пульс для расчёта среднего за месяц
                hr = activity.get('averageHeartRate', 0) or activity.get('avgHeartRate', 0)
                if hr and hr > 0:
                    heart_rates_month.append(hr)
                running_with_dates.append((activity, activity_date_dt, activity_id, activity_date_str))
            
            # Средний пульс за месяц
            avg_hr_month = None
            if heart_rates_month:
                avg_hr_month = round(sum(heart_rates_month) / len(heart_rates_month))

            last_id = str(user_data.get("last_activity_id") or "").strip()
            max_days = 60
            # Определяем, публиковал ли этот пользователь тренировки раньше
            user_was_published = any(
                key.startswith(f"{user_id}:") for key in garmin_published_ids
            )
            # Если это первый запуск пользователя (ни одной публикации) — показываем все пробежки за max_days
            # Иначе публикуем только свежие (за последние 4 часа) — по факту новой тренировки
            max_hours_recent = 4
            new_running = []
            for activity, activity_date_dt, activity_id, activity_date_str in running_with_dates:
                if str(activity_id) == last_id:
                    continue
                if f"{user_id}:{activity_id}" in processed_activities:
                    continue
                if f"{user_id}:{activity_id}" in garmin_published_ids:
                    continue
                if (now - activity_date_dt).days > max_days:
                    continue
                # Для пользователей без предыдущих публикаций — без ограничения по 4 часам
                if not user_was_published:
                    new_running.append((activity, activity_date_dt, activity_id, activity_date_str))
                    continue
                hours_ago = (now - activity_date_dt).total_seconds() / 3600
                if hours_ago > max_hours_recent:
                    continue
                new_running.append((activity, activity_date_dt, activity_id, activity_date_str))

            # Сортируем: самые свежие первыми
            new_running.sort(key=lambda x: x[1], reverse=True)

            if not new_running:
                continue

            # Для пользователей без предыдущих публикаций публикуем до N тренировок за раз
            # чтобы не спамить в чат. Остальные дождутся следующей проверки (30 мин).
            batch_limit = 3
            if not user_was_published:
                publish_list = new_running[:batch_limit]
            else:
                publish_list = [new_running[0]]  # только самую свежую

            for idx, (activity, activity_date_dt, activity_id, activity_date_str) in enumerate(publish_list):
                activity_key = f"{user_id}:{activity_id}"

                user_data["monthly_distance"] = total_km_month
                user_data["monthly_activities"] = total_activities_month
                old_activity_id = user_data.get("last_activity_id", "")
                user_data["last_activity_id"] = activity_id
                user_data["last_activity_date"] = activity_date_str
                save_garmin_users()

                logger.info(f"[GARMIN] Публикую пробежку: {activity_id} (за месяц: {total_km_month:.1f} км, {total_activities_month} тренировок, часть {idx+1}/{len(publish_list)}, ср.пульс: {avg_hr_month})")
                success = await publish_run_result(
                    user_id, user_data, activity, now, current_month,
                    total_km_month=total_km_month, total_activities_month=total_activities_month,
                    avg_hr_month=avg_hr_month,
                )
                if success:
                    processed_activities.add(activity_key)
                    garmin_published_ids.add(activity_key)
                    garmin_published_order.append(activity_key)
                    save_garmin_published_ids()
                    logger.info(f"[GARMIN] ✅ Пробежка {activity_id} опубликована")
                else:
                    user_data["last_activity_id"] = old_activity_id
                    save_garmin_users()
                    logger.warning(f"[GARMIN] ⚠️ Публикация не удалась, откат last_activity_id")
                    break  # если ошибка — стоп

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


async def publish_run_result(user_id, user_data, activity, now, current_month, total_km_month=None, total_activities_month=None, avg_hr_month=None):
    """Публикация результатов пробежки в чат. total_km_month/total_activities_month — итоги за месяц, avg_hr_month — средний пульс за месяц."""
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
        
        # Итоги за месяц: либо переданы снаружи, либо обновляем из текущей пробежки
        if total_km_month is not None and total_activities_month is not None:
            pass  # уже установлены в check_garmin_activities
        else:
            user_monthly = user_data.get("last_activity_date", "")
            if user_monthly and user_monthly[:7] != current_month:
                user_data["monthly_distance"] = 0.0
                user_data["monthly_activities"] = 0
                logger.info(f"[GARMIN] Новый месяц для {user_data['name']}, сброс счётчиков")
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
        await save_user_running_stats()
        
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
        
        if avg_hr_month is not None and avg_hr_month > 0:
            message_text += f"📊 *Средний пульс за месяц:* {avg_hr_month} уд/мин\n"
        
        if calories > 0:
            message_text += f"🔥 *Калории:* {calories} ккал\n"
        
        km_total = total_km_month if total_km_month is not None else user_data.get("monthly_distance", 0)
        count_total = total_activities_month if total_activities_month is not None else user_data.get("monthly_activities", 0)
        message_text += (
            f"\n📊 *Всего за месяц:* {km_total:.1f} км за {count_total} тренировок"
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
    """Планировщик проверки Garmin. Интервал по умолчанию 30 мин (меньше запросов к Garmin). Переменная GARMIN_CHECK_INTERVAL_SEC."""
    global bot_running
    
    try:
        check_interval = int(os.environ.get("GARMIN_CHECK_INTERVAL_SEC", "1800"))
    except ValueError:
        check_interval = 1800
    if check_interval < 300:
        check_interval = 300  # не чаще чем раз в 5 минут
    logger.info(f"[GARMIN] Интервал проверки: {check_interval} сек (~{check_interval // 60} мин)")
    
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

        # Сохраняем в SQLite
        db_save_json("birthdays", save_data)
        
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
        db_data = db_load_json("birthdays")
        if db_data:
            load_data = db_data
        else:
            migrate_legacy_file(BIRTHDAYS_FILE, LEGACY_BIRTHDAYS_FILE, "birthdays")
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


def save_passport_data():
    """Сохранение данных паспорта (город, личники) в файл и БД."""
    global user_passport_data
    try:
        save_data = {str(uid): data for uid, data in user_passport_data.items()}
        if DATA_DIR:
            with open(PASSPORT_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
        db_save_json("passport_data", save_data)
        logger.info(f"[PASSPORT] Данные паспорта сохранены: {len(user_passport_data)}")
    except Exception as e:
        logger.error(f"[PASSPORT] Ошибка сохранения: {e}")


def load_passport_data():
    """Загрузка данных паспорта (город, личники)."""
    global user_passport_data
    try:
        db_data = db_load_json("passport_data")
        if db_data:
            user_passport_data = {int(uid): data for uid, data in db_data.items()}
        else:
            migrate_legacy_file(PASSPORT_DATA_FILE, LEGACY_PASSPORT_DATA_FILE, "passport_data")
            if os.path.exists(PASSPORT_DATA_FILE):
                with open(PASSPORT_DATA_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                user_passport_data = {int(uid): data for uid, data in raw.items()}
            else:
                user_passport_data = {}
        logger.info(f"[PASSPORT] Загружено данных паспорта: {len(user_passport_data)}")
    except Exception as e:
        logger.error(f"[PASSPORT] Ошибка загрузки: {e}")
        user_passport_data = {}


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


def init_passport_data_on_startup():
    """Инициализация данных паспорта (город, личники) при запуске."""
    try:
        load_passport_data()
        logger.info(f"[PASSPORT] Инициализация завершена. Записей: {len(user_passport_data)}")
    except Exception as e:
        logger.error(f"[PASSPORT] Ошибка инициализации: {e}")


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
            "Ставь цель на тренировку: дистанция, время или самочувствие — это помогает держать фокус.",
            "Два лёгких бега + один темповый в неделю — уже прогресс.",
            "Если болит — остановись. Боль не равна росту.",
            "Старайся держать каденс 165–180 шагов в минуту — это снижает ударную нагрузку.",
            "Пей воду маленькими глотками каждые 10–15 минут на длительных тренировках.",
            "Добавляй силовые упражнения 2 раза в неделю — это укрепляет связки и мышцы.",
        ],
        "recovery": [
            "После пробежки обязательно сделай заминку: 5-10 минут медленной ходьбы.",
            "Растяжка после бега должна быть статической — удерживай позы 20-30 секунд.",
            "Пей воду сразу после тренировки — 200-300 мл, потом пей по жажде в течение дня.",
            "Сон — главный инструмент восстановления. 7-8 часов сна творят чудеса.",
            "Делай хотя бы 1 полный день отдыха в неделю — мышцы восстанавливаются именно в покое.",
            "Обязательны дни отдыха — рост формы происходит в восстановлении.",
            "Лёгкий самомассаж роллом после бега ускоряет восстановление.",
            "Контрастный душ помогает снять ощущение усталости в ногах.",
            "Питайся в течение часа после тренировки: белок + углеводы.",
            "Если чувствуешь сильную усталость — сделай лёгкий день или отдых.",
            "Записывай самочувствие после тренировок — так легче отслеживать перегрузку.",
        ],
        "equipment": [
            "Беговые кроссовки нужно менять каждые 500-800 км — изношенная амортизация ведёт к травмам.",
            "Бери кроссовки на 0,5-1,5 см больше обычного размера — нога отекает при беге.",
            "Одевайся так, чтобы в начале тренировки было прохладно — на один слой меньше, чем для прогулки.",
            "Синтетическая одежда отводит влагу лучше хлопка — выбирай технические ткани.",
            "Примеряй кроссовки вечером — к вечеру стопы немного отекают.",
            "Выбирай кроссовки под тип пронации: нейтральная, поддержка или контроль — зависит от стопы.",
            "Носки тоже важны: бесшовные снижают риск мозолей.",
            "Фонарь/жилет со светоотражателями обязателен в тёмное время суток.",
            "В жару выбирай кепку и солнцезащитные очки — это реально помогает.",
            "Смазка от натирания спасает на длительных тренировках.",
            "Новый инвентарь тестируй на короткой пробежке, не на длительной.",
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


def get_daily_advice_category() -> str:
    """Чередуем категории советов по дням."""
    categories = ["running", "recovery", "equipment"]
    idx = datetime.now(MOSCOW_TZ).toordinal() % len(categories)
    return categories[idx]


def get_category_label(category: str) -> str:
    labels = {
        "running": "бегу",
        "recovery": "восстановлению",
        "equipment": "экипировке",
    }
    return labels.get(category, "бегу")


def build_ai_advice_prompt(category: str | None) -> str:
    category_label = get_category_label(category) if category else "бегу"
    return (
        f"Дай ОДИН короткий практичный совет по {category_label}.\n"
        "Требования:\n"
        "- 1–2 предложения\n"
        "- без воды, конкретный совет\n"
        "- без маркетинга и ссылок\n"
    )


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
    "⏱️ 10 минут бега лучше нуля. Начни!",
    "🏃‍♂️ Темп не важен — важна регулярность!",
    "🌤️ Небольшая пробежка — большое настроение!",
    "🧠 Бег очищает мысли — попробуй!",
    "🦵 Твои ноги сильнее, чем твои сомнения!",
    "🏁 Сначала старт, потом всё остальное!",
    "💡 Хочешь гордиться собой вечером? Побеги утром!",
    "🔋 Движение заряжает лучше кофе!",
    "🌍 Короткий бег = длинный список побед!",
    "🏔️ Подъёмы делают нас сильнее — и на дороге, и в жизни!",
    "🎶 Поставь любимый трек и вперёд!",
    "🛣️ Дорога любит тех, кто не боится сделать шаг!",
    "📈 Каждый тренинг — плюс к твоей версии 2.0!",
    "🧭 Ты сам выбираешь направление. Выбери бег!",
    "💥 Пробежка — это кнопка «перезапуск» для дня!",
    "🦁 Сила в привычке — создай её бегом!",
    "🧩 Маленькая тренировка складывается в большой результат!",
    "✨ Ты уже на пути. Продолжай!",
    "⛅ Погода не идеальна? Зато характер идеален!",
    "🥇 Победа начинается с кроссовок на ногах!",
    "📅 Сегодня — лучший день для шага вперёд!",
    "🔥 Мотивация приходит в движении!",
    "🎯 Цель ближе, чем кажется. Беги!",
    "🧘 Бег — это медитация в движении!",
    "🏃‍♀️ Сделай это ради будущего себя!",
    "⚡ Один шаг — и ты уже не на месте!",
    "💪 Сильный день начинается с сильного выбора!",
    "🌄 Бег — лучший способ встретить новый день!",
    "🛡️ Тренировка сегодня защитит тебя от лени завтра!",
    "🎽 Выходишь на старт — значит уже победил!",
    "🏃 Не жди вдохновения — создай его бегом!",
    "🔍 Ищи не оправдания, а дистанции!",
    "💯 Меньше сомнений, больше шагов!",
    "🧗 Тяжело? Значит растёшь!",
    "🏃‍♂️ Ты быстрее своих мыслей о «потом».",
    "🌟 Выйти на улицу — уже половина тренировки!",
    "📣 Тело скажет спасибо за каждую минуту движения!",
    "💥 Делай сегодня то, чем будешь гордиться завтра!",
    "🏆 Твоё «могу» сильнее твоего «не хочу»!",
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
    "\\✨ {name}, с Днём рождения! Желаю много друзей-единомышленников и крутых забегов! 👟",
    "🎯 {name}, с ДР! Пусть цели будут достигнуты, а новые горизонты — покорены! 🎯",
    "💫 {name}, поздравляю! Желаю never stop running и always finish strong! 🏁",
    "🌅 {name}, с Днём рождения! Пусть утренние пробежки дают энергию на весь день! ☀️",
    "🎖️ {name}, с ДР! Желаю медалей, кубков и незабываемых соревнований! 🥇",
    "💝 {name}, поздравляю! Ты — звезда нашего бегового клуба! Пусть сияешь ещё ярче! \\🌟",
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

# GIF и стикеры для ответов бота (больше общения через медиа)
BOT_GIF_URLS = [
    "https://media.tenor.com/2FgB2LbqN_cAAAAC/running-run.gif",
    "https://media.tenor.com/4B2P2FQnL5sAAAAC/good-morning-sunshine.gif",
    "https://media.tenor.com/3fLtYJP_2EgAAAAC/thumbs-up-approve.gif",
    "https://media.tenor.com/1Vz9nD0Dv_cAAAAC/motivation-running.gif",
    "https://media.tenor.com/5HxNnB1u0MAAAAAC/high-five-celebrate.gif",
    "https://media.tenor.com/8BvB2VvR8EAAAAAC/runner-running.gif",
    "https://media.tenor.com/6fJzlO8e0AAAAAC/coffee-morning.gif",
    "https://media.tenor.com/9gS4QKbbQAAAAAC/clap-applause.gif",
    "https://media.tenor.com/7VlD1bCN1AAAAAC/wink-flirt.gif",
]
# file_id стикеров загружаются из bot_stickers.json (добавить через /add_sticker)
bot_sticker_ids = []


def load_bot_stickers():
    """Загрузить список file_id стикеров из файла."""
    global bot_sticker_ids
    try:
        if os.path.exists(BOT_STICKERS_FILE):
            with open(BOT_STICKERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            bot_sticker_ids = list(data) if isinstance(data, list) else []
        else:
            bot_sticker_ids = []
    except Exception as e:
        logger.warning(f"[STICKERS] Ошибка загрузки: {e}")
        bot_sticker_ids = []


def save_bot_stickers():
    """Сохранить список file_id стикеров в файл."""
    try:
        with open(BOT_STICKERS_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_sticker_ids, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[STICKERS] Ошибка сохранения: {e}")


async def send_random_sticker_or_gif(bot, chat_id: int, chance: float = 0.4):
    """С вероятностью chance отправить случайный стикер или GIF в чат. Ничего не делает при ошибке."""
    if not bot or random.random() >= chance:
        return
    try:
        if bot_sticker_ids and random.random() < 0.5:
            sticker_id = _pick_with_recent_filter(bot_sticker_ids, _recent_sticker_ids)
            if sticker_id:
                _remember_recent(_recent_sticker_ids, sticker_id)
                await bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
        elif BOT_GIF_URLS:
            gif_url = _pick_with_recent_filter(BOT_GIF_URLS, _recent_gif_urls)
            if gif_url:
                _remember_recent(_recent_gif_urls, gif_url)
                await bot.send_animation(chat_id=chat_id, animation=gif_url)
    except Exception as e:
        logger.debug(f"[STICKERS/GIF] Не удалось отправить медиа: {e}")


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
            def weather_code_text(code: int | None) -> str:
                mapping = {
                    0: "ясно",
                    1: "преимущественно ясно",
                    2: "переменная облачность",
                    3: "пасмурно",
                    45: "туман",
                    48: "изморозь",
                    51: "морось",
                    53: "морось",
                    55: "сильная морось",
                    61: "небольшой дождь",
                    63: "дождь",
                    65: "сильный дождь",
                    71: "слабый снег",
                    73: "снег",
                    75: "сильный снег",
                    80: "ливневый дождь",
                    81: "ливень",
                    82: "сильный ливень",
                    95: "гроза",
                }
                return mapping.get(code, "переменная погода")

            def gear_tip(temp_c: float, wind_kmh: float, precip_mm: float) -> str:
                wind_ms = wind_kmh / 3.6
                if precip_mm >= 0.4:
                    return "ветровка + кепка, лучше взять сухую сменку"
                if temp_c <= 2:
                    return "термолонгслив, перчатки и бафф"
                if temp_c <= 8:
                    return "лонгслив/ветровка, на старте не стой долго"
                if wind_ms >= 7:
                    return "ветровка обязательна, держи ровный темп против ветра"
                if temp_c >= 20:
                    return "легкая форма и вода до выхода"
                return "комфортно для easy run"

            async def fetch_city_weather(city_label: str, lat: float, lon: float) -> str:
                """Всегда возвращает строку, даже если API не отвечает"""
                try:
                    resp = await client.get(
                        "https://api.open-meteo.com/v1/forecast",
                        params={
                            "latitude": lat,
                            "longitude": lon,
                            "timezone": "Europe/Moscow",
                            "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                        },
                        timeout=10.0,
                    )
                    data = resp.json()
                    current = data.get("current") or {}
                    temp = current.get("temperature_2m")
                    feels_like = current.get("apparent_temperature")
                    precip = current.get("precipitation")
                    weather_code = current.get("weather_code")
                    wind = current.get("wind_speed_10m")
                    if temp is None or wind is None or feels_like is None or precip is None:
                        return f"{city_label}: *данные недоступны*"

                    weather_text = weather_code_text(int(weather_code) if weather_code is not None else None)
                    tip = gear_tip(float(temp), float(wind), float(precip))
                    wind_ms = float(wind) / 3.6
                    return (
                        f"{city_label}: **{temp:.0f}°C** (ощущается как {feels_like:.0f}°C)\n"
                        f"   • {weather_text}, осадки {precip:.1f} мм/ч, ветер {wind_ms:.1f} м/с\n"
                        f"   • 👟 {tip}"
                    )
                except Exception as e:
                    logger.warning(f"[WEATHER] Не удалось получить погоду для {city_label}: {e}")
                    return f"{city_label}: *данные недоступны*"

            # Москва, СПб, Ижевск - ВСЕГДА показываем все три города
            lines = []
            lines.append(await fetch_city_weather("🏙 Москва", 55.7558, 37.6173))
            lines.append(await fetch_city_weather("🌆 СПб", 59.9343, 30.3351))
            lines.append(await fetch_city_weather("🌇 Ижевск", 56.8498, 53.2045))

            # Теперь lines всегда содержит 3 элемента (даже если "данные недоступны")
            return "🌤 **Подробная погода для пробежки (06:00 МСК):**\n" + "\n".join(lines)
    except Exception as e:
        logger.error(f"Ошибка получения погоды: {e}")
        # В случае критической ошибки всё равно показываем все города
        return (
            "🌤 **Подробная погода для пробежки (06:00 МСК):**\n"
            "🏙 Москва: *данные недоступны*\n"
            "🌆 СПб: *данные недоступны*\n"
            "🌇 Ижевск: *данные недоступны*"
        )


# ============== УТРЕННЕЕ ПРИВЕТСТВИЕ ==============
def get_day_theme() -> str:
    now = datetime.now(MOSCOW_TZ)
    day_name_en = now.strftime("%A")
    return DAY_THEMES.get(day_name_en, "🌟 Отличный день для пробежки!")


def get_random_welcome() -> str:
    return random.choice(WELCOME_MESSAGES)


def get_random_leave_message() -> str:
    return random.choice(LEAVE_MESSAGES)


def get_random_motivation() -> str:
    return random.choice(MOTIVATION_QUOTES)


def get_marathon_training_plan() -> str:
    """
    Генерирует план тренировки на сегодня для подготовки к марафону 03.05.2026.
    Возвращает короткий план только на сегодня.
    """
    try:
        now = datetime.now(MOSCOW_TZ)
        marathon_date = datetime(2026, 5, 3, tzinfo=MOSCOW_TZ)
        days_left = (marathon_date - now).days
        
        if days_left < 0:
            return ""  # Марафон уже прошёл
        
        if days_left > 120:
            phase = "базовая"
        elif days_left > 60:
            phase = "строительная"
        elif days_left > 14:
            phase = "пиковая"
        else:
            phase = "снижение нагрузки"
        
        day_of_week = now.weekday()  # 0=понедельник, 6=воскресенье
        
        # План по дням недели
        if day_of_week == 0:  # Понедельник
            plan = "🎯 **Базовый бег**\n   • Дистанция: 8-10 км\n   • Темп: Комфортный\n   • Время: 45-55 мин"
        elif day_of_week == 1:  # Вторник
            plan = "🎯 **Интервалы**\n   • Разминка: 2 км\n   • Интервалы: 5×800м (быстро) + 400м (восстановление)\n   • Заминка: 2 км\n   • Всего: ~8 км"
        elif day_of_week == 2:  # Среда
            plan = "🎯 **Восстановительный бег**\n   • Дистанция: 5-7 км\n   • Темп: Разговорный (легко)\n   • Время: 30-40 мин\n   • Цель: Восстановление"
        elif day_of_week == 3:  # Четверг
            plan = "🎯 **Темповой бег**\n   • Разминка: 2 км\n   • Основная часть: 6 км в темпе марафона\n   • Заминка: 2 км\n   • Всего: ~10 км"
        elif day_of_week == 4:  # Пятница
            plan = "🎯 **Восстановительный бег**\n   • Дистанция: 5-6 км\n   • Темп: Очень легко\n   • Время: 30-35 мин"
        elif day_of_week == 5:  # Суббота
            if days_left > 14:
                long_distance = min(18 + (120 - days_left) // 7, 32)  # Увеличиваем до 32 км
                plan = f"🎯 **Длинный бег**\n   • Дистанция: {long_distance}-{min(long_distance+2, 32)} км\n   • Темп: Комфортный (на 30-60 сек/км медленнее марафонского)\n   • Время: {long_distance//6}-{long_distance//5} мин\n   • Цель: Выносливость"
            else:
                plan = "🎯 **Легкий длинный бег**\n   • Дистанция: 12-15 км\n   • Темп: Очень комфортный\n   • Время: 1:15-1:30"
        else:  # Воскресенье
            plan = "🎯 **Отдых или легкая активность**\n   • Прогулка: 30-40 мин\n   • Или: Восстановительный бег 3-5 км\n   • Цель: Полное восстановление"
        
        return f"🏃‍♂️ **План тренировки к марафону 03.05.2026**\n📅 До старта: {days_left} дней ({phase})\n{plan}"
    
    except Exception as e:
        logger.error(f"[TRAINING] Ошибка генерации плана: {e}")
        return ""


MORNING_TITLES = [
    "Лорд финишного спурта",
    "Король каденса",
    "Мастер ровного темпа",
    "Охотник за личником",
    "Легенда раннего старта",
]

MORNING_MISSIONS = [
    "25 минут easy + 4 ускорения по 20 сек",
    "6-8 км в спокойном темпе + 5 минут заминки",
    "3 км easy + 6 х 200 м бодро + 2 км заминка",
    "40 минут комфортного бега и планка 60 сек",
    "5 км прогрессией: каждый км чуть быстрее",
]


def get_morning_title() -> str:
    return random.choice(MORNING_TITLES)


def get_morning_mission() -> str:
    return random.choice(MORNING_MISSIONS)


async def _send_later_reminder(chat_id: int, user_id: int, user_name: str, delay_sec: int = 1800):
    """Отправляет мягкое напоминание после кнопки 'Позже'."""
    try:
        await asyncio.sleep(delay_sec)
        if user_id in morning_today_checkins:
            return
        safe_name = html_escape(user_name or "друг")
        await application.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>{safe_name}</b>, время победить диван. Утренняя миссия ждет тебя!",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"[MORNING] Не удалось отправить reminder: {e}")


async def handle_morning_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок утреннего сообщения."""
    global morning_checkins_date, morning_today_checkins
    query = update.callback_query
    if not query or not query.data.startswith("morning_"):
        return

    action = query.data.replace("morning_", "", 1)
    user = query.from_user
    user_id = user.id
    user_name = user.full_name or user.username or "Участник"
    safe_name = html_escape(user_name)
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    if morning_checkins_date != today:
        morning_checkins_date = today
        morning_today_checkins = set()

    if action == "run":
        if user_id in morning_today_checkins:
            await query.answer("Ты уже отметил пробежку сегодня ✅", show_alert=False)
            return
        stats = morning_streaks.get(user_id, {"streak": 0, "last_date": ""})
        last_date = stats.get("last_date", "")
        yesterday = (datetime.now(MOSCOW_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        if last_date == yesterday:
            streak = int(stats.get("streak", 0)) + 1
        elif last_date == today:
            streak = int(stats.get("streak", 0))
        else:
            streak = 1
        morning_streaks[user_id] = {"streak": streak, "last_date": today}
        morning_today_checkins.add(user_id)
        save_morning_streaks()
        await query.answer("Отметка принята! 🔥", show_alert=False)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🔥 <b>{safe_name}</b>, красавчик! Серия утренних отметок: <b>{streak}</b> дн.",
            parse_mode="HTML",
        )
        return

    if action == "later":
        await query.answer("Ок, мягкий пинок через 30 минут ⏰", show_alert=False)
        context.application.create_task(_send_later_reminder(update.effective_chat.id, user_id, user_name, 1800))
        return

    if action == "rest":
        await query.answer("Принято. Сегодня восстановление 😴", show_alert=False)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"😴 <b>{safe_name}</b>, хороший выбор. Восстановление - тоже часть прогресса.",
            parse_mode="HTML",
        )
        return

    if action == "mission":
        new_mission = get_morning_mission()
        await query.answer("Новая миссия готова 🎯", show_alert=False)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🎯 <b>{safe_name}</b>, новая миссия: <b>{html_escape(new_mission)}</b>",
            parse_mode="HTML",
        )
        return

    if action == "slots":
        await query.answer("Смотрю ближайшие слоты…", show_alert=False)
        try:
            events = await get_all_events()
            if not events:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="Сейчас слотов не нашел.")
                return
            lines = ["📅 Ближайшие слоты:"]
            for idx, event in enumerate(events[:3], start=1):
                title = event.get("title", "Без названия")
                date = event.get("date", "дата не указана")
                city = event.get("city", "город не указан")
                lines.append(f"{idx}. {title} — {date}, {city}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(lines))
        except Exception as e:
            logger.warning(f"[MORNING] Кнопка slots: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Не удалось получить слоты.")
        return


def get_random_insult() -> str:
    return random.choice(FUNNY_INSULTS)


def get_random_compliment() -> str:
    return random.choice(FUNNY_COMPLIMENTS)


def get_random_roast() -> str:
    return random.choice(PLAYFUL_ROASTS)


# Шаблон обязательных ключей daily_stats (защита от KeyError при старых данных из канала)
_DAILY_STATS_DEFAULTS = {
    "total_messages": 0,
    "user_messages": {},
    "photos": [],
    "message_owners": {},
    "message_likes": {},
    "first_photo_user_id": None,
    "first_photo_user_name": None,
    "summary_last_sent": "",
}


# ============== ОТСЛЕЖИВАНИЕ СТАТИСТИКИ ==============
def update_daily_stats(user_id: int, user_name: str, message_type: str, photo_info: dict = None, message_id: int = None):
    """Обновление ежедневной статистики"""
    global daily_stats
    
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    # Смена календарного дня без перезапуска бота (иначе счётчик «залипает» на вчера)
    if daily_stats.get("date") != today:
        logger.info(f"[STATS] Новый день: date было {daily_stats.get('date')}, сброс daily_stats на {today}")
        daily_stats = build_empty_daily_stats(today)
    
    # Гарантируем наличие всех ключей (на случай загрузки из канала без части полей)
    for key, default in _DAILY_STATS_DEFAULTS.items():
        if key not in daily_stats:
            daily_stats[key] = None if default is None else type(default)()
    
    daily_stats["total_messages"] += 1
    
    # Обновление счётчика сообщений пользователя
    if user_id not in daily_stats["user_messages"]:
        # Экранируем спецсимволы Markdown в имени при сохранении
        safe_name = user_name.replace('(', '\\(').replace(')', '\\)') if user_name else "Unknown"
        daily_stats["user_messages"][user_id] = {
            "name": safe_name,
            "count": 0,
        }
    daily_stats["user_messages"][user_id]["count"] += 1
    
    # Добавление фото в статистику + трек первого фото
    if message_type == "photo" and photo_info:
        if "photos" not in daily_stats:
            daily_stats["photos"] = []
        daily_stats["photos"].append(photo_info)
        # Запоминаем первого автора фото (для двойных баллов)
        if daily_stats.get("first_photo_user_id") is None:
            daily_stats["first_photo_user_id"] = user_id
            # Экранируем имя для Markdown
            safe_name = user_name.replace('(', '\\(').replace(')', '\\)') if user_name else "Unknown"
            daily_stats["first_photo_user_name"] = safe_name

    # Запоминаем автора текстового сообщения для учета лайков
    if message_type == "text" and message_id:
        if "message_owners" not in daily_stats:
            daily_stats["message_owners"] = {}
        if message_id not in daily_stats["message_owners"]:
            daily_stats["message_owners"][message_id] = {
                "user_id": user_id,
                "user_name": user_name or "Unknown",
            }

    # Периодически сохраняем, чтобы не потерять данные при деплое
    try:
        if daily_stats.get("total_messages", 0) % 20 == 0:
            save_daily_stats_local()
    except Exception:
        pass


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
async def update_running_stats(user_id: int, user_name: str, distance: float, duration: int, calories: int):
    """Обновление статистики бега для участника (накопльная)"""
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

    # Также обновляем ежедневную статистику
    update_daily_running_stats(user_id, user_name, distance, duration, calories)

    # Сохраняем статистику пробежек в канал
    await save_user_running_stats()


def update_daily_running_stats(user_id: int, user_name: str, distance: float, duration: int, calories: int):
    """Обновление ежедневной статистики бега"""
    global daily_running_stats

    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")

    if user_id not in daily_running_stats:
        daily_running_stats[user_id] = {
            "name": user_name,
            "activities": 0,
            "distance": 0.0,
            "duration": 0,
            "calories": 0,
            "date": today
        }

    # Проверяем, не новый ли день
    if daily_running_stats[user_id]["date"] != today:
        # Новый день - сбрасываем
        daily_running_stats[user_id] = {
            "name": user_name,
            "activities": 0,
            "distance": 0.0,
            "duration": 0,
            "calories": 0,
            "date": today
        }

    daily_running_stats[user_id]["activities"] += 1
    daily_running_stats[user_id]["distance"] += distance
    daily_running_stats[user_id]["duration"] = duration
    daily_running_stats[user_id]["calories"] = calories


def save_daily_running_to_weekly():
    """Сохранение ежедневной статистики в недельную (вызывается в полночь)"""
    global daily_running_stats, weekly_running_stats

    today = datetime.now(MOSCOW_TZ)
    week_start = today - timedelta(days=today.weekday())
    week_start_str = week_start.strftime("%Y-%m-%d")

    for user_id, data in daily_running_stats.items():
        user_name = data["name"]

        if user_id not in weekly_running_stats:
            weekly_running_stats[user_id] = {
                "name": user_name,
                "activities": 0,
                "distance": 0.0,
                "duration": 0,
                "calories": 0,
                "week_start": week_start_str
            }

        # Проверяем, не новый ли период недели
        if weekly_running_stats[user_id]["week_start"] != week_start_str:
            # Новый период недели - сбрасываем
            weekly_running_stats[user_id] = {
                "name": user_name,
                "activities": 0,
                "distance": 0.0,
                "duration": 0,
                "calories": 0,
                "week_start": week_start_str
            }

        weekly_running_stats[user_id]["activities"] += data["activities"]
        weekly_running_stats[user_id]["distance"] += data["distance"]
        weekly_running_stats[user_id]["duration"] += data["duration"]
        weekly_running_stats[user_id]["calories"] += data["calories"]

    logger.info(f"[RUNNING] Ежедневная статистика бега сохранена в недельную ({len(daily_running_stats)} пользователей)")


def save_daily_running_to_monthly():
    """Сохранение ежедневной статистики в месячную (вызывается в полночь)"""
    global daily_running_stats, monthly_running_stats

    today = datetime.now(MOSCOW_TZ)
    month_str = today.strftime("%Y-%m")

    for user_id, data in daily_running_stats.items():
        user_name = data["name"]

        if user_id not in monthly_running_stats:
            monthly_running_stats[user_id] = {
                "name": user_name,
                "activities": 0,
                "distance": 0.0,
                "duration": 0,
                "calories": 0,
                "month": month_str
            }

        # Проверяем, не новый ли месяц
        if monthly_running_stats[user_id]["month"] != month_str:
            # Новый месяц - сбрасываем
            monthly_running_stats[user_id] = {
                "name": user_name,
                "activities": 0,
                "distance": 0.0,
                "duration": 0,
                "calories": 0,
                "month": month_str
            }

        monthly_running_stats[user_id]["activities"] += data["activities"]
        monthly_running_stats[user_id]["distance"] += data["distance"]
        monthly_running_stats[user_id]["duration"] += data["duration"]
        monthly_running_stats[user_id]["calories"] += data["calories"]

    logger.info(f"[RUNNING] Ежедневная статистика бега сохранена в месячную ({len(daily_running_stats)} пользователей)")


def reset_daily_running_stats():
    """Сброс ежедневной статистики бега (вызывается в полночь)"""
    global daily_running_stats
    daily_running_stats = {}
    logger.info("[RUNNING] Ежедневная статистика бега сброшена")


def get_top_weekly_runners() -> list:
    """Получение топ-10 бегунов за неделю"""
    global weekly_running_stats

    if not weekly_running_stats:
        return []

    runners = []
    for user_id, stats in weekly_running_stats.items():
        runners.append({
            "user_id": user_id,
            "name": stats["name"],
            "activities": stats["activities"],
            "distance": stats["distance"],
            "duration": stats["duration"],
            "calories": stats["calories"]
        })

    runners.sort(key=lambda x: x["distance"], reverse=True)
    return runners[:10]


def get_top_monthly_runners() -> list:
    """Получение топ-10 бегунов за месяц"""
    global monthly_running_stats

    if not monthly_running_stats:
        return []

    runners = []
    for user_id, stats in monthly_running_stats.items():
        runners.append({
            "user_id": user_id,
            "name": stats["name"],
            "activities": stats["activities"],
            "distance": stats["distance"],
            "duration": stats["duration"],
            "calories": stats["calories"]
        })

    runners.sort(key=lambda x: x["distance"], reverse=True)
    return runners[:10]


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
    global application, weekly_running_stats

    try:
        if not weekly_running_stats:
            logger.info("[RUNNING] Нет данных для еженедельной сводки (weekly_running_stats пуст)")
            return

        now = datetime.utcnow() + timedelta(hours=UTC_OFFSET)
        week_num = now.isocalendar()[1]
        year = now.year

        # Считаем общую статистику за НЕДЕЛЮ
        total_activities = sum(stats["activities"] for stats in weekly_running_stats.values())
        total_distance = sum(stats["distance"] for stats in weekly_running_stats.values()) / 1000  # в км
        total_calories = sum(stats["calories"] for stats in weekly_running_stats.values())

        # Получаем топ бегунов за неделю
        top_runners = get_top_weekly_runners()

        weekly_text = f"🏃‍♂️ **Еженедельная сводка по бегу (Неделя #{week_num}, {year})**\n\n"

        # Общая статистика недели
        weekly_text += f"📊 **Общая статистика за эту неделю:**\n"
        weekly_text += f"🏃‍♂️ Всего пробежек: {total_activities}\n"
        weekly_text += f"📍 Общая дистанция: {total_distance:.1f} км\n"
        weekly_text += f"🔥 Сожжено калорий: {total_calories}\n"
        weekly_text += f"👥 Участников бега: {len(weekly_running_stats)}\n\n"

        # Топ-3 бегунов
        if top_runners:
            medals = ["🥇", "🥈", "🥉"]
            weekly_text += f"🏆 **Топ бегунов недели:**\n"
            for i, runner in enumerate(top_runners[:3]):
                distance_km = runner["distance"] / 1000
                safe_name = escape_markdown(runner['name'])
                weekly_text += f"{medals[i]} {safe_name} — {distance_km:.1f} км \\({runner['activities']} тренировок\\)\n"
            weekly_text += "\n"

        # Индивидуальная статистика всех
        weekly_text += "📝 **Все участники:**\n"
        for runner in top_runners:
            distance_km = runner["distance"] / 1000
            safe_name = escape_markdown(runner['name'])
            weekly_text += f"• {safe_name}: {distance_km:.1f} км \\({runner['activities']} тренировок\\)\n"

        # Мотивация - цитата великого бегуна с указанием автора
        quote = random.choice(GREAT_RUNNER_QUOTES)
        weekly_text += "\n" + "="*40 + "\n"
        weekly_text += f"💬 **Слова великих бегунов:**\n"
        weekly_text += f"{quote}\n"
        weekly_text += "="*40 + "\n"

        # Отправляем в чат; при ошибке — без топика, при ошибке Markdown — без разметки
        if application and CHAT_ID:
            send_kw = {"chat_id": CHAT_ID, "text": weekly_text, "parse_mode": "Markdown"}
            if NEWS_TOPIC_ID:
                send_kw["message_thread_id"] = NEWS_TOPIC_ID
            try:
                await application.bot.send_message(**send_kw)
            except Exception as e1:
                logger.warning(f"[RUNNING WEEKLY] Отправка не удалась: {e1}, пробуем в основной чат")
                send_kw.pop("message_thread_id", None)
                try:
                    await application.bot.send_message(**send_kw)
                except Exception as e2:
                    logger.warning(f"[RUNNING WEEKLY] Markdown не прошёл: {e2}, без разметки")
                    await application.bot.send_message(chat_id=CHAT_ID, text=weekly_text)

        # Сбрасываем недельную статистику после отправки
        weekly_running_stats.clear()
        logger.info("[RUNNING] Еженедельная сводка по бегу отправлена, статистика сброшена")

    except Exception as e:
        logger.error(f"[RUNNING] Ошибка еженедельной сводки: {e}", exc_info=True)


async def send_monthly_running_summary():
    """Отправка ежемесячной сводки по бегу (последний день месяца)"""
    global application, monthly_running_stats

    try:
        if not monthly_running_stats:
            logger.info("[RUNNING] Нет данных для ежемесячной сводки (monthly_running_stats пуст)")
            return

        now = datetime.utcnow() + timedelta(hours=UTC_OFFSET)
        month_name = now.strftime("%B %Y")

        # Считаем общую статистику за МЕСЯЦ
        total_activities = sum(stats["activities"] for stats in monthly_running_stats.values())
        total_distance = sum(stats["distance"] for stats in monthly_running_stats.values()) / 1000  # в км
        total_calories = sum(stats["calories"] for stats in monthly_running_stats.values())
        total_duration = sum(stats["duration"] for stats in monthly_running_stats.values())

        # Получаем топ бегунов за месяц
        top_runners = get_top_monthly_runners()

        monthly_text = f"🏆 **Ежемесячная сводка по бегу ({month_name})**\n\n"

        # Общая статистика месяца
        monthly_text += f"📊 **Итоги за этот месяц:**\n"
        monthly_text += f"🏃‍♂️ Всего пробежек: {total_activities}\n"
        monthly_text += f"📍 Общая дистанция: {total_distance:.1f} км\n"
        monthly_text += f"⏱️ Общее время: {total_duration // 3600}ч {(total_duration % 3600) // 60}м\n"
        monthly_text += f"🔥 Сожжено калорий: {total_calories}\n"
        monthly_text += f"👥 Участников бега: {len(monthly_running_stats)}\n\n"

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

        # Мотивация - цитата великого бегуна с указанием автора
        quote = random.choice(GREAT_RUNNER_QUOTES)
        monthly_text += "\n" + "="*40 + "\n"
        monthly_text += f"💬 **Слова великих бегунов:**\n"
        monthly_text += f"{quote}\n"
        monthly_text += "="*40 + "\n"

        # Отправляем в чат (в топик "Новости")
        if application and CHAT_ID:
            await application.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=NEWS_TOPIC_ID,
                text=monthly_text,
                parse_mode="Markdown"
            )

        # Сбрасываем месячную статистику после отправки
        monthly_running_stats.clear()
        logger.info("[RUNNING] Ежемесячная сводка по бегу отправлена, статистика сброшена")

    except Exception as e:
        logger.error(f"[RUNNING] Ошибка ежемесячной сводки: {e}", exc_info=True)


def reset_monthly_running_stats():
    """Сброс всей периодической статистики бега в новый период"""
    global user_running_stats, daily_running_stats, weekly_running_stats, monthly_running_stats

    logger.info("[RUNNING] Сброс всей периодической статистики бега")

    # Сбрасываем все периодические статистики
    daily_running_stats.clear()
    weekly_running_stats.clear()
    monthly_running_stats.clear()

    logger.info("[RUNNING] Вся периодическая статистика бега сброшена")


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
    global morning_message_id, morning_checkins_date, morning_today_checkins

    if application is None:
        logger.error("Application не инициализирован")
        return

    try:
        today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
        if morning_checkins_date != today:
            morning_checkins_date = today
            morning_today_checkins = set()

        weather = await get_weather()
        theme = get_day_theme()
        motivation = get_random_motivation()
        training_plan = get_marathon_training_plan()
        morning_mission = get_morning_mission()
        day_names = {
            0: "понедельник",
            1: "вторник",
            2: "среда",
            3: "четверг",
            4: "пятница",
            5: "суббота",
            6: "воскресенье",
        }
        now = datetime.now(MOSCOW_TZ)
        weekday_name = day_names.get(now.weekday(), "день")
        date_human = now.strftime("%d.%m")

        greeting_parts = [
            f"🌅 **Доброе утро, команда!** {weekday_name.title()}, {date_human}",
            "",
            f"🌤 **Погода:** {weather}",
            f"💡 **Фокус дня:** {theme}",
        ]

        if training_plan:
            greeting_parts.extend(["", f"🏃 **План дня:** {training_plan}"])

        greeting_parts.extend(
            [
                "",
                f"🎯 **Мини-миссия:** {morning_mission}",
                f"⚡ **Мотивация:** {motivation}",
                "",
                "✅ **Отметка:** нажми кнопку после пробежки",
            ]
        )
        greeting_text = "\n".join(greeting_parts)

        reply_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Пробежал", callback_data="morning_run"),
                InlineKeyboardButton("🕒 Побегу вечером", callback_data="morning_later"),
            ],
            [
                InlineKeyboardButton("😴 Сегодня восстановление", callback_data="morning_rest"),
                InlineKeyboardButton("🎯 Дай другую миссию", callback_data="morning_mission"),
            ],
            [
                InlineKeyboardButton("📅 Слоты", callback_data="morning_slots"),
            ],
        ])

        message = await application.bot.send_message(
            chat_id=CHAT_ID,
            text=greeting_text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

        morning_message_id = message.message_id
        logger.info(f"Утреннее сообщение отправлено: {morning_message_id}")

    except Exception as e:
        logger.error(f"Ошибка отправки утреннего сообщения: {e}")


async def morning_scheduler_task():
    """Планировщик утренней сводки (6:00 МСК, с окном и догоняющей отправкой)."""
    global morning_scheduled_date

    logger.info("[MORNING] Планировщик запущен (окно 5:59–6:10 МСК, догон до 10:00)")
    while bot_running:
        try:
            now = datetime.now(MOSCOW_TZ)
            current_hour = now.hour
            current_minute = now.minute
            today_date = now.strftime("%Y-%m-%d")

            in_morning_window = (
                (current_hour == 6 and current_minute <= 10)
                or (current_hour == 5 and current_minute >= 59)
            )
            # Если бот перезапустился после 6:00 — один раз догоняем до 10:00
            in_catchup_window = 6 <= current_hour < 10

            if morning_scheduled_date != today_date and (in_morning_window or in_catchup_window):
                label = "догоняющая" if in_catchup_window and not in_morning_window else "по расписанию"
                logger.info(
                    f"[MORNING] {current_hour:02d}:{current_minute:02d} — отправляем утреннее ({label})"
                )
                try:
                    await send_morning_greeting()
                    morning_scheduled_date = today_date
                    logger.info("[MORNING] Утреннее сообщение успешно отправлено")
                except Exception as e:
                    logger.error(f"[MORNING] Ошибка при отправке: {e}", exc_info=True)

            # В окне 5:55–6:10 проверяем чаще, чтобы не пропустить 6:00
            if (current_hour == 5 and current_minute >= 55) or (current_hour == 6 and current_minute <= 10):
                await asyncio.sleep(15)
            else:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[MORNING] Ошибка в планировщике: {e}", exc_info=True)
            await asyncio.sleep(60)


async def send_good_night_message():
    """Отправка пожелания спокойной ночи в 22:00."""
    if application is None:
        logger.error("Application не инициализирован")
        return

    try:
        text = get_good_night_message()
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=f"**{text}**",
            parse_mode="Markdown",
        )
        logger.info("[NIGHT] Сообщение спокойной ночи отправлено")
    except Exception as e:
        logger.error(f"[NIGHT] Ошибка отправки спокойной ночи: {e}")


async def good_night_scheduler_task():
    """Планировщик спокойной ночи (22:00 каждый день)."""
    global good_night_sent_date

    while bot_running:
        now = datetime.now(MOSCOW_TZ)
        current_hour = now.hour
        current_minute = now.minute
        today_date = now.strftime("%Y-%m-%d")

        # Окно 21:59-22:01 — чтобы не пропустить при sleep 60 сек
        if (current_hour == 22 and current_minute <= 1) or (current_hour == 21 and current_minute == 59):
            if good_night_sent_date != today_date:
                logger.info(f"Время {current_hour}:{current_minute} - отправляем спокойной ночи")
                try:
                    await send_good_night_message()
                    good_night_sent_date = today_date
                except Exception as e:
                    logger.error(f"[NIGHT] Ошибка при отправке: {e}")

        # С 21:55 до 22:05 проверяем каждые 15 сек
        if (current_hour == 21 and current_minute >= 55) or (current_hour == 22 and current_minute <= 5):
            await asyncio.sleep(15)
        else:
            await asyncio.sleep(60)


async def music_scheduler_task():
    """Планировщик музыки дня (14:00 каждый день)."""
    global music_sent_date

    while bot_running:
        now = datetime.now(MOSCOW_TZ)
        current_hour = now.hour
        current_minute = now.minute
        today_date = now.strftime("%Y-%m-%d")

        if current_hour == 14 and current_minute == 0:
            if music_sent_date != today_date:
                try:
                    music = get_music_of_day()
                    await application.bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"🎵 Музыка дня:\n{format_music_message(music)}",
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    music_sent_date = today_date
                    logger.info("[MUSIC] Музыка дня отправлена")
                except Exception as e:
                    logger.error(f"[MUSIC] Ошибка отправки музыки дня: {e}")

        await asyncio.sleep(60)


async def deals_scheduler_task():
    """Планировщик скидок (раз в неделю, понедельник 12:00)."""
    global deals_sent_week

    while bot_running:
        now = datetime.now(MOSCOW_TZ)
        week_key = f"{now.isocalendar().year}-W{now.isocalendar().week}"
        if now.weekday() == 0 and now.hour == 12 and now.minute == 0:
            if deals_sent_week != week_key:
                try:
                    text = await build_deals_message()
                    message_kwargs = {
                        "chat_id": CHAT_ID,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    }
                    if NEWS_TOPIC_ID:
                        message_kwargs["message_thread_id"] = NEWS_TOPIC_ID
                    await application.bot.send_message(**message_kwargs)
                    deals_sent_week = week_key
                    logger.info("[DEALS] Подборка скидок отправлена")
                except Exception as e:
                    logger.error(f"[DEALS] Ошибка отправки скидок: {e}")

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


# ============== ПОЗДРАВЛЕНИЯ С ПРАЗДНИКАМИ ==============
async def send_holiday_congrats():
    """Отправить поздравление с праздником в чат, если сегодня праздник из HOLIDAYS."""
    global application, holiday_congrats_sent_date
    now = datetime.now(MOSCOW_TZ)
    today_str = now.strftime("%Y-%m-%d")
    if not HOLIDAY_CONGRATS_ENABLED:
        logger.info("[HOLIDAY] Поздравления отключены (HOLIDAY_CONGRATS_ENABLED=False)")
        return
    if application is None or not CHAT_ID:
        logger.warning("[HOLIDAY] Нет application или CHAT_ID — поздравление не отправлено")
        return
    key = (now.month, now.day)
    if key not in HOLIDAYS:
        logger.info(f"[HOLIDAY] Сегодня {now.day}.{now.month} — не праздник из списка, поздравление не отправляется")
        return
    if holiday_congrats_sent_date == today_str:
        logger.info(f"[HOLIDAY] Поздравление за сегодня уже отправляли, пропуск")
        return
    name, messages = HOLIDAYS[key]
    text = random.choice(messages)
    try:
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=f"🎉 **{name}!**\n\n{text}",
            parse_mode="Markdown",
        )
        holiday_congrats_sent_date = today_str
        logger.info(f"[HOLIDAY] Поздравление с {name} отправлено")
    except Exception as e:
        try:
            await application.bot.send_message(chat_id=CHAT_ID, text=f"🎉 {name}!\n\n{text}")
            holiday_congrats_sent_date = today_str
        except Exception as e2:
            logger.error(f"[HOLIDAY] Ошибка отправки поздравления: {e2}")


async def holiday_scheduler_task():
    """Планировщик поздравлений с праздниками — в 12:45–12:49 по Москве (окно 5 мин)."""
    global holiday_congrats_sent_date
    while bot_running:
        try:
            await asyncio.sleep(60)
            now = datetime.now(MOSCOW_TZ)
            if now.hour == 0 and now.minute == 1:
                holiday_congrats_sent_date = ""
            # Окно 12:45–12:49: не пропустим, даже если проверка раз в 60 сек
            if now.hour == 12 and 45 <= now.minute <= 49:
                logger.info(f"[HOLIDAY] Время {now.hour}:{now.minute:02d} — проверка праздника (сегодня {now.day}.{now.month})")
                try:
                    await send_holiday_congrats()
                except Exception as e:
                    logger.error(f"[HOLIDAY] Ошибка: {e}", exc_info=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[HOLIDAY] Ошибка в планировщике: {e}")


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


async def send_daily_advice():
    """Ежедневный совет по бегу."""
    if application is None:
        return
    try:
        advice_text = None
        category = get_daily_advice_category()
        if YANDEX_AVAILABLE:
            try:
                today_label = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
                weekday_names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
                weekday = weekday_names[datetime.now(MOSCOW_TZ).weekday()]
                prompt = (
                    f"Сегодня {today_label}, {weekday}. "
                    + build_ai_advice_prompt(category)
                )
                payload = {
                    "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                    "messages": [
                        {"role": "system", "text": "Ты тренер по бегу. Пиши кратко и по делу."},
                        {"role": "user", "text": prompt},
                    ],
                    "completionOptions": {
                        "temperature": 0.7,
                        "maxTokens": 200
                    },
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                        json=payload,
                        headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    data = response.json()
                    if data and "result" in data and data["result"]["alternatives"]:
                        advice_text = data["result"]["alternatives"][0]["message"]["text"].strip()
            except Exception as e:
                logger.warning(f"[ADVICE] Ошибка ИИ: {e}")

        if not advice_text:
            await update_tips_cache()
            advice_text = get_random_tip(category)

        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=f"💡 Совет по {get_category_label(category)}:\n\n{advice_text}",
            parse_mode="Markdown",
        )
        logger.info("[ADVICE] Ежедневный совет отправлен")
    except Exception as e:
        logger.error(f"[ADVICE] Ошибка отправки ежедневного совета: {e}")


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


async def advice_scheduler_task():
    """Планировщик ежедневного совета в 12:00."""
    global advice_sent_date

    while bot_running:
        now = datetime.now(MOSCOW_TZ)
        today_date = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        current_minute = now.minute

        if current_hour == 0 and current_minute == 0:
            advice_sent_date = ""

        if current_hour == 12 and current_minute == 0:
            if advice_sent_date != today_date:
                try:
                    await send_daily_advice()
                    advice_sent_date = today_date
                except Exception as e:
                    logger.error(f"[ADVICE] Ошибка планировщика: {e}")

        await asyncio.sleep(60)


# ============== ЕЖЕДНЕВНАЯ СВОДКА ==============
async def get_top_liked_photos() -> list:
    """Получение топ фото по лайкам с уведомлениями"""
    global daily_stats, user_rating_stats, user_current_level
    
    photos = daily_stats.get("photos", [])
    if not photos:
        return []
    
    # Лайки обновляются в handle_reactions() по событиям реакций.
    # Здесь просто берём уже накопленные значения из daily_stats.
    updated_photos = []
    for photo in photos:
        updated_photos.append({
            "file_id": photo.get("file_id"),
            "user_id": photo.get("user_id"),
            "likes": int(photo.get("likes", 0) or 0),
            "message_id": photo.get("message_id"),
        })

    # Сортируем по лайкам и фильтруем (минимум 4)
    updated_photos.sort(key=lambda x: x["likes"], reverse=True)
    top_photos = [p for p in updated_photos if p["likes"] >= 4]

    return top_photos[:2]  # Возвращаем максимум 2 фото


async def get_top_users() -> list:
    """Получение топ 5 активных пользователей по сообщениям"""
    global daily_stats
    
    user_messages = daily_stats.get("user_messages", {})
    if not user_messages:
        return []
    
    # Сортируем по количеству сообщений
    sorted_users = sorted(
        user_messages.items(),
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


async def send_daily_summary(force: bool = False, ref_date: str | None = None):
    """Отправка ежедневной сводки в чат + сохранение данных.

    Args:
        force: Если True — отправить даже если уже отправляли за эту дату.
        ref_date: Дата сводки YYYY-MM-DD. Если None — берётся сегодня (для догоняющей отправки утром передать вчерашнюю дату).
    """
    global daily_summary_sent

    if application is None:
        logger.error("Application не инициализирован")
        return

    today = (ref_date or datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d"))
    if not force:
        last_sent = daily_stats.get("summary_last_sent", "")
        if last_sent == today:
            daily_summary_sent = True
            logger.info("Сводка уже отправлена за эту дату (используй force=True или /summary)")
            return
        if not ref_date and daily_summary_sent:
            logger.info("Сводка уже отправлена сегодня (используй force=True или /summary)")
            return

    try:


        # НЕ сбрасываем daily_stats даже если дата не совпадает - данные восстановлены из канала
        # saved_date = daily_stats.get("date", "") if isinstance(daily_stats, dict) else ""
        # if saved_date != today:
        #     logger.warning(f"[SUMMARY] Дата в daily_stats ({saved_date}) не совпадает с сегодня ({today}) - сбрасываем статистику")
        #     daily_stats = {
        #         "date": today,
        #         "total_messages": 0,
        #         "user_messages": {},
        #         "photos": [],
        #         "first_photo_user_id": None,
        #         "first_photo_user_name": None,
        #     }

        # ЛОГИРОВАНИЕ ДЛЯ ОТЛАДКИ - что у нас в daily_stats
        # ЛОГИРОВАНИЕ ДЛЯ ОТЛАДКИ - что у нас в daily_stats
        msg_count = daily_stats.get("total_messages", 0)
        photo_count = len(daily_stats.get("photos", []))
        user_count = len(daily_stats.get("user_messages", {}))
        logger.info(f"[SUMMARY] Формирование сводки за {today}")
        logger.info(f"[SUMMARY] daily_stats: {msg_count} сообщений, {photo_count} фото, {user_count} пользователей")
        logger.info(f"[SUMMARY] user_messages: {daily_stats.get('user_messages', {})}")
        logger.info(f"[SUMMARY] photos: {daily_stats.get('photos', [])[:3]}")  # первые 3 фото
        
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
        
        # Функция для экранирования Markdown-символов в именах (MarkdownV2)
        def escape_markdown(text: str) -> str:
            """Экранирует спецсимволы MarkdownV2"""
            if not text:
                return text
            # Экранируем все спецсимволы MarkdownV2: _ * [ ] ( ) ~ ` > # + - . ! |
            return text.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('`', '\\`').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('.', '\\.').replace('!', '\\!').replace('|', '\\|')

        # Компактная ежедневная сводка
        escaped_today = escape_markdown(today)
        summary_text = f"📊 *Ежедневная сводка за {escaped_today}*\n\n"
        summary_text += f"💬 *Всего сообщений:* {daily_stats.get('total_messages', 0)}\n"

        if most_active_user_name:
            escaped_name = escape_markdown(most_active_user_name)
            summary_text += f"🔥 *Самый активный:* {escaped_name} \\({most_messages_count} сообщений\\)\n"
        else:
            summary_text += "🔥 *Самый активный:* пока нет данных\n"

        photos = daily_stats.get("photos", []) or []
        top_photo = max(photos, key=lambda p: int(p.get("likes", 0) or 0), default=None)
        if top_photo and int(top_photo.get("likes", 0) or 0) > 0:
            top_photo_name = escape_markdown(top_photo.get("user_name", "Неизвестный"))
            top_photo_likes = int(top_photo.get("likes", 0) or 0)
            summary_text += f"📸 *Фото дня:* {top_photo_name} \\(❤️ {top_photo_likes}\\)"
        else:
            summary_text += "📸 *Фото дня:* сегодня без лайков"

        # === ОТЛАДКА: Проверяем текст перед отправкой ===
        logger.info(f"[SUMMARY] Проверка текста сводки перед отправкой (длина: {len(summary_text)})")
        
        # DEBUG: Показываем первые 500 символов summary_text
        logger.info(f"[SUMMARY DEBUG] summary_text (first 500 chars): {summary_text[:500]}")
        logger.info(f"[SUMMARY DEBUG] daily_stats['total_messages'] = {daily_stats.get('total_messages', 'NOT_FOUND')}")
        
        # Проверяем на неэкранированные скобки
        unescaped_parens = []
        for i, char in enumerate(summary_text):
            if char == '(' or char == ')':
                # Проверяем, экранирована ли скобка
                if i > 0 and summary_text[i-1] == '\\':
                    continue  # Экранирована
                unescaped_parens.append((i, char, summary_text[max(0,i-10):i+10]))

        if unescaped_parens:
            logger.error(f"[SUMMARY] Найдены неэкранированные скобки: {unescaped_parens[:3]}")

        # Отправляем в чат (в топик при наличии); при ошибке — в основной чат; при ошибке Markdown — без разметки
        send_kw = {"chat_id": CHAT_ID, "text": summary_text, "parse_mode": "Markdown"}
        if NEWS_TOPIC_ID:
            send_kw["message_thread_id"] = NEWS_TOPIC_ID
        sent_ok = False
        try:
            await application.bot.send_message(**send_kw)
            sent_ok = True
        except Exception as send_err:
            logger.warning(f"[SUMMARY] Отправка в топик не удалась: {send_err}, пробуем в основной чат")
            try:
                await application.bot.send_message(chat_id=CHAT_ID, text=summary_text, parse_mode="Markdown")
                sent_ok = True
            except Exception as fallback_err:
                logger.warning(f"[SUMMARY] Markdown не прошёл: {fallback_err}, отправляем без разметки")
                try:
                    await application.bot.send_message(chat_id=CHAT_ID, text=summary_text)
                    sent_ok = True
                except Exception as plain_err:
                    logger.error(f"[SUMMARY] Отправка ежедневной сводки не удалась: {plain_err}", exc_info=True)
                    raise
        if not sent_ok:
            raise RuntimeError("Ежедневная сводка не была отправлена")
        
        # Отправляем фото дня (самое залайканное), если есть
        try:
            photos = daily_stats.get("photos", []) or []
            top_photo = max(photos, key=lambda p: int(p.get("likes", 0) or 0), default=None)
            if top_photo and int(top_photo.get("likes", 0) or 0) > 0 and top_photo.get("file_id"):
                photo_caption = f"📸 Фото дня — ❤️ {int(top_photo.get('likes', 0) or 0)} лайков"
                photo_kw = {
                    "chat_id": CHAT_ID,
                    "photo": top_photo["file_id"],
                    "caption": photo_caption,
                }
                if NEWS_TOPIC_ID:
                    photo_kw["message_thread_id"] = NEWS_TOPIC_ID
                await application.bot.send_photo(**photo_kw)
        except Exception as e:
            logger.error(f"Ошибка получения фото: {e}")
        
        # Отмечаем отправку сводки
        daily_stats["summary_last_sent"] = today
        daily_summary_sent = True

        # Сохраняем данные в историю (СКРЫТО, в чат не выводится)
        await save_daily_stats()
        await save_user_rating_stats()
        await save_chat_history()
        await save_user_active_stats()
        logger.info("Ежедневная сводка отправлена в чат + данные сохранены")
        
    except Exception as e:
        logger.error(f"Ошибка ежедневной сводки: {e}", exc_info=True)

        # Показываем первые 200 символов текста сводки для отладки
        try:
            debug_text = summary_text[:200] if 'summary_text' in dir() else "summary_text не определён"
            logger.error(f"[SUMMARY DEBUG] Текст сводки (первые 200 символов): {debug_text}")
        except Exception:
            pass

        # Отладочная информация о состоянии данных
        logger.error(f"[SUMMARY DEBUG] daily_stats date: {daily_stats.get('date', 'EMPTY')}")
        logger.error(f"[SUMMARY DEBUG] daily_stats total_messages: {daily_stats.get('total_messages', 0)}")
        logger.error(f"[SUMMARY DEBUG] daily_stats user_messages: {daily_stats.get('user_messages', {})}")
        logger.error(f"[SUMMARY DEBUG] daily_stats photos: {daily_stats.get('photos', [])}")

        # Попытка отправить упрощённую версию сводки без Markdown
        try:
            simple_text = f"📊 Сводка за {today}\n"
            simple_text += f"💬 Сообщений: {daily_stats.get('total_messages', 0)}\n"
            simple_text += "⚠️ Расширенная версия временно недоступна"
            
            await application.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=NEWS_TOPIC_ID,
                text=simple_text,
            )
            logger.info("Упрощённая сводка отправлена")
        except Exception as e2:
            logger.error(f"Ошибка отправки упрощённой сводки: {e2}")


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
                escaped_level = escape_markdown(level)
                escaped_users = [escape_markdown(u['name']) for u in users]
                weekly_text += f"{level_emoji} **{escaped_level}** \\({len(users)} чел.\\):\n"
                
                # Показываем топ-3 каждого уровня
                top_users = users[:3]
                medals = ["🥇", "🥈", "🥉"]
                for i, user in enumerate(top_users):
                    escaped_name = escape_markdown(user['name'])
                    weekly_text += f"   {medals[i]} {escaped_name} — {user['points']} очков\n"
                
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
        
        # Отправляем в чат; при ошибке топика — в основной чат; при ошибке Markdown — без разметки
        send_kw = {"chat_id": CHAT_ID, "text": weekly_text, "parse_mode": "Markdown"}
        if NEWS_TOPIC_ID:
            send_kw["message_thread_id"] = NEWS_TOPIC_ID
        sent = False
        try:
            await application.bot.send_message(**send_kw)
            sent = True
        except Exception as e1:
            logger.warning(f"[WEEKLY] Отправка не удалась: {e1}, пробуем в основной чат")
            send_kw.pop("message_thread_id", None)
            try:
                await application.bot.send_message(**send_kw)
                sent = True
            except Exception as e2:
                logger.warning(f"[WEEKLY] Markdown не прошёл: {e2}, без разметки")
                try:
                    await application.bot.send_message(chat_id=CHAT_ID, text=weekly_text)
                    sent = True
                except Exception as e3:
                    logger.error(f"[WEEKLY] Отправка еженедельной сводки не удалась: {e3}", exc_info=True)
                    raise
        if not sent:
            await application.bot.send_message(chat_id=CHAT_ID, text=weekly_text)
        
        # Сохраняем данные в историю (СКРЫТО)
        await save_daily_stats()
        await save_user_rating_stats()
        await save_chat_history()
        await save_user_active_stats()
        
        logger.info("Еженедельная сводка отправлена в чат + данные сохранены")
        
    except Exception as e:
        logger.error(f"Ошибка еженедельной сводки: {e}")


# ============== ЕЖЕМЕСЯЧНАЯ СВОДКА ==============
async def send_monthly_summary(ref_date: datetime | None = None):
    """Отправка ежемесячной сводки с итогами месяца"""
    global user_rating_stats, user_running_stats, monthly_running_stats

    if application is None:
        logger.error("Application не инициализирован")
        return

    try:
        now = datetime.now(MOSCOW_TZ)
        ref_date = ref_date or now
        month_name = ref_date.strftime("%B %Y")
        month_key = ref_date.strftime("%Y-%m")

        # Функция для экранирования Markdown-символов (MarkdownV2)
        def escape_markdown(text: str) -> str:
            """Экранирует все спецсимволы MarkdownV2"""
            if not text:
                return text
            # Экранируем все спецсимволы MarkdownV2: _ * [ ] ( ) ~ ` > # + - . ! |
            return text.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('`', '\\`').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('.', '\\.').replace('!', '\\!').replace('|', '\\|')

        monthly_text = f"🏆 **Итоги месяца: {month_name}** 🏆\n\n"
        
        # Общий топ-10 участников за месяц
        top_rated = await get_top_rated_users()
        
        if top_rated:
            monthly_text += "🌟 **Топ-10 легенд месяца:**\n"
            medals_rating = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            
            for i, user in enumerate(top_rated):
                level_emoji = LEVEL_EMOJIS.get(user["level"], "")
                escaped_name = escape_markdown(user['name'])
                monthly_text += f"{medals_rating[i]} {level_emoji} **{escaped_name}**\n"
                monthly_text += f"   └─ 🏅 {user['points']} очков | 📝{user['messages']} | 📷{user['photos']} | ❤️{user['likes']} | 💬{user['replies']}\n"
            monthly_text += "\n"
        else:
            monthly_text += "🌟 **Топ-10 легенд месяца:** Пока никого нет\n\n"
        
        # Победители по номинациям
        monthly_text += "🎖️ **Номинации месяца:**\n"
        
        # Самое активное сообщество
        if top_rated:
            escaped_name = escape_markdown(top_rated[0]['name'])
            monthly_text += f"🥇 **{escaped_name}** — Абсолютный лидер месяца!\n"
        
        # Максимум сообщений
        if user_rating_stats:
            max_messages_user = max(user_rating_stats.items(), key=lambda x: x[1]["messages"])
            escaped_name = escape_markdown(max_messages_user[1]["name"])
            monthly_text += f"💬 **{escaped_name}** — Больше всего сообщений \\({max_messages_user[1]['messages']}\\)\n"
        
        # Максимум фото
        if user_rating_stats:
            max_photos_user = max(user_rating_stats.items(), key=lambda x: x[1]["photos"])
            if max_photos_user[1]["photos"] > 0:
                escaped_name = escape_markdown(max_photos_user[1]["name"])
                monthly_text += f"📷 **{escaped_name}** — Фотогений месяца \\({max_photos_user[1]['photos']} фото\\)\n"
        
        # Максимум лайков
        if user_rating_stats:
            max_likes_user = max(user_rating_stats.items(), key=lambda x: x[1]["likes"])
            if max_likes_user[1]["likes"] > 0:
                escaped_name = escape_markdown(max_likes_user[1]["name"])
                monthly_text += f"❤️ **{escaped_name}** — Самый любимый автор \\({max_likes_user[1]['likes']} лайков\\)\n"
        
        # Максимум ответов
        if user_rating_stats:
            max_replies_user = max(user_rating_stats.items(), key=lambda x: x[1]["replies"])
            if max_replies_user[1]["replies"] > 0:
                escaped_name = escape_markdown(max_replies_user[1]["name"])
                monthly_text += f"💬 **{escaped_name}** — Самый отзывчивый \\({max_replies_user[1]['replies']} ответов\\)\n"
        
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
        
        # Статистика бега за МЕСЯЦ
        if monthly_running_stats:
            running_distance = sum(stats["distance"] for stats in monthly_running_stats.values()) / 1000
            running_activities = sum(stats["activities"] for stats in monthly_running_stats.values())
            running_calories = sum(stats["calories"] for stats in monthly_running_stats.values())
            running_duration = sum(stats["duration"] for stats in monthly_running_stats.values())

            monthly_text += "🏃‍♂️ **Статистика бега за этот месяц:**\n"
            monthly_text += f"📍 Всего пробежали: {running_distance:.1f} км\n"
            monthly_text += f"🏃‍♂️ Всего тренировок: {running_activities}\n"
            monthly_text += f"⏱️ Общее время: {running_duration // 3600}ч {(running_duration % 3600) // 60}м\n"
            monthly_text += f"🔥 Сожгли калорий: {running_calories} ккал\n"
            monthly_text += f"👥 Бегунов в чате: {len(monthly_running_stats)}\n\n"

            # Топ бегунов месяца
            monthly_text += "🏆 **Лучшие бегуны месяца:**\n"
            top_monthly_runners = get_top_monthly_runners()
            medals = ["🥇", "🥈", "🥉"]
            for i, runner in enumerate(top_monthly_runners[:3]):
                escaped_name = escape_markdown(runner["name"])
                distance_km = runner["distance"] / 1000
                monthly_text += f"{medals[i]} {escaped_name} — {distance_km:.1f} км \\({runner['activities']} тренировок\\)\n"
            monthly_text += "\n"
        elif user_running_stats:
            # Fallback на накопленную статистику если monthly_running_stats пуст
            running_distance = sum(stats["distance"] for stats in user_running_stats.values()) / 1000
            running_activities = sum(stats["activities"] for stats in user_running_stats.values())
            running_calories = sum(stats["calories"] for stats in user_running_stats.values())

            monthly_text += "🏃‍♂️ **Статистика бега:**\n"
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
        
        # Отправляем в чат (в топик "Новости")
        await application.bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=NEWS_TOPIC_ID,
            text=monthly_text,
            parse_mode="Markdown",
        )
        
        # Сохраняем данные в историю (СКРЫТО)
        await save_daily_stats()
        await save_user_rating_stats()
        await save_chat_history()
        await save_user_active_stats()
        await save_user_running_stats()

        summary_state["monthly_last_sent"] = month_key
        save_summary_state()

        logger.info("Ежемесячная сводка отправлена в чат + данные сохранены")
        
        # НЕ сбрасываем статистику здесь - это делает планировщик в нужное время
        
    except Exception as e:
        logger.error(f"Ошибка отправки ежемесячной сводки: {e}")


async def daily_summary_scheduler_task():
    """Планировщик ежедневной, еженедельной и ежемесячной сводок + трекинг бега"""
    global daily_summary_sent, user_running_stats, daily_stats

    logger.info("[SUMMARY] Планировщик сводок запущен (ежедневно 23:50–23:59 МСК, еженедельно вс 23:55)")
    while bot_running:
        try:
            now = datetime.now(MOSCOW_TZ)
            current_hour = now.hour
            current_minute = now.minute
            today_date = now.strftime("%Y-%m-%d")

            # Синхронизация флага с сохранённой датой отправки
            if daily_stats.get("summary_last_sent") == today_date:
                daily_summary_sent = True

            # Сброс флага и дневной статистики после закрытия окна отправки
            if now.hour == 0 and current_minute >= 11 and daily_stats.get("date") != today_date:
                daily_summary_sent = False
                daily_stats = build_empty_daily_stats(today_date)
                logger.info("[SUMMARY] Сброс daily_stats на новый день")

            # === ПЕРЕХОД НА НОВЫЙ ДЕНЬ (полночь) ===
            if now.hour == 0 and current_minute == 0:
                logger.info("[RUNNING] Новый день - перенос статистики бега в недельную/месячную")
                try:
                    save_daily_running_to_weekly()
                    save_daily_running_to_monthly()
                    reset_daily_running_stats()
                except Exception as e:
                    logger.error(f"Ошибка при переносе статистики бега: {e}")

            # Отправка ежедневной сводки строго в окне 23:50–23:59
            if current_hour == 23 and current_minute >= 50:
                if daily_stats.get("summary_last_sent") != today_date:
                    logger.info(f"[SUMMARY] Время {current_hour}:{current_minute} — отправляем ежедневную сводку")
                    try:
                        await send_daily_summary(ref_date=today_date)
                    except Exception as e:
                        logger.error(f"Ошибка при отправке сводки: {e}", exc_info=True)

            # Еженедельная сводка: воскресенье 23:55–23:59
            iso_year, week_num, _ = now.isocalendar()
            week_key = f"{iso_year}-W{week_num:02d}"
            if now.weekday() == 6 and current_hour == 23 and current_minute >= 55:
                if summary_state.get("weekly_last_sent_week") != week_key:
                    logger.info(f"[SUMMARY] Воскресенье 23:55+ — еженедельная сводка (неделя {week_key})")
                    try:
                        await send_weekly_summary()
                        summary_state["weekly_last_sent_week"] = week_key
                        save_summary_state()
                    except Exception as e:
                        logger.error(f"Ошибка при отправке еженедельной сводки: {e}", exc_info=True)
                    try:
                        await send_weekly_running_summary()
                    except Exception as e:
                        logger.error(f"Ошибка при отправке еженедельной сводки по бегу: {e}", exc_info=True)

            # Ежемесячная сводка: последний день месяца 23:55+
            last_day_of_month = calendar.monthrange(now.year, now.month)[1]
            month_key = now.strftime("%Y-%m")
            if (
                now.day == last_day_of_month
                and current_hour == 23
                and current_minute >= 55
                and summary_state.get("monthly_last_sent") != month_key
            ):
                logger.info(f"Последний день месяца - отправляем ежемесячную сводку")
                try:
                    await send_monthly_summary(ref_date=now)
                except Exception as e:
                    logger.error(f"Ошибка при отправке ежемесячной сводки: {e}", exc_info=True)
                try:
                    await send_monthly_running_summary()
                except Exception as e:
                    logger.error(f"Ошибка при отправке ежемесячной сводки по бегу: {e}", exc_info=True)
                try:
                    global user_rating_stats
                    user_rating_stats = {}
                    reset_monthly_running_stats()
                    logger.info("[SUMMARY] Статистика рейтинга и бега сброшена для нового месяца")
                except Exception as e:
                    logger.error(f"Ошибка при сбросе статистики: {e}")

            if now.day == 1 and current_hour == 0 and current_minute <= 5:
                prev_date = now - timedelta(days=1)
                prev_month_key = prev_date.strftime("%Y-%m")
                if summary_state.get("monthly_last_sent") != prev_month_key:
                    logger.info("Догоняем ежемесячную сводку за прошлый месяц")
                    try:
                        await send_monthly_summary(ref_date=prev_date)
                    except Exception as e:
                        logger.error(f"Ошибка при догоняющей ежемесячной сводке: {e}", exc_info=True)

            in_summary_window = (current_hour == 23 and current_minute >= 50)
            await asyncio.sleep(15 if in_summary_window else 60)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[SUMMARY] Ошибка в планировщике сводок: {e}", exc_info=True)
            await asyncio.sleep(60)


# ============== АНОНИМНАЯ ОТПРАВКА ==============
async def anon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if context.args:
        text = " ".join(context.args)
        await context.bot.send_message(chat_id=CHAT_ID, text=f"🕵️ Анонимно:\n{text}")
        try:
            await update.message.delete()
        except Exception:
            pass
        return
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
        text += f"📊 *Участники \\({len(participants)}\\):*\n"
        
        # Сортируем по прогрессу
        sorted_parts = sorted(participants.items(), key=lambda x: x[1]["progress"], reverse=True)
        
        for uid, data in sorted_parts:
            emoji = "✅" if data["completed"] else "🔄"
            escaped_name = escape_markdown(data['name'])
            text += f"   {emoji} {escaped_name}: {data['progress']} / {goal['value']} {goal['unit']}\n"
        
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
    """Обработка обращений через @бота или слово «бот» (отдельным словом) в основном чате."""
    global _bot_keyword_last_reply
    try:
        if not update.message or not update.message.text:
            return
        
        user_name = update.message.from_user.full_name or update.message.from_user.username or "Пользователь"
        user_id = update.message.from_user.id
        message_text = update.message.text
        chat_id = update.effective_chat.id
        
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username.lower()
        
        logger.info(
            f"[MENTION] Проверка от {user_name}: '{message_text[:50]}...' (@{bot_username}, CHAT_ID={CHAT_ID})"
        )
        
        mention_patterns = [
            f"@{bot_username}",
            f"@{bot_username}:",
            f"@{bot_username}.",
        ]
        message_lower = message_text.lower()
        is_mention = any(pattern in message_lower for pattern in mention_patterns)
        
        is_bot_keyword = (
            (not is_mention)
            and chat_id == CHAT_ID
            and text_has_bot_keyword(message_text)
        )
        
        logger.info(f"[MENTION] is_mention={is_mention}, is_bot_keyword={is_bot_keyword}")
        
        if not is_mention and not is_bot_keyword:
            return
        
        if is_bot_keyword:
            now = time.monotonic()
            last = _bot_keyword_last_reply.get(user_id, 0.0)
            if now - last < BOT_KEYWORD_COOLDOWN_SEC:
                logger.info(f"[BOT-KW] Пропуск из-за cooldown ({BOT_KEYWORD_COOLDOWN_SEC}s), user_id={user_id}")
                return
            _bot_keyword_last_reply[user_id] = now
        
        is_female = await check_is_female_by_ai(user_name)
        
        if is_mention:
            clean_text = message_text
            for pattern in mention_patterns:
                clean_text = clean_text.replace(pattern, "").strip()
                clean_text = clean_text.replace(pattern.capitalize(), "").strip()
            clean_text = clean_text.strip(" ,:!-\n")
            logger.info(f"[MENTION] Обращение через @: '{clean_text}'")
        else:
            clean_text = message_text.strip()
            logger.info(f"[BOT-KW] Обращение по слову «бот»: '{clean_text[:120]}...'")
        
        thread_id = getattr(update.message, "message_thread_id", None)
        action_kwargs = {"chat_id": chat_id, "action": "typing"}
        if thread_id:
            action_kwargs["message_thread_id"] = thread_id
        await context.bot.send_chat_action(**action_kwargs)
        
        reply_context = None
        rtm = update.message.reply_to_message
        if rtm:
            reply_context = (getattr(rtm, "text", None) or getattr(rtm, "caption", None) or "").strip() or None
        
        if is_mention:
            response_data = await generate_toxic_response_with_media(
                clean_text, user_name, is_female, include_media=True
            )
            reply_to_id = None
        else:
            response_data = await generate_bot_keyword_ai_reply(
                clean_text, user_name, is_female, reply_context=reply_context
            )
            reply_to_id = update.message.message_id
        
        await send_toxic_response(
            context=context,
            chat_id=chat_id,
            text=response_data["text"],
            sticker=response_data["sticker"],
            gif=response_data["gif"],
            message_thread_id=thread_id,
            reply_to_message_id=reply_to_id,
        )
        
        logger.info(f"[MENTION] Ответ отправлен пользователю {user_name} (mention={is_mention}, bot_kw={is_bot_keyword})")
        
    except Exception as e:
        logger.error(f"[MENTION] Ошибка обработки обращения: {e}")


async def handle_reactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка событий реакций (лайков) на сообщения."""
    try:
        global daily_stats, user_rating_stats
        reaction_count_update = update.message_reaction_count
        reaction_update = update.message_reaction
        if not reaction_count_update and not reaction_update:
            return

        # Берём chat_id/message_id из доступного объекта
        chat_obj = reaction_count_update.chat if reaction_count_update else reaction_update.chat
        chat_id = getattr(chat_obj, "id", None)
        if chat_id is None or str(chat_id) != str(CHAT_ID):
            return

        message_id = reaction_count_update.message_id if reaction_count_update else reaction_update.message_id

        like_count = None
        if reaction_count_update:
            reactions = reaction_count_update.reactions or []
            like_count = 0
            for reaction in reactions:
                like_count += int(getattr(reaction, "count", 0) or 0)
        else:
            # Для message_reaction вычислим дельту по old/new
            old_reactions = reaction_update.old_reaction or []
            new_reactions = reaction_update.new_reaction or []
            old_count = len(old_reactions)
            new_count = len(new_reactions)
            like_count = None  # будем считать по дельте ниже

        # 1) Лайки на фото
        photos = daily_stats.get("photos", [])
        for photo in photos:
            if photo.get("message_id") == message_id:
                prev_likes = int(photo.get("likes", 0) or 0)
                if like_count is not None:
                    if like_count != prev_likes:
                        photo["likes"] = like_count
                    delta = max(like_count - prev_likes, 0)
                else:
                    # реакция без total counts: используем дельту по добавлениям
                    delta = max(new_count - old_count, 0)
                    photo["likes"] = prev_likes + delta
                if delta > 0:
                    user_id = photo.get("user_id")
                    user_name = photo.get("user_name", "Unknown")
                    if user_id is not None:
                        if user_id not in user_rating_stats:
                            user_rating_stats[user_id] = {
                                "name": user_name,
                                "messages": 0,
                                "photos": 0,
                                "likes": 0,
                                "replies": 0,
                                "bonus_points": 0,
                                "days_active": set(),
                            }
                        user_rating_stats[user_id]["likes"] += delta
                logger.info(f"[REACTIONS] Фото {message_id}: лайков={photo.get('likes', 0)}, дельта={delta}")
                save_daily_stats_local()
                return

        # 2) Лайки на обычные сообщения
        owners = daily_stats.get("message_owners", {})
        if message_id in owners:
            prev_likes = int(daily_stats.get("message_likes", {}).get(message_id, 0) or 0)
            if like_count is not None:
                delta = max(like_count - prev_likes, 0)
                new_total = like_count
            else:
                delta = max(new_count - old_count, 0)
                new_total = prev_likes + delta

            if "message_likes" not in daily_stats:
                daily_stats["message_likes"] = {}
            daily_stats["message_likes"][message_id] = new_total

            if delta > 0:
                user_id = owners[message_id].get("user_id")
                user_name = owners[message_id].get("user_name", "Unknown")
                if user_id is not None:
                    if user_id not in user_rating_stats:
                        user_rating_stats[user_id] = {
                            "name": user_name,
                            "messages": 0,
                            "photos": 0,
                            "likes": 0,
                            "replies": 0,
                            "bonus_points": 0,
                            "days_active": set(),
                        }
                    user_rating_stats[user_id]["likes"] += delta
            logger.info(f"[REACTIONS] Сообщение {message_id}: лайков={new_total}, дельта={delta}")
            save_daily_stats_local()
    except Exception as e:
        logger.error(f"[REACTIONS] Ошибка обработки реакций: {e}")


async def handle_replies_to_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений когда отвечают на сообщение бота"""
    try:
        if not update.message or not update.message.text:
            return
        
        # Проверяем, что это reply (ответ на сообщение)
        if not update.message.reply_to_message:
            return
        
        # Проверяем, что ответ на сообщение бота
        replied_from = update.message.reply_to_message.from_user
        if not replied_from or not replied_from.is_bot:
            return
        
        # Проверяем, что это наш бот (а не другой бот)
        bot_info = await context.bot.get_me()
        if replied_from.id != bot_info.id:
            return
        
        user_name = update.message.from_user.full_name or update.message.from_user.username or "Пользователь"
        user_id = update.message.from_user.id
        message_text = update.message.text
        
        logger.info(f"[REPLY] Пользователь {user_name} ответил на сообщение бота: '{message_text[:50]}...'")
        
        # Игнорируем пустые сообщения
        if not message_text or len(message_text.strip()) < 2:
            return
        
        # Проверяем пол для комплиментов
        is_female = await check_is_female_by_ai(user_name)
        
        # Отправляем "печатает" статус
        thread_id = getattr(update.message, "message_thread_id", None)
        action_kwargs = {"chat_id": update.effective_chat.id, "action": "typing"}
        if thread_id:
            action_kwargs["message_thread_id"] = thread_id
        await context.bot.send_chat_action(**action_kwargs)
        
        # Получаем ответ с медиа
        response_data = await generate_toxic_response_with_media(message_text, user_name, is_female, include_media=True)
        
        # Отправляем ответ с медиа
        await send_toxic_response(
            context=context,
            chat_id=update.effective_chat.id,
            text=response_data['text'],
            sticker=response_data['sticker'],
            gif=response_data['gif']
        )
        
        logger.info(f"[REPLY] Ответ отправлен пользователю {user_name}")
        
    except Exception as e:
        logger.error(f"[REPLY] Ошибка обработки ответа: {e}")


async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие новых участников (только если ранее не были в чате)."""
    try:
        if not update.message or not update.message.new_chat_members:
            return

        if str(update.effective_chat.id) != str(CHAT_ID):
            return

        for member in update.message.new_chat_members:
            if member.is_bot:
                continue
            user_id = member.id
            if user_id in known_users:
                continue

            known_users.add(user_id)
            save_known_users()

            user_name = member.full_name or member.username or "друг"
            welcome_text = get_random_welcome().replace("{user_name}", user_name)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_text,
            )
    except Exception as e:
        logger.error(f"[WELCOME] Ошибка приветствия: {e}")


async def handle_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шуточное сообщение, когда участник покидает чат."""
    try:
        if not update.message or not update.message.left_chat_member:
            return

        if str(update.effective_chat.id) != str(CHAT_ID):
            return

        left_member = update.message.left_chat_member
        if left_member.is_bot:
            return

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=get_random_leave_message(),
        )
    except Exception as e:
        logger.error(f"[LEAVE] Ошибка обработки выхода участника: {e}")


# ============== ОБРАБОТКА ГИФОК И СТИКЕРОВ ==============
async def handle_gifs_and_stickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка гифок и стикеров когда отвечают на сообщение бота"""
    try:
        if not update.message:
            return
        
        # Проверяем, что это reply (ответ на сообщение)
        if not update.message.reply_to_message:
            return
        
        # Проверяем, что ответ на сообщение бота
        replied_from = update.message.reply_to_message.from_user
        if not replied_from or not replied_from.is_bot:
            return
        
        # Проверяем, что это наш бот
        bot_info = await context.bot.get_me()
        if replied_from.id != bot_info.id:
            return
        
        user_name = update.message.from_user.full_name or update.message.from_user.username or "Пользователь"
        user_id = update.message.from_user.id
        
        # Определяем тип медиа
        is_gif = False
        is_sticker = False
        
        if update.message.sticker:
            is_sticker = True
            media_type = "стикер"
        elif update.message.document and update.message.document.mime_type == 'video/mp4':
            # Telegram GIFs usually have mime_type video/mp4
            is_gif = True
            media_type = "гифку"
        elif update.message.document and update.message.document.mime_type == 'image/gif':
            is_gif = True
            media_type = "гифку"
        else:
            return
        
        logger.info(f"[MEDIA] Пользователь {user_name} отправил {media_type} в ответ на сообщение бота")
        
        # Проверяем пол для комплиментов
        is_female = await check_is_female_by_ai(user_name)
        
        # Отправляем "печатает" статус
        thread_id = getattr(update.message, "message_thread_id", None)
        action_kwargs = {"chat_id": update.effective_chat.id, "action": "typing"}
        if thread_id:
            action_kwargs["message_thread_id"] = thread_id
        await context.bot.send_chat_action(**action_kwargs)
        
        # Генерируем случайный ответ
        import random
        
        responses_for_gift_sticker = [
            "Ого, крутая {media}! 🎉",
            "Неплохо! {media} принята! 👍",
            "Вау! {media} в тему! 🔥",
            "Класс! {media} заценил! 😎",
            "Отлично выглядит! {media} — огонь! 💯",
            "{media} — прямо в яблочко! 🍎",
            "Бро, {media} — это высший пилотаж! ✨",
            "{media} подняла настроение! 😊",
        ]
        
        compliment_responses = [
            "Ой, какая милая {media}! 💕 Ты такая классная! ✨",
            "Ух ты, {media}! Ты настоящая звезда! 🌟",
            "Вау, {media} — просто бомба! 💖 Ты как всегда на высоте!",
            "О, {media}! С тобой так весело! 🎀 Ты лучшая!",
            "Суперская {media}! 💝 Ты делаешь этот чат ярче!",
        ]
        
        if is_female:
            response_template = random.choice(compliment_responses)
        else:
            response_template = random.choice(responses_for_gift_sticker)
        
        response_text = response_template.format(media=media_type)
        
        # Отправляем ответ
        message_kwargs = {
            "chat_id": update.effective_chat.id,
            "text": response_text,
            "reply_to_message_id": update.message.message_id,
        }
        if thread_id:
            message_kwargs["message_thread_id"] = thread_id
        await context.bot.send_message(**message_kwargs)
        
        logger.info(f"[MEDIA] Ответ на {media_type} отправлен пользователю {user_name}")
        
    except Exception as e:
        logger.error(f"[MEDIA] Ошибка обработки гифки/стикера: {e}")


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

    try:
        # Проверяем, что это сообщение (не callback, не reaction и т.д.)
        if not update.message:
            return

        message = update.message
        user_id = message.from_user.id if message.from_user else None
        user_name = message.from_user.full_name or message.from_user.username or "Пользователь" if message.from_user else "Unknown"

        if not user_id:
            return

        # Игнорируем сообщения от ботов
        if message.from_user and message.from_user.is_bot:
            return

        # Триггер: слово «ремень» в тексте или подписи к фото
        belt_text = (message.text or message.caption or "").strip()
        if belt_text and message_has_belt_trigger(belt_text):
            await send_belt_photo(update, context)
            return

        # Если это reply на сообщение бота — отвечаем (текст или медиа)
        if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
            if message.sticker or (message.document and message.document.mime_type in ["video/mp4", "image/gif"]):
                await handle_gifs_and_stickers(update, context)
            else:
                await handle_replies_to_bot(update, context)
            return

        def is_plus_reply(text: str) -> bool:
            """Определяет ответ '+', допускает хвостовые точки/восклицания."""
            if not text:
                return False
            cleaned = text.strip()
            # Убираем хвостовую пунктуацию вроде "+...", "+!!", "+…"
            cleaned = re.sub(r"[.\u2026!?]+$", "", cleaned)
            cleaned = cleaned.strip()
            return cleaned in {"+", "++", "+1"}

        # Рейтинг: ответ "+" на сообщение пользователя
        if message.reply_to_message and message.reply_to_message.from_user and message.text:
            replied_from = message.reply_to_message.from_user
            if not replied_from.is_bot:
                plus_text = message.text.strip()
                if is_plus_reply(plus_text):
                    target_id = replied_from.id
                    target_name = replied_from.full_name or replied_from.username or "Unknown"
                    if target_id not in user_rating_stats:
                        user_rating_stats[target_id] = {
                            "name": target_name,
                            "messages": 0,
                            "photos": 0,
                            "likes": 0,
                            "replies": 0,
                            "bonus_points": 0,
                            "days_active": set(),
                        }
                    user_rating_stats[target_id]["likes"] += 1

                    # учитываем лайк на сообщение для сводки
                    replied_message_id = message.reply_to_message.message_id
                    if "message_owners" not in daily_stats:
                        daily_stats["message_owners"] = {}
                    daily_stats["message_owners"][replied_message_id] = {
                        "user_id": target_id,
                        "user_name": target_name,
                    }
                    if "message_likes" not in daily_stats:
                        daily_stats["message_likes"] = {}
                    prev = int(daily_stats["message_likes"].get(replied_message_id, 0) or 0)
                    daily_stats["message_likes"][replied_message_id] = prev + 1
                    save_daily_stats_local()
                    logger.info(f"[PLUS] + на сообщение {replied_message_id} для {target_name}")
                    try:
                        reply_kwargs = {
                            "chat_id": update.effective_chat.id,
                            "text": "✅ Балл засчитан! Продолжаем в том же духе 💪",
                            "reply_to_message_id": message.message_id,
                        }
                        thread_id = getattr(message, "message_thread_id", None)
                        if thread_id:
                            reply_kwargs["message_thread_id"] = thread_id
                        await context.bot.send_message(**reply_kwargs)
                    except Exception:
                        pass

        # Анонимные сообщения
        if user_id in user_anon_state:
            state = user_anon_state.get(user_id)
            if state == "waiting_for_text" and message.text:
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=f"🕵️ Анонимно:\n{message.text}",
                )
                user_anon_state.pop(user_id, None)
                try:
                    await message.delete()
                except Exception:
                    pass
                return
            if state == "waiting_for_photo" and message.photo:
                await context.bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=message.photo[-1].file_id,
                    caption="🕵️ Анонимное фото",
                )
                user_anon_state.pop(user_id, None)
                try:
                    await message.delete()
                except Exception:
                    pass
                return

        # Обновляем время последней активности
        user_last_active[user_id] = datetime.now(MOSCOW_TZ)

        chat_ok = str(update.effective_chat.id) == str(CHAT_ID)

        # Сначала учитываем сообщение в daily_stats и рейтинге (до «доброго утра» и return, иначе приветствие не попадало в сводку)
        message_type = "text"
        photo_info = None
        if message.photo:
            message_type = "photo"
            photo_info = {
                "user_id": user_id,
                "user_name": user_name,
                "message_id": message.message_id,
                "file_id": message.photo[-1].file_id if message.photo else None,
                "timestamp": datetime.now(MOSCOW_TZ).isoformat()
            }

        update_daily_stats(user_id, user_name, message_type, photo_info, message.message_id if message else None)

        if user_id not in user_rating_stats:
            user_rating_stats[user_id] = {
                "name": user_name,
                "messages": 0,
                "photos": 0,
                "likes": 0,
                "replies": 0,
                "bonus_points": 0,
                "days_active": set()
            }

        user_rating_stats[user_id]["messages"] += 1
        today_str = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
        user_rating_stats[user_id]["days_active"].add(today_str)

        if message_type == "photo":
            user_rating_stats[user_id]["photos"] += 1

        # Ответ на "доброе утро" от участников в чате (без @mention и reply)
        if message.text and chat_ok:
            text_lower = message.text.lower().strip()
            morning_phrases = ("доброе утро", "добрый день", "добрый вечер", "доброго утра", "доброго дня", "доброго вечера")
            morning_match = any(p in text_lower for p in morning_phrases) and len(text_lower) <= 80
            logger.info(f"[MORNING] chat_ok={chat_ok} text_len={len(text_lower)} morning_match={morning_match} text='{text_lower[:60]}'")
            if morning_match:
                logger.info(f"[MORNING] Найдено приветствие от {user_name}: '{text_lower[:50]}'")
                try:
                    is_female = False
                    try:
                        is_female = await check_is_female_by_ai(user_name)
                    except Exception as gender_err:
                        logger.warning(f"[MORNING] Не удалось определить пол для {user_name}: {gender_err}")
                    reply_text = get_random_good_morning_flirt() if is_female else get_random_good_morning()
                    await message.reply_text(reply_text)
                    await send_random_sticker_or_gif(context.bot, update.effective_chat.id, chance=0.45)
                    logger.info(f"[MORNING] Ответ на приветствие от {user_name}")
                    return
                except Exception as e:
                    logger.error(f"[MORNING] Ошибка отправки: {e}")

        # Ежедневная сводка по первому сообщению в чате в окне 23:50–23:59
        if chat_ok:
            now = datetime.now(MOSCOW_TZ)
            today_date = now.strftime("%Y-%m-%d")
            in_summary_window = (now.hour == 23 and now.minute >= 50)
            ref_date = today_date
            if in_summary_window and daily_stats.get("summary_last_sent") != ref_date:
                logger.info(f"[SUMMARY] Сообщение в чате в окне сводки — отправляем ежедневную сводку за {ref_date}")
                try:
                    await send_daily_summary(ref_date=ref_date)
                except Exception as e:
                    logger.error(f"[SUMMARY] Ошибка отправки сводки по триггеру чата: {e}")

        # Обработка ночного режима (22:00 - 06:00)
        now = datetime.now(MOSCOW_TZ)
        current_hour = now.hour

        if current_hour >= 22 or current_hour < 6:
            # Ночной режим активен
            if user_id not in user_night_messages:
                user_night_messages[user_id] = 0

            user_night_messages[user_id] += 1

            # Отправляем предупреждение после 10 сообщений
            if user_night_messages[user_id] == 10 and user_id not in user_night_warning_sent:
                warning = random.choice(NIGHT_WARNINGS)
                try:
                    await message.reply_text(warning)
                    user_night_warning_sent[user_id] = True
                    logger.info(f"[NIGHT] Предупреждение отправлено пользователю {user_name} (ID: {user_id})")
                except Exception as e:
                    logger.error(f"[NIGHT] Ошибка отправки предупреждения: {e}")
        else:
            # Дневное время - сбрасываем счётчик ночных сообщений
            if user_id in user_night_messages:
                del user_night_messages[user_id]
            if user_id in user_night_warning_sent:
                del user_night_warning_sent[user_id]

    except Exception as e:
        logger.error(f"[HANDLE_ALL] Ошибка обработки сообщения: {e}", exc_info=True)


async def slots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /slots — список мероприятий с открытой регистрацией."""
    try:
        target_thread_id = EVENTS_TOPIC_ID if EVENTS_TOPIC_ID else getattr(update.message, "message_thread_id", None)
        action_kwargs = {"chat_id": update.effective_chat.id, "action": "typing"}
        if target_thread_id:
            action_kwargs["message_thread_id"] = target_thread_id
        await context.bot.send_chat_action(**action_kwargs)

        status_kwargs = {"chat_id": update.effective_chat.id, "text": "🔎 Ищу мероприятия и слоты..."}
        if target_thread_id:
            status_kwargs["message_thread_id"] = target_thread_id
        status_message = await context.bot.send_message(**status_kwargs)

        events = await get_all_events()
        parser_errors = []
        try:
            parser_errors = get_last_events_errors()
        except Exception:
            parser_errors = []
        # Дополнительная фильтрация по регионам (защита от зарубежных и пустых городов)
        def is_allowed_city(city_text: str, source_text: str, title_text: str) -> bool:
            if not city_text:
                city_lower = ""
            else:
                city_lower = city_text.lower()
            title_lower = (title_text or "").lower()
            if city_lower in ("россия", "russia") or not city_lower:
                russian_sources = {
                    "russiarunning", "марафонец", "пробег", "беговое сообщество",
                    "лига героев", "забег.рф", "s10.run", "забег обещаний",
                    "бегом по золотому кольцу", "академия марафона",
                    "кразмарафон", "orgeo.ru", "pushkin run", "golden ring ultra",
                    "пробег трейлы", "пробег календарь",
                    "open band trails", "чулково trail",
                }
                return (source_text or "").lower() in russian_sources
            if any(k in title_lower for k in ["забег.рф", "забег рф", "чулков", "лига героев", "hero league"]):
                if not city_lower or city_lower in ("россия", "russia"):
                    return True
            moscow_region = [
                "москва", "moscow", "московск", "подмосков", "подмосковье",
                "московской", "химки", "мытищи", "королев", "балашиха",
                "красногорск", "одинцово", "люберцы", "электросталь",
                "коломна", "серпухов", "подольск", "домодедово",
                "зеленоград", "раменск", "жуковск", "бронниц",
                "чулков", "ильинск", "быково", "лыткарино",
                "дзержинск", "вельяминово", "яхрома",
            ]
            spb_region = [
                "санкт-петербург", "saint petersburg", "st. petersburg",
                "петербург", "питер", "спб", "spb",
                "ленинградск", "ленинградской", "ленинградская",
                "гатчина", "выборг", "всеволожск", "тосно",
            ]
            izhevsk_region = [
                "ижевск", "izhevsk",
                "удмурт", "удмуртия", "udmurt", "udmurtia",
            ]
            central_region = [
                "твер", "ярослав", "костром", "иванов", "владимир", "калуг", "тул", "рязан",
                "смолен", "брянск", "орел", "орёл", "курск", "белгород", "воронеж", "липецк", "тамбов",
            ]
            return (
                any(x in city_lower for x in moscow_region)
                or any(x in city_lower for x in spb_region)
                or any(x in city_lower for x in izhevsk_region)
                or any(x in city_lower for x in central_region)
            )
        events = [e for e in events if is_allowed_city(e.get("city", ""), e.get("source", ""), e.get("title", ""))]
        if not events:
            extra = ""
            if parser_errors:
                extra = "\n\n⚠️ Ошибки парсинга источников:\n" + "\n".join(f"• {e}" for e in parser_errors)
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text="Сейчас нет доступных слотов на мероприятия." + extra,
            )
            return

        lines = ["🏃 <b>Открытые регистрации на забеги:</b>\n"]
        for idx, event in enumerate(events, start=1):
            title = html_escape(event.get("title", "Без названия"))
            date = html_escape(event.get("date", "Дата не указана"))
            city = html_escape(event.get("city", "Город не указан"))
            distances = html_escape(event.get("distances", ""))
            price = html_escape(event.get("price", "")) if event.get("price") else ""
            url = event.get("url") or event.get("link") or ""
            url = html_escape(url) if url else ""

            lines.append(f"{idx}. <b>{title}</b>")
            lines.append(f"📅 {date} | 📍 {city}")
            if distances:
                lines.append(f"🏁 Дистанции: {distances}")
            if price:
                lines.append(f"💰 Стоимость: {price}")
            if url:
                lines.append(f'🔗 <a href="{url}">Страница регистрации</a>')
            lines.append("")

        # Telegram лимит 4096 символов — отправляем частями
        if parser_errors:
            lines.append("⚠️ Ошибки парсинга источников:")
            lines.extend([f"• {e}" for e in parser_errors])
            lines.append("")
        full_text = "\n".join(lines).strip()
        max_len = 3800
        chunks = []
        while full_text:
            if len(full_text) <= max_len:
                chunks.append(full_text)
                break
            split_at = full_text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(full_text[:split_at].strip())
            full_text = full_text[split_at:].strip()

        # Первую часть редактируем в статусное сообщение, остальные — отдельными
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=chunks[0],
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        for chunk in chunks[1:]:
            extra_kwargs = {"chat_id": update.effective_chat.id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
            if target_thread_id:
                extra_kwargs["message_thread_id"] = target_thread_id
            await context.bot.send_message(**extra_kwargs)

    except Exception as e:
        logger.error(f"[SLOTS] Ошибка команды slots: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при получении списка мероприятий. Попробуйте позже.",
        )


def _parse_watch_region(value: str) -> str | None:
    v = (value or "").strip().lower()
    aliases = {
        "any": "any", "all": "any", "все": "any",
        "msk": "msk", "moscow": "msk", "москва": "msk",
        "spb": "spb", "питер": "spb", "спб": "spb",
        "central": "central", "центр": "central", "цр": "central", "central_ru": "central",
    }
    return aliases.get(v)


def _parse_watch_kind(value: str) -> str | None:
    v = (value or "").strip().lower()
    aliases = {
        "any": "any", "all": "any", "все": "any",
        "trail": "trail", "трейл": "trail",
        "road": "road", "шоссе": "road", "асфальт": "road",
    }
    return aliases.get(v)


def _parse_watch_distance(value: str) -> str | None:
    v = (value or "").strip().lower().replace("км", "").replace("km", "")
    aliases = {
        "any": "any", "all": "any", "все": "any",
        "5": "5", "5k": "5",
        "10": "10", "10k": "10",
        "21": "21", "21.1": "21", "21k": "21", "hm": "21", "half": "21",
        "42": "42", "42.2": "42", "42k": "42", "marathon": "42",
    }
    return aliases.get(v)


def _watch_region_match(event: dict, region: str) -> bool:
    if region == "any":
        return True
    city = (event.get("city", "") or "").lower()
    text = f"{city} {(event.get('title', '') or '').lower()}"
    msk = ["москв", "подмосков", "moscow", "химки", "мытищи", "королев", "балаших", "подольск"]
    spb = ["санкт", "петербург", "спб", "питер", "leningrad", "ленинград"]
    central = [
        "твер", "ярослав", "костром", "иванов", "владимир", "калуг", "тул", "рязан",
        "смолен", "брянск", "орел", "орёл", "курск", "белгород", "воронеж", "липецк", "тамбов",
    ]
    if region == "msk":
        return any(x in text for x in msk)
    if region == "spb":
        return any(x in text for x in spb)
    if region == "central":
        return any(x in text for x in central)
    return False


def _watch_kind_match(event: dict, kind: str) -> bool:
    if kind == "any":
        return True
    text = f"{event.get('title', '')} {event.get('source', '')} {event.get('distances', '')}".lower()
    is_trail = ("trail" in text) or ("трейл" in text)
    return is_trail if kind == "trail" else not is_trail


def _extract_distance_tags(event: dict) -> set:
    text = f"{event.get('title', '')} {event.get('distances', '')}".lower().replace(",", ".")
    tags = set()
    if re.search(r"(^|[^0-9])5(\.0)?\s*(км|km|к|k)\b", text):
        tags.add("5")
    if re.search(r"(^|[^0-9])10(\.0)?\s*(км|km|к|k)\b", text):
        tags.add("10")
    if re.search(r"(21(\.1)?)\s*(км|km|к|k)\b|полумара", text):
        tags.add("21")
    if re.search(r"(42(\.2)?)\s*(км|km|к|k)\b|марафон", text):
        tags.add("42")
    return tags


def _watch_distance_match(event: dict, distance: str) -> bool:
    if distance == "any":
        return True
    tags = _extract_distance_tags(event)
    return distance in tags


def _event_hash_for_watch(event: dict) -> str:
    title = (event.get("title", "") or "").strip().lower()
    date = (event.get("date", "") or "").strip().lower()
    city = (event.get("city", "") or "").strip().lower()
    source = (event.get("source", "") or "").strip().lower()
    return f"{title}|{date}|{city}|{source}"


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /watch — создать подписку на интересные забеги."""
    args = context.args or []
    if not args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "Подписка на забеги:\n"
                "Формат: /watch <регион> <тип> <дистанция>\n\n"
                "Регионы: msk | spb | central | any\n"
                "Тип: trail | road | any\n"
                "Дистанция: 5 | 10 | 21 | 42 | any\n\n"
                "Пример: /watch central trail 21"
            ),
        )
        return

    region = _parse_watch_region(args[0]) if len(args) >= 1 else "any"
    kind = _parse_watch_kind(args[1]) if len(args) >= 2 else "any"
    distance = _parse_watch_distance(args[2]) if len(args) >= 3 else "any"
    if region is None or kind is None or distance is None:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не понял параметры. Пример: /watch central trail 21",
        )
        return

    user_id = update.effective_user.id
    subs = watch_subscriptions.setdefault(user_id, [])
    # Ищем совпадающую подписку, чтобы не плодить дубликаты.
    for sub in subs:
        if sub.get("region") == region and sub.get("kind") == kind and sub.get("distance") == distance:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Такая подписка уже есть.")
            return

    next_id = 1 + max((int(s.get("id", 0)) for s in subs), default=0)
    subs.append({
        "id": next_id,
        "region": region,
        "kind": kind,
        "distance": distance,
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
    })
    save_watch_subscriptions()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ Подписка #{next_id} создана: регион={region}, тип={kind}, дистанция={distance}",
    )


async def watch_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /watch_list — список подписок пользователя."""
    user_id = update.effective_user.id
    subs = watch_subscriptions.get(user_id, [])
    if not subs:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="У тебя пока нет подписок. Добавь: /watch central trail 21",
        )
        return
    lines = ["🔔 Твои подписки:"]
    for sub in subs:
        lines.append(
            f"#{sub.get('id')} — регион={sub.get('region')}, тип={sub.get('kind')}, дистанция={sub.get('distance')}"
        )
    await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(lines))


async def watch_del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /watch_del <id> — удаление подписки."""
    user_id = update.effective_user.id
    subs = watch_subscriptions.get(user_id, [])
    if not subs:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Удалять нечего: подписок нет.")
        return
    if not context.args or not str(context.args[0]).isdigit():
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Используй: /watch_del <id>")
        return
    sub_id = int(context.args[0])
    new_subs = [s for s in subs if int(s.get("id", 0)) != sub_id]
    if len(new_subs) == len(subs):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Подписка #{sub_id} не найдена.")
        return
    if new_subs:
        watch_subscriptions[user_id] = new_subs
    else:
        watch_subscriptions.pop(user_id, None)
    save_watch_subscriptions()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🗑️ Подписка #{sub_id} удалена.")


async def _dispatch_watch_notifications(events: list):
    """Отправляет уведомления по подпискам на новые события."""
    if not watch_subscriptions:
        return
    notifications_sent = 0
    for user_id, subs in list(watch_subscriptions.items()):
        sent_to_user = 0
        for sub in subs:
            for event in events:
                if not _watch_region_match(event, sub.get("region", "any")):
                    continue
                if not _watch_kind_match(event, sub.get("kind", "any")):
                    continue
                if not _watch_distance_match(event, sub.get("distance", "any")):
                    continue

                event_hash = _event_hash_for_watch(event)
                notify_key = f"{user_id}:{sub.get('id')}:{event_hash}"
                if notify_key in watch_notified_ids:
                    continue

                title = html_escape(event.get("title", "Без названия"))
                date = html_escape(event.get("date", "Дата не указана"))
                city = html_escape(event.get("city", "Город не указан"))
                distances = html_escape(event.get("distances", ""))
                url = event.get("url") or event.get("link") or ""
                url = html_escape(url) if url else ""
                text = (
                    f"🔔 <b>Новый забег по твоей подписке #{sub.get('id')}</b>\n"
                    f"🏁 <b>{title}</b>\n"
                    f"📅 {date}\n"
                    f"📍 {city}\n"
                )
                if distances:
                    text += f"📏 {distances}\n"
                if url:
                    text += f'🔗 <a href="{url}">Страница регистрации</a>'
                try:
                    await application.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
                    watch_notified_ids.add(notify_key)
                    sent_to_user += 1
                    notifications_sent += 1
                except Exception as e:
                    logger.warning(f"[WATCH] Не удалось отправить уведомление user_id={user_id}: {e}")
                    # Не помечаем как отправленное, чтобы попробовать снова позже.
                if sent_to_user >= 3:
                    break
            if sent_to_user >= 3:
                break
    if notifications_sent:
        save_watch_subscriptions()
        logger.info(f"[WATCH] Отправлено уведомлений: {notifications_sent}")


async def watch_scheduler_task():
    """Фоновая проверка забегов для персональных подписок."""
    global bot_running
    try:
        check_interval = int(os.environ.get("WATCH_CHECK_INTERVAL_SEC", "1800"))
        if check_interval < 300:
            check_interval = 300
    except Exception:
        check_interval = 1800

    logger.info(f"[WATCH] Планировщик подписок запущен, интервал {check_interval} сек")
    while bot_running:
        try:
            if watch_subscriptions:
                events = await get_all_events()
                await _dispatch_watch_notifications(events)
        except Exception as e:
            logger.error(f"[WATCH] Ошибка планировщика подписок: {e}", exc_info=True)
        await asyncio.sleep(check_interval)


BOT_HELP_TEXT = (
    "**Бот для бегового чата**\n\n"
    "**📌 Основные команды:**\n"
    "• /start — список команд\n"
    "• /getid — показать ID чата\n"
    "• /stop — остановить бота (только админы)\n\n"
    "**🌅 Утреннее приветствие:**\n"
    "• /morning — отправить утреннее приветствие сейчас\n"
    "• /stopmorning — удалить утреннее сообщение\n\n"
    "**😄 Развлечения:**\n"
    "• /remen — картинка с ремнём (или напиши «ремень» в чат)\n"
    "• /antiremen — получить комплименты\n"
    "• /roast — подколоть кого-то в чате\n"
    "• /flirt — отправить комплимент девушкам\n"
    "• /mam — \"Не зли маму...\"\n"
    "• /joke — случайный анекдот про бег\n"
    "• /facts — интересные факты о беге\n"
    "• /motivation — мотивация на тренировку\n\n"
    "**📊 Статистика и рейтинг:**\n"
    "• /summary — сводка за сегодня (можно вызывать много раз!)\n"
    "• /rating — топ-10 участников по рейтингу\n"
    "• /likes — рейтинг по лайкам\n"
    "• /levels — участники по уровням\n"
    "• /passport — паспорт \\(карточка с фото\\); заполнить: имя \\| город \\| личники\n"
    "• /passport\\_photo — добавить фото в паспорт \\(отправь фото или ответь на фото\\)\n"
    "• /passport\\_edit — \\(админ\\) ответь на сообщение и введи данные для правки паспорта\n"
    "• /passport\\_delete — удалить свой паспорт; админ: ответь на сообщение и /passport\\_delete\n"
    "• /running — рейтинг бегунов за месяц\n"
    "• /weekly — еженедельная сводка (можно вызывать много раз!)\n"
    "• /monthly — итоги месяца (можно вызывать много раз!)\n\n"
    "**🏆 Челленджи:**\n"
    "• /challenge — статус текущего челленджа\n"
    "• /challenge\\_start weekly|monthly — запустить новый челлендж\n"
    "• /challenge join — присоединиться к челленджу\n"
    "• /challenge done — отметить выполнение цели\n"
    "• /votechallenges — запустить голосование за челлендж\n"
    "• /vote — проголосовать в челлендже\n"
    "• /votestatus — статус голосования\n\n"
    "**🎂 Дни рождения:**\n"
    "• /birthday DD.MM — указать свою дату рождения\n"
    "• /add\\_birthday @никнейм DD.MM — добавить день рождения\n"
    "• /del\\_birthday @никнейм DD.MM — удалить день рождения\n"
    "• /list\\_birthdays — показать все дни рождения\n\n"
    "**🔔 Анонимные сообщения:**\n"
    "• /anon текст — анонимное сообщение\n"
    "• /anonphoto — анонимная отправка фото\n\n"
    "**📱 Garmin интеграция:**\n"
    "• /garmin email пароль — привязать аккаунт Garmin Connect\n"
    "• /garmin\\_stop — отключить аккаунт Garmin\n"
    "• /garmin\\_list — список пользователей Garmin\n\n"
    "**🏃 Слоты на забеги:**\n"
    "• /slots — показать открытые регистрации на беговые мероприятия\n\n"
    "**🔔 Подписки на забеги:**\n"
    "• /watch регион тип дистанция — подписка (пример: /watch central trail 21)\n"
    "• /watch\\_list — твои подписки\n"
    "• /watch\\_del ID — удалить подписку\n\n"
    "**💡 Полезное:**\n"
    "• /plan — план подготовки к забегу (выбор дистанции и целевого времени)\n"
    "• /advice — совет по бегу из интернета\n"
    "• /music — музыка дня\n"
    "• /deals — скидки на экипировку\n"
    "• Ежедневный совет — в 12:00\n"
)


def get_target_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Получает имя цели из reply или аргументов."""
    if update.message and update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
        return target_user.full_name or target_user.username or "друг"
    if context.args:
        return " ".join(context.args)
    return update.message.from_user.full_name if update.message and update.message.from_user else "друг"


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — проверка, что бот жив."""
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=BOT_HELP_TEXT,
        parse_mode="Markdown",
    )


async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответ на неизвестную команду."""
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Команда не распознана. Напишите /start для списка команд.",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок."""
    logger.error("Unhandled exception", exc_info=context.error)


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stop — остановить бота (для админов)."""
    global bot_running
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id if update.message else None
    if user_id and not await is_user_admin(user_id, chat_id, context.bot):
        await context.bot.send_message(chat_id=chat_id, text="❌ Эта команда только для администраторов!")
        return
    bot_running = False
    await context.bot.send_message(chat_id=chat_id, text="⛔ Бот остановлен по команде администратора.")


async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /getid — показать ID чата и топика."""
    thread_id = getattr(update.message, "message_thread_id", None) if update.message else None
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"ID чата: `{update.effective_chat.id}`\nID топика: `{thread_id}`",
        parse_mode="Markdown",
    )


async def morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /morning — отправить утреннее приветствие."""
    await send_morning_greeting()


async def stopmorning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stopmorning — удалить утреннее сообщение."""
    global morning_message_id
    if not morning_message_id:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Утреннего сообщения нет.")
        return
    try:
        await context.bot.delete_message(chat_id=CHAT_ID, message_id=morning_message_id)
        morning_message_id = None
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Утреннее сообщение удалено.")
    except Exception as e:
        logger.error(f"[MORNING] Ошибка удаления сообщения: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Не удалось удалить сообщение.")


async def remen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /remen — картинка с ремнём."""
    await send_belt_photo(update, context)


async def antiremen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /antiremen — получить комплимент."""
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=get_random_compliment(),
    )
    await send_random_sticker_or_gif(context.bot, update.effective_chat.id, chance=0.4)


async def roast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /roast — подколоть кого-то."""
    target = get_target_name(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🎯 {target}, {get_random_roast()}",
    )
    await send_random_sticker_or_gif(context.bot, update.effective_chat.id, chance=0.4)


async def flirt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /flirt — комплименты девушкам."""
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=get_random_flirt(),
    )
    await send_random_sticker_or_gif(context.bot, update.effective_chat.id, chance=0.45)


async def mam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /mam — 'Не зли маму...'"""
    if os.path.exists(MAM_PHOTO_PATH):
        with open(MAM_PHOTO_PATH, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption="Не зли маму... 😅",
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не зли маму... 😅",
        )


async def joke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /joke — шутка."""
    user_name = update.message.from_user.full_name if update.message else "друг"
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=random.choice(JOKE_RESPONSES).format(user_name=user_name),
    )


async def motivation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /motivation — мотивация."""
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💪 {get_random_motivation()}",
    )
    await send_random_sticker_or_gif(context.bot, update.effective_chat.id, chance=0.45)


async def add_sticker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админов: ответь на стикер и напиши /add_sticker — стикер попадёт в пул ответов бота."""
    global bot_sticker_ids
    if not update.message or not update.message.from_user:
        return
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id
    if not await is_user_admin(user_id, chat_id, context.bot):
        await context.bot.send_message(chat_id=chat_id, text="❌ Только для администраторов.")
        return
    reply = update.message.reply_to_message
    if not reply or not reply.sticker:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Ответь на сообщение со стикером и напиши /add_sticker — стикер будет использоваться в ответах бота.",
        )
        return
    fid = reply.sticker.file_id
    if fid not in bot_sticker_ids:
        bot_sticker_ids.append(fid)
        save_bot_stickers()
    await context.bot.send_message(chat_id=chat_id, text=f"✅ Стикер добавлен в пул (всего {len(bot_sticker_ids)}).")


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /summary — ежедневная сводка."""
    await send_daily_summary(force=True)


async def rating_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /rating — топ-10 по рейтингу."""
    top_rated = await get_top_rated_users()
    if not top_rated:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Рейтинг пока пуст.")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["⭐ *Топ-10 рейтинга:*"]
    for i, user in enumerate(top_rated):
        name = escape_markdown(user["name"])
        lines.append(f"{medals[i]} {name} — {user['points']} очков")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


async def likes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /likes — рейтинг по лайкам."""
    if not user_rating_stats:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Лайков пока нет.")
        return
    sorted_users = sorted(user_rating_stats.items(), key=lambda x: x[1].get("likes", 0), reverse=True)[:10]
    lines = ["❤️ *Топ-10 по лайкам:*"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, (user_id, stats) in enumerate(sorted_users):
        name = escape_markdown(stats.get("name", "Unknown"))
        likes = stats.get("likes", 0)
        lines.append(f"{medals[i]} {name} — {likes}")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /levels — участники по уровням."""
    if not user_rating_stats:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Данных по уровням пока нет.")
        return
    levels_summary = {"Легенда чата": [], "Лидер": [], "Активный": [], "Новичок": []}
    for user_id, stats in user_rating_stats.items():
        level = get_user_level(user_id)
        points = calculate_user_rating(user_id)
        levels_summary[level].append((stats.get("name", "Unknown"), points))
    lines = ["🏅 *Уровни участников:*"]
    for level in ["Легенда чата", "Лидер", "Активный", "Новичок"]:
        users = sorted(levels_summary[level], key=lambda x: x[1], reverse=True)
        if not users:
            continue
        level_emoji = LEVEL_EMOJIS.get(level, "")
        lines.append(f"{level_emoji} *{escape_markdown(level)}* ({len(users)}):")
        for name, points in users[:5]:
            lines.append(f"   • {escape_markdown(name)} — {points} очков")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


def build_passport_text(target_user_id: int, target_name: str) -> str:
    """Формирует текст паспорта участника (HTML)."""
    level = get_user_level(target_user_id)
    level_emoji = LEVEL_EMOJIS.get(level, "🌱")
    details = get_rating_details(target_user_id)
    points = details["total_points"]
    lines = [
        "🪪 <b>ПАСПОРТ УЧАСТНИКА</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 <b>{html_escape(target_name)}</b>",
        f"{level_emoji} Уровень: <b>{html_escape(level)}</b>",
        f"⭐ Очки: <b>{points}</b>",
        "",
        "📊 <b>Активность:</b>",
        f"   • Сообщений: {details['messages']}",
        f"   • Фото: {details['photos']}",
        f"   • Лайков получено: {details['likes']}",
        f"   • Ответов на сообщения: {details['replies']}",
    ]
    if target_user_id in user_birthdays:
        bd = user_birthdays[target_user_id].get("birthday", "")
        if bd:
            lines.append("")
            lines.append(f"🎂 День рождения: <b>{html_escape(bd)}</b>")
    if target_user_id in garmin_users:
        lines.append("")
        lines.append("⌚ Garmin: <b>подключён</b>")
    if target_user_id in user_running_stats:
        run = user_running_stats[target_user_id]
        km = run.get("distance", 0) / 1000
        acts = run.get("activities", 0)
        lines.append("")
        lines.append(f"🏃 Бег (в боте): <b>{km:.1f}</b> км, <b>{acts}</b> тренировок")
    if target_user_id in user_passport_data:
        ext = user_passport_data[target_user_id]
        stored_name = ext.get("name", "").strip()
        if stored_name:
            target_name = stored_name
        lines[2] = f"👤 <b>{html_escape(target_name)}</b>"
        city = ext.get("city", "").strip()
        if city:
            lines.append("")
            lines.append(f"📍 Город: <b>{html_escape(city)}</b>")
        pbs = []
        for key, label in [("pb_5k", "5 км"), ("pb_10k", "10 км"), ("pb_21", "21.1 км"), ("pb_42", "42.2 км")]:
            val = ext.get(key, "").strip()
            if val:
                pbs.append(f"{label}: {html_escape(val)}")
        if pbs:
            lines.append("")
            lines.append("🏁 <b>Личник по бегу:</b>")
            for s in pbs:
                lines.append(f"   • {s}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_passport_card_caption(target_user_id: int, target_name: str) -> str:
    """Короткая подпись под фото паспорта (карточка, до 1024 символов)."""
    level = get_user_level(target_user_id)
    level_emoji = LEVEL_EMOJIS.get(level, "🌱")
    details = get_rating_details(target_user_id)
    points = details["total_points"]
    if target_user_id in user_passport_data:
        ext = user_passport_data[target_user_id]
        if ext.get("name", "").strip():
            target_name = ext["name"].strip()
        city = ext.get("city", "").strip()
    else:
        city = ""
    lines = [
        "🪪 <b>ПАСПОРТ УЧАСТНИКА</b>",
        "━━━━━━━━━━━━━━━━",
        f"👤 <b>{html_escape(target_name)}</b>",
        f"{level_emoji} <b>{html_escape(level)}</b> • {points} очков",
    ]
    if city:
        lines.append(f"📍 {html_escape(city)}")
    if target_user_id in user_birthdays and user_birthdays[target_user_id].get("birthday"):
        lines.append(f"🎂 {html_escape(user_birthdays[target_user_id]['birthday'])}")
    if target_user_id in user_passport_data:
        ext = user_passport_data[target_user_id]
        pbs = []
        for key, label in [("pb_5k", "5 км"), ("pb_10k", "10 км"), ("pb_21", "21.1 км"), ("pb_42", "42.2 км")]:
            val = ext.get(key, "").strip()
            if val:
                pbs.append(f"{label} {html_escape(val)}")
        if pbs:
            lines.append("🏁 " + " • ".join(pbs))
    if target_user_id in garmin_users:
        lines.append("⌚ Garmin")
    lines.append("━━━━━━━━━━━━━━━━")
    text = "\n".join(lines)
    return text[:1020] + "…" if len(text) > 1024 else text


# Ключи личников в user_passport_data
PB_KEYS = {"5к": "pb_5k", "5км": "pb_5k", "5k": "pb_5k", "10к": "pb_10k", "10км": "pb_10k", "10k": "pb_10k", "21.1": "pb_21", "21": "pb_21", "полумарафон": "pb_21", "42.2": "pb_42", "42": "pb_42", "марафон": "pb_42"}


def _parse_passport_pbs(rest: str) -> dict:
    """Парсит строку личников вида '5к 22:30 10к 45:00' в словарь pb_5k=22:30, ..."""
    result = {}
    tokens = rest.strip().split()
    i = 0
    while i < len(tokens):
        key = PB_KEYS.get(tokens[i].lower())
        if key and i + 1 < len(tokens):
            result[key] = tokens[i + 1][:30]
            i += 2
        else:
            i += 1
    return result


async def passport_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /passport — показать паспорт или заполнить: имя | город | личники (5к 22:30 10к 45:00)."""
    global user_passport_data
    if not update.message:
        return
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id
    # Берём данные из аргументов или из текста сообщения (после команды)
    raw_args = " ".join(context.args or []).strip()
    raw_text = (update.message.text or "").strip()
    if raw_text and raw_text.startswith("/"):
        first_word = raw_text.split(None, 1)[0] if raw_text else ""
        raw_text = raw_text[len(first_word):].strip() if first_word else raw_text
    raw = raw_args or raw_text

    # Разделитель — «|» с пробелами или без: Имя | Город или Имя|Город
    if raw and "|" in raw:
        parts = [p.strip() for p in raw.split("|", 2)]
        if len(parts) < 2:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Формат: <code>/passport Имя | Город | 5к 22:30</code>\n"
                "Или: <code>/passport Имя | Город</code> (без личников)",
                parse_mode="HTML",
            )
            return
        name = (parts[0] or "")[:80]
        city = (parts[1] or "")[:100] if len(parts) > 1 else ""
        if user_id not in user_passport_data:
            user_passport_data[user_id] = {}
        if name:
            user_passport_data[user_id]["name"] = name
        if city:
            user_passport_data[user_id]["city"] = city
        if len(parts) > 2 and parts[2]:
            pbs = _parse_passport_pbs(parts[2])
            for k, v in pbs.items():
                user_passport_data[user_id][k] = v
        save_passport_data()
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Паспорт обновлён: {html_escape(name or '—')} | {html_escape(city or '—')}",
            parse_mode="HTML",
        )

    target_user_id = user_id
    target_name = update.message.from_user.full_name or (f"@{update.message.from_user.username}" if update.message.from_user.username else "Участник")
    if update.message.reply_to_message and update.message.reply_to_message.from_user and not raw:
        u = update.message.reply_to_message.from_user
        target_user_id = u.id
        target_name = u.full_name or (f"@{u.username}" if u.username else "Участник")
    if target_user_id is None:
        await context.bot.send_message(chat_id=chat_id, text="Не удалось определить участника.")
        return
    text = build_passport_text(target_user_id, target_name)
    pass_data = user_passport_data.get(target_user_id) or {}
    photo_file_id = (pass_data.get("photo_file_id") or "").strip() if isinstance(pass_data.get("photo_file_id"), str) else ""
    if photo_file_id:
        caption = text
        if len(caption) > 1024:
            caption = caption[:1020] + "…"
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_file_id,
                caption=caption,
                parse_mode="HTML",
            )
            return
        except Exception as e:
            logger.warning(f"[PASSPORT] send_photo failed (file_id=%s...): %s", photo_file_id[:20] if photo_file_id else "", e)
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        return
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def _save_passport_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, photo) -> bool:
    """Сохраняет фото в паспорт пользователя. Возвращает True если успешно."""
    global user_passport_data
    if not update.message or not update.message.from_user or not photo:
        return False
    user_id = update.message.from_user.id
    if user_id not in user_passport_data:
        user_passport_data[user_id] = {}
    user_passport_data[user_id]["photo_file_id"] = photo.file_id
    save_passport_data()
    return True


async def passport_photo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить фото в паспорт: отправь фото с подписью /passport_photo или ответь /passport_photo на фото."""
    if not update.message or not update.message.from_user:
        return
    user_id = update.message.from_user.id
    photo = None
    if update.message.photo:
        photo = update.message.photo[-1]
    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        photo = update.message.reply_to_message.photo[-1]
    if not photo:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Отправь фото с подписью <code>/passport_photo</code> или ответь этой командой на любое фото.",
            parse_mode="HTML",
        )
        return
    if await _save_passport_photo(update, context, photo):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Фото добавлено в паспорт. Теперь /passport будет показывать карточку с фото.",
            parse_mode="HTML",
        )


async def passport_photo_from_caption_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото с подписью /passport_photo (команда в подписи не вызывает CommandHandler)."""
    if not update.message or not update.message.photo or not update.message.from_user:
        return
    caption = (update.message.caption or "").strip()
    if "/passport_photo" not in caption and "passport_photo" not in caption.lower():
        return
    photo = update.message.photo[-1]
    if await _save_passport_photo(update, context, photo):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Фото добавлено в паспорт. Теперь /passport будет показывать карточку с фото.",
            parse_mode="HTML",
        )


async def passport_edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админов: редактировать паспорт участника. Ответь на сообщение участника и напиши /passport_edit Имя | Город | 5к 22:30"""
    global user_passport_data
    if not update.message or not update.message.from_user:
        return
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id
    if not await is_user_admin(user_id, chat_id, context.bot):
        await context.bot.send_message(chat_id=chat_id, text="❌ Только для администраторов.")
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Ответь на сообщение участника и напиши:\n<code>/passport_edit Имя | Город | 5к 22:30</code>",
            parse_mode="HTML",
        )
        return
    target_user_id = update.message.reply_to_message.from_user.id
    target_name = update.message.reply_to_message.from_user.full_name or "Участник"
    raw_args = " ".join(context.args or []).strip()
    raw_text = (update.message.text or "").strip()
    if raw_text.startswith("/"):
        first_word = raw_text.split(None, 1)[0] if raw_text else ""
        raw_text = raw_text[len(first_word):].strip() if first_word else raw_text
    raw = raw_args or raw_text
    if not raw or "|" not in raw:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Укажи новые данные: <code>/passport_edit Имя | Город | 5к 22:30 10к 45:00</code>",
            parse_mode="HTML",
        )
        return
    parts = [p.strip() for p in raw.split("|", 2)]
    if len(parts) < 2:
        await context.bot.send_message(chat_id=chat_id, text="Нужны минимум имя и город через |")
        return
    name = (parts[0] or "")[:80]
    city = (parts[1] or "")[:100] if len(parts) > 1 else ""
    if target_user_id not in user_passport_data:
        user_passport_data[target_user_id] = {}
    if name:
        user_passport_data[target_user_id]["name"] = name
    if city:
        user_passport_data[target_user_id]["city"] = city
    if len(parts) > 2 and parts[2]:
        pbs = _parse_passport_pbs(parts[2])
        for k, v in pbs.items():
            user_passport_data[target_user_id][k] = v
    save_passport_data()
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Паспорт участника <b>{html_escape(target_name)}</b> обновлён: {html_escape(name or '—')} | {html_escape(city or '—')}",
        parse_mode="HTML",
    )


async def passport_delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить паспорт: свой — просто /passport_delete; админ может ответить на сообщение участника и /passport_delete — удалит паспорт того участника."""
    global user_passport_data
    if not update.message or not update.message.from_user:
        return
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id
    target_user_id = user_id
    target_name = update.message.from_user.full_name or "Вы"

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        if not await is_user_admin(user_id, chat_id, context.bot):
            await context.bot.send_message(chat_id=chat_id, text="❌ Удалить чужой паспорт могут только администраторы.")
            return
        target_user_id = update.message.reply_to_message.from_user.id
        target_name = update.message.reply_to_message.from_user.full_name or "Участник"

    if target_user_id not in user_passport_data or not user_passport_data[target_user_id]:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"У {target_name} нет сохранённого паспорта." if target_user_id != user_id else "У вас нет сохранённого паспорта.",
        )
        return
    user_passport_data.pop(target_user_id, None)
    save_passport_data()
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Паспорт участника <b>{html_escape(target_name)}</b> удалён." if target_user_id != user_id else "✅ Ваш паспорт удалён.",
        parse_mode="HTML",
    )


async def running_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /running — рейтинг бегунов за месяц."""
    runners = get_top_monthly_runners() or get_top_runners()
    if not runners:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Данных по бегу пока нет.")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["🏃 *Топ-10 бегунов за месяц:*"]
    for i, user in enumerate(runners):
        name = escape_markdown(user["name"])
        km = user["distance"] / 1000
        lines.append(f"{medals[i]} {name} — {km:.1f} км")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


async def weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /weekly — еженедельные сводки."""
    await send_weekly_summary()
    await send_weekly_running_summary()


async def monthly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /monthly — итоги месяца."""
    await send_monthly_summary()
    await send_monthly_running_summary()


async def advice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /advice — совет по бегу."""
    try:
        advice_text = None
        category = context.args[0] if context.args else None
        if YANDEX_AVAILABLE:
            prompt = build_ai_advice_prompt(category)
            payload = {
                "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                "messages": [
                    {"role": "system", "text": "Ты тренер по бегу. Пиши кратко и по делу."},
                    {"role": "user", "text": prompt},
                ],
                "completionOptions": {
                    "temperature": 0.7,
                    "maxTokens": 200
                },
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                    json=payload,
                    headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()
                if data and "result" in data and data["result"]["alternatives"]:
                    advice_text = data["result"]["alternatives"][0]["message"]["text"].strip()

        if not advice_text:
            await update_tips_cache()
            advice_text = get_random_tip(category)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=advice_text,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"[ADVICE] Ошибка команды /advice: {e}")


# План подготовки к забегу: выбор дистанции → выбор целевого времени → генерация плана
# callback_data: plan_dist_5k | plan_dist_10k | plan_dist_21 | plan_dist_42
# затем: plan_time_5k_25 | plan_time_10k_50 | plan_time_21_120 | plan_time_42_270 (время в минутах)
PLAN_DISTANCES = {
    "5k": {"label": "5 км", "weeks": 6, "times": [(20, "20 мин"), (25, "25 мин"), (30, "30 мин"), (35, "35 мин"), (40, "40 мин"), (45, "45 мин"), (50, "50 мин")]},
    "10k": {"label": "10 км", "weeks": 8, "times": [(40, "40 мин"), (45, "45 мин"), (50, "50 мин"), (55, "55 мин"), (60, "60 мин"), (65, "65 мин"), (70, "70 мин"), (80, "80 мин")]},
    "21": {"label": "21.1 км (полумарафон)", "weeks": 12, "times": [(90, "1:30"), (105, "1:45"), (120, "2:00"), (135, "2:15"), (150, "2:30"), (165, "2:45"), (180, "3:00")]},
    "42": {"label": "42.2 км (марафон)", "weeks": 16, "times": [(180, "3:00"), (210, "3:30"), (240, "4:00"), (270, "4:30"), (300, "5:00"), (330, "5:30"), (360, "6:00")]},
}


def _format_plan_time(minutes: int, dist_key: str) -> str:
    """Форматирует целевое время для отображения (например 90 → 1:30, 25 → 25 мин)."""
    if dist_key in ("21", "42") and minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}:{m:02d}"
    return f"{minutes} мин"


async def plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /plan — выбор дистанции, затем целевого времени, затем генерация плана."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [
        [InlineKeyboardButton("5 км", callback_data="plan_dist_5k"), InlineKeyboardButton("10 км", callback_data="plan_dist_10k")],
        [InlineKeyboardButton("21.1 км", callback_data="plan_dist_21"), InlineKeyboardButton("42.2 км", callback_data="plan_dist_42")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🏃 <b>План подготовки к забегу</b>\n\nВыбери дистанцию:",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def handle_plan_distance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """После выбора дистанции — показываем варианты целевого времени."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    query = update.callback_query
    await query.answer()
    data = query.data  # plan_dist_5k | plan_dist_10k | plan_dist_21 | plan_dist_42
    if not data.startswith("plan_dist_"):
        return
    dist_key = data.replace("plan_dist_", "")
    if dist_key not in PLAN_DISTANCES:
        return
    info = PLAN_DISTANCES[dist_key]
    label = info["label"]
    times = info["times"]
    buttons = []
    row = []
    for i, (mins, text) in enumerate(times):
        row.append(InlineKeyboardButton(text, callback_data=f"plan_time_{dist_key}_{mins}"))
        if len(row) >= 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(
        text=f"🏃 Дистанция: <b>{label}</b>\n\nВыбери целевое время, за которое хочешь пробежать:",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def handle_plan_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """После выбора времени — генерируем план и отправляем."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    query = update.callback_query
    await query.answer()
    data = query.data  # plan_time_5k_25 | plan_time_21_120 ...
    if not data.startswith("plan_time_"):
        return
    parts = data.split("_")
    if len(parts) != 4:
        return
    dist_key = parts[2]
    try:
        time_mins = int(parts[3])
    except ValueError:
        return
    if dist_key not in PLAN_DISTANCES:
        return
    info = PLAN_DISTANCES[dist_key]
    label = info["label"]
    weeks = info["weeks"]
    time_display = _format_plan_time(time_mins, dist_key)
    if time_mins >= 60:
        h, m = divmod(time_mins, 60)
        target_str = f"{h}:{m:02d}"
    else:
        target_str = f"{time_mins} мин"

    await query.edit_message_text(
        text=f"⏳ Генерирую план: <b>{label}</b>, цель <b>{time_display}</b>…",
        parse_mode="HTML",
    )
    chat_id = update.effective_chat.id

    if YANDEX_AVAILABLE:
        user_prompt = (
            f"Составь подробный недельный план подготовки к забегу на дистанцию {label}. "
            f"До старта {weeks} недель. Целевое время на финиш: {target_str}.\n\n"
            "Обязательно включи в каждую неделю:\n"
            "• Объём в км, длинная пробежка, отдых/восстановление.\n"
            "• Скоростные тренировки: интервалы (например 8×400 м, 5×1 км), темповый бег в целевом темпе, повторы на холмах.\n"
            "• Фартлек: 1–2 раза в неделю или по чередованию — чередование быстрых и медленных отрезков в одной пробежке (например 1 мин быстро / 2 мин легко), укажи примеры.\n"
            "• Силовые/ОФП: 1–2 раза в неделю — упражнения для бегунов: приседания, выпады, планка, кор, ягодичные, икра. Можно дома или в зале, 20–40 мин.\n\n"
            "По неделям: что именно бегать (дистанции, темп), когда фартлек, когда интервалы, когда силовая. "
            "Кратко, по делу, на русском. В конце: 2–3 совета на день старта (питание, разминка, темп)."
        )
        system_prompt = (
            "Ты — опытный тренер по бегу. Дай информативный план подготовки: по неделям — километраж, длинная, "
            "скоростные (интервалы, темповый бег), фартлек (чередование быстрых/лёгких отрезков), силовые/ОФП для бегунов. "
            "Указывай конкретику: примеры интервалов, длительность фартлека, примеры силовых упражнений. "
            "Формат: заголовки недель, списки с типами тренировок и рекомендациями."
        )
        try:
            payload = {
                "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_MODEL}",
                "completionOptions": {"stream": False, "temperature": 0.5, "maxTokens": "2500"},
                "messages": [
                    {"role": "system", "text": system_prompt},
                    {"role": "user", "text": user_prompt},
                ],
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                    json=payload,
                    headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"},
                )
                response.raise_for_status()
                data_resp = response.json()
            if data_resp and "result" in data_resp and "alternatives" in data_resp["result"]:
                plan_text = data_resp["result"]["alternatives"][0]["message"]["text"]
                header = f"🏃 План: <b>{label}</b>, цель <b>{time_display}</b> ({weeks} нед.)\n\n"
                full = header + plan_text.replace("*", "").strip()
                max_len = 3800
                chunks = []
                while full:
                    if len(full) <= max_len:
                        chunks.append(full)
                        break
                    split_at = full.rfind("\n", 0, max_len)
                    if split_at == -1:
                        split_at = max_len
                    chunks.append(full[:split_at].strip())
                    full = full[split_at:].strip()
                for chunk in chunks:
                    await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
                return
        except Exception as api_err:
            logger.warning(f"[PLAN] Ошибка Yandex API: {api_err}")

    fallback = (
        f"🏃 План: {label}, цель {time_display}. "
        "Генерация планов временно недоступна. Используй Hal Higdon, Nike Run Club или Strava."
    )
    await context.bot.send_message(chat_id=chat_id, text=fallback, parse_mode="HTML")


async def music_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /music — музыка дня."""
    music = get_music_of_day()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🎵 Музыка дня:\n{format_music_message(music)}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def deals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /deals — скидки на беговую экипировку."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [
            InlineKeyboardButton("Мужское", callback_data="deals_gender_male"),
            InlineKeyboardButton("Женское", callback_data="deals_gender_female"),
        ],
        [InlineKeyboardButton("Все", callback_data="deals_gender_all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Выберите категорию скидок:",
        reply_markup=reply_markup,
    )


async def handle_deals_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gender = query.data.replace("deals_gender_", "")
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [
            InlineKeyboardButton("Кроссовки", callback_data=f"deals_cat_{gender}_shoes"),
            InlineKeyboardButton("Шорты", callback_data=f"deals_cat_{gender}_shorts"),
            InlineKeyboardButton("Носки", callback_data=f"deals_cat_{gender}_socks"),
        ],
        [
            InlineKeyboardButton("Лонги", callback_data=f"deals_cat_{gender}_longsleeve"),
            InlineKeyboardButton("Тайтсы", callback_data=f"deals_cat_{gender}_tights"),
            InlineKeyboardButton("Куртки", callback_data=f"deals_cat_{gender}_jackets"),
        ],
        [
            InlineKeyboardButton("Штаны", callback_data=f"deals_cat_{gender}_pants"),
            InlineKeyboardButton("Футболки", callback_data=f"deals_cat_{gender}_shirts"),
            InlineKeyboardButton("Аксессуары", callback_data=f"deals_cat_{gender}_accessories"),
        ],
        [InlineKeyboardButton("Все категории", callback_data=f"deals_cat_{gender}_all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="Выберите категорию одежды:",
        reply_markup=reply_markup,
    )


async def handle_deals_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 3)  # deals cat gender category
    gender = parts[2] if len(parts) > 2 else "all"
    category = parts[3] if len(parts) > 3 else "all"
    if gender == "all":
        gender = None
    if category == "all":
        category = None
    text = await build_deals_message(gender, category)
    await query.edit_message_text(
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def garmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /garmin — привязка аккаунта Garmin."""
    if BLOCK_GARMIN_REQUESTS:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Garmin временно отключен для безопасности аккаунтов. Попробуйте позже.",
        )
        return

    if not GARMIN_AVAILABLE:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Garmin интеграция недоступна.")
        return
    if not context.args or len(context.args) < 2:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Используй: /garmin email пароль",
        )
        return
    user_id = update.message.from_user.id
    user_name = update.message.from_user.full_name or "Unknown"
    email = context.args[0]
    password = " ".join(context.args[1:])
    encrypted_password = encrypt_garmin_password(password)
    garmin_users[user_id] = {
        "name": user_name,
        "email": email,
        "encrypted_password": encrypted_password,
        "last_activity_id": "",
        "monthly_distance": 0.0,
        "monthly_activities": 0,
        "last_activity_date": "",
    }
    save_garmin_users()
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Garmin аккаунт привязан.")


async def garmin_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /garmin_stop — отключить Garmin."""
    user_id = update.message.from_user.id
    if user_id in garmin_users:
        del garmin_users[user_id]
        save_garmin_users()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Garmin отключён.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Garmin аккаунт не найден.")


async def garmin_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /garmin_list — список пользователей Garmin."""
    if not garmin_users:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Список Garmin пуст.")
        return
    lines = ["📱 *Garmin пользователи:*"]
    for user_id, data in garmin_users.items():
        email = data.get("email", "")
        masked_email = email
        if "@" in email:
            name_part, domain = email.split("@", 1)
            masked_email = f"{name_part[:2]}***@{domain}"
        safe_name = escape_markdown(data.get('name', 'Unknown'))
        safe_email = escape_markdown(masked_email)
        lines.append(f"• {safe_name} — {safe_email}")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


async def challenge_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Маршрутизатор команды /challenge для подкоманд."""
    if not context.args:
        await challenge_status(update, context)
        return

    action = context.args[0].lower()
    if action in ("weekly", "monthly"):
        await start_challenge(update, context)
        return
    if action == "join":
        await join_challenge(update, context)
        return
    if action == "done":
        await done_challenge(update, context)
        return
    if action in ("status", "info"):
        await challenge_status(update, context)
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🏆 *Команда /challenge*\n\n"
             "Доступные варианты:\n"
             "   `/challenge weekly` — недельный\n"
             "   `/challenge monthly` — месячный\n"
             "   `/challenge join` — вступить\n"
             "   `/challenge done` — отметить прогресс\n"
             "   `/challenge status` — статус\n",
        parse_mode="Markdown"
    )


def register_handlers(app):
    """Регистрирует обработчики команд и сообщений."""
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("getid", getid_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("morning", morning_cmd))
    app.add_handler(CommandHandler("stopmorning", stopmorning_cmd))
    app.add_handler(CommandHandler("facts", facts_cmd))
    app.add_handler(CallbackQueryHandler(handle_facts_ai_callback, pattern=r"^fact_ai_more_"))
    app.add_handler(CallbackQueryHandler(handle_facts_callback, pattern=r"^fact_more_"))

    app.add_handler(CommandHandler("remen", remen_cmd))
    app.add_handler(CommandHandler("antiremen", antiremen_cmd))
    app.add_handler(CommandHandler("roast", roast_cmd))
    app.add_handler(CommandHandler("flirt", flirt_cmd))
    app.add_handler(CommandHandler("mam", mam_cmd))
    app.add_handler(CommandHandler("joke", joke_cmd))
    app.add_handler(CommandHandler("motivation", motivation_cmd))
    app.add_handler(CommandHandler("add_sticker", add_sticker_cmd))

    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("rating", rating_cmd))
    app.add_handler(CommandHandler("likes", likes_cmd))
    app.add_handler(CommandHandler("levels", levels_cmd))
    app.add_handler(CommandHandler("passport", passport_cmd))
    app.add_handler(CommandHandler("passport_photo", passport_photo_cmd))
    app.add_handler(CommandHandler("passport_edit", passport_edit_cmd))
    app.add_handler(CommandHandler("passport_delete", passport_delete_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, passport_photo_from_caption_handler))
    app.add_handler(CommandHandler("running", running_cmd))
    app.add_handler(CommandHandler("weekly", weekly_cmd))
    app.add_handler(CommandHandler("monthly", monthly_cmd))

    app.add_handler(CommandHandler("garmin", garmin_cmd))
    app.add_handler(CommandHandler("garmin_stop", garmin_stop_cmd))
    app.add_handler(CommandHandler("garmin_list", garmin_list_cmd))

    app.add_handler(CommandHandler("plan", plan_cmd))
    app.add_handler(CallbackQueryHandler(handle_plan_distance_callback, pattern=r"^plan_dist_"))
    app.add_handler(CallbackQueryHandler(handle_plan_time_callback, pattern=r"^plan_time_"))
    app.add_handler(CallbackQueryHandler(handle_morning_action_callback, pattern=r"^morning_"))
    app.add_handler(CommandHandler("advice", advice_cmd))
    app.add_handler(CommandHandler("music", music_cmd))

    app.add_handler(CommandHandler("deals", deals_cmd))
    app.add_handler(CommandHandler("voice_test", voice_test_cmd))
    app.add_handler(CallbackQueryHandler(handle_deals_gender_callback, pattern=r"^deals_gender_"))
    app.add_handler(CallbackQueryHandler(handle_deals_category_callback, pattern=r"^deals_cat_"))
    app.add_handler(CommandHandler("slots", slots_cmd))
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("watch_list", watch_list_cmd))
    app.add_handler(CommandHandler("watch_del", watch_del_cmd))
    app.add_handler(CommandHandler("anon", anon))
    app.add_handler(CommandHandler("anonphoto", anonphoto))
    app.add_handler(CommandHandler("birthday", birthday))
    app.add_handler(CommandHandler("add_birthday", add_birthday))
    app.add_handler(CommandHandler("del_birthday", del_birthday))
    app.add_handler(CommandHandler("list_birthdays", list_birthdays))

    app.add_handler(CommandHandler("challenge", challenge_router))
    app.add_handler(CommandHandler("challenge_start", start_challenge))
    app.add_handler(PollHandler(handle_challenge_poll))

    app.add_handler(CommandHandler("votechallenges", start_vote))
    app.add_handler(CommandHandler("vote", vote_challenge))
    app.add_handler(CommandHandler("votestatus", vote_status))

    # Events tracker handlers
    for handler in get_handlers():
        app.add_handler(handler)

    # Message handlers
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mentions, block=False)
    )
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members, block=False)
    )
    app.add_handler(
        MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_member, block=False)
    )
    app.add_handler(
        MessageReactionHandler(
            handle_reactions,
            message_reaction_types=MessageReactionHandler.MESSAGE_REACTION,
            block=False,
        )
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_replies_to_bot, block=False)
    )
    app.add_handler(
        MessageHandler(
            filters.Sticker.ALL | filters.Document.ALL | filters.ANIMATION,
            handle_gifs_and_stickers,
            block=False,
        )
    )
    app.add_handler(MessageHandler(filters.ALL, handle_all_messages), group=1)
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
    app.add_error_handler(error_handler)


def start_background_threads():
    """Запускает фоновые потоки (Flask, events scheduler, keep-alive пинг)."""
    flask_thread = threading.Thread(
        target=run_flask,
        name="flask-server",
        daemon=True,
    )
    flask_thread.start()

    keepalive_thread = threading.Thread(
        target=keepalive_ping_loop,
        name="keepalive-ping",
        daemon=True,
    )
    keepalive_thread.start()

    events_thread = threading.Thread(
        target=events_scheduler_task,
        name="events-scheduler",
        daemon=True,
    )
    events_thread.start()


def add_background_task(app, coro):
    """Создаёт задачу и сохраняет для корректного завершения."""
    task = app.create_task(coro)
    background_tasks.append(task)
    return task


async def post_init(app):
    """Инициализация бота и запуск фоновых задач."""
    global application
    application = app

    try:
        # На всякий случай отключаем webhook, чтобы polling не конфликтовал
        await app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("[STARTUP] Webhook отключён (polling mode)")
    except Exception as e:
        logger.error(f"[STARTUP] Ошибка отключения webhook: {e}")

    # Готовим SQLite БД (создание файла в /data)
    ensure_sqlite_db()
    load_daily_stats()
    load_known_users()
    load_morning_streaks()
    load_watch_subscriptions()
    load_summary_state()

    init_garmin_on_startup()
    init_birthdays_on_startup()
    init_passport_data_on_startup()
    init_belt_photo_on_startup()
    await warmup_belt_photo_file_id(app.bot)
    load_bot_stickers()
    try:
        load_user_rating_stats()
    except Exception as e:
        logger.warning(f"[STARTUP] Ошибка загрузки рейтинга: {e}")

    set_config(GENERAL_CHAT_ID, app, asyncio.get_running_loop(), EVENTS_TOPIC_ID, NEWS_TOPIC_ID, DATA_DIR)
    start_background_threads()

    add_background_task(app, facts_scheduler_task())
    add_background_task(app, birthday_scheduler_task())
    add_background_task(app, morning_scheduler_task())
    add_background_task(app, good_night_scheduler_task())
    add_background_task(app, music_scheduler_task())
    add_background_task(app, deals_scheduler_task())
    add_background_task(app, coffee_scheduler_task())
    add_background_task(app, lunch_scheduler_task())
    add_background_task(app, motivation_scheduler_task())
    add_background_task(app, advice_scheduler_task())
    add_background_task(app, daily_summary_scheduler_task())
    add_background_task(app, watch_scheduler_task())
    if BLOCK_GARMIN_REQUESTS:
        logger.warning("[GARMIN] Планировщик Garmin не запущен (BLOCK_GARMIN_REQUESTS=1)")
    else:
        add_background_task(app, garmin_scheduler_task())
    add_background_task(app, holiday_scheduler_task())


async def post_shutdown(app):
    """Аккуратное завершение фоновых задач."""
    global background_tasks
    logger.warning(
        "[SHUTDOWN] Остановка приложения. Обычно это SIGTERM от Render (перезапуск/деплой/лимиты) "
        "или конфликт двух экземпляров бота. Команда /stop не завершает процесс — только сбрасывает флаг."
    )
    for task in background_tasks:
        if not task.done():
            task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
    background_tasks = []


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    register_handlers(app)
    logger.info("[STARTUP] Бот запущен, стартуем polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
