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
    MESSAGE_FILTERS, validate_config, setup_logging, JOB_CHECK_INTERVAL_MINUTES
)
import data_manager as dm
import bot_handlers # Импортируем модуль с обработчиками
import jobs

# Импорты из библиотеки telegram
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Defaults, ApplicationBuilder,
    CallbackQueryHandler, ConversationHandler, filters # Нужны для регистрации
)
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
    bot_start_time = time.time() # Устанавливаем точное время старта
    app.bot_data['bot_start_time'] = bot_start_time # Сохраняем в bot_data
    app.bot_data['last_job_run_time'] = None
    app.bot_data['last_job_error'] = None
    logger = logging.getLogger(__name__)
    try:
        bot_info = await app.bot.get_me()
        logger.info(f"Бот {bot_info.username} (ID: {bot_info.id}) успешно запущен.")
        # Устанавливаем глобальные команды один раз при старте
        commands = [
            BotCommand("start", "Start & Status"),
            BotCommand("help", "Help"),
            BotCommand("generate_now", "Generate Story Now"),
            BotCommand("regenerate_story", "Regenerate Story"),
            BotCommand("story_settings", "Story Settings (Admin)"),
            BotCommand("set_language", "Set Language"),
            BotCommand("set_timezone", "Set Timezone (Admin)")
            # Команду status не показываем всем в списке
        ]
        await app.bot.set_my_commands(commands)
        logging.info("Глобальные команды установлены.")
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

    # --- Регистрация обработчиков ---
    # Сначала команды
    app.add_handler(CommandHandler("start", bot_handlers.start))
    app.add_handler(CommandHandler("help", bot_handlers.help_command))
    app.add_handler(CommandHandler("generate_now", bot_handlers.generate_now))
    app.add_handler(CommandHandler("regenerate_story", bot_handlers.regenerate_story))
    app.add_handler(CommandHandler("story_settings", bot_handlers.story_settings_command))
    app.add_handler(CommandHandler("status", bot_handlers.status_command))
    # Команды set_language и set_timezone являются entry_points для диалогов

    # Затем диалоги (они включают свои CallbackQueryHandlers для кнопок внутри диалога)
    app.add_handler(lang_conv_handler)
    app.add_handler(time_conv_handler)
    app.add_handler(tz_conv_handler)

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
    global application, bot_start_time # Доступ к глобальным переменным

    # 1. Настройка Логирования (в самом начале)
    setup_logging()
    logger = logging.getLogger(__name__) # Получаем логгер после настройки

    logger.info("="*30 + " ЗАПУСК БОТА " + "="*30)

    # 2. Проверка конфигурации и инициализация БД
    try:
        validate_config()
        dm.load_data() # Инициализация БД
    except Exception as e:
         logging.critical(f"КРИТИЧЕСКАЯ ОШИБКА ИНИЦИАЛИЗАЦИИ: {e}", exc_info=True)
         return # Не запускаемся

    # 3. Создание экземпляра Application
    defaults = Defaults(parse_mode=ParseMode.HTML)
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .defaults(defaults)
        .post_init(post_init) # Функция для действий после инициализации
        .build() # JobQueue создается автоматически, если есть APScheduler
    )

    # 4. Инициализация bot_data для статуса
    # Убедимся, что ключи существуют
    application.bot_data.setdefault('last_job_run_time', None)
    application.bot_data.setdefault('last_job_error', None)
    application.bot_data.setdefault('bot_start_time', time.time()) # Записываем время старта

    # 5. Регистрация обработчиков
    configure_handlers(application)

    # 6. Настройка планировщика
    configure_scheduler(application)

    # 7. Настройка обработки сигналов остановки
    logger.info("Настройка обработчиков сигналов...")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Если запускается не из async контекста (маловероятно с run_polling)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    def _signal_wrapper(sig_num, frame):
        logging.debug(f"Перехвачен сигнал {sig_num} через signal.signal")
        # Запускаем асинхронную функцию в правильном цикле
        loop.create_task(shutdown_signal_handler(sig_num))

    signals_to_handle = (signal.SIGINT, signal.SIGTERM)
    is_windows = platform.system() == "Windows"
    logger.info(f"Настройка сигналов через {'signal.signal' if is_windows else 'loop.add_signal_handler'}...")
    sig_target = (signal.SIGINT,) if is_windows else signals_to_handle

    for sig in sig_target:
        try:
             if not is_windows:
                 # Предпочтительный способ для Unix
                 loop.add_signal_handler(sig, lambda s=sig: loop.create_task(shutdown_signal_handler(s)))
             else:
                 # Fallback для Windows
                 signal.signal(sig, _signal_wrapper)
        except (NotImplementedError, ValueError, RuntimeError, AttributeError, OSError) as e:
             # Ловим больше потенциальных ошибок, особенно на Windows
             logging.error(f"Не удалось установить обработчик для сигнала {sig}: {e}")

    # 8. Запуск бота
    logger.info("Запуск polling...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logging.critical(f"Критическая ошибка во время работы polling: {e}", exc_info=True)
    finally:
        # Этот блок выполнится после остановки run_polling
        logging.info("Polling завершен или был остановлен.")
        # Дополнительное закрытие соединений на случай, если обработчик сигнала не сработал
        dm.close_all_connections()
        logging.info("Финальное закрытие соединений с БД выполнено.")
        logging.info("="*30 + " БОТ ОСТАНОВЛЕН " + "="*30)

if __name__ == "__main__":
    main()