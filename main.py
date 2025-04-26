# main.py
import logging
import os
import platform
import datetime
import signal
import asyncio
import time # Добавляем time
from typing import Optional

# Импортируем компоненты из других файлов
from config import (
    TELEGRAM_BOT_TOKEN, SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE,
    MESSAGE_FILTERS, validate_config, setup_logging, JOB_CHECK_INTERVAL_MINUTES,
    BOT_OWNER_ID # <-- Добавил импорт BOT_OWNER_ID
)
import data_manager as dm
import bot_handlers # Импортируем модуль с обработчиками
import jobs
from utils import notify_owner

# Импорты из библиотеки telegram
from telegram import Update, BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Defaults, ApplicationBuilder,
    CallbackQueryHandler, filters # Убрали ConversationHandler
)
from localization import get_text, DEFAULT_LANGUAGE
from telegram.constants import ParseMode

# Глобальная переменная для доступа к приложению при остановке
application: Application | None = None
# Глобальные переменные статуса (инициализируются в post_init)
bot_start_time: float = 0.0
last_job_run_time: Optional[datetime.datetime] = None
last_job_error: Optional[str] = None

logger = logging.getLogger(__name__)

# =============================================================================
# ФУНКЦИИ ИНИЦИАЛИЗАЦИИ И НАСТРОЙКИ
# =============================================================================

async def post_init(app: Application):
    """
    Выполняется после инициализации Application.
    Устанавливает начальные данные в bot_data и команды бота.
    """
    # Инициализация глобальных данных бота
    app.bot_data['bot_start_time'] = time.time()
    app.bot_data['last_job_run_time'] = None
    app.bot_data['last_job_error'] = None
    logger = logging.getLogger(__name__)
    try:
        bot_info = await app.bot.get_me()
        logger.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")

        # Определение набора команд для обычных пользователей
        commands = [
            BotCommand("start", get_text("cmd_start_desc", DEFAULT_LANGUAGE)),
            BotCommand("help", get_text("cmd_help_desc", DEFAULT_LANGUAGE)),
            BotCommand("generate_now", get_text("cmd_generate_now_desc", DEFAULT_LANGUAGE)),
            BotCommand("regenerate_story", get_text("cmd_regenerate_desc", DEFAULT_LANGUAGE)),
            BotCommand("summarize", get_text("cmd_summarize_desc", DEFAULT_LANGUAGE)),
            BotCommand("story_settings", get_text("cmd_story_settings_desc", DEFAULT_LANGUAGE)),
            # Команды set_language и set_timezone больше не основные, настройки через /story_settings
            # Можно оставить их для совместимости или убрать
            # BotCommand("set_language", get_text("cmd_set_language_desc", DEFAULT_LANGUAGE)),
            # BotCommand("set_timezone", get_text("cmd_set_timezone_desc", DEFAULT_LANGUAGE)),
        ]

        # Установка команд для разных областей видимости
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        logger.info("Общие команды бота установлены.")

        # Установка дополнительных команд для владельца
        if BOT_OWNER_ID:
            owner_commands = commands + [BotCommand("status", get_text("cmd_status_desc", DEFAULT_LANGUAGE))]
            try:
                await app.bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(BOT_OWNER_ID))
                logger.info(f"Команды для владельца (ID: {BOT_OWNER_ID}) установлены.")
            except Exception as e:
                logger.error(f"Не удалось установить команды для владельца {BOT_OWNER_ID}: {e}")
        else:
            logger.warning("BOT_OWNER_ID не установлен, команда /status не будет доступна.")

    except Exception as e:
        logger.error(f"Не удалось получить информацию о боте или установить команды: {e}", exc_info=True)

def configure_handlers(app: Application):
    """Регистрирует все обработчики команд, кнопок и сообщений."""
    logger = logging.getLogger(__name__)

    # --- Команды ---
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(CommandHandler("regenerate_story", bot_handlers.regenerate_story))
    app.add_handler(CommandHandler("status", bot_handlers.status_command)) # Доступ проверит сам обработчик
    app.add_handler(CommandHandler("summarize", bot_handlers.summarize_command))
    # Команда для вызова меню настроек
    app.add_handler(CommandHandler("story_settings", bot_handlers.story_settings_command))
    # Можно добавить обработчики для /set_language и /set_timezone, которые просто вызывают story_settings_command
    # app.add_handler(CommandHandler("set_language", bot_handlers.story_settings_command))
    # app.add_handler(CommandHandler("set_timezone", bot_handlers.story_settings_command))

    # --- Обработчики CallbackQuery ---
    # Единый обработчик для ВСЕХ кнопок меню настроек (префикс 'settings_')
    app.add_handler(CallbackQueryHandler(bot_handlers.settings_callback_handler, pattern="^settings_"))
    # Обработчик кнопок фидбэка (префикс 'feedback_')
    app.add_handler(CallbackQueryHandler(bot_handlers.feedback_button_handler, pattern="^feedback_"))
    # Обработчик кнопок выбора периода саммари (префикс 'summary_')
    app.add_handler(CallbackQueryHandler(bot_handlers.summary_period_button_handler, pattern="^summary_"))

    # --- Обработчик сообщений (Должен быть зарегистрирован ПОСЛЕ команд и колбэков!) ---
    # Он сохраняет сообщения из групп и обрабатывает ожидаемый ввод времени в настройках
    app.add_handler(MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.PRIVATE) & # Ловим и в группах, и в ЛС (для ввода времени)
        (filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL | filters.Document.ALL) &
        ~filters.COMMAND,
        bot_handlers.handle_message
    ))

    logger.info("Обработчики команд, кнопок и сообщений зарегистрированы.")


def configure_scheduler(app: Application):
    """Настраивает периодический запуск задачи проверки и генерации историй."""
    job_queue = app.job_queue
    if not job_queue:
        logger.warning("JobQueue не инициализирована (возможно, отсутствует APScheduler?). Планировщик не настроен.")
        return

    interval_seconds = datetime.timedelta(minutes=JOB_CHECK_INTERVAL_MINUTES).total_seconds()
    # Убедимся, что интервал не слишком маленький
    interval_seconds = max(interval_seconds, 60) # Не чаще раза в минуту

    # Передаем application в context задачи через 'data', чтобы jobs.py мог обновлять bot_data
    job = job_queue.run_repeating(
        jobs.daily_story_job,
        interval=interval_seconds,
        first=15, # Запустить через 15 секунд после старта
        name="daily_story_job", # Имя для логирования и управления
        data={'application': app} # Передаем application в данные задачи
    )
    if job:
        logger.info(f"Задача 'daily_story_job' запланирована каждые {JOB_CHECK_INTERVAL_MINUTES} мин.")
    else:
        logger.error("Не удалось запланировать задачу 'daily_story_job'.")


async def shutdown_signal_handler(signal_num):
    """Обрабатывает сигналы остановки (SIGINT, SIGTERM) для корректного завершения."""
    global application
    sig = signal.Signals(signal_num)
    logging.warning(f"Получен сигнал остановки {sig.name} ({sig.value}). Завершаю работу...")

    # Останавливаем планировщик, если он есть
    if application and application.job_queue:
        logging.info("Остановка JobQueue...")
        try:
            application.job_queue.stop()
            # Дадим немного времени на завершение текущих задач
            await asyncio.sleep(2)
        except Exception as e:
            logging.error(f"Ошибка при остановке JobQueue: {e}")

    # Останавливаем Telegram Application
    if application:
        logging.info("Запускаю штатную остановку Telegram Bot Application...")
        shutdown_task = asyncio.create_task(application.shutdown(), name="PTB Shutdown")
        try:
            await asyncio.wait_for(shutdown_task, timeout=10.0)
            logging.info("Telegram Bot Application штатно остановлен.")
        except asyncio.TimeoutError:
            logging.warning("Таймаут ожидания остановки PTB Application.")
        except Exception as e:
            logging.error(f"Ошибка при остановке PTB Application: {e}", exc_info=True)
    else:
        logging.warning("Объект Application не найден при попытке остановки.")

    # Закрываем соединения с БД
    logging.info("Закрытие соединений с базой данных...")
    dm.close_all_connections()
    logging.info("Соединения с базой данных закрыты. Бот остановлен.")

# =============================================================================
# ОСНОВНАЯ ТОЧКА ВХОДА
# =============================================================================

def main() -> None:
    """Основная функция запуска бота."""
    global application # Доступ к глобальной переменной

    # 1. Настройка Логирования (в самом начале)
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("="*30 + " ЗАПУСК БОТА 'Летописец Чата' " + "="*30)
    logger.info(f"Версия Python: {platform.python_version()}")
    logger.info(f"Операционная система: {platform.system()} {platform.release()}")

    # 2. Проверка конфигурации и инициализация БД
    try:
        validate_config()
        logger.info("Конфигурация успешно проверена.")
        dm.load_data() # Инициализация БД
        logger.info("База данных успешно инициализирована.")
    except ValueError as e:
        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА КОНФИГУРАЦИИ: {e}")
        return # Не запускаемся без конфигурации
    except Exception as e:
         logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА ИНИЦИАЛИЗАЦИИ БД: {e}", exc_info=True)
         return # Не запускаемся без БД

    # 3. Создание экземпляра Application
    logger.info("Создание Telegram Bot Application...")
    try:
        # Установка настроек по умолчанию для всех запросов
        defaults = Defaults(parse_mode=ParseMode.HTML, block=False)
        application = (
            ApplicationBuilder()
            .token(TELEGRAM_BOT_TOKEN)
            .defaults(defaults)
            .post_init(post_init) # Функция для действий после инициализации (установка команд и т.д.)
            # Настройки для асинхронной обработки
            .concurrent_updates(True) # Разрешить параллельную обработку обновлений
            .pool_timeout(30) # Таймаут ожидания обновлений от Telegram API
            .connect_timeout(15) # Таймаут подключения к Telegram
            .read_timeout(20)    # Таймаут чтения от Telegram
            .write_timeout(20)   # Таймаут записи в Telegram
            .build() # JobQueue создается автоматически, если установлен APScheduler
        )
        logger.info("Telegram Bot Application успешно создан.")
    except Exception as e:
        logger.critical(f"Не удалось создать Telegram Bot Application: {e}", exc_info=True)
        return

    # 4. Регистрация обработчиков
    logger.info("Регистрация обработчиков...")
    configure_handlers(application)

    # 5. Настройка планировщика
    logger.info("Настройка планировщика задач...")
    configure_scheduler(application)

    # 6. Настройка обработки сигналов остановки (SIGINT: Ctrl+C, SIGTERM: systemd stop)
    logger.info("Настройка обработчиков сигналов (SIGINT, SIGTERM)...")
    loop = asyncio.get_event_loop()

    signals_to_handle = (signal.SIGINT, signal.SIGTERM)
    for sig in signals_to_handle:
        try:
            # Предпочтительный способ для Unix-подобных систем
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_signal_handler(s)))
            logger.info(f"Обработчик для сигнала {signal.Signals(sig).name} успешно добавлен через add_signal_handler.")
        except (NotImplementedError, RuntimeError, AttributeError):
            # Fallback для систем, где add_signal_handler недоступен (включая Windows)
            try:
                 signal.signal(sig, lambda s, frame: asyncio.create_task(shutdown_signal_handler(s)))
                 logger.info(f"Обработчик для сигнала {signal.Signals(sig).name} успешно добавлен через signal.signal (fallback).")
            except Exception as e_sig:
                 logger.error(f"Не удалось установить обработчик для сигнала {sig} ни одним из способов: {e_sig}")

    # 7. Запуск бота (в режиме polling)
    logger.info("Запуск polling...")
    try:
        # Запускаем polling в текущем цикле событий asyncio
        application.run_polling(
             allowed_updates=Update.ALL_TYPES, # Получать все типы обновлений
             drop_pending_updates=True, # Сбрасывать обновления, пришедшие пока бот был офлайн
             close_loop=False # Не закрывать цикл событий asyncio после остановки polling
        )
    except Exception as e:
        logger.critical(f"Критическая ошибка во время работы polling: {e}", exc_info=True)
        # Попытка уведомить владельца о падении
        if application and application.bot:
             try:
                 # Запускаем уведомление в новом временном цикле, т.к. основной мог быть поврежден
                 asyncio.run(notify_owner(bot=application.bot, message="Бот критически упал во время polling!", operation="run_polling", exception=e, important=True))
             except Exception as notify_e:
                 logger.error(f"Не удалось уведомить владельца о падении: {notify_e}")
    finally:
        # Этот блок выполнится после остановки run_polling (штатной или из-за ошибки)
        logger.warning("Polling завершен или был остановлен.")
        # Дополнительное закрытие соединений на случай, если обработчик сигнала не сработал полностью
        logger.info("Финальное закрытие соединений с БД...")
        dm.close_all_connections()
        logger.info("Соединения с БД закрыты.")
        logger.info("="*30 + " БОТ ОСТАНОВЛЕН " + "="*30)

if __name__ == "__main__":
    main()