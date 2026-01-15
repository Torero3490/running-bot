import asyncio
import os
import datetime
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Привет! Я ваш бот. Отправьте мне сообщение, и я повторю его.'
    )

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(update.message.text)

async def good_morning(context: ContextTypes.DEFAULT_TYPE):
    """Функция отправки доброго утра"""
    chat_id = context.job.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text="☀️ Доброе утро! Отличного дня и продуктивной пробежки!"
    )

async def set_daily_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /morning для включения ежедневных утренних сообщений"""
    chat_id = update.message.chat_id
    
    current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    context.job_queue.run_daily(
        good_morning,
        time=datetime.time(3, 0),  # 06:00 по Москве (UTC+3)
        chat_id=chat_id,
        name=f"morning_{chat_id}"
    )
    
    await update.message.reply_text("✅ Теперь я буду писать 'Доброе утро!' каждый день в 06:00")

async def stop_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stopmorning для отключения утренних сообщений"""
    chat_id = update.message.chat_id
    
    current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    await update.message.reply_text("❌ Утренние сообщения отключены")

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /chatid для получения ID чата"""
    chat = update.message.chat
    await update.message.reply_text(f"ID этого чата: {chat.id}")

async def main():
    application = Application.builder().token(os.environ.get("BOT_TOKEN")).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", set_daily_morning))
    application.add_handler(CommandHandler("stopmorning", stop_morning))
    application.add_handler(CommandHandler("chatid", get_chat_id))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
@app.route('/')
def home():
    return 'Bot is running!'

@app.route('/health')
def health():
    return 'OK', 200

async def run_flask():
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 10000))

if __name__ == "__main__":
    import threading
    
    # Запуск Flask в отдельном потоке
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000))
    flask_thread.daemon = True
    flask_thread.start()
    
    # Запуск бота
    asyncio.run(main())