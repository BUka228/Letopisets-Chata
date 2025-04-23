# main.py
import logging
import os
import platform
import datetime
import signal
import asyncio

# Импортируем компоненты
from config import (
    TELEGRAM_BOT_TOKEN, SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE,
    MESSAGE_FILTERS, validate_config, setup_logging # Импортируем setup_logging
)
import data_manager as dm
import bot_handlers # Импортируем все обработчики
import jobs

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Defaults, ApplicationBuilder,
    CallbackQueryHandler # Добавляем CallbackQueryHandler
)
from telegram.constants import ParseMode

# Настройка логирования будет вызвана в main()
application: Application | None = None

async def post_init(app: Application):
    try:
        bot_info = await app.bot.get_me()
        logging.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")
    except Exception as e:
        logging.error(f"Не удалось получить информацию о боте при запуске: {e}")

def configure_handlers(app: Application):
    # Команды
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(CommandHandler("regenerate_story", bot_handlers.regenerate_story))
    app.add_handler(CommandHandler("story_on", bot_handlers.story_on_off))
    app.add_handler(CommandHandler("story_off", bot_handlers.story_on_off))
    app.add_handler(CommandHandler("story_settings", bot_handlers.story_settings))
    app.add_handler(CommandHandler("set_language", bot_handlers.set_language))
    app.add_handler(CommandHandler("status", bot_handlers.status_command))
    # Кнопки
    app.add_handler(CallbackQueryHandler(bot_handlers.button_handler))
    # Сообщения
    app.add_handler(MessageHandler(MESSAGE_FILTERS, bot_handlers.handle_message))
    logging.info("Обработчики команд, кнопок и сообщений зарегистрированы.")

def configure_scheduler(app: Application):
    job_queue = app.job_queue
    if not job_queue:
        logging.warning("JobQueue не инициализирована. Планировщик не настроен.")
        return
    run_time = datetime.time(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, tzinfo=SCHEDULE_TIMEZONE)
    # Сохраняем ссылку на job для возможного обновления времени в будущем
    app.bot_data['daily_job'] = job_queue.run_daily(
        jobs.daily_story_job, time=run_time, name="daily_story_generation"
    )
    logging.info(f"Ежедневная задача запланирована на {run_time.strftime('%H:%M:%S %Z')}.")

async def shutdown_signal_handler(signal_num):
    global application
    sig = signal.Signals(signal_num)
    logging.warning(f"Получен сигнал остановки {sig.name} ({sig.value}). Завершаю работу...")
    if application:
        logging.info("Запускаю штатную остановку Telegram Bot Application...")
        await asyncio.create_task(application.shutdown(), name="PTB Shutdown")
        await asyncio.sleep(1)
        logging.info("Telegram Bot Application остановлен.")
    else:
        logging.warning("Объект Application не найден при попытке остановки.")
    logging.info("Закрытие соединений с базой данных...")
    dm.close_all_connections()
    logging.info("Соединения с базой данных закрыты. Бот остановлен.")

def main() -> None:
    global application
    setup_logging() # Настраиваем логирование в самом начале
    logger = logging.getLogger(__name__) # Получаем логгер после настройки

    logger.info("Инициализация бота...")
    try:
        validate_config()
        dm.load_data() # Инициализация БД
    except Exception as e:
         logging.critical(f"Ошибка инициализации: {e}", exc_info=True)
         return

    # Настройка клиента Gemini больше не нужна здесь

    defaults = Defaults(parse_mode=ParseMode.HTML)
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .defaults(defaults)
        .post_init(post_init)
        .build()
    )

    configure_handlers(application)
    configure_scheduler(application)

    logger.info("Настройка обработчиков сигналов...")
    loop = asyncio.get_event_loop()

    def _signal_wrapper(sig_num, frame):
        logging.debug(f"Перехват сигнала {sig_num} через signal.signal")
        asyncio.ensure_future(shutdown_signal_handler(sig_num))

    signals_to_handle = (signal.SIGINT, signal.SIGTERM)
    is_windows = platform.system() == "Windows"
    logger.info(f"Настройка сигналов через {'signal.signal' if is_windows else 'loop.add_signal_handler'}...")
    sig_target = (signal.SIGINT,) if is_windows else signals_to_handle
    for sig in sig_target:
        try:
             if not is_windows:
                 loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_signal_handler(s)))
             else:
                 signal.signal(sig, _signal_wrapper)
        except Exception as e:
            logging.error(f"Не удалось установить обработчик для {sig}: {e}")

    # --- ИСПРАВЛЕНО ЗДЕСЬ ---
    logger.info("Запуск polling...")
    try: # <--- Начало блока try на новой строке
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logging.critical(f"Критическая ошибка polling: {e}", exc_info=True)
    finally:
        logging.info("Polling завершен или остановлен.")
        dm.close_all_connections()
        logging.info("Финальное закрытие соединений БД выполнено.")
        logging.info("Процесс бота завершен.")
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

if __name__ == "__main__":
    main()