# config.py
import logging
import logging.handlers # <--- Добавлен импорт для RotatingFileHandler
import os
import pytz
from dotenv import load_dotenv
from telegram.ext import filters
from telegram.constants import ChatType

# --- Загрузка переменных окружения ---
# load_dotenv() будет работать локально, на Render переменные задаются через интерфейс
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Настройки планировщика ---
# Используем значения из окружения или значения по умолчанию
SCHEDULE_TIMEZONE_STR = os.getenv("SCHEDULE_TIMEZONE", "UTC")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "0"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "5"))

# --- Настройки данных ---
# Определяем директорию для данных в зависимости от окружения (Render или локально)
if os.getenv('RENDER') == 'true':
    # Мы на Render, используем персистентный диск, смонтированный в /data
    DATA_DIR = "/data"
    logger_initial = logging.getLogger(__name__) # Временный логгер для сообщения о пути
    logger_initial.info("Запуск на Render.com. Путь к данным: /data")
else:
    # Локальный запуск, используем текущую директорию
    DATA_DIR = "."
    logger_initial = logging.getLogger(__name__)
    logger_initial.info("Локальный запуск. Путь к данным: текущая директория")

# Создаем директорию, если она не существует (особенно важно для локального запуска)
os.makedirs(DATA_DIR, exist_ok=True)

# Полный путь к файлу базы данных SQLite
DATA_FILE = os.path.join(DATA_DIR, "bot_data.db")
# Полный путь к файлу логов
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

# --- Настройки Gemini ---
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash-latest")
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "1024"))
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.7"))

# --- Настройки логирования ---
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
# Преобразуем строку уровня лога в константу logging
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)
LOG_FORMAT = "%(asctime)s - %(name)s [%(levelname)s] - %(message)s" # Немного изменил формат

# --- Фильтры сообщений ---
# Собираем сообщения из групп и супергрупп
MESSAGE_FILTERS = (
    filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO |
    filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL |
    filters.Document.ALL
) & ~filters.COMMAND & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)


# --- Функции инициализации ---
def setup_logging():
    """Настраивает логирование в консоль и в файл с ротацией."""
    log_formatter = logging.Formatter(LOG_FORMAT)
    root_logger = logging.getLogger() # Получаем корневой логгер
    root_logger.setLevel(LOG_LEVEL) # Устанавливаем уровень для всех логгеров

    # Очищаем существующих обработчиков (если были добавлены ранее или при перезапуске)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 1. Обработчик для вывода в консоль (stdout)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(log_formatter)
    # Устанавливаем уровень для обработчика (можно сделать отличным от root)
    stream_handler.setLevel(LOG_LEVEL)
    root_logger.addHandler(stream_handler)

    # 2. Обработчик для записи в файл с ротацией
    try:
        # Ротация логов: 5 файлов по 5MB
        file_handler = logging.handlers.RotatingFileHandler(
             LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(log_formatter)
        # Устанавливаем уровень для файлового обработчика
        file_handler.setLevel(LOG_LEVEL)
        root_logger.addHandler(file_handler)
        logging.info(f"Логирование в файл настроено: {LOG_FILE}") # Используем logging.info вместо logger.info
    except Exception as e:
        logging.error(f"Ошибка настройки логирования в файл {LOG_FILE}: {e}") # Используем logging.error

    # Уменьшаем шум от библиотек (после настройки обработчиков)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING) # Сделаем потише
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO) # Логи самой библиотеки PTB
    logging.getLogger("apscheduler").setLevel(logging.WARNING) # Если будет использоваться

def get_schedule_timezone():
    """Возвращает объект часового пояса для планировщика."""
    try:
        return pytz.timezone(SCHEDULE_TIMEZONE_STR)
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Неизвестный часовой пояс '{SCHEDULE_TIMEZONE_STR}'. Используется UTC.")
        return pytz.utc

def validate_config():
    """Проверяет наличие критически важных настроек."""
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Критическая ошибка: TELEGRAM_BOT_TOKEN не найден!")
    if not GEMINI_API_KEY:
        raise ValueError("Критическая ошибка: GEMINI_API_KEY не найден!")
    logging.info("Ключевые переменные окружения (токены) успешно загружены.")

# --- Вызов инициализации при импорте ---
# Сначала настраиваем логирование, чтобы остальные сообщения выводились корректно
setup_logging()
# Затем получаем часовой пояс
SCHEDULE_TIMEZONE = get_schedule_timezone()
# Проверку конфигурации validate_config() лучше вызывать явно в main.py перед запуском
# чтобы убедиться, что логирование уже настроено к моменту возможной ошибки.