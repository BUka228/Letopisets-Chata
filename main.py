# =============================================================================
# ФАЙЛ: main.py
# (Финальная версия с согласованными функциями)
# =============================================================================
import logging
import os
import platform
import datetime
import signal
import asyncio
import time
from typing import Optional

# Импортируем компоненты из других файлов
from config import (
    TELEGRAM_BOT_TOKEN, # Убрал MESSAGE_FILTERS т.к. он используется только в bot_handlers
    validate_config, setup_logging, JOB_CHECK_INTERVAL_MINUTES, BOT_OWNER_ID,
    PURGE_JOB_INTERVAL_HOURS # Интервал для задачи очистки
)
import data_manager as dm
import bot_handlers # Основной модуль с логикой команд и колбэков
import jobs # Модуль с фоновыми задачами
from localization import get_text, DEFAULT_LANGUAGE # Для установки команд
from utils import notify_owner # Для уведомления об ошибках

# Импорты из библиотеки telegram
from telegram import Update, BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Defaults, ApplicationBuilder,
    CallbackQueryHandler, filters # Filters используется в bot_handlers
)
from telegram.constants import ParseMode

# Глобальная переменная для доступа к приложению при остановке
application: Application | None = None

# =============================================================================
# ФУНКЦИИ ИНИЦИАЛИЗАЦИИ И НАСТРОЙКИ
# =============================================================================

async def post_init(app: Application):
    """
    Выполняется после инициализации Application.
    Устанавливает начальные данные в bot_data и команды бота.
    """
    
    logger = logging.getLogger(__name__)
    
    # Инициализация глобальных данных бота для отслеживания статуса задач
    app.bot_data['bot_start_time'] = time.time()
    app.bot_data['last_daily_story_job_run_time'] = None
    app.bot_data['last_daily_story_job_error'] = None
    app.bot_data['last_purge_job_run_time'] = None
    app.bot_data['last_purge_job_error'] = None

    logger = logging.getLogger(__name__)
    try:
        bot_info = await app.bot.get_me()
        logger.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")

        # Определение набора команд для пользователей
        commands = [
            BotCommand("start", get_text("cmd_start_desc", DEFAULT_LANGUAGE)),
            BotCommand("help", get_text("cmd_help_desc", DEFAULT_LANGUAGE)),
            BotCommand("generate_now", get_text("cmd_generate_now_desc", DEFAULT_LANGUAGE)),
            BotCommand("regenerate_story", get_text("cmd_regenerate_desc", DEFAULT_LANGUAGE)),
            BotCommand("summarize", get_text("cmd_summarize_desc", DEFAULT_LANGUAGE)),
            BotCommand("story_settings", get_text("cmd_story_settings_desc", DEFAULT_LANGUAGE)),
            BotCommand("chat_stats", get_text("cmd_chat_stats_desc", DEFAULT_LANGUAGE)),
            # BotCommand("onthisday", ...), # <-- УДАЛЕНО
            BotCommand("purge_history", get_text("cmd_purge_history_desc", DEFAULT_LANGUAGE)),
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
            logger.warning("BOT_OWNER_ID не установлен, команда /status не будет эксклюзивной.")

    except Exception as e:
        logger.error(f"Ошибка в post_init (получение инфо или установка команд): {e}", exc_info=True)

def configure_handlers(app: Application):
    """Регистрирует все обработчики команд, кнопок и сообщений."""
    logger = logging.getLogger(__name__)

    # --- Команды ---
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(CommandHandler("regenerate_story", bot_handlers.regenerate_story))
    app.add_handler(CommandHandler("status", bot_handlers.status_command))
    app.add_handler(CommandHandler("summarize", bot_handlers.summarize_command))
    app.add_handler(CommandHandler("story_settings", bot_handlers.story_settings_command))
    app.add_handler(CommandHandler("chat_stats", bot_handlers.chat_stats_command)) # Статистика
    app.add_handler(CommandHandler("purge_history", bot_handlers.purge_history_command)) # Очистка
    # app.add_handler(CommandHandler("onthisday", ...)) # <-- УДАЛЕНО

    # --- Обработчики CallbackQuery ---
    # Единый обработчик для меню настроек (префикс 'settings_')
    app.add_handler(CallbackQueryHandler(bot_handlers.settings_callback_handler, pattern="^settings_"))
    # Обработчик фидбэка (префикс 'feedback_')
    app.add_handler(CallbackQueryHandler(bot_handlers.feedback_button_handler, pattern="^feedback_"))
    # Обработчик выбора периода саммари (префикс 'summary_period_')
    app.add_handler(CallbackQueryHandler(bot_handlers.summary_period_button_handler, pattern="^summary_period_"))
    # Обработчик выбора периода статистики (префикс 'stats_period_')
    app.add_handler(CallbackQueryHandler(bot_handlers.stats_period_callback, pattern="^stats_period_"))
    # Обработчик подтверждения очистки (префикс 'purge_')
    app.add_handler(CallbackQueryHandler(bot_handlers.purge_confirm_callback, pattern="^purge_"))

    # --- Обработчик сообщений (ПОСЛЕДНИЙ!) ---
    # Сохраняет сообщения И обрабатывает ожидаемый ввод времени
    app.add_handler(MessageHandler(
        # Фильтры вынесены в bot_handlers или можно использовать config.MESSAGE_FILTERS для групп
        (filters.ChatType.GROUPS | filters.ChatType.PRIVATE) &
        (filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL | filters.Document.ALL) &
        ~filters.COMMAND,
        bot_handlers.handle_message
    ))

    logger.info("Обработчики команд, кнопок и сообщений успешно зарегистрированы.")


def configure_scheduler(app: Application):
    """Настраивает и запускает периодические задачи."""
    logger = logging.getLogger(__name__)
    job_queue = app.job_queue
    if not job_queue:
        logger.warning("JobQueue не доступен (APScheduler не установлен?). Планировщик не настроен.")
        return

    # 1. Задача генерации сводок (историй/дайджестов)
    interval_gen = max(datetime.timedelta(minutes=JOB_CHECK_INTERVAL_MINUTES).total_seconds(), 60) # Не чаще раза в минуту
    job_gen = job_queue.run_repeating(
        jobs.daily_story_job,       # Функция задачи из jobs.py
        interval=interval_gen,      # Интервал в секундах
        first=15,                   # Первый запуск через 15 секунд
        name="daily_story_job",     # Имя для логов и управления
        data={'application': app}   # Передача application в задачу
    )
    if job_gen:
        logger.info(f"Задача 'daily_story_job' запланирована (интервал {interval_gen:.0f} секунд).")
    else:
        logger.error("Не удалось запланировать задачу 'daily_story_job'.")

    # 2. Задача очистки старых сообщений
    interval_purge = max(datetime.timedelta(hours=PURGE_JOB_INTERVAL_HOURS).total_seconds(), 3600) # Не чаще раза в час
    job_purge = job_queue.run_repeating(
        jobs.purge_old_messages_job, # Функция задачи из jobs.py
        interval=interval_purge,     # Интервал в секундах
        first=300,                   # Первый запуск через 5 минут
        name="purge_job",            # Имя для логов и управления
        data={'application': app}   # Передача application в задачу (если нужно будет что-то оттуда брать)
    )
    if job_purge:
        logger.info(f"Задача 'purge_job' запланирована (интервал {interval_purge:.0f} секунд).")
    else:
        logger.error("Не удалось запланировать задачу 'purge_job'.")


async def shutdown_signal_handler(signal_num):
    """Обрабатывает сигналы SIGINT и SIGTERM для корректного завершения."""
    
    global application
    sig = signal.Signals(signal_num)
    
    logging.warning(f"Получен сигнал остановки {sig.name} ({sig.value}). Завершаю работу...")

    # Останавливаем планировщик задач
    if application and application.job_queue:
        logging.info("Остановка JobQueue...")
        try:
            # Завершаем работу job_queue gracefully
            await application.job_queue.shutdown()
            logging.info("JobQueue остановлен.")
        except Exception as e:
            logging.error(f"Ошибка при остановке JobQueue: {e}")

    # Останавливаем основное приложение PTB
    if application:
        logging.info("Запускаю штатную остановку Telegram Bot Application...")
        shutdown_task = asyncio.create_task(application.shutdown(), name="PTB Shutdown")
        try:
            await asyncio.wait_for(shutdown_task, timeout=10.0) # Даем 10 секунд на завершение
            logging.info("Telegram Bot Application штатно остановлен.")
        except asyncio.TimeoutError:
            logging.warning("Таймаут ожидания остановки PTB Application.")
        except Exception as e:
            logging.error(f"Ошибка при остановке PTB Application: {e}", exc_info=True)
    else:
        logging.warning("Объект Application не найден при попытке остановки.")

    # Закрываем соединения с базой данных
    logging.info("Закрытие соединений с базой данных...")
    dm.close_all_connections() # Используем функцию из data_manager
    logging.info("Соединения с базой данных закрыты.")
    logging.info(f"Бот остановлен после сигнала {sig.name}.")

# =============================================================================
# ОСНОВНАЯ ТОЧКА ВХОДА
# =============================================================================

def main() -> None:
    """Основная функция: инициализация, настройка и запуск бота."""
    global application # Делаем application доступным для shutdown_signal_handler

    # 1. Настройка Логирования (самое первое действие)
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("="*30 + " ЗАПУСК БОТА 'Летописец Чата' " + "="*30)
    logger.info(f"Версия Python: {platform.python_version()}")
    logger.info(f"Операционная система: {platform.system()} {platform.release()}")

    # 2. Проверка Конфигурации и Инициализация БД
    try:
        validate_config() # Проверяем наличие токенов и т.д.
        logger.info("Конфигурация успешно проверена.")
        dm.load_data() # Создаем/проверяем таблицы БД
        logger.info("База данных успешно инициализирована.")
    except ValueError as e:
        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА КОНФИГУРАЦИИ: {e}")
        return # Выход, если нет конфигурации
    except Exception as e:
         logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА ИНИЦИАЛИЗАЦИИ БД: {e}", exc_info=True)
         return # Выход, если не можем работать с БД

    # 3. Создание Экземпляра Application
    logger.info("Создание Telegram Bot Application...")
    try:
        # Настройки по умолчанию для всех запросов (например, режим парсинга)
        defaults = Defaults(parse_mode=ParseMode.HTML, block=False) # block=False для неблокирующих вызовов
        application_builder = (
            ApplicationBuilder()
            .token(TELEGRAM_BOT_TOKEN)
            .defaults(defaults)
            .post_init(post_init) # Функция, выполняемая после инициализации (установка команд)
            # Настройки производительности и таймаутов
            .concurrent_updates(True) # Параллельная обработка входящих обновлений
            .pool_timeout(30) # Таймаут для long polling
            .connect_timeout(15) # Таймаут подключения
            .read_timeout(20)    # Таймаут чтения
            .write_timeout(20)   # Таймаут записи
        )
        application = application_builder.build() # Создаем приложение
        logger.info("Telegram Bot Application успешно создан.")
    except Exception as e:
        logger.critical(f"Не удалось создать Telegram Bot Application: {e}", exc_info=True)
        return

    # 4. Регистрация Обработчиков
    logger.info("Регистрация обработчиков...")
    configure_handlers(application)

    # 5. Настройка Планировщика Задач
    logger.info("Настройка планировщика задач...")
    configure_scheduler(application)

    # 6. Настройка Обработки Сигналов Остановки
    logger.info("Настройка обработчиков сигналов (SIGINT, SIGTERM)...")
    loop = asyncio.get_event_loop() # Получаем текущий цикл событий asyncio

    # Сигналы для корректного завершения (Ctrl+C и от systemd/docker)
    signals_to_handle = (signal.SIGINT, signal.SIGTERM)
    for sig in signals_to_handle:
        try:
            # Предпочтительный способ добавления обработчика сигнала для asyncio
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_signal_handler(s)))
        except (NotImplementedError, RuntimeError, AttributeError):
            # Альтернативный способ для систем, где add_signal_handler недоступен (например, Windows)
            signal.signal(sig, lambda s, frame: asyncio.create_task(shutdown_signal_handler(s)))
        finally:
            # Логируем успешное добавление (или невозможность)
             try: logger.info(f"Обработчик для сигнала {signal.Signals(sig).name} успешно добавлен.")
             except NameError: logger.info(f"Обработчик для сигнала {sig} успешно добавлен (имя не определено).")
             except Exception as e_sig: logger.error(f"Не удалось установить обработчик для сигнала {sig}: {e_sig}")

    # 7. Запуск Бота
    logger.info("Запуск polling...")
    try:
        # Основной цикл работы бота: получение обновлений от Telegram
        application.run_polling(
             allowed_updates=Update.ALL_TYPES, # Принимать все типы обновлений
             drop_pending_updates=True, # Сбросить "старые" обновления при старте
             close_loop=False # Не закрывать цикл asyncio после остановки polling
        )
    except Exception as e:
        logger.critical(f"Критическая ошибка во время работы polling: {e}", exc_info=True)
        # Пытаемся уведомить владельца о падении, если возможно
        if application and application.bot and BOT_OWNER_ID:
             try:
                 # Создаем временный цикл событий для отправки уведомления
                 asyncio.run(notify_owner(bot=application.bot, message="Бот критически упал во время polling!", operation="run_polling", exception=e, important=True))
             except Exception as notify_e:
                 logger.error(f"Не удалось уведомить владельца о падении: {notify_e}")
    finally:
        # Этот блок выполнится после штатной остановки run_polling или из-за ошибки
        logger.warning("Polling завершен или был остановлен.")
        # Дополнительное закрытие соединений на случай, если shutdown_signal_handler не сработал
        logger.info("Финальное закрытие соединений с БД (на всякий случай)...")
        dm.close_all_connections()
        logger.info("="*30 + " БОТ ОСТАНОВЛЕН " + "="*30)

if __name__ == "__main__":
    main()