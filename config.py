# config.py
import logging
import logging.handlers # <-- Убедитесь, что этот импорт есть
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
# Определяем директорию для данных
if os.getenv('RENDER') == 'true':
    DATA_DIR = "/data" # Путь монтирования диска на Render
else:
    # Для локального запуска создаем директорию data, если ее нет
    DATA_DIR = "data"
    if not os.path.exists(DATA_DIR):
         try:
              os.makedirs(DATA_DIR)
              print(f"Локальная директория '{DATA_DIR}' создана.")
         except OSError as e:
              print(f"Не удалось создать локальную директорию '{DATA_DIR}': {e}")


DATA_FILE = os.path.join(DATA_DIR, "bot_data.db") # Полный путь к файлу БД
LOG_FILE = os.path.join(DATA_DIR, "bot.log") # Полный путь к лог-файлу

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
    """Настраивает логирование в консоль и в файл на диске."""
    log_formatter = logging.Formatter(LOG_FORMAT)
    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)

    # Очищаем существующие обработчики, если они есть (на случай повторного вызова)
    # for handler in logger.handlers[:]:
    #    logger.removeHandler(handler)

    # Обработчик для вывода в консоль (stdout/stderr)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(log_formatter)
    logger.addHandler(stream_handler)

    # Обработчик для записи в файл с ротацией
    try:
        # --- УДАЛЕНО: Строка os.makedirs(DATA_DIR, exist_ok=True) ---
        # Директория /data уже существует на Render
        # Локально мы создали 'data' выше, если ее не было

        # Создаем файл логов (или открываем существующий)
        # RotatingFileHandler сам создаст файл, если его нет, но директория ДОЛЖНА существовать
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
        # Используем print здесь, т.к. логгер может еще не быть полностью настроен
        print(f"INFO: Логирование в файл настроено: {LOG_FILE}")
    except Exception as e:
        # Логируем ошибку в консоль, если настройка файла не удалась
        logging.error(f"Ошибка настройки логирования в файл {LOG_FILE}: {e}", exc_info=True)

    # Уменьшаем шум от библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.INFO) # Часто шумит

def get_schedule_timezone():
    try:
        return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC.")
        return pytz.utc

def validate_config():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Критическая ошибка: TELEGRAM_BOT_TOKEN не найден!")
    if not GEMINI_API_KEY:
        raise ValueError("Критическая ошибка: GEMINI_API_KEY не найден!")
    logging.info("Конфигурация успешно загружена и проверена.")

# --- Вызов инициализации при импорте ---
# print("DEBUG: Начало инициализации config.py") # Для отладки
setup_logging() # Настраиваем логирование
SCHEDULE_TIMEZONE = get_schedule_timezone()
# print("DEBUG: Конец инициализации config.py")