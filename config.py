# config.py
import logging
import os
import pytz
from dotenv import load_dotenv
from telegram.ext import filters
from telegram.constants import ChatType

# --- Загрузка переменных окружения ---
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Настройки планировщика ---
SCHEDULE_TIMEZONE_STR = os.getenv("SCHEDULE_TIMEZONE", "UTC")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "0"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "5"))

# --- Настройки данных ---
# ИЗМЕНЕНО: Используем расширение .db для SQLite
DATA_FILE = "bot_data.db" # Новое имя файла базы данных

# --- Настройки Gemini ---
GEMINI_MODEL_NAME = "gemini-1.5-flash-latest"
GEMINI_MAX_OUTPUT_TOKENS = 1024
GEMINI_TEMPERATURE = 0.7

# --- Настройки логирования ---
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# --- Фильтры сообщений ---
MESSAGE_FILTERS = (
    filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO |
    filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL |
    filters.Document.ALL
) & ~filters.COMMAND & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)

# --- Функции инициализации ---
def setup_logging():
    logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("google").setLevel(logging.WARNING)

def get_schedule_timezone():
    try:
        return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC.")
        return pytz.utc

def validate_config():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Критическая ошибка: TELEGRAM_BOT_TOKEN не найден в .env файле!")
    if not GEMINI_API_KEY:
        raise ValueError("Критическая ошибка: GEMINI_API_KEY не найден в .env файле!")
    logging.info("Конфигурация успешно загружена и проверена.")

# --- Вызов инициализации при импорте ---
setup_logging()
SCHEDULE_TIMEZONE = get_schedule_timezone()