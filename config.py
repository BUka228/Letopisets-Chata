# config.py
import logging
import os
import pytz
from dotenv import load_dotenv
from telegram.ext import filters
from telegram.constants import ChatType

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # <-- БОЛЬШЕ НЕ НУЖЕН ЗДЕСЬ

# --- ИЗМЕНЕНО: Настройки для Cloudflare Worker Proxy ---
CLOUDFLARE_WORKER_URL = os.getenv("CLOUDFLARE_WORKER_URL")
CLOUDFLARE_AUTH_TOKEN = os.getenv("CLOUDFLARE_AUTH_TOKEN") # Токен, который мы придумали

SCHEDULE_TIMEZONE_STR = os.getenv("SCHEDULE_TIMEZONE", "UTC")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "0"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "5"))

DATA_FILE = os.getenv("DATA_FILE_PATH", "bot_data.db")

# --- Настройки Gemini БОЛЬШЕ НЕ НУЖНЫ ЗДЕСЬ ---
# GEMINI_MODEL_NAME = "gemini-1.5-flash-latest"
# GEMINI_MAX_OUTPUT_TOKENS = 1024
# GEMINI_TEMPERATURE = 0.7

LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

MESSAGE_FILTERS = (
    filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO |
    filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL |
    filters.Document.ALL
) & ~filters.COMMAND & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)

def setup_logging():
    # ... (код без изменений) ...
    logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("google").setLevel(logging.WARNING) # Можно убрать, google больше не используется напрямую
    logging.getLogger("telegram.ext").setLevel(max(LOG_LEVEL, logging.INFO))
    logging.getLogger("telegram.bot").setLevel(max(LOG_LEVEL, logging.INFO))

def get_schedule_timezone():
    # ... (код без изменений) ...
    try:
        return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC.")
        return pytz.utc

def validate_config():
    missing_vars = []
    if not TELEGRAM_BOT_TOKEN: missing_vars.append("TELEGRAM_BOT_TOKEN")
    # if not GEMINI_API_KEY: missing_vars.append("GEMINI_API_KEY") # <-- Убираем проверку Gemini ключа
    if not CLOUDFLARE_WORKER_URL: missing_vars.append("CLOUDFLARE_WORKER_URL") # <-- Добавляем проверку URL Worker'а
    if not CLOUDFLARE_AUTH_TOKEN: missing_vars.append("CLOUDFLARE_AUTH_TOKEN") # <-- Добавляем проверку токена Worker'а

    if missing_vars:
        raise ValueError(f"Критические переменные окружения не установлены: {', '.join(missing_vars)}!")
    logging.info("Переменные окружения успешно загружены и проверены.")

setup_logging()
SCHEDULE_TIMEZONE = get_schedule_timezone()