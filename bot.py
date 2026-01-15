import asyncio
import os
import datetime
import httpx
import random
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ApplicationBuilder

app = Flask(__name__)

MOSCOW_LAT = 55.7558
MOSCOW_LON = 37.6173
PITER_LAT = 59.9343
PITER_LON = 30.3351

# Хранилище чатов с утренними сообщениями
morning_chats = {}

sunny_greetings = ["☀️ Доброе утро! Солнце светит — бег ждёт!", "🌞 Отличный день для пробежки!", "☀️ Солнце встало — и ты вставай!"]
cloudy_greetings = ["☁️ Облачно — идеально для бега!", "🌥️ Небо серое, а ты — яркий!", "☁️ Облака не помеха бегу!"]
rainy_greetings = ["🌧️ Дождь — это душ для бегуна!", "☔ Промокни и беги!", "🌧️ Капли дождя одобряют твою пробежку!"]
snowy_greetings = ["❄️ Снег скрипит — ты беги!", "🏃‍♂️❄️ Зимняя сказка для бега!", "❄️ Снежное утро — волшебная пробежка!"]
default_greetings = ["🏃‍♂️ Доброе утро! Время бежать!", "🚀 Доброе утро! Покоряй дистанции!", "💪 Доброе утро! Ты готов!"]

async def get_weather(lat: float, lon: float) -> dict:
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&windspeed_unit=ms&timezone=auto&lang=ru"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            data = response.json()
        current = data.get("current_weather", {})
        return {"temp": current.get("temperature", 0), "weathercode": current.get("weathercode", 0)}
    except Exception as e:
        print(f"Weather error: {e}")
        return {"temp": 0, "weathercode": 0}

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

async def send_good_morning(bot, chat_id: int):
    """Отправить утреннее сообщение"""
    weather_moscow = await get_weather(MOSCOW_LAT, MOSCOW_LON)
    weather_piter = await get_weather(PITER_LAT, PITER_LON)
    greeting = get_greeting(weather_moscow["weathercode"])
    
    message = f"{greeting}\n\n📍 Москва: {weather_moscow['temp']:+.1f}°C, {get_description(weather_moscow['weathercode'])}\n📍 Питер: {weather_piter['temp']:+.1f}°C, {get_description(weather_piter['weathercode'])}"
    
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        print(f"Error sending message to {chat_id}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Привет! Бот бегового чата.\n/morning - доброе утро в 06:00\n/stopmorning - отключить')

async def set_daily_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    morning_chats[chat_id] = True
    await update.message.reply_text("✅ Доброе утро в 06:00 со смешными сообщениями и погодой!")

async def stop_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in morning_chats:
        del morning_chats[chat_id]
    await update.message.reply_text("❌ Утренние сообщения отключены")

@app.route('/')
def home():
    return 'Bot is running!'

@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

async def check_morning():
    """Проверять время и отправлять утренние сообщения"""
    while True:
        now = datetime.datetime.now()
        # 06:00 по Москве (UTC+3 = 03:00 UTC)
        if now.hour == 3 and now.minute == 0:
            bot = application.bot
            for chat_id in morning_chats.keys():
                await send_good_morning(bot, chat_id)
        await asyncio.sleep(60)

application = None

async def main():
    global application
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask started")
    
    application = ApplicationBuilder().token(os.environ.get("BOT_TOKEN")).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", set_daily_morning))
    application.add_handler(CommandHandler("stopmorning", stop_morning))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    print("Bot started successfully!")
    
    await check_morning()

if __name__ == "__main__":
    asyncio.run(main())
