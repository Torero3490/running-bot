import asyncio
import os
import datetime
import json
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from flask import Flask
import threading

# Flask app for Render
app = Flask(__name__)

@app.route('/')
def index():
    return 'Bot is running!'

def run_flask():
    app.run(host='0.0.0.0', port=10000)

# Telegram Bot Token
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# Open-Meteo API для получения погоды (бесплатный API, не требует ключа)
async def get_weather(city: str) -> str:
    """Получает текущую погоду через Open-Meteo API"""
    coordinates = {
        'москва': {'lat': 55.7558, 'lon': 37.6173},
        'питер': {'lat': 59.9343, 'lon': 30.3351},
        'спб': {'lat': 59.9343, 'lon': 30.3351},
        'санкт-петербург': {'lat': 59.9343, 'lon': 30.3351}
    }
    
    city_lower = city.lower()
    if city_lower not in coordinates:
        return None
    
    coords = coordinates[city_lower]
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        'latitude': coords['lat'],
        'longitude': coords['lon'],
        'current_weather': 'true',
        'temperature_unit': 'celsius',
        'windspeed_unit': 'kmh'
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                current = data.get('current_weather', {})
                temp = current.get('temperature', 0)
                wind = current.get('windspeed', 0)
                
                # Определяем emoji погоды
                if temp > 20:
                    weather_emoji = "☀️"
                elif temp > 10:
                    weather_emoji = "🌤️"
                elif temp > 0:
                    weather_emoji = "🌥️"
                elif temp == 0:
                    weather_emoji = "🌨️"
                else:
                    weather_emoji = "❄️"
                
                return f"{weather_emoji} {temp}°C, ветер {wind} км/ч"
            else:
                return None
    except Exception as e:
        print(f"Ошибка получения погоды: {e}")
        return None

# Функция для получения случайного приветствия
def get_random_greeting(weather_moscow: str, weather_piter: str) -> str:
    """Возвращает случайное приветствие с учётом погоды"""
    
    greetings = [
        "Доброе утро, бегуны! 🏃‍♂️\nСегодня отличный день для тренировки!",
        "Утро доброе! 👟\nКроссовки наготове? Ноги ждут!",
        "С добрым утром! 🌅\nСегодня побежим или как?",
        "Утренний привет! ☕\nКофе выпит, можно и бежать!",
        "Доброе утро, чемпионы! 🏆\nЖду на утренней пробежке!",
        "С утра пораньше! 🌞\nЛучшее время для бега уже наступило!",
    ]
    
    # Дополнительные приветствия с учётом погоды
    if "❄️" in weather_moscow or "🌨️" in weather_moscow or temp < 5:
        cold_greetings = [
            "Бррр, доброе утро! 🥶\nСегодня холодно, но мы не сдаёмся!",
            "Морозное утро! ❄️\nОдевайтесь теплее, бегуны!",
            "Холодное утро, но тёплые сердца! ❤️\nСегодня бежим, чтобы согреться!",
        ]
        greetings.extend(cold_greetings)
    elif "☀️" in weather_moscow and temp > 20:
        warm_greetings = [
            "Жаркое утро! 🔥\nНе забудьте воду с собой!",
            "Солнечное утро! ☀️\nИдеальная погода для длинных дистанций!",
        ]
        greetings.extend(warm_greetings)
    
    import random
    return random.choice(greetings)

# Функция для получения температуры из строки погоды
def get_temp_from_weather(weather_str: str) -> float:
    """Извлекает температуру из строки погоды"""
    try:
        # Ищем температуру в формате "XX°C"
        import re
        match = re.search(r'(-?\d+)°C', weather_str)
        if match:
            return float(match.group(1))
        return 0
    except:
        return 0

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start"""
    user_name = update.message.from_user.first_name
    welcome_text = f"Привет, {user_name}! 👋\n\nЯ бот для бегового чата. Каждое утро в 06:00 я буду писать мотивационные сообщения с погодой в Москве и Санкт-Петербурге. Также буду приветствовать новых участников чата!\n\nУдачных пробежек! 🏃‍♂️"
    await update.message.reply_text(welcome_text)

# Команда /morning (ручной запуск утреннего сообщения)
async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принудительная отправка утреннего сообщения"""
    await send_morning_message(context.bot)
    await update.message.reply_text("✅ Утреннее сообщение отправлено!")

# Функция отправки утреннего сообщения
async def send_morning_message(bot) -> None:
    """Формирует и отправляет утреннее сообщение"""
    # Получаем погоду
    weather_moscow = await get_weather("москва") or "🌡️ данные о погоде недоступны"
    weather_piter = await get_weather("питер") or "🌡️ данные о погоде недоступны"
    
    # Получаем случайное приветствие
    temp_moscow = get_temp_from_weather(weather_moscow)
    greeting = get_random_greeting(weather_moscow, weather_piter)
    
    # Формируем сообщение
    today = datetime.datetime.now()
    day_name = today.strftime("%A")
    day_names_ru = {
        "Monday": "Понедельник",
        "Tuesday": "Вторник",
        "Wednesday": "Среда",
        "Thursday": "Четверг",
        "Friday": "Пятница",
        "Saturday": "Суббота",
        "Sunday": "Воскресенье"
    }
    day_ru = day_names_ru.get(day_name, day_name)
    
    current_date = today.strftime("%d.%m.%Y")
    
    message = f"""
{greeting}

📅 Сегодня {current_date}, {day_ru}

🌤 Погода в Москве: {weather_moscow}
🌤 Погода в Санкт-Петербурге: {weather_piter}

🏃‍♂️ Желаем отличной пробежки! Не забудьте:
• Размяться перед бегом
• Взять воду
• Проверить пульс
• Наслаждаться бегом!

#утро #бег #пробежка
"""
    
    # Отправляем в чат (нужно заменить на ваш chat_id)
    CHAT_ID = os.environ.get('CHAT_ID', '-1001234567890')
    
    try:
        sent_message = await bot.send_message(chat_id=CHAT_ID, text=message)
        # Планируем удаление сообщения через 30 секунд
        asyncio.create_task(delete_message_later(bot, CHAT_ID, sent_message.message_id, 30))
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")

# Функция автоматического удаления сообщений
async def delete_message_later(bot, chat_id, message_id, delay: int) -> None:
    """Удаляет сообщение через указанное количество секунд"""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"Не удалось удалить сообщение: {e}")

# Автоматическое удаление команд пользователя
async def delete_user_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет сообщения пользователей (команды) через 30 секунд"""
    if update.message:
        chat_id = update.message.chat_id
        message_id = update.message.message_id
        
        # Удаляем через 30 секунд (небольшая задержка для подтверждения)
        asyncio.create_task(delete_message_later(context.bot, chat_id, message_id, 5))

# Приветствие новых участников
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствует новых участников чата"""
    for member in update.message.new_chat_members:
        user_name = member.first_name
        
        today = datetime.datetime.now()
        day_name = today.strftime("%A")
        day_names_ru = {
            "Monday": "Понедельник",
            "Tuesday": "Вторник",
            "Wednesday": "Среда",
            "Thursday": "Четверг",
            "Friday": "Пятница",
            "Saturday": "Суббота",
            "Sunday": "Воскресенье"
        }
        day_ru = day_names_ru.get(day_name, day_name)
        current_date = today.strftime("%d.%m.%Y")
        
        welcome_messages = [
            f"🎉 Привет, {user_name}! Добро пожаловать в наш беговой чат!\n\nСегодня {current_date}, {day_ru}. Отличный день для первой пробежки! 🏃‍♂️",
            f"👋 {user_name}, приветствуем! Теперь ты часть нашей беговой семьи!\n\n📅 {current_date}, {day_ru}. Желаем ярких тренировок!",
            f"🏃‍♂️ Привет, {user_name}! Рады видеть нового бегуна!\n\nСегодня {day_ru}, {current_date}. Ждём тебя на утренней пробежке!",
        ]
        
        import random
        welcome_text = random.choice(welcome_messages)
        
        sent_message = await update.message.reply_text(welcome_text)
        # Удаляем приветствие через 30 секунд
        asyncio.create_task(delete_message_later(context.bot, update.message.chat_id, sent_message.message_id, 30))

# Планировщик для отправки утренних сообщений
async def morning_scheduler(application: Application) -> None:
    """Планировщик для автоматической отправки утренних сообщений"""
    while True:
        now = datetime.datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        
        # Если текущее время 06:00 (с учётом минуты)
        if current_hour == 6 and current_minute == 0:
            await send_morning_message(application.bot)
            # Ждём минуту, чтобы не отправить сообщение дважды
            await asyncio.sleep(60)
        else:
            # Проверяем каждую минуту
            await asyncio.sleep(60)

async def post_init(application: Application) -> None:
    """Запускается после инициализации бота"""
    # Запускаем планировщик утренних сообщений в фоновом режиме
    asyncio.create_task(morning_scheduler(application))
    print("✅ Бот запущен и готов к работе!")

async def post_shutdown(application: Application) -> None:
    """Запускается при остановке бота"""
    print("🛑 Бот остановлен")

def main() -> None:
    """Основная функция запуска бота"""
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Flask сервер запущен")
    
    # Создаём приложение бота
    application = Application.builder()\
        .token(BOT_TOKEN)\
        .post_init(post_init)\
        .post_shutdown(post_shutdown)\
        .build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", morning))
    
    # Обработчик новых участников
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    
    # Обработчик для удаления команд пользователей
    application.add_handler(MessageHandler(filters.COMMAND, delete_user_commands))
    
    # Запускаем бота (_polling для работы без вебхуков)
    print("🚀 Запуск бота...")
    application.run_polling()

if __name__ == "__main__":
    main()
