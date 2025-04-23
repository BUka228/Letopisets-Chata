# config.py
import logging
import os
import pytz
from dotenv import load_dotenv
from telegram.ext import filters
from telegram.constants import ChatType

load_dotenv()

# --- Основные токены и URL ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLOUDFLARE_WORKER_URL = os.getenv("CLOUDFLARE_WORKER_URL")
CLOUDFLARE_AUTH_TOKEN = os.getenv("CLOUDFLARE_AUTH_TOKEN") # Токен для авторизации на Worker'е

# --- ID владельца бота (для уведомлений об ошибках и статуса) ---
# Установите ваш Telegram User ID
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))

# --- Настройки планировщика ---
SCHEDULE_TIMEZONE_STR = os.getenv("SCHEDULE_TIMEZONE", "UTC")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "0"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "5"))

# --- Настройки данных ---
DATA_FILE = os.getenv("DATA_FILE_PATH", "bot_data.db")

# --- Настройки логирования ---
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)
LOG_FORMAT = "%(asctime)s - %(name)s [%(levelname)s] - %(message)s" # Добавил квадратные скобки для уровня
LOG_FILE = os.getenv("LOG_FILE_PATH", "bot.log") # Путь к файлу лога

# --- Настройки локализации ---
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "ru")
SUPPORTED_LANGUAGES = ["ru", "en"]

# --- Фильтры сообщений ---
MESSAGE_FILTERS = (
    filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO |
    filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL |
    filters.Document.ALL
) & ~filters.COMMAND & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)

# --- Функции инициализации и валидации ---
def setup_logging():
    """Настраивает логирование в консоль и файл с ротацией."""
    log_formatter = logging.Formatter(LOG_FORMAT)
    logger = logging.getLogger() # Получаем корневой логгер
    logger.setLevel(LOG_LEVEL)

    # Обработчик для вывода в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)

    # Обработчик для вывода в файл с ротацией (например, раз в день)
    try:
        # Убедимся, что директория для лога существует
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # Ротация каждый день (midnight), храним 7 последних лог-файлов
        file_handler = logging.handlers.TimedRotatingFileHandler(
            LOG_FILE, when='midnight', interval=1, backupCount=7, encoding='utf-8'
        )
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
        logging.info(f"Логирование в файл '{LOG_FILE}' настроено.")
    except Exception as e:
        logging.error(f"Ошибка настройки файлового логирования: {e}", exc_info=True)

    # Уменьшаем шум от библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("telegram.ext").setLevel(max(LOG_LEVEL, logging.INFO))
    logging.getLogger("telegram.bot").setLevel(max(LOG_LEVEL, logging.INFO))
    logging.getLogger("apscheduler").setLevel(logging.WARNING) # Уменьшаем шум от планировщика


def get_schedule_timezone():
    try:
        return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC.")
        return pytz.utc

def validate_config():
    missing_vars = []
    if not TELEGRAM_BOT_TOKEN: missing_vars.append("TELEGRAM_BOT_TOKEN")
    if not CLOUDFLARE_WORKER_URL: missing_vars.append("CLOUDFLARE_WORKER_URL")
    if not CLOUDFLARE_AUTH_TOKEN: missing_vars.append("CLOUDFLARE_AUTH_TOKEN")
    if not BOT_OWNER_ID or BOT_OWNER_ID == 0:
        logging.warning("BOT_OWNER_ID не установлен! Уведомления об ошибках и команда /status не будут работать.")
        # Не считаем критической ошибкой, но предупреждаем

    if missing_vars:
        raise ValueError(f"Критические переменные окружения не установлены: {', '.join(missing_vars)}!")
    logging.info("Переменные окружения успешно загружены и проверены.")

# --- Вызов инициализации при импорте ---
# setup_logging() # Переносим вызов в main.py, чтобы избежать проблем с импортами
SCHEDULE_TIMEZONE = get_schedule_timezone()