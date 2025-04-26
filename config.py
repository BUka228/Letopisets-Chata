# config.py
import logging
import os
import pytz
from dotenv import load_dotenv
from telegram.ext import filters
from telegram.constants import ChatType
import logging.handlers # <-- Добавляем для ротации логов

load_dotenv()

# --- Основные токены и URL ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLOUDFLARE_WORKER_URL = os.getenv("CLOUDFLARE_WORKER_URL")
CLOUDFLARE_AUTH_TOKEN = os.getenv("CLOUDFLARE_AUTH_TOKEN")

# --- ID владельца бота (для уведомлений об ошибках и статуса) ---
# !!! ВАЖНО: Установите ваш реальный Telegram User ID !!!
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))

# --- Настройки планировщика ---
SCHEDULE_TIMEZONE_STR = os.getenv("SCHEDULE_TIMEZONE", "UTC")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "0")) # Время по умолчанию (час UTC)
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "5")) # Время по умолчанию (минута UTC)
JOB_CHECK_INTERVAL_MINUTES = int(os.getenv("JOB_CHECK_INTERVAL_MINUTES", "5")) # Интервал проверки

# --- Настройки данных ---
DATA_FILE = os.getenv("DATA_FILE_PATH", "bot_data.db")

# --- Настройки логирования ---
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)
LOG_FORMAT = "%(asctime)s - %(name)s [%(levelname)s] - %(message)s"
LOG_FILE = os.getenv("LOG_FILE_PATH", "bot.log") # Путь к файлу лога

# --- Настройки локализации ---
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "ru")
SUPPORTED_LANGUAGES = ["ru", "en"]

SUPPORTED_GENRES = {
    'default': 'Стандартный', # Отображаемое имя берется из localization
    'humor': 'Юмористический',
    'detective': 'Детективный',
    'fantasy': 'Фэнтезийный',
    'news_report': 'Новостной репортаж'
}


PURGE_JOB_INTERVAL_HOURS = int(os.getenv("PURGE_JOB_INTERVAL_HOURS", "24")) 

DEFAULT_RETENTION_DAYS = int(os.getenv("DEFAULT_RETENTION_DAYS", "90"))

# --- НОВОЕ: Личности Летописца ---
SUPPORTED_PERSONALITIES = {
    'neutral': 'Нейтральный', # Имя для localization
    'wise': 'Мудрый Старец',
    'sarcastic': 'Саркастичный Наблюдатель',
    'poet': 'Поэт-Романтик',
}
DEFAULT_PERSONALITY = 'neutral'

# --- НОВОЕ: Форматы Вывода ---
SUPPORTED_OUTPUT_FORMATS = { 'story': 'История', 'digest': 'Дайджест' }
# --- ВОТ ЭТА СТРОКА ДОЛЖНА БЫТЬ ---
DEFAULT_OUTPUT_FORMAT = 'story'

# --- НОВОЕ: Настройки Вмешательств ---
INTERVENTION_ENABLED_DEFAULT = False # ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ!


# Пределы
INTERVENTION_MIN_COOLDOWN_MIN = 60      # Минимальный кулдаун - 1 час
INTERVENTION_MAX_COOLDOWN_MIN = 60*24*7 # Максимальный кулдаун - 1 неделя
INTERVENTION_MIN_MIN_MSGS = 3           # Минимум 3 сообщения для реакции
INTERVENTION_MAX_MIN_MSGS = 50          # Максимум 50 сообщений
INTERVENTION_MIN_TIMESPAN_MIN = 5       # Минимальное окно проверки - 5 минут
INTERVENTION_MAX_TIMESPAN_MIN = 120     # Максимальное окно проверки - 2 часа

# Значения по умолчанию (если в БД NULL)
INTERVENTION_DEFAULT_COOLDOWN_MIN = 180 # 3 часа
INTERVENTION_DEFAULT_MIN_MSGS = 5
INTERVENTION_DEFAULT_TIMESPAN_MIN = 15

# Другие настройки вмешательств
INTERVENTION_PROMPT_MESSAGE_COUNT = 5 # Сколько последних сообщ. давать ИИ
INTERVENTION_MAX_RETRY = 1 # Макс. 1 повтор для генерации вмешательства
INTERVENTION_TIMEOUT_SEC = 10 # Короткий таймаут для ИИ


COMMON_TIMEZONES = {
    "UTC": "UTC±00:00",
    "Europe/London": "Лондон (GMT+0/GMT+1)",
    "Europe/Berlin": "Берлин (CET/CEST)",
    "Europe/Moscow": "Москва (MSK)",
    "Asia/Yekaterinburg": "Екатеринбург (YEKT)",
    "Asia/Dubai": "Дубай (GST)",
    "Asia/Tashkent": "Ташкент (UZT)",
    "Asia/Almaty": "Алматы (ALMT)",
    "Asia/Tokyo": "Токио (JST)",
    "America/New_York": "Нью-Йорк (EST/EDT)",
    "America/Chicago": "Чикаго (CST/CDT)",
    "America/Denver": "Денвер (MST/MDT)",
    "America/Los_Angeles": "Лос-Анджелес (PST/PDT)"
}

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
    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)
    for handler in logger.handlers[:]: logger.removeHandler(handler) # Убираем старые обработчики
    console_handler = logging.StreamHandler(); console_handler.setFormatter(log_formatter); logger.addHandler(console_handler)
    try:
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir and not os.path.exists(log_dir): os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(LOG_FILE, when='midnight', interval=1, backupCount=7, encoding='utf-8')
        file_handler.setFormatter(log_formatter); logger.addHandler(file_handler)
        logging.info(f"Логирование в файл '{LOG_FILE}' настроено.")
    except Exception as e: logging.error(f"Ошибка настройки файлового логирования в '{LOG_FILE}': {e}", exc_info=True)
    # Уменьшаем шум
    logging.getLogger("httpx").setLevel(logging.WARNING); logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(max(LOG_LEVEL, logging.INFO)); logging.getLogger("telegram.bot").setLevel(max(LOG_LEVEL, logging.INFO))
    logging.getLogger("apscheduler").setLevel(logging.WARNING); logging.getLogger("tenacity").setLevel(logging.WARNING) # Логи tenacity
    # Устанавливаем уровень для наших модулей
    log_modules = ["data_manager", "gemini_client", "bot_handlers", "jobs", "localization"]
    for mod_name in log_modules: logging.getLogger(mod_name).setLevel(LOG_LEVEL)

def get_schedule_timezone():
    try: return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError: logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC."); return pytz.utc

def validate_config():
    missing_vars = []
    if not TELEGRAM_BOT_TOKEN: missing_vars.append("TELEGRAM_BOT_TOKEN")
    if not CLOUDFLARE_WORKER_URL: missing_vars.append("CLOUDFLARE_WORKER_URL")
    if not CLOUDFLARE_AUTH_TOKEN: missing_vars.append("CLOUDFLARE_AUTH_TOKEN")
    if not BOT_OWNER_ID or BOT_OWNER_ID == 0: logging.error("!!! BOT_OWNER_ID не установлен! Уведомления об ошибках и команда /status не будут работать. !!!")
    if missing_vars: raise ValueError(f"Критические переменные окружения не установлены: {', '.join(missing_vars)}!")
    logging.info("Переменные окружения успешно загружены и проверены.")

# --- Вызов при импорте ---
SCHEDULE_TIMEZONE = get_schedule_timezone()