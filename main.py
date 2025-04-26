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

# Импорты из библиотеки telegram
from telegram import Update, BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Defaults, ApplicationBuilder,
    CallbackQueryHandler, ConversationHandler, filters # Нужны для регистрации
)
from localization import get_text, DEFAULT_LANGUAGE
from telegram.constants import ParseMode

# Глобальная переменная для доступа к приложению при остановке
application: Application | None = None
# Глобальные переменные статуса (инициализируются в post_init)
bot_start_time: float = 0.0
last_job_run_time: Optional[datetime.datetime] = None
last_job_error: Optional[str] = None

# =============================================================================
# ФУНКЦИИ ИНИЦИАЛИЗАЦИИ И НАСТРОЙКИ
# =============================================================================

async def post_init(app: Application):
    """Выполняется после инициализации Application для логирования и установки команд."""
    global bot_start_time
    bot_start_time = time.time()
    app.bot_data['bot_start_time'] = bot_start_time
    app.bot_data['last_job_run_time'] = None
    app.bot_data['last_job_error'] = None
    logger = logging.getLogger(__name__)
    try:
        bot_info = await app.bot.get_me()
        logger.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")
        # --- ИЗМЕНЕНО: Добавляем команду /summarize ---
        commands = [
            BotCommand("start", get_text("cmd_start_desc", DEFAULT_LANGUAGE)), # Используем язык по умолчанию для глобальных
            BotCommand("help", get_text("cmd_help_desc", DEFAULT_LANGUAGE)),
            BotCommand("generate_now", get_text("cmd_generate_now_desc", DEFAULT_LANGUAGE)),
            BotCommand("regenerate_story", get_text("cmd_regenerate_desc", DEFAULT_LANGUAGE)),
            BotCommand("summarize", get_text("cmd_summarize_desc", DEFAULT_LANGUAGE)), # <-- Новая команда
            BotCommand("story_settings", get_text("cmd_story_settings_desc", DEFAULT_LANGUAGE)),
            BotCommand("set_language", get_text("cmd_set_language_desc", DEFAULT_LANGUAGE)),
            BotCommand("set_timezone", get_text("cmd_set_timezone_desc", DEFAULT_LANGUAGE)),
            # BotCommand("set_genre", get_text("cmd_set_genre_desc", DEFAULT_LANGUAGE)), # Можно добавить, если нужна команда
        ]
        if BOT_OWNER_ID:
             owner_commands = commands + [BotCommand("status", get_text("cmd_status_desc", DEFAULT_LANGUAGE))]
             await app.bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(BOT_OWNER_ID))
             # Устанавливаем команды без статуса для остальных
             await app.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
             await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        else:
             # Если владелец не указан, ставим команды для всех
             await app.bot.set_my_commands(commands)

        logging.info("Глобальные и/или приватные команды установлены.")
    except Exception as e:
        logging.error(f"Не удалось получить информацию о боте или установить команды: {e}", exc_info=True)


def configure_handlers(app: Application):
    """Регистрирует все обработчики команд, диалогов, кнопок и сообщений."""
    logger = logging.getLogger(__name__)

    # --- ConversationHandler для выбора языка ---
    # Вход: команда /set_language или кнопка с CB_CHANGE_LANG
    # Состояния: SELECTING_LANG (ожидание нажатия кнопки языка)
    # Выход: обработчики set_language_conv или cancel_conv
    lang_conv_handler = ConversationHandler(
        entry_points=[
             CommandHandler("set_language", bot_handlers.ask_language),
             CallbackQueryHandler(
                 bot_handlers.ask_language,
                 pattern=f"^{bot_handlers.CB_CHANGE_LANG}$" # Обращаемся через модуль
            )
        ],
        states={
            bot_handlers.SELECTING_LANG: [ # Обращаемся через модуль
                CallbackQueryHandler(
                    bot_handlers.set_language_conv, pattern="^conv_setlang_"
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", bot_handlers.cancel_conv),
            CallbackQueryHandler(
                bot_handlers.cancel_conv, pattern=f"^{bot_handlers.CB_CANCEL_CONV}$" # Общая отмена
            )
        ],
        conversation_timeout=datetime.timedelta(minutes=5).total_seconds() # Таймаут 5 минут
    )

    # --- ConversationHandler для установки времени ---
    # Вход: кнопка с CB_CHANGE_TIME
    # Состояния: AWAITING_TIME (ожидание ввода времени или нажатия кнопок)
    # Выход: обработчики set_time_input, set_time_default_button или cancel_conv
    time_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                bot_handlers.ask_set_time, pattern=f"^{bot_handlers.CB_CHANGE_TIME}$" # Обращаемся через модуль
            )
        ],
        states={
            bot_handlers.AWAITING_TIME: [ # Обращаемся через модуль
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, bot_handlers.set_time_input
                ),
                CallbackQueryHandler(
                    bot_handlers.set_time_default_button,
                    pattern=f"^{bot_handlers.CB_SET_TIME_DEFAULT}$" # Обращаемся через модуль
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", bot_handlers.cancel_conv),
            CallbackQueryHandler(
                bot_handlers.cancel_conv, pattern=f"^{bot_handlers.CB_CANCEL_CONV}$" # Общая отмена
            )
        ],
        conversation_timeout=datetime.timedelta(minutes=2).total_seconds() # Таймаут 2 минуты
    )

    # --- ConversationHandler для установки таймзоны ---
    # Вход: команда /set_timezone или кнопка с CB_CHANGE_TZ
    # Состояния: SELECTING_TZ (ожидание нажатия кнопки таймзоны)
    # Выход: обработчики set_timezone_conv или cancel_conv
    tz_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("set_timezone", bot_handlers.ask_timezone),
            CallbackQueryHandler(
                bot_handlers.ask_timezone, pattern=f"^{bot_handlers.CB_CHANGE_TZ}$" # Обращаемся через модуль
            )
        ],
        states={
            bot_handlers.SELECTING_TZ: [ # Обращаемся через модуль
                CallbackQueryHandler(
                    bot_handlers.set_timezone_conv, pattern="^conv_settz_"
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", bot_handlers.cancel_conv),
            CallbackQueryHandler(
                bot_handlers.cancel_conv, pattern=f"^{bot_handlers.CB_CANCEL_CONV}$" # Общая отмена
            )
        ],
        conversation_timeout=datetime.timedelta(minutes=5).total_seconds() # Таймаут 5 минут
    )
    
    genre_conv_handler = ConversationHandler(
        entry_points=[
            # CommandHandler("set_genre", bot_handlers.ask_genre), # Если нужна команда
            CallbackQueryHandler(bot_handlers.ask_genre, pattern=f"^{bot_handlers.CB_CHANGE_GENRE}$")
        ],
        states={
            bot_handlers.SELECTING_GENRE: [
                CallbackQueryHandler(bot_handlers.set_genre_conv, pattern=f"^{bot_handlers.CB_PREFIX_SET_GENRE}")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", bot_handlers.cancel_conv),
            CallbackQueryHandler(bot_handlers.cancel_conv, pattern=f"^{bot_handlers.CB_CANCEL_CONV}$")
        ],
        conversation_timeout=datetime.timedelta(minutes=5).total_seconds()
    )

    # --- Регистрация обработчиков ---
    # Сначала команды
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(CommandHandler("regenerate_story", bot_handlers.regenerate_story))
    app.add_handler(CommandHandler("story_settings", bot_handlers.story_settings_command))
    app.add_handler(CommandHandler("status", bot_handlers.status_command))
    app.add_handler(CommandHandler("summarize", bot_handlers.summarize_command))
    # Команды set_language и set_timezone являются entry_points для диалогов

    # Затем диалоги (они включают свои CallbackQueryHandlers для кнопок внутри диалога)
    app.add_handler(lang_conv_handler)
    app.add_handler(time_conv_handler)
    app.add_handler(tz_conv_handler)
    app.add_handler(genre_conv_handler)

    # Затем обработчики для кнопок, НЕ связанных с диалогами
    # Обрабатываем кнопки статуса и показа настроек
    app.add_handler(CallbackQueryHandler(
        bot_handlers.settings_button_handler,
        pattern=f"^({bot_handlers.CB_TOGGLE_STATUS}|{bot_handlers.CB_SHOW_SETTINGS})$"
    ))
    # Обрабатываем кнопки фидбэка
    app.add_handler(CallbackQueryHandler(
        bot_handlers.feedback_button_handler, pattern="^feedback_"
    ))
    
    app.add_handler(CallbackQueryHandler(
        bot_handlers.summary_period_button_handler, pattern=f"^{bot_handlers.CB_PREFIX_SUMMARIZE}"
    ))

    # В конце - обработчик всех остальных сообщений
    app.add_handler(MessageHandler(MESSAGE_FILTERS, bot_handlers.handle_message))
    
    

    logger.info("Обработчики команд, диалогов, кнопок и сообщений зарегистрированы.")


def configure_scheduler(app: Application):
    """Настраивает периодический запуск задачи проверки и генерации историй."""
    job_queue = app.job_queue
    if not job_queue:
        logging.warning("JobQueue не инициализирована (возможно, отсутствует APScheduler?). Планировщик не настроен.")
        return

    interval_seconds = datetime.timedelta(minutes=JOB_CHECK_INTERVAL_MINUTES).total_seconds()
    # Передаем application в context задачи через 'data', чтобы jobs.py мог обновлять bot_data
    app.bot_data['scheduled_check_job'] = job_queue.run_repeating(
        jobs.daily_story_job,
        interval=interval_seconds,
        first=15, # Запустить через 15 секунд после старта
        name="check_due_stories_job",
        data={'application': app} # Передаем application в данные задачи
    )
    logging.info(f"Задача проверки историй запланирована каждые {JOB_CHECK_INTERVAL_MINUTES} мин.")


async def shutdown_signal_handler(signal_num):
    """Обрабатывает сигналы остановки (SIGINT, SIGTERM)."""
    global application
    sig = signal.Signals(signal_num)
    logging.warning(f"Получен сигнал остановки {sig.name} ({sig.value}). Завершаю работу...")
    if application:
        logging.info("Запускаю штатную остановку Telegram Bot Application...")
        # Добавим таймаут на всякий случай
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

    logging.info("Закрытие соединений с базой данных...")
    dm.close_all_connections() # Закрываем соединения SQLite
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
        defaults = Defaults(parse_mode=ParseMode.HTML, block=False) # block=False для асинхронности по умолчанию
        application = (
            ApplicationBuilder()
            .token(TELEGRAM_BOT_TOKEN)
            .defaults(defaults)
            .post_init(post_init) # Функция для действий после инициализации
            # Укажем concurrent_updates=True и pool_timeout для лучшей производительности
            .concurrent_updates(True)
            .pool_timeout(30) # Увеличим таймаут ожидания событий от Telegram
            .connect_timeout(15) # Таймаут подключения к Telegram
            .read_timeout(20)    # Таймаут чтения от Telegram
            .write_timeout(20)   # Таймаут записи в Telegram
            .build() # JobQueue создается автоматически
        )
        logger.info("Telegram Bot Application успешно создан.")
    except Exception as e:
        logger.critical(f"Не удалось создать Telegram Bot Application: {e}", exc_info=True)
        return

    # 4. Инициализация bot_data (перенесено в post_init)

    # 5. Регистрация обработчиков
    logger.info("Регистрация обработчиков...")
    configure_handlers(application)

    # 6. Настройка планировщика
    logger.info("Настройка планировщика задач...")
    configure_scheduler(application)

    # 7. Настройка обработки сигналов остановки
    logger.info("Настройка обработчиков сигналов (SIGINT, SIGTERM)...")
    loop = asyncio.get_event_loop() # Получаем текущий цикл событий

    signals_to_handle = (signal.SIGINT, signal.SIGTERM)
    is_windows = platform.system() == "Windows"

    for sig in signals_to_handle:
        try:
            # loop.add_signal_handler предпочтительнее, но может не работать везде
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_signal_handler(s, loop)))
            logger.info(f"Обработчик для сигнала {sig.name} успешно добавлен через add_signal_handler.")
        except (NotImplementedError, RuntimeError, AttributeError):
            # Fallback для систем, где add_signal_handler недоступен (включая Windows)
            try:
                 # Используем functools.partial для передачи loop в обработчик
                 import functools
                 signal.signal(sig, functools.partial(lambda s, l, frame: asyncio.create_task(shutdown_signal_handler(s, l)), sig, loop))
                 logger.info(f"Обработчик для сигнала {sig.name} успешно добавлен через signal.signal (fallback).")
            except Exception as e_sig:
                 logger.error(f"Не удалось установить обработчик для сигнала {sig} ни одним из способов: {e_sig}")


    # 8. Запуск бота
    logger.info("Запуск polling...")
    try:
        # Запускаем polling в текущем цикле событий
        application.run_polling(
             allowed_updates=Update.ALL_TYPES,
             drop_pending_updates=True, # Сбрасываем старые обновления при старте
             close_loop=False # Не закрываем цикл событий после остановки polling
        )
    except Exception as e:
        logger.critical(f"Критическая ошибка во время работы polling: {e}", exc_info=True)
        # Уведомим владельца о падении, если бот успел инициализироваться
        if application and application.bot:
             # Запускаем уведомление в новом временном цикле, т.к. основной мог быть поврежден
             try:
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