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
    MESSAGE_FILTERS, validate_config, setup_logging, # Импортируем setup_logging
    JOB_CHECK_INTERVAL_MINUTES # <-- Импортируем интервал
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
    """Выполняется после инициализации Application для логирования информации о боте."""
    try:
        bot_info = await app.bot.get_me()
        logging.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")
        # Можно здесь установить команды один раз при старте, если язык не важен для них
        # await bot_handlers.set_default_commands(app)
    except Exception as e:
        logging.error(f"Не удалось получить информацию о боте при запуске: {e}")

def configure_handlers(app: Application):
    """Регистрирует все обработчики команд, кнопок и сообщений."""
    # Команды
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(CommandHandler("regenerate_story", bot_handlers.regenerate_story))
    app.add_handler(CommandHandler("story_on", bot_handlers.story_on_off))
    app.add_handler(CommandHandler("story_off", bot_handlers.story_on_off))
    app.add_handler(CommandHandler("story_settings", bot_handlers.story_settings))
    app.add_handler(CommandHandler("set_story_time", bot_handlers.set_story_time_command))
    app.add_handler(CommandHandler("set_language", bot_handlers.set_language))
    app.add_handler(CommandHandler("status", bot_handlers.status_command))
    # Кнопки
    app.add_handler(CallbackQueryHandler(bot_handlers.button_handler))
    # Сообщения
    app.add_handler(MessageHandler(MESSAGE_FILTERS, bot_handlers.handle_message))
    logging.info("Обработчики команд, кнопок и сообщений зарегистрированы.")

def configure_scheduler(app: Application):
    """Настраивает периодический запуск задачи проверки и генерации историй."""
    job_queue = app.job_queue
    if not job_queue:
        logging.warning("JobQueue не инициализирована (возможно, отсутствует APScheduler?). Планировщик не настроен.")
        return
    # Запускаем задачу периодически
    interval_seconds = datetime.timedelta(minutes=JOB_CHECK_INTERVAL_MINUTES).total_seconds()
    # Запускаем первый раз с небольшой задержкой
    app.bot_data['scheduled_check_job'] = job_queue.run_repeating(
        jobs.daily_story_job, # Вызываем обновленную функцию из jobs.py
        interval=interval_seconds,
        first=15, # Запустить через 15 секунд после старта
        name="check_due_stories_job" # Имя задачи
    )
    logging.info(f"Задача проверки историй запланирована каждые {JOB_CHECK_INTERVAL_MINUTES} мин.")

async def shutdown_signal_handler(signal_num):
    """Обрабатывает сигналы остановки."""
    global application
    sig = signal.Signals(signal_num)
    logging.warning(f"Получен сигнал остановки {sig.name} ({sig.value}). Завершаю работу...")
    if application:
        logging.info("Запускаю штатную остановку Telegram Bot Application...")
        await asyncio.create_task(application.shutdown(), name="PTB Shutdown")
        await asyncio.sleep(1) # Даем время завершиться
        logging.info("Telegram Bot Application остановлен.")
    else:
        logging.warning("Объект Application не найден при попытке остановки.")
    logging.info("Закрытие соединений с базой данных...")
    dm.close_all_connections()
    logging.info("Соединения с базой данных закрыты. Бот остановлен.")

def main() -> None:
    """Основная функция запуска бота."""
    global application
    setup_logging() # Настраиваем логирование в самом начале
    logger = logging.getLogger(__name__) # Получаем логгер после настройки

    logger.info("Инициализация бота...")
    try:
        validate_config() # Проверяем переменные окружения
        dm.load_data() # Инициализация БД
    except Exception as e:
         logging.critical(f"Критическая ошибка инициализации: {e}", exc_info=True)
         return # Не запускаемся, если инициализация не удалась

    # Настройка клиента Gemini (вызов API) больше не нужна здесь

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
        # Важно: создаем задачу в правильном цикле событий
        loop.create_task(shutdown_signal_handler(sig_num))

    signals_to_handle = (signal.SIGINT, signal.SIGTERM)
    is_windows = platform.system() == "Windows"
    logger.info(f"Настройка сигналов через {'signal.signal' if is_windows else 'loop.add_signal_handler'}...")
    sig_target = (signal.SIGINT,) if is_windows else signals_to_handle
    for sig in sig_target:
        try:
             if not is_windows:
                 # Этот метод предпочтительнее для async
                 loop.add_signal_handler(sig, lambda s=sig: loop.create_task(shutdown_signal_handler(s)))
             else:
                 # Fallback для Windows
                 signal.signal(sig, _signal_wrapper)
        except (NotImplementedError, ValueError, RuntimeError, AttributeError) as e:
             # Ловим разные ошибки, которые могут возникнуть при настройке сигналов
             logging.error(f"Не удалось установить обработчик для {sig}: {e}")

    logger.info("Запуск polling...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logging.critical(f"Критическая ошибка polling: {e}", exc_info=True)
    finally:
        logging.info("Polling завершен или остановлен.")
        # Финальное закрытие соединений на всякий случай
        dm.close_all_connections()
        logging.info("Финальное закрытие соединений БД выполнено.")
        logging.info("Процесс бота завершен.")

if __name__ == "__main__":
    main()