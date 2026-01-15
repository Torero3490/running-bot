import os
import asyncio
import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, JobQueue, filters

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
        time=datetime.time(3, 0),
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
    
    # Здесь добавляем ВСЕ обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", set_daily_morning))
    application.add_handler(CommandHandler("stopmorning", stop_morning))
    application.add_handler(CommandHandler("chatid", get_chat_id))  # <-- Добавлен сюда
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    await application.initialize()
    await application.start()
    
    await application.updater.start_polling()
    
    await asyncio.Event().wait()

if __name__ == "__main__":

    asyncio.run(main())
