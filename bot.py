import asyncio
import os
import datetime
import httpx
import random
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue, ApplicationBuilder

app = Flask(__name__)

# Координаты городов
MOSCOW_LAT = 55.7558
MOSCOW_LON = 37.6173
PITER_LAT = 59.9343
PITER_LON = 30.3351

# Приветствия
sunny_greetings = [
    "☀️ Доброе утро, солнце уже встало, а ты? Время на пробежку!",
    "🌞 Утро начинается с улыбки и кроссовок!",
    "☀️ Доброе утро! Такая погода создана для идеальной пробежки!",
]

cloudy_greetings = [
    "☁️ Доброе утро! Облака не помешают твоему бегу!",
    "🌥️ Утро облачное, но ты точно зажжёшь!",
    "☁️ Небо серое, а ты — яркий!",
]

rainy_greetings = [
    "🌧️ Доброе утро! Дождь? Это просто душ для бегуна!",
    "☔ Промокни, но не сдавайся!",
    "🌧️ Капли дождя будут аплодировать!",
]

snowy_greetings = [
    "❄️ Доброе утро! Снег скрипит, а ты — беги!",
    "🏃‍♂️❄️ Снежное утро — волшебная пробежка!",
    "❄️ Буквально утро в снежном королевстве!",
]

default_greetings = [
    "🏃‍♂️ Доброе утро! Твоя пробежка ждёт тебя!",
    "🚀 Доброе утро! Пора покорять дистанции!",
    "💪 Доброе утро! Каждый км делает тебя сильнее!",
]

async def get_weather(lat: float, lon: float) -> dict:
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&windspeed_unit=ms&timezone=auto&lang=ru"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            data = response.json()
        current = data.get("current_weather", {})
        return {
            "temp": current.get("temperature", 0),
            "windspeed": current.get("windspeed", 0),
            "weathercode": current.get("weathercode", 0)
        }
    except Exception as e:
        print(f"Weather error: {e}")
        return {"temp": 0, "windspeed": 0, "weathercode": 0}

def get_description(code: int) -> str:
    codes = {0: "ясно", 1: "малооблачно", 2: "облачно", 3: "пасмурно",
             45: "туман", 51: "морось", 61: "дождь", 63: "дождь",
             71: "снег", 73: "снег", 80: "дождь со снегом", 95: "гроза"}
    return codes.get(code, "неизвестно")

def get_greeting(code: int) -> str:
    if 51 <= code <= 67: return random.choice(rainy_greetings)
    elif 71 <= code <= 77: return random.choice(snowy_greetings)
    elif 80 <= code <= 82: return random.choice(snowy_greetings)
    elif code == 0: return random.choice(sunny_greetings)
    elif 1 <= code <= 3: return random.choice(cloudy_greetings)
    elif 95 <= code <= 99: return random.choice(rainy_greetings)
    else: return random.choice(default_greetings)

async def good_morning(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    weather_moscow = await get_weather(MOSCOW_LAT, MOSCOW_LON)
    weather_piter = await get_weather(PITER_LAT, PITER_LON)
    
    greeting = get_greeting(weather_moscow["weathercode"])
    
    message = (
        f"{greeting}\n\n"
        f"📍 Москва: {weather_moscow['temp']:+.1f}°C, {get_description(weather_moscow['weathercode'])}\n"
        f"📍 Санкт-Петербург: {weather_piter['temp']:+.1f}°C, {get_description(weather_piter['weathercode'])}"
    )
    
    await context.bot.send_message(chat_id=job.chat_id, text=message)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Привет! Я бот для бегового чата.\n'
        '/morning - доброе утро в 06:00\n'
        '/stopmorning - отключить\n'
        '/weather - погода Москва\n'
        '/piter - погода Питер'
    )

async def set_daily_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.job_queue is None:
        await update.message.reply_text("Ошибка планировщика")
        return
    
    chat_id = update.message.chat_id
    current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    context.job_queue.run_daily(
        good_morning,
        time=datetime.time(3, 0),
        chat_id=chat_id,
        name=f"morning_{chat_id}"
    )
    
    await update.message.reply_text("✅ Доброе утро со смешными сообщениями и погодой в 06:00!")

async def stop_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if context.job_queue:
        current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
        for job in current_jobs:
            job.schedule_removal()
    await update.message.reply_text("❌ Утренние сообщения отключены")

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    w = await get_weather(MOSCOW_LAT, MOSCOW_LON)
    await update.message.reply_text(f"🌤️ Москва: {w['temp']:+.1f}°C, {get_description(w['weathercode'])}")

async def piter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    w = await get_weather(PITER_LAT, PITER_LON)
    await update.message.reply_text(f"🌤️ Питер: {w['temp']:+.1f}°C, {get_description(w['weathercode'])}")

# Flask routes для Render
@app.route('/')
def home():
    return 'Bot is running!'

@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

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
    
    print("Bot started!")
    await asyncio.Event().wait()

async def main():
    # Запускаем Flask и бота параллельно
    loop = asyncio.get_event_loop()
    flask_task = loop.run_in_executor(None, run_flask)
    await asyncio.gather(
        run_bot(),
        flask_task
    )

if __name__ == "__main__":
    asyncio.run(main())
