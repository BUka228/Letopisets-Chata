# main.py
import logging
import os
import platform
import datetime
import signal
import asyncio
import time # Добавляем time
from typing import Optional

from config import (
    TELEGRAM_BOT_TOKEN, SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE,
    MESSAGE_FILTERS, validate_config, setup_logging, JOB_CHECK_INTERVAL_MINUTES
)
import data_manager as dm
import bot_handlers
import jobs

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Defaults, ApplicationBuilder,
    CallbackQueryHandler, ConversationHandler, filters # Импортируем ConversationHandler и filters
)
from telegram.constants import ParseMode

application: Application | None = None
# Глобальные переменные для статуса
bot_start_time: float = 0.0 # Будет установлено в post_init
last_job_run_time: Optional[datetime.datetime] = None
last_job_error: Optional[str] = None

async def post_init(app: Application):
    global bot_start_time
    bot_start_time = time.time() # Устанавливаем время старта здесь
    app.bot_data['bot_start_time'] = bot_start_time # Сохраняем в bot_data
    app.bot_data['last_job_run_time'] = None
    app.bot_data['last_job_error'] = None
    logger = logging.getLogger(__name__) # Получаем логгер
    try:
        bot_info = await app.bot.get_me()
        logger.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")
        # Устанавливаем команды глобально (без локализации описаний)
        commands = [
            BotCommand("start", "Start the bot & see status"),
            BotCommand("help", "Show help message"),
            BotCommand("generate_now", "Generate today's story now"),
            BotCommand("regenerate_story", "Regenerate today's story"),
            BotCommand("story_settings", "Manage story settings (Admins)"),
            BotCommand("set_language", "Choose bot language"),
            # Команду status не показываем всем
        ]
        await app.bot.set_my_commands(commands)
        logging.info("Глобальные команды установлены.")
    except Exception as e:
        logging.error(f"Не удалось получить информацию о боте или установить команды: {e}")

def configure_handlers(app: Application):
    """Регистрирует все обработчики, включая ConversationHandlers."""
    logger = logging.getLogger(__name__)

    # --- ConversationHandler для выбора языка ---
    lang_conv_handler = ConversationHandler(
        entry_points=[
             CommandHandler("set_language", bot_handlers.ask_language),
             CallbackQueryHandler(bot_handlers.ask_language, pattern=f"^{bot_handlers.CB_CHANGE_LANG}$")
        ],
        states={
            bot_handlers.SELECTING_LANG: [
                CallbackQueryHandler(bot_handlers.set_language_conv, pattern="^conv_setlang_"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", bot_handlers.cancel_conv), # Отмена командой
            CallbackQueryHandler(bot_handlers.cancel_conv, pattern=f"^{bot_handlers.CB_CANCEL_CONV}$") # Отмена кнопкой
            ],
        conversation_timeout=300 # 5 минут
    )

    # --- ConversationHandler для установки времени ---
    time_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(bot_handlers.ask_set_time, pattern=f"^{bot_handlers.CB_CHANGE_TIME}$")
        ],
        states={
            bot_handlers.AWAITING_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handlers.set_time_input),
                CallbackQueryHandler(bot_handlers.set_time_default_button, pattern=f"^{bot_handlers.CB_SET_TIME_DEFAULT}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", bot_handlers.cancel_conv), # Отмена командой
            CallbackQueryHandler(bot_handlers.cancel_conv, pattern=f"^{bot_handlers.CB_CANCEL_CONV}$") # Отмена кнопкой
            ],
        conversation_timeout=120 # 2 минуты
    )

    # --- Добавляем обработчики ---
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(CommandHandler("regenerate_story", bot_handlers.regenerate_story))
    app.add_handler(CommandHandler("story_settings", bot_handlers.story_settings_command))
    app.add_handler(CommandHandler("status", bot_handlers.status_command))

    # Добавляем ConversationHandlers (должны идти перед некоторыми CallbackQueryHandlers)
    app.add_handler(lang_conv_handler)
    app.add_handler(time_conv_handler)

    # Добавляем обработчики для кнопок НАСТРОЕК и ФИДБЭКА
    # Убедимся, что паттерны не пересекаются с ConversationHandler
    app.add_handler(CallbackQueryHandler(bot_handlers.settings_button_handler, pattern=f"^({bot_handlers.CB_TOGGLE_STATUS}|{bot_handlers.CB_SHOW_SETTINGS})$")) # Кнопки настроек
    app.add_handler(CallbackQueryHandler(bot_handlers.feedback_button_handler, pattern="^feedback_")) # Кнопки фидбэка

    # Обработчик сообщений (должен идти одним из последних)
    app.add_handler(MessageHandler(MESSAGE_FILTERS, bot_handlers.handle_message))

    logger.info("Обработчики команд, диалогов, кнопок и сообщений зарегистрированы.")


def configure_scheduler(app: Application):
    # ... (код без изменений) ...
    job_queue = app.job_queue
    if not job_queue: logging.warning("JobQueue не инициализирована."); return
    interval_seconds = datetime.timedelta(minutes=JOB_CHECK_INTERVAL_MINUTES).total_seconds()
    app.bot_data['scheduled_check_job'] = job_queue.run_repeating(
        jobs.daily_story_job, interval=interval_seconds, first=15,
        name="check_due_stories_job", data={'application': app} # Передаем application
    )
    logging.info(f"Задача проверки историй запланирована каждые {JOB_CHECK_INTERVAL_MINUTES} мин.")

# Функция shutdown_signal_handler (без изменений)
async def shutdown_signal_handler(signal_num):
    global application; sig = signal.Signals(signal_num); logging.warning(f"Получен сигнал остановки {sig.name}. Завершаю работу...");
    if application: logging.info("Остановка PTB Application..."); await asyncio.create_task(application.shutdown(), name="PTB Shutdown"); await asyncio.sleep(1); logging.info("PTB Application остановлен.")
    else: logging.warning("Объект Application не найден при остановке.")
    logging.info("Закрытие соединений БД..."); dm.close_all_connections(); logging.info("Соединения БД закрыты. Бот остановлен.")

# Функция main (без изменений, кроме вызова setup_logging и инициализации bot_data)
def main() -> None:
    global application, bot_start_time
    setup_logging(); logger = logging.getLogger(__name__)
    logger.info("Инициализация бота..."); 
    try: validate_config(); dm.load_data(); 
    except Exception as e: logging.critical(f"Ошибка инициализации: {e}", exc_info=True); return
    defaults = Defaults(parse_mode=ParseMode.HTML); application = (ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).defaults(defaults).post_init(post_init).build())
    # Инициализация bot_data
    application.bot_data['last_job_run_time'] = None; application.bot_data['last_job_error'] = None; application.bot_data['bot_start_time'] = time.time() # Сохраняем время старта

    configure_handlers(application); configure_scheduler(application); logger.info("Настройка сигналов..."); loop = asyncio.get_event_loop()
    def _signal_wrapper(sig_num, frame): logging.debug(f"Перехват сигнала {sig_num}"); loop.create_task(shutdown_signal_handler(sig_num))
    signals_to_handle = (signal.SIGINT, signal.SIGTERM); is_windows = platform.system() == "Windows"; logger.info(f"Настройка сигналов через {'signal.signal' if is_windows else 'loop.add_signal_handler'}..."); sig_target = (signal.SIGINT,) if is_windows else signals_to_handle
    for sig in sig_target: 
        try: 
            if not is_windows: loop.add_signal_handler(sig, lambda s=sig: loop.create_task(shutdown_signal_handler(s)))
            else: signal.signal(sig, _signal_wrapper)
        except Exception as e: logging.error(f"Не удалось установить обработчик для {sig}: {e}")
    logger.info("Запуск polling..."); 
    try: application.run_polling(allowed_updates=Update.ALL_TYPES); 
    except Exception as e: logging.critical(f"Критическая ошибка polling: {e}", exc_info=True)
    finally: logging.info("Polling завершен."); dm.close_all_connections(); logging.info("Финальное закрытие соединений БД."); logging.info("Процесс бота завершен.")

if __name__ == "__main__": main()