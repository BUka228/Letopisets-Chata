# main.py
import logging
import os # <--- Добавлен импорт os
import platform # <--- Можно использовать platform для более явной проверки
import datetime
import signal
import asyncio

# Импортируем компоненты из других файлов
from config import (
    TELEGRAM_BOT_TOKEN, SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE,
    MESSAGE_FILTERS, validate_config, setup_logging
)
import data_manager as dm
import gemini_client as gc
import bot_handlers
import jobs

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Defaults, ApplicationBuilder
)
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)
application: Application | None = None

async def post_init(app: Application):
    bot_info = await app.bot.get_me()
    logger.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")

def configure_handlers(app: Application):
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(MessageHandler(MESSAGE_FILTERS, bot_handlers.handle_message))
    logger.info("Обработчики команд и сообщений зарегистрированы.")

def configure_scheduler(app: Application):
    job_queue = app.job_queue
    run_time = datetime.time(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, tzinfo=SCHEDULE_TIMEZONE)
    job_daily = job_queue.run_daily(
        jobs.daily_story_job,
        time=run_time,
        name="daily_gemini_story_generation"
    )
    logger.info(f"Ежедневная задача генерации историй запланирована на {run_time.strftime('%H:%M:%S %Z')}.")

async def shutdown_signal_handler(signal_num):
    """Обрабатывает сигналы остановки (SIGINT, SIGTERM)."""
    global application
    sig = signal.Signals(signal_num)
    logger.warning(f"Получен сигнал остановки {sig.name} ({sig.value}). Завершаю работу...")
    if application:
        logger.info("Запускаю штатную остановку Telegram Bot Application...")
        # Важно: shutdown() может быть не полностью async, лучше запускать через create_task
        await asyncio.create_task(application.shutdown(), name="PTB Shutdown")
        # Небольшая пауза, чтобы дать время на завершение внутренних процессов PTB
        await asyncio.sleep(1)
        logger.info("Telegram Bot Application остановлен.")
    else:
        logger.warning("Объект Application не найден при попытке остановки.")

    logger.info("Закрытие соединений с базой данных...")
    dm.close_all_connections()
    logger.info("Соединения с базой данных закрыты. Бот остановлен.")
    # Можно добавить принудительный выход, если процесс зависает
    # asyncio.get_event_loop().stop() # Может потребоваться для чистого выхода из цикла

def main() -> None:
    global application
    logger.info("Инициализация бота...")
    try:
        validate_config()
    except ValueError as e:
        logger.critical(e)
        return

    try:
        dm.load_data()
    except Exception as e:
         logger.critical(f"Не удалось инициализировать базу данных: {e}. Запуск отменен.", exc_info=True)
         return

    if not gc.configure_gemini():
        logger.critical("Не удалось настроить Gemini API. Запуск бота отменен.")
        dm.close_all_connections()
        return

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

    # --- ИЗМЕНЕНО: Явная проверка ОС для настройки обработчиков сигналов ---
    logger.info("Настройка обработчиков сигналов остановки...")
    loop = asyncio.get_event_loop()

    # Определяем функцию-обертку для вызова из signal.signal
    def _signal_wrapper(sig_num, frame):
        logger.debug(f"Перехват сигнала {sig_num} через signal.signal")
        # Запускаем асинхронную функцию в цикле событий
        asyncio.ensure_future(shutdown_signal_handler(sig_num))

    if platform.system() != "Windows": # Используем platform.system() для надежности
        logger.info("Настройка через loop.add_signal_handler (Unix-like)...")
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                # Используем lambda, чтобы передать сам сигнал в обработчик
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_signal_handler(s)))
            except (NotImplementedError, RuntimeError) as e:
                 # Если add_signal_handler все же недоступен по какой-то причине
                 logger.warning(f"loop.add_signal_handler не поддерживается ({e}), используем fallback signal.signal...")
                 try:
                      signal.signal(sig, _signal_wrapper)
                 except Exception as sig_e:
                      logger.error(f"Не удалось установить обработчик через signal.signal для {sig}: {sig_e}")
    else:
        logger.info("Настройка через signal.signal (Windows)...")
        # На Windows используем signal.signal.
        # ПРИМЕЧАНИЕ: Graceful shutdown на Windows с сигналами менее надежен.
        # Ctrl+C (SIGINT) обычно работает, SIGTERM может не работать.
        # Ctrl+Break может быть более надежным способом прервать на Windows.
        supported_signals = (signal.SIGINT,) # На Windows часто только SIGINT надежно ловится
        for sig in supported_signals:
            try:
                signal.signal(sig, _signal_wrapper)
            except Exception as sig_e:
                logger.error(f"Не удалось установить обработчик через signal.signal для {sig}: {sig_e}")

    logger.info("Запуск бота...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"Критическая ошибка во время работы polling: {e}", exc_info=True)
    finally:
        logger.info("Polling завершен или остановлен.")
        # Закрываем соединения БД здесь на всякий случай, если обработчик сигнала не успел/не смог
        dm.close_all_connections()
        logger.info("Финальное закрытие соединений БД выполнено.")
        logger.info("Процесс бота завершен.")

if __name__ == "__main__":
    main()