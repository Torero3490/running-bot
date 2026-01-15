import asyncio
import os
import datetime
import httpx
import random
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, 
    JobQueue, ApplicationBuilder
)

app = Flask(__name__)

# Координаты городов
MOSCOW_LAT = 55.7558
MOSCOW_LON = 37.6173
PITER_LAT = 59.9343
PITER_LON = 30.3351

# Приветствия для солнечной погоды
sunny_greetings = [
    "☀️ Доброе утро, солнце уже встало, а ты? Время на пробежку!",
    "🌞 Утро начинается с улыбки и кроссовок! Сегодня будет отличный день!",
    "😎 Солнце светит, птицы поют, дорога ждёт! Бегом к успеху!",
    "☀️ Доброе утро! Такая погода создана для идеальной пробежки!",
    "🌅 Солнечное утро = идеальный бег! Выводи себя на старт!",
]

# Приветствия для пасмурной погоды
cloudy_greetings = [
    "☁️ Доброе утро! Облака не помешают твоему бегу!",
    "🌥️ Утро облачное, но ты точно зажжёшь своей пробежкой!",
    "☁️ Небо серое, а ты — яркий! Время бежать!",
    "🌫️ Легкий туман, легкий бег! Доброе утро!",
    "☁️ Облака — это просто фон для твоей крутой пробежки!",
]

# Приветствия для дождливой погоды
rainy_greetings = [
    "🌧️ Доброе утро! Дождь? Это просто душ для бегуна!",
    "☔ Промокни, но не сдавайся! Дождь — это твой союзник!",
    "🌧️ Капли дождя будут аплодировать твоей пробежке!",
    "☔ Дождь усиливается, твоя мотивация — тоже! Бегом!",
    "🌧️ Сегодня будешь самым мокрым, но довольным!",
]

# Приветствия для снежной погоды
snowy_greetings = [
    "❄️ Доброе утро! Снег скрипит, а ты — беги!",
    "🏃‍♂️❄️ Снежное утро — волшебная пробежка ждёт тебя!",
    "❄️ Буквально утро в снежном королевстве! Беги и наслаждайся!",
    "🌨️ Снег под ногами, радость в сердце! Доброе утро!",
    "❄️ Сегодня ты — главная звезда зимней пробежки!",
]

# Приветствия для ветреной погоды
windy_greetings = [
    "💨 Доброе утро! Ветер будет подталкивать тебя сзади!",
    "🌬️ Ветер? Это просто природа делает тебе интервалы!",
    "💨 Сильный ветер — ты бежишь, а он сопротивляется. Ты сильнее!",
    "🌬️ Попутный ветер в спину! Доброе утро!",
    "💨 Ветер добавляет драмы твоей пробежке!",
]

# Универсальные приветствия
default_greetings = [
    "🏃‍♂️ Доброе утро! Твоя пробежка ждёт тебя!",
    "🚀 Доброе утро! Пора покорять дистанции!",
    "💪 Доброе утро! Каждый километр делает тебя сильнее!",
    "🎯 Доброе утро! Цель дня — хотя бы одна пробежка!",
    "🔥 Доброе утро! Зажги этот день своей пробежкой!",
    "⭐ Доброе утро! Сегодня твой день блистать!",
    "🏆 Доброе утро! Чемпионы встают рано!",
    "❤️ Доброе утро! Бег — это лучший подарок себе!",
]

async def get_weather_openmeteo(lat: float, lon: float) -> dict:
    """Получить погоду через Open-Meteo (бесплатно, без API ключа)"""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&windspeed_unit=ms&timezone=auto&lang=ru"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            data = response.json()
        
        current = data.get("current_weather", {})
        
        return {
            "temp": current.get("temperature", 0),
            "windspeed": current.get("windspeed", 0),
            "weathercode": current.get("weathercode", 0)
        }
        
    except Exception as e:
        return {"temp": 0, "windspeed": 0, "weathercode": 0}

def get_weather_description(weathercode: int) -> str:
    """Получить описание погоды по коду WMO"""
    codes = {
        0: "ясно",
        1: "малооблачно",
        2: "облачно с прояснениями",
        3: "пасмурно",
        45: "туман",
        48: "туман и изморозь",
        51: "морось",
        53: "умеренная морось",
        55: "сильная морось",
        61: "слабый дождь",
        63: "умеренный дождь",
        65: "сильный дождь",
        71: "слабый снег",
        73: "умеренный снег",
        75: "сильный снег",
        80: "слабый дождь со снегом",
        81: "умеренный дождь со снегом",
        82: "сильный дождь со снегом",
        95: "гроза",
        96: "гроза с градом",
        99: "сильная гроза с градом",
    }
    return codes.get(weathercode, "неизвестно")

def get_greeting_by_weather(weathercode: int) -> str:
    """Выбрать приветствие по погоде"""
    # Дождь со снегом
    if 80 <= weathercode <= 82:
        return random.choice(snowy_greetings)
    # Дождь
    elif 51 <= weathercode <= 67:
        return random.choice(rainy_greetings)
    # Снег
    elif 71 <= weathercode <= 77:
        return random.choice(snowy_greetings)
    # Туман
    elif 45 <= weathercode <= 48:
        return random.choice(cloudy_greetings)
    # Ясно
    elif weathercode == 0:
        return random.choice(sunny_greetings)
    # Облачно
    elif 1 <= weathercode <= 3:
        return random.choice(cloudy_greetings)
    # Гроза
    elif 95 <= weathercode <= 99:
        return random.choice(rainy_greetings)
    # По умолчанию
    else:
        return random.choice(default_greetings)

async def good_morning(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    
    # Получаем погоду
    weather_moscow = await get_weather_openmeteo(MOSCOW_LAT, MOSCOW_LON)
    weather_piter = await get_weather_openmeteo(PITER_LAT, PITER_LON)
    
    # Выбираем приветствие по погоде в Москве
    greeting = get_greeting_by_weather(weather_moscow["weathercode"])
    
    # Формируем сообщение
    moscow_desc = get_weather_description(weather_moscow["weathercode"])
    piter_desc = get_weather_description(weather_piter["weathercode"])
    
    message = (
        f"{greeting}\n\n"
        f"📍 Москва: {weather_moscow['temp']:+.1f}°C, {moscow_desc}\n"
        f"📍 Санкт-Петербург: {weather_piter['temp']:+.1f}°C, {piter_desc}"
    )
    
    await context.bot.send_message(chat_id=job.chat_id, text=message)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Привет! Я ваш бот для бегового чата.\n'
        'Команды:\n'
        '/morning - включить доброе утро в 06:00\n'
        '/stopmorning - отключить утренние сообщения\n'
        '/weather - погода в Москве\n'
        '/piter - погода в Санкт-Петербурге'
    )

async def set_daily_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    if context.job_queue is None:
        await update.message.reply_text("Ошибка: планировщик не инициализирован")
        return
    
    current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    context.job_queue.run_daily(
        good_morning,
        time=datetime.time(3, 0),  # 06:00 по Москве
        chat_id=chat_id,
        name=f"morning_{chat_id}"
    )
    
    await update.message.reply_text("✅ Теперь я буду писать доброе утро каждый день в 06:00 со смешными сообщениями и погодой!")

async def stop_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    if context.job_queue is None:
        await update.message.reply_text("Ошибка: планировщик не инициализирован")
        return
    
    current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    await update.message.reply_text("❌ Утренние сообщения отключены")

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Погода в Москве"""
    weather_data = await get_weather_openmeteo(MOSCOW_LAT, MOSCOW_LON)
    description = get_weather_description(weather_data["weathercode"])
    
    await update.message.reply_text(
        f"🌤️ Москва: {weather_data['temp']:+.1f}°C, {description}\n"
        f"💨 Ветер: {weather_data['windspeed']} м/с"
    )

async def piter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Погода в Санкт-Петербурге"""
    weather_data = await get_weather_openmeteo(PITER_LAT, PITER_LON)
    description = get_weather_description(weather_data["weathercode"])
    
    await update.message.reply_text(
        f"🌤️ Санкт-Петербург: {weather_data['temp']:+.1f}°C, {description}\n"
        f"💨 Ветер: {weather_data['windspeed']} м/с"
    )

async def run_bot():
    job_queue = JobQueue()
    
    application = (
        ApplicationBuilder()
        .token(os.environ.get("BOT_TOKEN"))
        .job_queue(job_queue)
        .build()
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", set_daily_morning))
    application.add_handler(CommandHandler("stopmorning", stop_morning))
    application.add_handler(CommandHandler("weather", weather))
    application.add_handler(CommandHandler("piter", piter))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    print("Bot started successfully!")
    await asyncio.Event().wait()

async def main():
    await asyncio.gather(
        run_bot(),
        asyncio.to_thread(app.run, host='0.0.0.0', port=10000, use_reloader=False)
    )

if __name__ == "__main__":
    asyncio.run(main())

if __name__ == "__main__":
    asyncio.run(main())


