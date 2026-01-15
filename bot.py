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
    chat_id = context.job.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text="☀️ Доброе утро! Отличного дня и продуктивной пробежки!"
    )

async def set_daily_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    context.job_queue.run_daily(
        good_morning,
        time=datetime.time(3, 0),  # 06:00 по Москве
        chat_id=chat_id,
        name=f"morning_{chat_id}"
    )
    
    await update.message.reply_text("✅ Теперь я буду писать 'Доброе утро!' каждый день в 06:00")

async def stop_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    current_jobs = context.job_queue.get_jobs_by_name(f"morning_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    await update.message.reply_text("❌ Утренние сообщения отключены")

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.chat
    await update.message.reply_text(f"ID этого чата: {chat.id}")

async def run_bot():
    application = Application.builder().token(os.environ.get("BOT_TOKEN")).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("morning", set_daily_morning))
    application.add_handler(CommandHandler("stopmorning", stop_morning))
    application.add_handler(CommandHandler("chatid", get_chat_id))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
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

    asyncio.run(main())
