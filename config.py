# config.py
import logging
import os
import pytz
from dotenv import load_dotenv
from telegram.ext import filters
from telegram.constants import ChatType

# --- Загрузка переменных окружения ---
# На Render переменные будут установлены через их интерфейс,
# но dotenv удобен для локальной разработки
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Настройки планировщика ---
SCHEDULE_TIMEZONE_STR = os.getenv("SCHEDULE_TIMEZONE", "UTC")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "0"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "5"))

# --- Настройки данных ---
# Имя файла БД. На Render он будет на персистентном диске.
# Путь к персистентному диску Render обычно /var/data
# Можно сделать путь абсолютным или относительным (если рабочая директория совпадает)
# Оставим пока относительным, но укажем Render монтировать диск в корень проекта
DATA_FILE = os.getenv("DATA_FILE_PATH", "bot_data.db") # Позволяет переопределить путь через env

# --- Настройки Gemini ---
GEMINI_MODEL_NAME = "gemini-1.5-flash-latest"
GEMINI_MAX_OUTPUT_TOKENS = 1024
GEMINI_TEMPERATURE = 0.7

# --- Настройки логирования ---
# Уровень DEBUG может быть полезен при отладке на Render
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# --- Фильтры сообщений ---
MESSAGE_FILTERS = (
    filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO |
    filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL |
    filters.Document.ALL
) & ~filters.COMMAND & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)

# --- Функции инициализации ---
def setup_logging():
    logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL, force=True) # force=True для переопределения базовой конф.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("google").setLevel(logging.WARNING)
    # Уменьшаем логгирование от самого telegram.ext при INFO уровне
    logging.getLogger("telegram.ext").setLevel(max(LOG_LEVEL, logging.INFO)) # Не ниже INFO
    logging.getLogger("telegram.bot").setLevel(max(LOG_LEVEL, logging.INFO))

def get_schedule_timezone():
    try:
        return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC.")
        return pytz.utc

def validate_config():
    missing_vars = []
    if not TELEGRAM_BOT_TOKEN:
        missing_vars.append("TELEGRAM_BOT_TOKEN")
    if not GEMINI_API_KEY:
        missing_vars.append("GEMINI_API_KEY")

    if missing_vars:
        raise ValueError(f"Критические переменные окружения не установлены: {', '.join(missing_vars)}!")
    logging.info("Переменные окружения успешно загружены и проверены.")

# --- Вызов инициализации при импорте ---
# Настройка логирования должна быть одной из первых операций
setup_logging()
SCHEDULE_TIMEZONE = get_schedule_timezone()