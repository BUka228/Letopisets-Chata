# main.py
import logging
import os
import platform
import datetime
import signal
import asyncio

# Импортируем компоненты из других файлов
from config import (
    TELEGRAM_BOT_TOKEN, SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE,
    MESSAGE_FILTERS, validate_config, setup_logging, DATABASE_URL # Добавили DATABASE_URL для ясности
)
import data_manager as dm # Теперь работает с PostgreSQL
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

# configure_handlers, configure_scheduler без изменений

async def shutdown_signal_handler(signal_num):
    """Обрабатывает сигналы остановки (SIGINT, SIGTERM)."""
    global application
    sig = signal.Signals(signal_num)
    logger.warning(f"Получен сигнал остановки {sig.name} ({sig.value}). Завершаю работу...")
    if application:
        logger.info("Запускаю штатную остановку Telegram Bot Application...")
        await asyncio.create_task(application.shutdown(), name="PTB Shutdown")
        await asyncio.sleep(1)
        logger.info("Telegram Bot Application остановлен.")
    else:
        logger.warning("Объект Application не найден при попытке остановки.")

    # УДАЛЕНО: dm.close_all_connections() # Больше не нужно
    logger.info("Бот остановлен.")


# --- ИЗМЕНЕНО: main теперь async ---
async def main() -> None: # <--- Стала async
    global application
    logger.info("Инициализация бота...")
    try:
        validate_config()
    except ValueError as e:
        logger.critical(e)
        return

    # --- ИЗМЕНЕНО: Асинхронная инициализация БД ---
    try:
        await dm.load_data() # <--- Вызываем через await
    except Exception as e:
         # Лог ошибки уже внутри load_data
         return # Прерываем запуск

    # --- ИЗМЕНЕНО: Настройка Gemini вынесена, т.к. не async ---
    if not gc.configure_gemini():
        logger.critical("Не удалось настроить Gemini API. Запуск бота отменен.")
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

    logger.info("Настройка обработчиков сигналов остановки...")
    loop = asyncio.get_event_loop()

    def _signal_wrapper(sig_num, frame):
        logger.debug(f"Перехват сигнала {sig_num} через signal.signal")
        asyncio.ensure_future(shutdown_signal_handler(sig_num))

    if platform.system() != "Windows":
        logger.info("Настройка через loop.add_signal_handler (Unix-like)...")
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_signal_handler(s)))
            except (NotImplementedError, RuntimeError) as e:
                 logger.warning(f"loop.add_signal_handler не поддерживается ({e}), используем fallback signal.signal...")
                 try: signal.signal(sig, _signal_wrapper)
                 except Exception as sig_e: logger.error(f"Не удалось установить fallback обработчик для {sig}: {sig_e}")
    else:
        logger.info("Настройка через signal.signal (Windows)...")
        supported_signals = (signal.SIGINT,)
        for sig in supported_signals:
            try: signal.signal(sig, _signal_wrapper)
            except Exception as sig_e: logger.error(f"Не удалось установить обработчик для {sig}: {sig_e}")

    logger.info("Запуск бота...")
    try:
        # Application.run_polling() сама по себе блокирующая в старых версиях,
        # но с ApplicationBuilder она должна интегрироваться с существующим циклом asyncio
        # Если run_polling блокирует, возможно, придется запускать его в executor'е,
        # но сначала попробуем так.
        await application.initialize() # Инициализируем приложение
        await application.start()      # Начинаем получать обновления
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES) # Запускаем polling
        logger.info("Бот работает и получает обновления...")
        # Держим основной процесс живым (например, бесконечным ожиданием)
        # Обработчик сигналов остановит цикл при необходимости
        await asyncio.Event().wait() # Ждем вечно (пока не будет остановлен)

    except Exception as e:
        logger.critical(f"Критическая ошибка во время работы polling: {e}", exc_info=True)
    finally:
        logger.info("Polling завершен или остановлен.")
        if application and application.updater and application.updater.is_running:
             await application.updater.stop()
        if application and application.running:
             await application.stop()
        if application:
            await application.shutdown() # Повторный вызов на всякий случай
        # УДАЛЕНО: dm.close_all_connections() # Больше не нужно
        logger.info("Финальная очистка завершена.")
        logger.info("Процесс бота завершен.")

# --- ИЗМЕНЕНО: Запуск через asyncio.run ---
if __name__ == "__main__":
    asyncio.run(main()) # <--- Запускаем async main