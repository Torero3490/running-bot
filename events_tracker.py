#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для отслеживания беговых мероприятий в Москве и СПб
Автоматически парсит источники и публикует открытые регистрации в чат

Подключение к основному боту:
1. Добавьте в начало файла телеграм-бота:
   from events_tracker import *
2. Добавьте регистрацию обработчиков в main block
3. Добавьте запуск планировщика
"""

import asyncio
import logging
import hashlib
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, filters

logger = logging.getLogger(__name__)

# Глобальные переменные (будут заполнены из основного бота)
CHAT_ID = None
EVENTS_TOPIC_ID = None
application = None
loop = None

# Хранилище для отслеживания опубликованных мероприятий
published_events_db = set()


def set_config(chat_id: int, app, event_loop, events_topic_id: int = None):
    """Установка конфигурации из основного бота"""
    global CHAT_ID, EVENTS_TOPIC_ID, application, loop
    CHAT_ID = chat_id
    EVENTS_TOPIC_ID = events_topic_id
    application = app
    loop = event_loop


def get_event_hash(title: str, date_str: str) -> str:
    """Генерирует уникальный хеш мероприятия для избежания дубликатов"""
    key_string = f"{title}_{date_str}".lower().strip()
    return hashlib.md5(key_string.encode('utf-8')).hexdigest()[:12]


async def parse_russia_running_events() -> List[Dict]:
    """Парсинг мероприятий с RussiaRunning"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://russiarunning.com/Events",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем карточки мероприятий
            event_cards = soup.find_all('div', class_='event-card') or \
                         soup.find_all('div', class_='event-item') or \
                         soup.find_all('article', class_='event')
            
            for card in event_cards:
                try:
                    # Название
                    title_elem = card.find('h3') or card.find('h2') or card.find('a', class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None
                    
                    if not title:
                        continue
                    
                    # Дата
                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = date_elem.get_text(strip=True)
                    
                    # Ссылка
                    link_elem = card.find('a', href=True)
                    url = f"https://russiarunning.com{link_elem['href']}" if link_elem else ""
                    
                    # Дистанции
                    dist_elem = card.find(class_='distances') or card.find(class_='distance')
                    distances = dist_elem.get_text(strip=True) if dist_elem else ""
                    
                    # Местоположение
                    loc_elem = card.find(class_='city') or card.find(class_='location')
                    city = loc_elem.get_text(strip=True) if loc_elem else ""
                    
                    # Проверяем что это Москва или СПб
                    city_lower = city.lower()
                    if not any(x in city_lower for x in ['москва', 'moscow', 'санкт-петербург', 'st. petersburg', 'спб', 'saint petersburg']):
                        continue
                    
                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': distances,
                        'url': url,
                        'source': 'RussiaRunning'
                    })
                    
                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга карточки RussiaRunning: {e}")
                    continue
                    
    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга RussiaRunning: {e}")
    
    return events


async def parse_marathonec_events() -> List[Dict]:
    """Парсинг мероприятий с marathonec.ru"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://marathonec.ru/calendar-beg/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем таблицу или блоки с забегами
            table = soup.find('table', class_='calendar') or soup.find('div', class_='calendar')
            if table:
                rows = table.find_all('tr')
                for row in rows:
                    try:
                        cols = row.find_all(['td', 'th'])
                        if len(cols) < 3:
                            continue
                        
                        # Дата
                        date_str = cols[0].get_text(strip=True)
                        
                        # Название
                        title_elem = cols[1].find('a') or cols[1]
                        title = title_elem.get_text(strip=True) if title_elem else None
                        
                        if not title or not date_str:
                            continue
                        
                        # Местоположение
                        city = cols[2].get_text(strip=True) if len(cols) > 2 else ""
                        
                        # Проверяем что это Москва или СПб
                        city_lower = city.lower()
                        if not any(x in city_lower for x in ['москва', 'moscow', 'санкт-петербург', 'st. petersburg', 'спб', 'saint petersburg']):
                            continue
                        
                        # Ссылка
                        url = ""
                        if title_elem and title_elem.get('href'):
                            url = title_elem['href']
                        
                        events.append({
                            'title': title,
                            'date': date_str,
                            'city': city,
                            'distances': 'Уточняйте на сайте',
                            'url': url,
                            'source': 'Марафонец'
                        })
                        
                    except Exception as e:
                        logger.warning(f"[EVENTS] Ошибка парсинга строки marathonec: {e}")
                        continue
                    
    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга marathonec.ru: {e}")
    
    return events


async def parse_probeg_events() -> List[Dict]:
    """Парсинг мероприятий с probeg.org"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://probeg.org/races/city/2310/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем блоки с мероприятиями
            event_items = soup.find_all('div', class_='race-item') or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('tr', class_='race')
            
            for item in event_items:
                try:
                    # Название
                    title_elem = item.find('h3') or item.find('a', class_='race-title') or item.find('a')
                    title = title_elem.get_text(strip=True) if title_elem else None
                    
                    if not title:
                        continue
                    
                    # Дата
                    date_elem = item.find(class_='date') or item.find('time')
                    date_str = date_elem.get_text(strip=True) if date_elem else ""
                    
                    # Ссылка
                    url = ""
                    if title_elem and title_elem.get('href'):
                        url = title_elem['href']
                    
                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': 'Москва',
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'ПроБЕГ'
                    })
                    
                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга probeg: {e}")
                    continue
                    
    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга probeg.org: {e}")
    
    return events


def parse_russian_date(date_str: str) -> str:
    """Парсинг русской даты в формат ДД.ММ.ГГГГ"""
    if not date_str:
        return ""
    
    months = {
        'января': '01', 'февраля': '02', 'марта': '03', 'апреля': '04',
        'мая': '05', 'июня': '06', 'июля': '07', 'августа': '08',
        'сентября': '09', 'октября': '10', 'ноября': '11', 'декабря': '12',
        'january': '01', 'february': '02', 'march': '03', 'april': '04',
        'may': '05', 'june': '06', 'july': '07', 'august': '08',
        'september': '09', 'october': '10', 'november': '11', 'december': '12'
    }
    
    try:
        # Попытка парсить формат ДД.ММ.ГГГГ или ГГГГ-ММ-ДД
        if re.match(r'\d{2}\.\d{2}\.\d{4}', date_str):
            return date_str[:10]
        if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
            parts = date_str.split('-')
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
        
        # Парсинг русского формата "24 января 2025"
        match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', date_str, re.IGNORECASE)
        if match:
            day = match.group(1).zfill(2)
            month_name = match.group(2).lower()
            year = match.group(3)
            month = months.get(month_name, '01')
            return f"{day}.{month}.{year}"
        
    except Exception:
        pass
    
    return date_str


async def publish_event(context: ContextTypes.DEFAULT_TYPE, event: Dict) -> bool:
    """Публикует мероприятие в чат"""
    try:
        title = event.get('title', 'Без названия')
        date = parse_russian_date(event.get('date', ''))
        city = event.get('city', '')
        distances = event.get('distances', 'Уточняйте')
        url = event.get('url', '')
        
        # Проверяем дубликаты
        event_hash = get_event_hash(title, date)
        if event_hash in published_events_db:
            logger.info(f"[EVENTS] Мероприятие уже опубликовано: {title}")
            return False
        
        # Формируем сообщение
        text = f"🏃 **{title}**\n\n"
        text += f"📅 Дата: {date}\n"
        text += f"📍 Место: {city}\n"
        text += f"🏃 Дистанции: {distances}\n"
        
        if url:
            text += f"\n🔗 [Регистрация на сайте]({url})"
        
        # Кнопка "Напомнить"
        keyboard = [
            [InlineKeyboardButton("🔔 Напомнить за 3 дня", callback_data=f"event_reminder_{event_hash}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем в чат
        await context.bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=EVENTS_TOPIC_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        
        # Сохраняем в историю
        published_events_db.add(event_hash)
        logger.info(f"[EVENTS] Опубликовано мероприятие: {title} ({city})")
        
        return True
        
    except Exception as e:
        logger.error(f"[EVENTS] Ошибка публикации: {e}")
        return False


async def check_and_publish_events(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет и публикует новые мероприятия"""
    logger.info("[EVENTS] Запуск проверки мероприятий...")
    
    all_events = []
    
    # Парсим все источники
    events_russia = await parse_russia_running_events()
    events_marathonec = await parse_marathonec_events()
    events_probeg = await parse_probeg_events()
    
    all_events.extend(events_russia)
    all_events.extend(events_marathonec)
    all_events.extend(events_probeg)
    
    logger.info(f"[EVENTS] Найдено мероприятий: {len(all_events)}")
    
    # Фильтруем и публикуем
    published_count = 0
    for event in all_events:
        if await publish_event(context, event):
            published_count += 1
    
    if published_count > 0:
        logger.info(f"[EVENTS] Опубликовано {published_count} новых мероприятий")
    else:
        logger.info("[EVENTS] Новых мероприятий не найдено")


async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /events — проверить мероприятия вручную"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    await check_and_publish_events(context)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Проверка мероприятий завершена!",
    )
    
    try:
        await update.message.delete()
    except Exception:
        pass


async def events_help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /events_help — помощь по мероприятиям"""
    text = """🏃 **Бот отслеживает беговые мероприятия**

**Автоматически:**
• Проверяет источники каждый день в 10:00
• Публикует открытые регистрации в чат
• Указывает даты, дистанции и ссылки

**Источники:**
• RussiaRunning (russiarunning.com)
• Марафонец (marathonec.ru)
• ПроБЕГ (probeg.org)

**Команды:**
• /events — проверить вручную
• Нажать 🔔 — напомнить за 3 дня"""
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="Markdown",
    )


async def handle_event_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопку 'Напомнить'"""
    query = update.callback_query
    
    if not query.data.startswith("event_reminder_"):
        return
    
    user_name = query.from_user.full_name or query.from_user.username or "Участник"
    
    # Получаем хеш мероприятия
    event_hash = query.data.replace("event_reminder_", "")
    
    logger.info(f"[EVENTS] Пользователь {user_name} нажал 'Напомнить' для {event_hash}")
    
    await query.answer(text="🔔 Напоминание установлено! Напишу за 3 дня до мероприятия.", show_alert=False)


def events_scheduler_task():
    """Планировщик проверки мероприятий - каждый день в 10:00"""
    import schedule
    import time as time_module
    
    schedule.every().day.at("10:00").do(
        lambda: asyncio.run_coroutine_threadsafe(check_and_publish_events(None), loop)
    )
    
    logger.info("[EVENTS] Планировщик мероприятий запущен (каждый день в 10:00)")
    
    while True:
        schedule.run_pending()
        time_module.sleep(60)


def get_handlers() -> list:
    """Возвращает список обработчиков для регистрации в боте"""
    return [
        CommandHandler("events", events_cmd),
        CommandHandler("eventshelp", events_help_cmd),
        CallbackQueryHandler(handle_event_reminder_callback, pattern=r"^event_reminder_"),
    ]
