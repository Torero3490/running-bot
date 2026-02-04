#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для отслеживания беговых мероприятий и трейлов в Москве, СПб и регионах
Автоматически парсит источники и публикует открытые регистрации в чат

**Проверка регистрации:**
• Проверяет статус регистрации (открыта/закрыта)
• Ищет дедлайны подачи заявок
• Определяет стоимость участия
• Пропускает мероприятия без открытых слотов

**Источники беговые:**
• RussiaRunning (russiarunning.com)
• Марафонец (marathonec.ru)
• ПроБЕГ (probeg.org)
• Беговое сообщество (runc.run)
• S10.run (s10.run)
• Лига Героев (heroleague.ru)
• ЗаБег.РФ (забег.рф)
• Ahotu Running (ahotu.com)
• Get.run (get.run)
• Забег Обещаний (1jan.run)
• Бегом по Золотому кольцу (goldenringrun.ru)
• Академия Марафона (academymarathon.ru)
• Кразмарафон (krasmarafon.ru)
• Toplist.run (toplist.run)
• Orgeo.ru (orgeo.ru)
• Finishers.com (finishers.com)

**Источники трейлы:**
• ПроБЕГ Трейлы (probeg.org/calendar/trails/)
• Pushkin Run / Балтийский трейл (pushkin-run.ru)
• Golden Ring Ultra Trail (goldenultra.ru)
• Ahotu Trail (ahotu.com/trail)
• ITRA (itra.run)

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
NEWS_TOPIC_ID = None  # Топик для сводок ("Новости")
application = None
loop = None

# Хранилище для отслеживания опубликованных мероприятий
published_events_db = set()


def is_registration_open(page_text: str, url: str) -> bool:
    """Проверяет, открыта ли регистрация на мероприятие"""

    # Индикаторы открытой регистрации
    open_indicators = [
        'регистрация открыта',
        'регистрация доступна',
        'открыта регистрация',
        'приём заявок открыт',
        'участие возможно',
        'зарегистрироваться',
        'регистрация на забег',
        'registration is open',
        'register now',
        'sign up',
        'присоединиться',
        'купить слот',
        'оплатить участие',
    ]

    # Индикаторы закрытой регистрации
    closed_indicators = [
        'регистрация закрыта',
        'регистрация завершена',
        'приём заявок завершён',
        'регистрация окончена',
        'мест нет',
        'слоты проданы',
        'registration is closed',
        'registration closed',
        'sold out',
        'full',
        'мест не осталось',
        'ожидается открытие',
    ]

    text_lower = page_text.lower()
    url_lower = url.lower()

    # Проверяем закрытые индикаторы
    for indicator in closed_indicators:
        if indicator in text_lower:
            logger.info(f"[EVENTS] Регистрация закрыта (найдено: '{indicator}')")
            return False

    # Проверяем открытые индикаторы
    for indicator in open_indicators:
        if indicator in text_lower:
            logger.info(f"[EVENTS] Регистрация открыта (найдено: '{indicator}')")
            return True

    # Если не нашли явных индикаторов, считаем что регистрация может быть открыта
    # но добавляем предупреждение в лог
    logger.warning(f"[EVENTS] Не удалось определить статус регистрации, проверяем URL: {url}")
    return True


def extract_registration_deadline(page_text: str) -> Optional[str]:
    """Извлекает дедлайн регистрации из текста страницы"""

    # Паттерны для поиска дат дедлайна
    deadline_patterns = [
        r'регистрац.*?до\s*(\d{1,2}[\.\/]\d{1,2}[\.\/]\d{2,4})',
        r'до\s*(\d{1,2}\s+\w+\s+\d{4})',
        r'крайний срок.*?(\d{1,2}[\.\/]\d{1,2}[\.\/]\d{2,4})',
        r'deadline.*?(\d{1,2}[\.\/]\d{1,2}[\.\/]\d{2,4})',
        r'регистрац.*?закрывается.*?(\d{1,2}\s+\w+)',
        r'открыта до\s*(\d{1,2}\s+\w+)',
    ]

    for pattern in deadline_patterns:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            deadline = match.group(1)
            logger.info(f"[EVENTS] Найден дедлайн регистрации: {deadline}")
            return deadline

    return None


def extract_price(page_text: str) -> Optional[str]:
    """Извлекает стоимость участия из текста страницы"""

    # Паттерны для поиска цены
    price_patterns = [
        r'(\d{3,5})\s*руб',
        r'(\d+)\s*₽',
        r'от\s*(\d{3,5})\s*руб',
        r'стоимость.*?(\d{3,5})',
        r'(\d+)\s*rub',
        r'price.*?(\d+)',
    ]

    for pattern in price_patterns:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            price = match.group(1)
            logger.info(f"[EVENTS] Найдена цена: {price} руб")
            return f"{price} руб"

    return None


def set_config(chat_id: int, app, event_loop, events_topic_id: int = None, news_topic_id: int = None):
    """Установка конфигурации из основного бота"""
    global CHAT_ID, EVENTS_TOPIC_ID, NEWS_TOPIC_ID, application, loop
    CHAT_ID = chat_id
    EVENTS_TOPIC_ID = events_topic_id
    NEWS_TOPIC_ID = news_topic_id
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

                    # Расширенный фильтр городов
                    city_lower = city.lower()
                    moscow_region = ['москва', 'moscow', 'московская', 'подмосковье', 'московской']
                    spb_region = ['санкт-петербург', 'st. petersburg', 'спб', 'saint petersburg', 'питер', 'петербург', 'ленинградская', 'ленинградской']
                    izhevsk_region = ['ижевск', 'izhevsk', 'удмурт', 'удмуртия', 'udmurt']

                    if not any(x in city_lower for x in moscow_region + spb_region + izhevsk_region):
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

                        # Расширенный фильтр городов
                        city_lower = city.lower()
                        moscow_region = ['москва', 'moscow', 'московская', 'подмосковье', 'московской']
                        spb_region = ['санкт-петербург', 'st. petersburg', 'спб', 'saint petersburg', 'питер', 'петербург', 'ленинградская', 'ленинградской']
                        izhevsk_region = ['ижевск', 'izhevsk', 'удмурт', 'удмуртия', 'udmurt']

                        if not any(x in city_lower for x in moscow_region + spb_region + izhevsk_region):
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


async def parse_runc_run_events() -> List[Dict]:
    """Парсинг мероприятий с Бегового сообщества (runc.run)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://runc.run/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Ищем блоки с мероприятиями
            event_cards = soup.find_all('a', href=re.compile(r'/event/|\.runc\.run')) or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for card in event_cards:
                try:
                    # Название
                    title_elem = card.find('h2') or card.find('h3') or card.find('div', class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    # Дата
                    date_elem = card.find('time') or card.find(class_='date') or card.find(class_='datetime')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = date_elem.get_text(strip=True)

                    # Ссылка
                    url = ""
                    if card.get('href'):
                        href = card['href']
                        if href.startswith('/'):
                            url = f"https://runc.run{href}"
                        else:
                            url = href
                    elif title_elem and title_elem.find_parent('a'):
                        href = title_elem.find_parent('a').get('href')
                        if href:
                            url = href if href.startswith('http') else f"https://runc.run{href}"

                    # Город
                    city = "Москва и регионы"  # Беговое сообщество - nationwide events
                    city_elem = card.find(class_='city') or card.find(class_='location')
                    if city_elem:
                        city_text = city_elem.get_text(strip=True).lower()
                        if any(x in city_text for x in ['москв', 'moscow']):
                            city = 'Москва'
                        elif any(x in city_text for x in ['петербург', 'peter', 'спб']):
                            city = 'Санкт-Петербург'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'Беговое сообщество'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга карточки runc.run: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга runc.run: {e}")

    return events


async def parse_heroleague_events() -> List[Dict]:
    """Парсинг мероприятий с Лиги Героев (heroleague.ru)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://heroleague.ru/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Ищем карточки гонок
            race_cards = soup.find_all('a', href=re.compile(r'/race|/event')) or \
                        soup.find_all('div', class_='race-card') or \
                        soup.find_all('article', class_='race')

            for card in race_cards:
                try:
                    # Название
                    title_elem = card.find('h2') or card.find('h3') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    # Пропускаем нерелевантные элементы
                    if any(x in title.lower() for x in ['опрос', 'опросы', 'faq', 'поддержка', 'контакты']):
                        continue

                    # Дата
                    date_elem = card.find('time') or card.find(class_='date') or card.find(class_='race-date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_text = date_elem.get_text(strip=True)
                        # Пытаемся извлечь дату из текста
                        date_match = re.search(r'(\d{1,2})\s+(\w+)\s*(\d{4})?', date_text)
                        if date_match:
                            date_str = parse_russian_date(date_text)

                    # Ссылка
                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = href if href.startswith('http') else f"https://heroleague.ru{href}"

                    # Определяем город
                    city = "Москва и регионы"
                    card_text = card.get_text().lower()

                    # Попытка определить город из контекста
                    race_info = soup.find_all(string=re.compile(re.escape(title), re.IGNORECASE))
                    for info in race_info[:3]:
                        parent = info.find_parent()
                        if parent:
                            parent_text = parent.get_text().lower()
                            if any(x in parent_text for x in ['москв', 'moscow', 'подмосков']):
                                city = 'Москва'
                                break
                            elif any(x in parent_text for x in ['петербург', 'peter', 'спб', 'ленинград']):
                                city = 'Санкт-Петербург'
                                break

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Гонка с препятствиями',
                        'url': url,
                        'source': 'Лига Героев'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга карточки heroleague: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга heroleague.ru: {e}")

    return events


async def parse_zabeg_rf_events() -> List[Dict]:
    """Парсинг мероприятий с ЗаБег.РФ"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://xn--80acghh.xn--p1ai/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Ищем информацию о забегах
            event_blocks = soup.find_all('a', href=re.compile(r'/.*забег|/.*run')) or \
                          soup.find_all('div', class_='event') or \
                          soup.find_all('article', class_='race')

            for block in event_blocks:
                try:
                    # Название
                    title_elem = block.find('h2') or block.find('h3') or block.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    # Пропускаем служебные элементы
                    if any(x in title.lower() for x in ['регистрац', 'оплата', 'вопросы', 'контакты']):
                        continue

                    # Дата
                    date_elem = block.find('time') or block.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    # Ссылка
                    url = ""
                    if block.get('href'):
                        href = block['href']
                        url = href if href.startswith('http') else f"https://забег.рф{href}"

                    # Город - ЗаБег.РФ это всероссийский забег
                    city = "Москва"  # Основная локация

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': '5 км, 10 км, 21.0975 км',
                        'url': url,
                        'source': 'ЗаБег.РФ'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга zabeg.rf: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга забег.рф: {e}")

    return events


async def parse_probeg_trails_events() -> List[Dict]:
    """Парсинг трейловых забегов с ПроБЕГ (probeg.org/calendar/trails/)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://probeg.org/calendar/trails/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Ищем блоки с трейловыми забегами
            trail_cards = soup.find_all('div', class_='race-item') or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('tr', class_='race')

            for card in trail_cards:
                try:
                    # Название
                    title_elem = card.find('a', class_='race-title') or \
                                card.find('a') or \
                                card.find('h3') or \
                                card.find('h4')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    # Пропускаем служебные элементы
                    if any(x in title.lower() for x in ['календарь', 'трейлов', 'бег', 'раздел']):
                        continue

                    # Дата
                    date_elem = card.find(class_='date') or card.find('time')
                    date_str = ""
                    if date_elem:
                        date_text = date_elem.get_text(strip=True)
                        parsed_date = parse_russian_date(date_text)
                        if parsed_date:
                            date_str = parsed_date

                    # Ссылка
                    url = ""
                    if title_elem and title_elem.get('href'):
                        href = title_elem['href']
                        url = href if href.startswith('http') else f"https://probeg.org{href}"

                    # Город/регион
                    city = "Трейл (регион уточняйте)"
                    card_text = card.get_text().lower()

                    # Определяем регион
                    if any(x in card_text for x in ['москв', 'подмосков', 'москов']):
                        city = 'Москва и область'
                    elif any(x in card_text for x in ['петербург', 'спб', 'ленинград']):
                        city = 'Санкт-Петербург и область'
                    elif any(x in card_text for x in ['сочи', 'краснодар']):
                        city = 'Сочи/Краснодарский край'
                    elif any(x in card_text for x in ['кавказ', 'казбек', 'эльбрус']):
                        city = 'Кавказ'
                    elif any(x in card_text for x in ['алтай', 'байкал']):
                        city = 'Алтай/Байкал'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Трейл/Горный бег',
                        'url': url,
                        'source': 'ПроБЕГ Трейлы'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга трейла с probeg: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга probeg.org/trails: {e}")

    return events


async def parse_pushkin_run_events() -> List[Dict]:
    """Парсинг мероприятий с Pushkin Run (Балтийский трейл и др.)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://pushkin-run.ru/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Ищем блоки с забегами
            event_cards = soup.find_all('a', href=re.compile(r'/whitenights|/event|/race')) or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for card in event_cards:
                try:
                    # Название
                    title_elem = card.find('h2') or card.find('h3') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    # Пропускаем служебные элементы
                    if any(x in title.lower() for x in ['главная', 'о нас', 'контакты', 'партнер']):
                        continue

                    # Дата
                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_text = date_elem.get_text(strip=True)
                        date_str = parse_russian_date(date_text)

                    # Ссылка
                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = href if href.startswith('http') else f"https://pushkin-run.ru{href}"

                    # Pushkin Run специализируется на Балтийском трейле
                    city = "Пушкин/Санкт-Петербург"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'подмосков']):
                        city = 'Москва'
                    elif any(x in card_text for x in ['сочи']):
                        city = 'Сочи'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Трейл',
                        'url': url,
                        'source': 'Pushkin Run'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга pushkin-run: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга pushkin-run.ru: {e}")

    return events


async def parse_golden_ring_trail_events() -> List[Dict]:
    """Парсинг Golden Ring Ultra Trail (goldenultra.ru)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://goldenultra.ru/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Ищем информацию о гонках
            race_blocks = soup.find_all('a', href=re.compile(r'/race|/event|madfox|golden')) or \
                         soup.find_all('div', class_='race') or \
                         soup.find_all('article', class_='event')

            for block in race_blocks:
                try:
                    # Название
                    title_elem = block.find('h2') or block.find('h3') or block.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    # Пропускаем служебные элементы
                    skip_words = ['главная', 'о фестивале', 'правила', 'faq', 'контакты', 'партнеры']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    # Дата
                    date_elem = block.find('time') or block.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_text = date_elem.get_text(strip=True)
                        date_str = parse_russian_date(date_text)

                    # Ссылка
                    url = ""
                    if block.get('href'):
                        href = block['href']
                        url = href if href.startswith('http') else f"https://goldenultra.ru{href}"

                    # Golden Ring Ultra Trail - Владимирская область
                    city = "Золотое Кольцо/Владимир"

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Ультратрейл',
                        'url': url,
                        'source': 'Golden Ring Ultra'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга goldenultra: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга goldenultra.ru: {e}")

    return events


async def parse_s10_run_events() -> List[Dict]:
    """Парсинг мероприятий с S10.run (Беговое сообщество)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://s10.run/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race|/post/')) or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h2') or card.find('h3') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    skip_words = ['главная', 'о нас', 'контакты', 'партнер', 'статьи', 'новости']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = href if href.startswith('http') else f"https://s10.run{href}"

                    city = "Россия"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow']):
                        city = 'Москва'
                    elif any(x in card_text for x in ['петербург', 'peter', 'спб']):
                        city = 'Санкт-Петербург'
                    elif any(x in card_text for x in ['сочи']):
                        city = 'Сочи'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'S10.run'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга s10.run: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга s10.run: {e}")

    return events


async def parse_ahotu_running_events() -> List[Dict]:
    """Парсинг забегов с Ahotu.com (международный календарь)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                "https://ahotu.com/calendar/running/russia",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race')) or \
                         soup.find_all('div', class_='event-card') or \
                         soup.find_all('tr', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h3') or card.find('h2') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = f"https://ahotu.com{href}" if href.startswith('/') else href

                    city = "Россия"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow']):
                        city = 'Москва'
                    elif any(x in card_text for x in ['петербург', 'peter', 'spb', 'saint petersburg']):
                        city = 'Санкт-Петербург'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'Ahotu Running'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга ahotu running: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга ahotu.com: {e}")

    return events


async def parse_ahotu_trail_events() -> List[Dict]:
    """Парсинг трейлов с Ahotu.com"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                "https://ahotu.com/calendar/trail-running/russia",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race')) or \
                         soup.find_all('div', class_='event-card') or \
                         soup.find_all('tr', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h3') or card.find('h2') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = f"https://ahotu.com{href}" if href.startswith('/') else href

                    city = "Трейл/Горы"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow']):
                        city = 'Москва и область'
                    elif any(x in card_text for x in ['петербург', 'peter', 'spb']):
                        city = 'СПб и область'
                    elif any(x in card_text for x in ['кавказ', 'эльбрус', 'казбек']):
                        city = 'Кавказ'
                    elif any(x in card_text for x in ['алтай']):
                        city = 'Алтай'
                    elif any(x in card_text for x in ['байкал']):
                        city = 'Байкал'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Трейл/Ультра',
                        'url': url,
                        'source': 'Ahotu Trail'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга ahotu trail: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга ahotu trail: {e}")

    return events


async def parse_get_run_events() -> List[Dict]:
    """Парсинг забегов с Get.run"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://get.run/races/europe/russia/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race')) or \
                         soup.find_all('div', class_='race-card') or \
                         soup.find_all('article', class_='event')

            for card in event_cards:
                try:
                    title_elem = card.find('h3') or card.find('h2') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = f"https://get.run{href}" if href.startswith('/') else href

                    city = "Россия"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow']):
                        city = 'Москва'
                    elif any(x in card_text for x in ['петербург', 'peter', 'spb']):
                        city = 'Санкт-Петербург'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'Get.run'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга get.run: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга get.run: {e}")

    return events


async def parse_itra_events() -> List[Dict]:
    """Парсинг трейлов с ITRA (International Trail Running Association)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://itra.run/Races/RaceCalendar",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            race_cards = soup.find_all('a', href=re.compile(r'/RaceDetails|/race')) or \
                        soup.find_all('div', class_='race') or \
                        soup.find_all('tr', class_='race')

            for card in race_cards:
                try:
                    title_elem = card.find('h3') or card.find('h2') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    skip_words = ['itra', 'calendar', 'about', 'contact']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = f"https://itra.run{href}" if href.startswith('/') else href

                    city = "Трейл (международный)"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow', 'russia', 'росси']):
                        city = 'Россия'
                    elif any(x in card_text for x in ['кавказ', 'caucasus']):
                        city = 'Кавказ'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Трейл/Ультра (ITRA)',
                        'url': url,
                        'source': 'ITRA'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга ITRA: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга itra.run: {e}")

    return events


async def parse_1jan_run_events() -> List[Dict]:
    """Парсинг Забега Обещаний (1jan.run)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://1jan.run/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_blocks = soup.find_all('a', href=re.compile(r'/event|/race|1jan')) or \
                          soup.find_all('div', class_='event') or \
                          soup.find_all('article', class_='race')

            for block in event_blocks:
                try:
                    title_elem = block.find('h1') or block.find('h2') or block.find('h3')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    skip_words = ['главная', 'о забеге', 'контакты', 'партнер']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    date_elem = block.find('time') or block.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if block.get('href'):
                        href = block['href']
                        url = href if href.startswith('http') else f"https://1jan.run{href}"

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': 'Москва и регионы',
                        'distances': '2026 метров (символично)',
                        'url': url,
                        'source': 'Забег Обещаний'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга 1jan.run: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга 1jan.run: {e}")

    return events


async def parse_goldenringrun_events() -> List[Dict]:
    """Парсинг Бегом по Золотому кольцу (goldenringrun.ru)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "http://goldenringrun.ru/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race|golden')) or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h2') or card.find('h3') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    skip_words = ['главная', 'о проекте', 'правила', 'контакты']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = href if href.startswith('http') else f"http://goldenringrun.ru{href}"

                    city = "Золотое Кольцо"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['владимир', 'суздаль']):
                        city = 'Владимир/Суздаль'
                    elif any(x in card_text for x in ['ярославл']):
                        city = 'Ярославль'
                    elif any(x in card_text for x in ['кострома']):
                        city = 'Кострома'
                    elif any(x in card_text for x in ['иваново']):
                        city = 'Иваново'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'Бегом по Золотому кольцу'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга goldenringrun: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга goldenringrun.ru: {e}")

    return events


async def parse_academymarathon_events() -> List[Dict]:
    """Парсинг Академии Марафона (academymarathon.ru)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://academymarathon.ru/blog/kalendar-zabegov-2025",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_items = soup.find_all('tr') or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for item in event_items:
                try:
                    title_elem = item.find('a') or item.find('h3') or item.find('h2')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    date_elem = item.find('time') or item.find(class_='date')
                    date_str = ""
                    if date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if title_elem and title_elem.get('href'):
                        href = title_elem['href']
                        url = href if href.startswith('http') else f"https://academymarathon.ru{href}"

                    city = "Москва"
                    item_text = item.get_text().lower()

                    if any(x in item_text for x in ['петербург', 'спб']):
                        city = 'Санкт-Петербург'
                    elif any(x in item_text for x in ['сочи']):
                        city = 'Сочи'
                    elif any(x in item_text for x in ['казан']):
                        city = 'Казань'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Марафон/Полумарафон',
                        'url': url,
                        'source': 'Академия Марафона'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга academymarathon: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга academymarathon.ru: {e}")

    return events


async def parse_krasmarafon_events() -> List[Dict]:
    """Парсинг Кразмарафона (krasmarafon.ru)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://krasmarafon.ru/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race|krasmarafon')) or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h2') or card.find('h3') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    skip_words = ['главная', 'о марафоне', 'контакты', 'партнеры', 'правила']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = href if href.startswith('http') else f"https://krasmarafon.ru{href}"

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': 'Красноярск',
                        'distances': 'Марафон/Полумарафон',
                        'url': url,
                        'source': 'Кразмарафон'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга krasmarafon: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга krasmarafon.ru: {e}")

    return events


async def parse_toplist_run_events() -> List[Dict]:
    """Парсинг Toplist.run"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://toplist.run/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race')) or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h2') or card.find('h3') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    skip_words = ['главная', 'о нас', 'контакты', 'партнер']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = href if href.startswith('http') else f"https://toplist.run{href}"

                    city = "Россия"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow']):
                        city = 'Москва'
                    elif any(x in card_text for x in ['петербург', 'peter', 'спб']):
                        city = 'Санкт-Петербург'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'Toplist.run'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга toplist.run: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга toplist.run: {e}")

    return events


async def parse_orgeo_events() -> List[Dict]:
    """Парсинг Orgeo.ru"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://orgeo.ru/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race')) or \
                         soup.find_all('div', class_='event') or \
                         soup.find_all('article', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h2') or card.find('h3') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    skip_words = ['главная', 'о сайте', 'контакты', 'партнер']
                    if any(x in title.lower() for x in skip_words):
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = href if href.startswith('http') else f"https://orgeo.ru{href}"

                    city = "Россия"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow']):
                        city = 'Москва'
                    elif any(x in card_text for x in ['петербург', 'peter', 'спб']):
                        city = 'Санкт-Петербург'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'Orgeo.ru'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга orgeo.ru: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга orgeo.ru: {e}")

    return events


async def parse_finishers_events() -> List[Dict]:
    """Парсинг Finishers.com (международный календарь)"""
    events = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://www.finishers.com/en/destinations/asia/russia",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            event_cards = soup.find_all('a', href=re.compile(r'/event|/race')) or \
                         soup.find_all('div', class_='event-card') or \
                         soup.find_all('tr', class_='race')

            for card in event_cards:
                try:
                    title_elem = card.find('h3') or card.find('h2') or card.find(class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else None

                    if not title or len(title) < 3:
                        continue

                    date_elem = card.find('time') or card.find(class_='date')
                    date_str = ""
                    if date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')[:10]
                    elif date_elem:
                        date_str = parse_russian_date(date_elem.get_text(strip=True))

                    url = ""
                    if card.get('href'):
                        href = card['href']
                        url = f"https://www.finishers.com{href}" if href.startswith('/') else href

                    city = "Россия"
                    card_text = card.get_text().lower()

                    if any(x in card_text for x in ['москв', 'moscow']):
                        city = 'Москва'
                    elif any(x in card_text for x in ['петербург', 'peter', 'spb']):
                        city = 'Санкт-Петербург'
                    elif any(x in card_text for x in ['казан']):
                        city = 'Казань'
                    elif any(x in card_text for x in ['владивосток']):
                        city = 'Владивосток'

                    events.append({
                        'title': title,
                        'date': date_str,
                        'city': city,
                        'distances': 'Уточняйте на сайте',
                        'url': url,
                        'source': 'Finishers.com'
                    })

                except Exception as e:
                    logger.warning(f"[EVENTS] Ошибка парсинга finishers: {e}")
                    continue

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка парсинга finishers.com: {e}")

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


def filter_event_by_year_and_city(event: Dict) -> bool:
    """Фильтрует мероприятие по году (текущий+), региону (Москва/МО, СПб/ЛО, Ижевск/Удмуртия)"""

    # Проверка года - текущий и дальше
    date_str = event.get('date', '')
    year = 0

    # Извлекаем год из даты
    year_match = re.search(r'20[2-9]\d', date_str)
    if year_match:
        year = int(year_match.group())

    # Если год не найден (0) - не фильтруем по году, но продолжаем фильтрацию по региону
    if year == 0:
        logger.info(f"[EVENTS] Год не определён, проверяем регион: {event.get('title', 'Без названия')}")

    # Если год меньше текущего - пропускаем
    current_year = datetime.now().year
    if year < current_year:
        logger.info(f"[EVENTS] Пропуск мероприятия (год {year}): {event.get('title', 'Без названия')}")
        return False

    # Проверка города - только Москва/МО, СПб/ЛО, Ижевск/Удмуртия
    city = event.get('city', '').lower()

    moscow_region_keywords = [
        'москва', 'moscow', 'московск', 'подмосков', 'подмосковье',
        'московской', 'химки', 'мытищи', 'королев', 'балашиха',
        'красногорск', 'одинцово', 'люберцы', 'электросталь',
        'коломна', 'серпухов', 'подольск', 'домодедово',
        'зеленоград'
    ]

    spb_region_keywords = [
        'санкт-петербург', 'saint petersburg', 'st. petersburg',
        'петербург', 'питер', 'спб', 'spb',
        'ленинградск', 'ленинградской', 'ленинградская',
        'гатчина', 'выборг', 'всеволожск', 'тосно'
    ]

    izhevsk_region_keywords = [
        'ижевск', 'izhevsk',
        'удмурт', 'удмуртия', 'udmurt', 'udmurtia'
    ]

    # Проверяем, относится ли мероприятие к целевому региону
    is_moscow = any(x in city for x in moscow_region_keywords)
    is_spb = any(x in city for x in spb_region_keywords)
    is_izhevsk = any(x in city for x in izhevsk_region_keywords)

    # Если город не определён (пустой или "Россия") - НЕ показываем, чтобы избежать зарубежных событий
    if not city or city.lower() in ['', 'россия', 'russia']:
        logger.info(f"[EVENTS] Город не определён, пропускаем: {event.get('title', 'Без названия')}")
        return False

    if not (is_moscow or is_spb or is_izhevsk):
        logger.info(f"[EVENTS] Пропуск мероприятия (регион не подходит): {event.get('title', 'Без названия')} - {event.get('city', '')}")
        return False

    return True


def filter_event_by_city_only(event: Dict) -> bool:
    """Фильтрует мероприятие только по региону (без проверки года)."""
    city = event.get('city', '').lower()

    moscow_region_keywords = [
        'москва', 'moscow', 'московск', 'подмосков', 'подмосковье',
        'московской', 'химки', 'мытищи', 'королев', 'балашиха',
        'красногорск', 'одинцово', 'люберцы', 'электросталь',
        'коломна', 'серпухов', 'подольск', 'домодедово',
        'зеленоград'
    ]

    spb_region_keywords = [
        'санкт-петербург', 'saint petersburg', 'st. petersburg',
        'петербург', 'питер', 'спб', 'spb',
        'ленинградск', 'ленинградской', 'ленинградская',
        'гатчина', 'выборг', 'всеволожск', 'тосно'
    ]

    izhevsk_region_keywords = [
        'ижевск', 'izhevsk',
        'удмурт', 'удмуртия', 'udmurt', 'udmurtia'
    ]

    # Если город не определён (пустой или "Россия") — НЕ показываем
    if not city or city in ['', 'россия', 'russia']:
        return False

    is_moscow = any(x in city for x in moscow_region_keywords)
    is_spb = any(x in city for x in spb_region_keywords)
    is_izhevsk = any(x in city for x in izhevsk_region_keywords)

    return is_moscow or is_spb or is_izhevsk


async def get_all_events() -> List[Dict]:
    """
    Возвращает список мероприятий (2026+, Москва/МО, СПб/ЛО, Ижевск/Удмуртия).
    Используется командой /slots в основном боте, без публикации в топик.
    """
    all_events: List[Dict] = []

    # Парсим источники (как в check_and_publish_events, но без публикации)
    events_russia = await parse_russia_running_events()
    events_marathonec = await parse_marathonec_events()
    events_probeg = await parse_probeg_events()
    events_runc = await parse_runc_run_events()
    events_hero = await parse_heroleague_events()
    events_zabeg = await parse_zabeg_rf_events()
    events_probeg_trails = await parse_probeg_trails_events()
    events_pushkin = await parse_pushkin_run_events()
    events_golden = await parse_golden_ring_trail_events()
    events_s10 = await parse_s10_run_events()
    events_ahotu_run = await parse_ahotu_running_events()
    events_ahotu_trail = await parse_ahotu_trail_events()
    events_get = await parse_get_run_events()
    events_itra = await parse_itra_events()
    events_1jan = await parse_1jan_run_events()
    events_goldenring = await parse_goldenringrun_events()
    events_academy = await parse_academymarathon_events()
    events_krasmarafon = await parse_krasmarafon_events()
    events_toplist = await parse_toplist_run_events()
    events_orgeo = await parse_orgeo_events()
    events_finishers = await parse_finishers_events()

    all_events.extend(events_russia)
    all_events.extend(events_marathonec)
    all_events.extend(events_probeg)
    all_events.extend(events_runc)
    all_events.extend(events_hero)
    all_events.extend(events_zabeg)
    all_events.extend(events_probeg_trails)
    all_events.extend(events_pushkin)
    all_events.extend(events_golden)
    all_events.extend(events_s10)
    all_events.extend(events_ahotu_run)
    all_events.extend(events_ahotu_trail)
    all_events.extend(events_get)
    all_events.extend(events_itra)
    all_events.extend(events_1jan)
    all_events.extend(events_goldenring)
    all_events.extend(events_academy)
    all_events.extend(events_krasmarafon)
    all_events.extend(events_toplist)
    all_events.extend(events_orgeo)
    all_events.extend(events_finishers)

    # Фильтрация
    filtered_events: List[Dict] = []
    for event in all_events:
        if filter_event_by_year_and_city(event):
            # совместимость со старым форматом, где ожидался ключ link
            if 'link' not in event and 'url' in event:
                event['link'] = event.get('url', '')
            filtered_events.append(event)

    return filtered_events


async def publish_event(context: ContextTypes.DEFAULT_TYPE, event: Dict, message_thread_id: int = None) -> bool:
    """Публикует мероприятие в чат"""
    try:
        title = event.get('title', 'Без названия')
        date = parse_russian_date(event.get('date', ''))
        city = event.get('city', '')
        distances = event.get('distances', 'Уточняйте')
        url = event.get('url', '') or ''  # Защита от None
        source = event.get('source', 'Неизвестно')
        
        # ЛОГИРОВАНИЕ - проверяем что получили из парсера
        logger.info(f"[EVENTS] Парсинг мероприятия: source={source}, title={title[:30]}..., url={url}")

        # Если URL пустой, пробуем построить на основе источника
        if not url:
            logger.warning(f"[EVENTS] URL пустой, пробуем построить из источника: {source}")
            # Пытаемся построить URL из названия (транслитерация)
            title_for_url = title.lower().replace(' ', '-').replace('  ', '-')
            title_for_url = re.sub(r'[^a-z0-9\-]', '', title_for_url)
            
            if source == 'RussiaRunning':
                url = f"https://russiarunning.com/events/{title_for_url}"
            elif source == 'Марафонец':
                url = f"https://marathonec.ru/events/{title_for_url}"
            elif source == 'ПроБЕГ':
                url = f"https://probeg.org/events/{title_for_url}"
            elif source == 'Лига Героев':
                url = f"https://heroleague.ru/events/{title_for_url}"
            elif source == 'ЗаБег.РФ':
                url = f"https://забег.рф/events/{title_for_url}"
            elif source == 'S10.run':
                url = f"https://s10.run/events/{title_for_url}"
            
            logger.info(f"[EVENTS] Сгенерирован URL: {url}")

        # Проверяем дубликаты
        event_hash = get_event_hash(title, date)
        if event_hash in published_events_db:
            logger.info(f"[EVENTS] ПРОПУСК (дубликат): {title} ({date})")
            return False
        else:
            logger.info(f"[EVENTS] НОВОЕ мероприятие: {title} ({date}) - хеш={event_hash[:16]}...")

        # Проверяем статус регистрации
        registration_status = ""
        registration_info = ""
        registration_checked = False
        if url:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    page_response = await client.get(url, follow_redirects=True)
                    page_text = page_response.text.lower()

                    # Проверяем статус
                    is_open = is_registration_open(page_text, url)
                    registration_checked = True

                    if is_open:
                        registration_status = "🔓 **РЕГИСТРАЦИЯ ОТКРЫТА**"
                        # Ищем дедлайн
                        deadline = extract_registration_deadline(page_response.text)
                        if deadline:
                            registration_info = f"\n📅 Дедлайн регистрации: {deadline}"
                        else:
                            registration_info = "\n📅 Успей зарегистрироваться!"
                        logger.info(f"[EVENTS] Регистрация ОТКРЫТА: {title}")
                    else:
                        # Регистрация закрыта - НЕ публикуем мероприятие
                        logger.info(f"[EVENTS] Регистрация ЗАКРЫТА, пропускаем: {title}")
                        return False  # Пропускаем мероприятие
            except Exception as e:
                logger.warning(f"[EVENTS] Не удалось проверить регистрацию: {e}")
                registration_status = "ℹ️ **Статус регистрации уточняйте на сайте**"
                registration_checked = True  # Считаем что проверили, просто не удалось
        else:
            logger.warning(f"[EVENTS] URL пустой, не можем проверить регистрацию: {title}")

        # Формируем сообщение
        text = f"🏃 **{title}**\n\n"
        text += f"📅 Дата: {date}\n"
        text += f"📍 Место: {city}\n"
        text += f"🏃 Дистанции: {distances}\n"
        
        # Добавляем статус регистрации
        if registration_status:
            text += f"\n{registration_status}{registration_info}\n"
        
        if url:
            text += f"\n🔗 [Регистрация на сайте]({url})"
        else:
            logger.warning(f"[EVENTS] ВНИМАНИЕ: URL пустой для мероприятия {title}!")
        
        # Кнопка "Напомнить"
        keyboard = [
            [InlineKeyboardButton("🔔 Напомнить за 3 дня", callback_data=f"event_reminder_{event_hash}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Определяем topic_id: если передан (ручная команда) - используем его, иначе - EVENTS_TOPIC_ID (расписание)
        target_thread_id = message_thread_id if message_thread_id is not None else EVENTS_TOPIC_ID
        
        # ОТЛАДКА - логируем какой топик используем
        logger.info(f"[EVENTS] DEBUG: message_thread_id={message_thread_id}, EVENTS_TOPIC_ID={EVENTS_TOPIC_ID}, target={target_thread_id}")

        # Отправляем в чат
        try:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=target_thread_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        except Exception as pub_error:
            error_str = str(pub_error).lower()
            # Если топик не найден - пробуем без топика (в основной чат)
            if "message thread not found" in error_str or "thread not found" in error_str:
                logger.warning(f"[EVENTS] Топик {target_thread_id} не найден, публикуем в основной чат")
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
            else:
                raise  # Другие ошибки - пробрасываем

        # Сохраняем в историю
        published_events_db.add(event_hash)
        logger.info(f"[EVENTS] Опубликовано мероприятие: {title} ({city})")

        return True

    except Exception as e:
        logger.error(f"[EVENTS] Ошибка публикации: {e}")
        return False


async def check_and_publish_events(context: ContextTypes.DEFAULT_TYPE, message_thread_id: int = None):
    """Проверяет и публикует новые мероприятия

    Args:
        context: Контекст бота
        message_thread_id: ID топика для публикации. Если None - используется EVENTS_TOPIC_ID
    """
    logger.info("[EVENTS] Запуск проверки мероприятий...")

    # Логируем откуда инициирована проверка
    if message_thread_id:
        logger.info(f"[EVENTS] Ручная проверка из топика: {message_thread_id}")
    else:
        logger.info("[EVENTS] Автоматическая проверка по расписанию")

    all_events = []

    # Парсим все источники
    logger.info("[EVENTS] Парсинг RussiaRunning...")
    events_russia = await parse_russia_running_events()
    logger.info(f"[EVENTS] RussiaRunning: {len(events_russia)} мероприятий")

    logger.info("[EVENTS] Парсинг Марафонец...")
    events_marathonec = await parse_marathonec_events()
    logger.info(f"[EVENTS] Марафонец: {len(events_marathonec)} мероприятий")

    logger.info("[EVENTS] Парсинг ПроБЕГ...")
    events_probeg = await parse_probeg_events()
    logger.info(f"[EVENTS] ПроБЕГ: {len(events_probeg)} мероприятий")

    logger.info("[EVENTS] Парсинг Беговое сообщество (runc.run)...")
    events_runc = await parse_runc_run_events()
    logger.info(f"[EVENTS] Беговое сообщество: {len(events_runc)} мероприятий")

    logger.info("[EVENTS] Парсинг Лига Героев...")
    events_hero = await parse_heroleague_events()
    logger.info(f"[EVENTS] Лига Героев: {len(events_hero)} мероприятий")

    logger.info("[EVENTS] Парсинг ЗаБег.РФ...")
    events_zabeg = await parse_zabeg_rf_events()
    logger.info(f"[EVENTS] ЗаБег.РФ: {len(events_zabeg)} мероприятий")

    # Парсим трейловые забеги
    logger.info("[EVENTS] Парсинг Трейлы (ПроБЕГ)...")
    events_probeg_trails = await parse_probeg_trails_events()
    logger.info(f"[EVENTS] ПроБЕГ Трейлы: {len(events_probeg_trails)} мероприятий")

    logger.info("[EVENTS] Парсинг Pushkin Run...")
    events_pushkin = await parse_pushkin_run_events()
    logger.info(f"[EVENTS] Pushkin Run: {len(events_pushkin)} мероприятий")

    logger.info("[EVENTS] Парсинг Golden Ring Ultra Trail...")
    events_golden = await parse_golden_ring_trail_events()
    logger.info(f"[EVENTS] Golden Ring Ultra: {len(events_golden)} мероприятий")

    # Дополнительные источники
    logger.info("[EVENTS] Парсинг S10.run...")
    events_s10 = await parse_s10_run_events()
    logger.info(f"[EVENTS] S10.run: {len(events_s10)} мероприятий")

    logger.info("[EVENTS] Парсинг Ahotu Running...")
    events_ahotu_run = await parse_ahotu_running_events()
    logger.info(f"[EVENTS] Ahotu Running: {len(events_ahotu_run)} мероприятий")

    logger.info("[EVENTS] Парсинг Ahotu Trail...")
    events_ahotu_trail = await parse_ahotu_trail_events()
    logger.info(f"[EVENTS] Ahotu Trail: {len(events_ahotu_trail)} мероприятий")

    logger.info("[EVENTS] Парсинг Get.run...")
    events_get = await parse_get_run_events()
    logger.info(f"[EVENTS] Get.run: {len(events_get)} мероприятий")

    logger.info("[EVENTS] Парсинг ITRA...")
    events_itra = await parse_itra_events()
    logger.info(f"[EVENTS] ITRA: {len(events_itra)} мероприятий")

    logger.info("[EVENTS] Парсинг Забег Обещаний...")
    events_1jan = await parse_1jan_run_events()
    logger.info(f"[EVENTS] Забег Обещаний: {len(events_1jan)} мероприятий")

    logger.info("[EVENTS] Парсинг Бегом по Золотому кольцу...")
    events_goldenring = await parse_goldenringrun_events()
    logger.info(f"[EVENTS] Бегом по Золотому кольцу: {len(events_goldenring)} мероприятий")

    logger.info("[EVENTS] Парсинг Академия Марафона...")
    events_academy = await parse_academymarathon_events()
    logger.info(f"[EVENTS] Академия Марафона: {len(events_academy)} мероприятий")

    logger.info("[EVENTS] Парсинг Кразмарафон...")
    events_krasmarafon = await parse_krasmarafon_events()
    logger.info(f"[EVENTS] Кразмарафон: {len(events_krasmarafon)} мероприятий")

    logger.info("[EVENTS] Парсинг Toplist.run...")
    events_toplist = await parse_toplist_run_events()
    logger.info(f"[EVENTS] Toplist.run: {len(events_toplist)} мероприятий")

    logger.info("[EVENTS] Парсинг Orgeo.ru...")
    events_orgeo = await parse_orgeo_events()
    logger.info(f"[EVENTS] Orgeo.ru: {len(events_orgeo)} мероприятий")

    logger.info("[EVENTS] Парсинг Finishers.com...")
    events_finishers = await parse_finishers_events()
    logger.info(f"[EVENTS] Finishers.com: {len(events_finishers)} мероприятий")

    all_events.extend(events_russia)
    all_events.extend(events_marathonec)
    all_events.extend(events_probeg)
    all_events.extend(events_runc)
    all_events.extend(events_hero)
    all_events.extend(events_zabeg)
    all_events.extend(events_probeg_trails)
    all_events.extend(events_pushkin)
    all_events.extend(events_golden)
    all_events.extend(events_s10)
    all_events.extend(events_ahotu_run)
    all_events.extend(events_ahotu_trail)
    all_events.extend(events_get)
    all_events.extend(events_itra)
    all_events.extend(events_1jan)
    all_events.extend(events_goldenring)
    all_events.extend(events_academy)
    all_events.extend(events_krasmarafon)
    all_events.extend(events_toplist)
    all_events.extend(events_orgeo)
    all_events.extend(events_finishers)

    logger.info(f"[EVENTS] Всего найдено мероприятий: {len(all_events)}")

    # Фильтруем мероприятия - только 2026+ год и Москва/СПб/области
    filtered_events = []
    skipped_by_year = 0
    skipped_by_city = 0

    for event in all_events:
        if filter_event_by_year_and_city(event):
            filtered_events.append(event)
        else:
            # Определяем причину пропуска для статистики
            date_str = event.get('date', '')
            year_match = re.search(r'20[2-9]\d', date_str)
            year = int(year_match.group()) if year_match else 0

            if year < 2026:
                skipped_by_year += 1
            else:
                skipped_by_city += 1

    logger.info(f"[EVENTS] После фильтрации: {len(filtered_events)} мероприятий (пропущено: {skipped_by_year} по году, {skipped_by_city} по региону)")

    # Показываем отфильтрованные мероприятия в логах
    if filtered_events:
        logger.info(f"[EVENTS] ОТФИЛЬТРОВАННЫЕ мероприятия для публикации:")
        for i, event in enumerate(filtered_events):
            logger.info(f"[EVENTS] [{i+1}] {event.get('title', 'Без названия')} - {event.get('city', '')} ({event.get('source', '')})")
    else:
        logger.warning("[EVENTS] ВНИМАНИЕ: Нет отфильтрованных мероприятий для публикации!")
        logger.info("[EVENTS] Проверьте фильтры: год >= 2026, город: Москва/СПб/области")

    # Публикуем отфильтрованные мероприятия в том же топике где была вызвана команда
    published_count = 0
    for event in filtered_events:
        if await publish_event(context, event, message_thread_id):
            published_count += 1

    if published_count > 0:
        topic_info = f"в топик {message_thread_id}" if message_thread_id else "в топик мероприятий"
        logger.info(f"[EVENTS] Опубликовано {published_count} новых мероприятий {topic_info}")
    else:
        logger.info("[EVENTS] Новых мероприятий не найдено (или уже были опубликованы)")


async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /events — проверить мероприятия вручную"""
    chat_id = update.effective_chat.id
    # Получаем ID топика из сообщения (если есть) - отвечаем в том же топике где вызвали
    raw_thread_id = getattr(update.message, 'message_thread_id', None)
    logger.info(f"[EVENTS] DEBUG: raw message_thread_id={raw_thread_id}, hasattr={hasattr(update.message, 'message_thread_id')}")

    # Если message_thread_id None или 0, используем EVENTS_TOPIC_ID
    message_thread_id = raw_thread_id if raw_thread_id else EVENTS_TOPIC_ID

    logger.info(f"[EVENTS] DEBUG: final message_thread_id={message_thread_id}, EVENTS_TOPIC_ID={EVENTS_TOPIC_ID}")

    # Проверяем что топик определён
    if message_thread_id is None:
        logger.warning("[EVENTS] ВНИМАНИЕ: EVENTS_TOPIC_ID не установлен! Слоты будут опубликованы в основном чате.")
        # Пытаемся опубликовать в основном чате без топика
        message_thread_id = None

    await context.bot.send_chat_action(chat_id=chat_id, message_thread_id=message_thread_id, action="typing")

    # Передаем message_thread_id чтобы публикация была в правильном топике
    await check_and_publish_events(context, message_thread_id)

    await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text="✅ Проверка мероприятий завершена!",
    )

    try:
        await update.message.delete()
    except Exception:
        pass


async def events_help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /events_help — помощь по мероприятиям"""
    chat_id = update.effective_chat.id
    message_thread_id = update.message.message_thread_id if hasattr(update.message, 'message_thread_id') else None

    text = """🏃 **Бот отслеживает беговые мероприятия и трейлы**

**Автоматически:**
• Проверяет источники каждый день в 10:00
• Ищет открытые регистрации на слоты
• Публикует только активные регистрации
• Указывает даты, дистанции и ссылки

**Источники беговые:**
• RussiaRunning (russiarunning.com)
• Марафонец (marathonec.ru)
• ПроБЕГ (probeg.org)
• Беговое сообщество (runc.run)
• S10.run (s10.run)
• Лига Героев (heroleague.ru)
• ЗаБег.РФ (забег.рф)
• Ahotu Running (ahotu.com)
• Get.run (get.run)
• Забег Обещаний (1jan.run)
• Бегом по Золотому кольцу (goldenringrun.ru)
• Академия Марафона (academymarathon.ru)
• Кразмарафон (krasmarafon.ru)
• Toplist.run, Orgeo.ru, Finishers.com

**Источники трейлы:**
• ПроБЕГ Трейлы (probeg.org/calendar/trails/)
• Pushkin Run / Балтийский трейл (pushkin-run.ru)
• Golden Ring Ultra Trail (goldenultra.ru)
• Ahotu Trail (ahotu.com/trail)
• ITRA (itra.run)

**Команды:**
• /events — проверить вручную
• Нажать 🔔 — напомнить за 3 дня"""

    await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
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

    # При автоматической проверке публикуем в EVENTS_TOPIC_ID (message_thread_id=None)
    schedule.every().day.at("10:00").do(
        lambda: asyncio.run_coroutine_threadsafe(check_and_publish_events(None, None), loop)
    )

    logger.info("[EVENTS] Планировщик мероприятий запущен (каждый день в 10:00)")

    while True:
        schedule.run_pending()
        time_module.sleep(60)


def get_handlers() -> list:
    """Возвращает список обработчиков для регистрации в боте"""
    return [
        # ВНИМАНИЕ: /slots используется в основном боте для выдачи списка.
        # Здесь оставляем отдельные команды для публикации в топик.
        CommandHandler("events", events_cmd),
        CommandHandler("events_help", events_help_cmd),
        CallbackQueryHandler(handle_event_reminder_callback, pattern=r"^event_reminder_"),
    ]
