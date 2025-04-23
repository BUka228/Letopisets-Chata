# config.py
import logging
import logging.handlers
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
if os.getenv('RENDER') == 'true':
    DATA_DIR = "/data"
else:
    DATA_DIR = "data"
    # Локально создаем директорию, если нужно
    if not os.path.exists(DATA_DIR):
         try:
              os.makedirs(DATA_DIR)
              print(f"INFO: Локальная директория '{DATA_DIR}' создана.")
         except OSError as e:
              print(f"ERROR: Не удалось создать локальную директорию '{DATA_DIR}': {e}")

DATA_FILE = os.path.join(DATA_DIR, "bot_data.db")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

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
    log_formatter = logging.Formatter(LOG_FORMAT)
    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)

    # Очищаем существующие обработчики (на случай перезапуска)
    for handler in logger.handlers[:]:
       logger.removeHandler(handler)

    # Обработчик для консоли
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(log_formatter)
    logger.addHandler(stream_handler)

    # --- ИЗМЕНЕНИЕ: Устойчивая настройка файлового логгера ---
    try:
        # Проверяем, существует ли директория ПЕРЕД созданием хендлера
        # Хотя Render должен ее создать, эта проверка не помешает
        if not os.path.exists(DATA_DIR):
             # Попытка создать еще раз на всякий случай
             os.makedirs(DATA_DIR, exist_ok=True)
             print(f"INFO: Директория '{DATA_DIR}' проверена/создана для логов.")

        # Создаем файл логов (или открываем существующий)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
        print(f"INFO: Логирование в файл настроено: {LOG_FILE}")
    except (OSError, FileNotFoundError, PermissionError) as e:
        # Логируем ошибку в консоль, если настройка файла не удалась
        # Используем print, т.к. стандартный логгер может быть не готов
        print(f"ERROR: Ошибка настройки логирования в файл {LOG_FILE}: {e.__class__.__name__}: {e}. Логирование будет только в консоль.")
    except Exception as e:
         print(f"ERROR: Неожиданная ошибка настройки логирования в файл: {e.__class__.__name__}: {e}. Логирование будет только в консоль.")

    # Уменьшаем шум от библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.INFO)

def get_schedule_timezone():
    try:
        return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError:
        # Используем logging здесь, т.к. он уже должен быть настроен (хотя бы на консоль)
        logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC.")
        return pytz.utc

def validate_config():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Критическая ошибка: TELEGRAM_BOT_TOKEN не найден!")
    if not GEMINI_API_KEY:
        raise ValueError("Критическая ошибка: GEMINI_API_KEY не найден!")
    logging.info("Конфигурация успешно загружена и проверена.")

# --- Вызов инициализации при импорте ---
setup_logging()
SCHEDULE_TIMEZONE = get_schedule_timezone()