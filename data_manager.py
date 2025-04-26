# data_manager.py
import logging
import sqlite3
import threading
import time
import datetime
import pytz
import re # Для валидации времени

from typing import List, Dict, Any, Optional, Tuple

# Импортируем путь к файлу БД и языки из конфига
from config import DATA_FILE, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

# Используем thread-local storage для соединений к БД
local_storage = threading.local()

def _get_db_connection() -> sqlite3.Connection:
    """Получает или создает соединение с БД SQLite для текущего потока."""
    # Проверяем, существует ли соединение для этого потока и не закрыто ли оно
    connection = getattr(local_storage, 'connection', None)
    if connection:
        try:
            # Быстрая проверка соединения
            connection.execute("PRAGMA schema_version;")
            return connection
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            logger.warning("Обнаружено закрытое соединение SQLite, пересоздаем...")
            close_db_connection() # Закрываем некорректное

    # Создаем новое соединение
    retry_count = 0
    max_retries = 3
    while retry_count < max_retries:
        try:
            logger.debug(f"Создание/получение SQLite соединения для потока {threading.current_thread().name} к '{DATA_FILE}'...")
            conn = sqlite3.connect(DATA_FILE, timeout=15, check_same_thread=False)
            # Настройки для улучшения производительности и надежности
            conn.execute("PRAGMA journal_mode=WAL;") # Write-Ahead Logging
            conn.execute("PRAGMA busy_timeout = 5000;") # Ждать 5 сек при блокировке
            conn.execute("PRAGMA foreign_keys = ON;") # Включаем поддержку внешних ключей
            conn.row_factory = sqlite3.Row # Возвращать строки как словари
            local_storage.connection = conn
            logger.debug("SQLite соединение успешно получено/создано.")
            return conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and retry_count < max_retries - 1:
                retry_count += 1
                wait_time = (retry_count ** 2) * 0.1 + (retry_count * 0.05) # Небольшая экспоненциальная задержка
                logger.warning(f"БД '{DATA_FILE}' заблокирована, повтор через {wait_time:.2f} сек... (попытка {retry_count}/{max_retries})")
                time.sleep(wait_time)
                continue
            else:
                logger.critical(f"Не удалось подключиться к БД SQLite '{DATA_FILE}' после {max_retries} попыток: {e}", exc_info=True)
                raise
        except sqlite3.Error as e:
            logger.critical(f"Ошибка SQLite при подключении к '{DATA_FILE}': {e}", exc_info=True)
            raise

    # Сюда не должны дойти, но для type checker
    raise sqlite3.Error(f"Не удалось получить соединение к БД после {max_retries} попыток.")


def close_db_connection():
    """Закрывает соединение с БД для текущего потока, если оно существует."""
    connection = getattr(local_storage, 'connection', None)
    if connection:
        logger.debug(f"Закрытие SQLite соединения для потока {threading.current_thread().name}")
        try:
            connection.close()
        except sqlite3.Error as e:
            logger.error(f"Ошибка при закрытии SQLite соединения: {e}")
        finally:
            local_storage.connection = None

def _execute_query(sql: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False) -> Any:
    """Вспомогательная функция для выполнения SQL запросов с обработкой блокировок."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if fetch_one:
            return cursor.fetchone()
        if fetch_all:
            return cursor.fetchall()
        conn.commit()
        return cursor.rowcount # Для INSERT, UPDATE, DELETE
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite: {sql} | {params} | {e}", exc_info=True)
        try:
            # Попытка отката транзакции при ошибке записи
            if hasattr(local_storage, 'connection') and local_storage.connection:
                local_storage.connection.rollback()
                logger.warning("Транзакция SQLite отменена из-за ошибки.")
        except sqlite3.Error as rollback_e:
            logger.error(f"Ошибка при откате транзакции SQLite: {rollback_e}")
        raise # Передаем исходное исключение для обработки выше

def _init_db():
    """Инициализирует БД: создает/обновляет таблицы и индексы."""
    try:
        logger.info("Инициализация/проверка структуры БД...")
        # --- Таблица messages ---
        _execute_query("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                timestamp TEXT NOT NULL, -- Храним как ISO строку UTC
                message_type TEXT NOT NULL, -- 'text', 'photo', etc.
                content TEXT, -- Текст сообщения или подпись к медиа
                file_id TEXT, -- Telegram file_id
                file_unique_id TEXT, -- Telegram file_unique_id
                file_name TEXT, -- Имя файла для документов/аудио и т.д.
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        _execute_query("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id)")
        _execute_query("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp)")
        logger.info("Таблица 'messages' проверена/создана.")

        # --- Таблица chat_settings ---
        _execute_query(f"""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                lang TEXT DEFAULT '{DEFAULT_LANGUAGE}',
                enabled BOOLEAN DEFAULT 1, -- 1 = True, 0 = False
                custom_schedule_time TEXT DEFAULT NULL, -- Время HH:MM UTC или NULL
                timezone TEXT DEFAULT 'UTC', -- pytz Timezone Name
                story_genre TEXT DEFAULT 'default' -- Ключ жанра ('default', 'humor', etc.)
            )
        """)
        # Безопасное добавление новых колонок (не вызовет ошибку, если они уже есть)
        # Используем PRAGMA table_info для проверки существования колонки перед ALTER TABLE
        conn = _get_db_connection()
        cursor = conn.cursor()

        def column_exists(table_name, column_name):
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [row['name'] for row in cursor.fetchall()]
            return column_name in columns

        if not column_exists('chat_settings', 'custom_schedule_time'):
            _execute_query("ALTER TABLE chat_settings ADD COLUMN custom_schedule_time TEXT DEFAULT NULL")
            logger.info("Добавлено поле 'custom_schedule_time' в 'chat_settings'.")

        if not column_exists('chat_settings', 'timezone'):
            _execute_query("ALTER TABLE chat_settings ADD COLUMN timezone TEXT DEFAULT 'UTC'")
            logger.info("Добавлено поле 'timezone' в 'chat_settings'.")

        if not column_exists('chat_settings', 'story_genre'):
             _execute_query("ALTER TABLE chat_settings ADD COLUMN story_genre TEXT DEFAULT 'default'")
             logger.info("Добавлено поле 'story_genre' в 'chat_settings'.")

        logger.info("Таблица 'chat_settings' проверена/обновлена.")

        # --- Таблица feedback ---
        _execute_query("""
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL, -- ID сообщения *с историей*, к которому относится отзыв
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                rating INTEGER NOT NULL, -- Например, 1 для 👍, -1 для 👎
                timestamp TEXT NOT NULL -- Время отзыва в UTC ISO
            )
        """)
        _execute_query("CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback (chat_id, message_id)")
        logger.info("Таблица 'feedback' проверена/создана.")

        logger.info(f"База данных '{DATA_FILE}' успешно инициализирована/проверена.")
    except Exception as e:
         logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации БД: {e}", exc_info=True)
         raise

# --- Функции для сообщений ---
def load_data():
    """Загружает (инициализирует) базу данных."""
    _init_db()

def add_message(chat_id: int, message_data: Dict[str, Any]):
    """Добавляет или заменяет сообщение в базе данных."""
    if not isinstance(message_data, dict):
        logger.warning(f"Попытка добавить некорректные данные (не словарь) для чата {chat_id}")
        return
    # Проверка обязательных полей
    required_fields = ['message_id', 'user_id', 'timestamp', 'type']
    if not all(field in message_data for field in required_fields):
        logger.warning(f"Пропущено добавление сообщения для чата {chat_id}: отсутствуют обязательные поля в {message_data.keys()}")
        return

    sql = """
        INSERT OR REPLACE INTO messages
        (chat_id, message_id, user_id, username, timestamp, message_type, content, file_id, file_unique_id, file_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        chat_id,
        message_data.get('message_id'),
        message_data.get('user_id'),
        message_data.get('username'),
        message_data.get('timestamp'), # Должен быть в ISO формате UTC
        message_data.get('type'),
        message_data.get('content'),
        message_data.get('file_id'),
        message_data.get('file_unique_id'),
        message_data.get('file_name')
    )
    try:
        _execute_query(sql, params)
        logger.debug(f"Сообщение {message_data.get('message_id')} добавлено/заменено для чата {chat_id}.")
    except Exception: # Ловим все ошибки _execute_query
        # Логирование уже произошло внутри _execute_query
        logger.error(f"Не удалось добавить/заменить сообщение {message_data.get('message_id')} для чата {chat_id}.")

def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    """Возвращает все сообщения для указанного чата, отсортированные по времени."""
    messages = []
    sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp ASC"
    try:
        rows = _execute_query(sql, (chat_id,), fetch_all=True)
        if rows:
            # Преобразуем sqlite3.Row в словари и переименовываем поле
            messages = []
            for row in rows:
                msg_dict = dict(row)
                msg_dict['type'] = msg_dict.pop('message_type', None) # Переименовать обратно
                messages.append(msg_dict)
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id}.")
    except Exception: # Ловим все ошибки _execute_query
        logger.error(f"Не удалось получить сообщения для чата {chat_id}.")
    return messages

def clear_messages_for_chat(chat_id: int):
    """Удаляет все сообщения для указанного чата."""
    sql = "DELETE FROM messages WHERE chat_id = ?"
    try:
        deleted_rows = _execute_query(sql, (chat_id,))
        if deleted_rows is not None and deleted_rows > 0:
            logger.info(f"Удалено {deleted_rows} сообщений для чата {chat_id} из БД.")
        else:
            logger.info(f"Нет сообщений для удаления в чате {chat_id} или произошла ошибка (deleted_rows={deleted_rows}).")
    except Exception: # Ловим все ошибки _execute_query
        logger.error(f"Не удалось удалить сообщения для чата {chat_id}.")

# --- НОВЫЕ Функции для выборки сообщений (для саммари) ---

def get_messages_for_chat_since(chat_id: int, since_datetime_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Возвращает сообщения из чата, начиная с указанной даты/времени UTC."""
    messages = []
    # Убедимся, что datetime имеет таймзону UTC для корректного сравнения ISO строк
    if since_datetime_utc.tzinfo is None:
         since_datetime_utc = pytz.utc.localize(since_datetime_utc)
    else:
         since_datetime_utc = since_datetime_utc.astimezone(pytz.utc)

    since_iso_str = since_datetime_utc.isoformat()
    sql = "SELECT * FROM messages WHERE chat_id = ? AND timestamp >= ? ORDER BY timestamp ASC"
    try:
        rows = _execute_query(sql, (chat_id, since_iso_str), fetch_all=True)
        if rows:
            messages = []
            for row in rows:
                msg_dict = dict(row)
                msg_dict['type'] = msg_dict.pop('message_type', None)
                messages.append(msg_dict)
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id} с {since_iso_str}.")
    except Exception:
        logger.error(f"Не удалось получить сообщения для чата {chat_id} с {since_iso_str}.")
    return messages

def get_messages_for_chat_last_n(chat_id: int, limit: int) -> List[Dict[str, Any]]:
    """Возвращает последние N сообщений из чата, отсортированные по времени."""
    messages = []
    if limit <= 0:
        return messages
    # Выбираем последние N по timestamp DESC, затем разворачиваем в Python
    sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?"
    try:
        rows = _execute_query(sql, (chat_id, limit), fetch_all=True)
        if rows:
            # Разворачиваем, чтобы получить хронологический порядок
            messages = []
            for row in reversed(rows):
                msg_dict = dict(row)
                msg_dict['type'] = msg_dict.pop('message_type', None)
                messages.append(msg_dict)
        logger.debug(f"Извлечено {len(messages)} последних сообщений для чата {chat_id} (лимит {limit}).")
    except Exception:
        logger.error(f"Не удалось получить последние {limit} сообщений для чата {chat_id}.")
    return messages

def get_messages_for_chat_today(chat_id: int) -> List[Dict[str, Any]]:
    """Возвращает сообщения из чата за сегодня (UTC)."""
    today_start_utc = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return get_messages_for_chat_since(chat_id, today_start_utc)


# --- Функции для Настроек Чата ---
def get_chat_settings(chat_id: int) -> Dict[str, Any]:
    """
    Возвращает настройки для чата (язык, статус, время, таймзону, жанр).
    Если настроек нет, создает их со значениями по умолчанию.
    """
    # Выбираем все актуальные поля
    sql_select = "SELECT lang, enabled, custom_schedule_time, timezone, story_genre FROM chat_settings WHERE chat_id = ?"
    # Вставляем значения по умолчанию для всех полей при первом обращении
    sql_insert = f"""
        INSERT OR IGNORE INTO chat_settings
        (chat_id, lang, enabled, custom_schedule_time, timezone, story_genre)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    # Значения по умолчанию, используемые при вставке и как fallback
    default_settings = {
        'lang': DEFAULT_LANGUAGE,
        'enabled': True, # В БД хранится как 1
        'custom_schedule_time': None,
        'timezone': 'UTC',
        'story_genre': 'default'
    }
    try:
        row = _execute_query(sql_select, (chat_id,), fetch_one=True)
        if row:
            # Преобразуем row в словарь
            settings = dict(row)
            # Преобразуем enabled из 0/1 в bool для удобства
            settings['enabled'] = bool(settings.get('enabled', 1)) # По умолчанию True, если вдруг NULL
            return settings
        else:
            # Настроек нет, создаем по умолчанию
            logger.info(f"Создание настроек по умолчанию для чата {chat_id}")
            _execute_query(sql_insert, (
                chat_id,
                default_settings['lang'],
                1 if default_settings['enabled'] else 0, # Сохраняем как 1
                default_settings['custom_schedule_time'],
                default_settings['timezone'],
                default_settings['story_genre']
            ))
            # Возвращаем словарь с дефолтными значениями
            return default_settings
    except Exception: # Ловим ошибки _execute_query или другие
        logger.exception(f"Не удалось получить/создать настройки для чата {chat_id}. Возвращаются значения по умолчанию.")
        return default_settings # Возвращаем дефолт при любой ошибке

def update_chat_setting(chat_id: int, setting_key: str, setting_value: Optional[str | bool | int]) -> bool:
    """
    Обновляет одну настройку чата (lang, enabled, custom_schedule_time, timezone, story_genre).
    Использует UPSERT для атомарного обновления/вставки.
    Возвращает True при успехе, False при ошибке.
    """
    # Список допустимых ключей настроек (колонок в таблице chat_settings, кроме chat_id)
    allowed_keys = ['lang', 'enabled', 'custom_schedule_time', 'timezone', 'story_genre']
    if setting_key not in allowed_keys:
        logger.error(f"Попытка обновить неверный ключ настройки '{setting_key}' для чата {chat_id}")
        return False

    # --- Валидация и подготовка значения для сохранения в БД ---
    value_to_save: Optional[str | int] = None # Тип для БД: TEXT или INTEGER (для boolean)

    if setting_key == 'lang':
        if not isinstance(setting_value, str) or setting_value not in SUPPORTED_LANGUAGES:
            logger.error(f"Недопустимое значение языка '{setting_value}' для чата {chat_id}. Доступные: {SUPPORTED_LANGUAGES}")
            return False
        value_to_save = setting_value
    elif setting_key == 'enabled':
        # Преобразуем bool в 0 или 1 для SQLite
        value_to_save = 1 if bool(setting_value) else 0
    elif setting_key == 'custom_schedule_time':
        # Разрешаем NULL для сброса на дефолтное время
        if setting_value is None:
            value_to_save = None
        # Проверяем формат HH:MM
        elif isinstance(setting_value, str) and re.fullmatch(r"^(?:[01]\d|2[0-3]):[0-5]\d$", setting_value):
            value_to_save = setting_value
        else:
            logger.error(f"Неверный формат времени UTC '{setting_value}' для чата {chat_id}. Ожидается HH:MM или None.")
            return False
    elif setting_key == 'timezone':
        if not isinstance(setting_value, str):
            logger.error(f"Неверный тип для timezone '{setting_value}' (ожидался str) чат={chat_id}"); return False
        try:
            # Проверяем, что pytz знает такую таймзону
            pytz.timezone(setting_value)
            value_to_save = setting_value
        except pytz.exceptions.UnknownTimeZoneError:
            logger.error(f"Неизвестная или некорректная таймзона '{setting_value}' для чата {chat_id}")
            return False
    elif setting_key == 'story_genre':
        # TODO: Заменить этот список на импорт из bot_handlers или config, когда он будет там определен
        TEMP_SUPPORTED_GENRES_KEYS = ['default', 'humor', 'detective', 'fantasy', 'news_report']
        if not isinstance(setting_value, str) or setting_value not in TEMP_SUPPORTED_GENRES_KEYS:
             logger.error(f"Недопустимый жанр '{setting_value}' для чата {chat_id}. Доступные: {TEMP_SUPPORTED_GENRES_KEYS}")
             return False
        value_to_save = setting_value
    # Не должно быть других ключей из-за проверки allowed_keys
    # --- Конец валидации ---

    # SQL-запрос UPSERT (INSERT или UPDATE)
    sql = f"""
        INSERT INTO chat_settings (chat_id, {setting_key}) VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET {setting_key} = excluded.{setting_key}
    """
    try:
        _execute_query(sql, (chat_id, value_to_save))
        logger.info(f"Настройка '{setting_key}' для чата {chat_id} успешно обновлена на '{value_to_save}'.")

        # Специфичные действия после обновления, например, обновление кэша языка
        if setting_key == 'lang' and isinstance(value_to_save, str):
            try:
                # Используем динамический импорт для избежания циклических зависимостей
                from localization import update_chat_lang_cache
                update_chat_lang_cache(chat_id, value_to_save)
                logger.debug(f"Кэш языка для чата {chat_id} обновлен на '{value_to_save}'.")
            except ImportError:
                logger.error("Не удалось импортировать localization для обновления кэша языка.")
            except Exception as cache_e:
                 logger.error(f"Ошибка при обновлении кэша языка для чата {chat_id}: {cache_e}")

        return True # Успех
    except Exception: # Ловим ошибки _execute_query
        logger.error(f"Не удалось обновить настройку '{setting_key}' для чата {chat_id} на значение '{setting_value}'.")
        return False # Неудача

# --- Функции для получения отдельных настроек ---

def get_chat_language(chat_id: int) -> str:
    """Получает язык чата из кэша или БД."""
    # Предпочтительно использовать кэшированную версию из localization
    try:
         from localization import get_chat_lang as get_cached_lang
         return get_cached_lang(chat_id) # Используем кэшированную версию
    except ImportError: # На случай проблем с импортом
         logger.error("Не удалось импортировать get_chat_lang из localization. Получение напрямую из БД.")
         settings = get_chat_settings(chat_id) # Получаем из БД
         return settings.get('lang', DEFAULT_LANGUAGE) # Возвращаем язык из настроек или дефолтный

def get_enabled_chats() -> List[int]:
    """Возвращает список ID чатов, у которых 'enabled' = 1 (True)."""
    chat_ids = []
    # Выбираем только chat_id, где enabled = 1
    sql = "SELECT chat_id FROM chat_settings WHERE enabled = 1"
    try:
        rows = _execute_query(sql, fetch_all=True)
        if rows:
            chat_ids = [row['chat_id'] for row in rows]
        logger.debug(f"Найдено {len(chat_ids)} активных чатов для обработки.")
    except Exception: # Ловим ошибки _execute_query
        logger.error(f"Не удалось получить список активных чатов.")
    return chat_ids

def get_chat_timezone(chat_id: int) -> str:
    """Получает строку таймзоны для чата из БД."""
    settings = get_chat_settings(chat_id) # Получаем полные настройки
    tz_str = settings.get('timezone', 'UTC') # Значение по умолчанию 'UTC'
    # Дополнительная проверка на валидность (на случай некорректных данных в БД)
    try:
        pytz.timezone(tz_str)
        return tz_str
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"В БД найдена невалидная таймзона '{tz_str}' для чата {chat_id}. Используется UTC по умолчанию.")
        # Можно опционально исправить в БД здесь, но лучше делать при записи
        # update_chat_setting(chat_id, 'timezone', 'UTC')
        return 'UTC' # Возвращаем 'UTC' при ошибке

def get_chat_genre(chat_id: int) -> str:
    """Получает ключ жанра ('default', 'humor', etc.) для чата из БД."""
    settings = get_chat_settings(chat_id) # Получаем полные настройки
    genre = settings.get('story_genre', 'default') # Значение по умолчанию 'default'
    # Дополнительная проверка на валидность
    # TODO: Заменить TEMP_SUPPORTED_GENRES_KEYS на импорт
    TEMP_SUPPORTED_GENRES_KEYS = ['default', 'humor', 'detective', 'fantasy', 'news_report']
    if genre not in TEMP_SUPPORTED_GENRES_KEYS:
        logger.warning(f"В БД найден невалидный жанр '{genre}' для чата {chat_id}. Используется 'default'.")
        # Опционально исправляем в БД
        # update_chat_setting(chat_id, 'story_genre', 'default')
        return 'default'
    return genre

# --- Функции для Отзывов ---
def add_feedback(message_id: int, chat_id: int, user_id: int, rating: int):
    """Сохраняет отзыв пользователя (1 для 👍, -1 для 👎)."""
    # Используем datetime из стандартной библиотеки для получения текущего времени UTC
    timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sql = "INSERT INTO feedback (message_id, chat_id, user_id, rating, timestamp) VALUES (?, ?, ?, ?, ?)"
    params = (message_id, chat_id, user_id, rating, timestamp_utc)
    try:
        _execute_query(sql, params)
        logger.info(f"Отзыв (rating={rating}) от user {user_id} для msg {message_id} в чате {chat_id} сохранен.")
    except Exception: # Ловим ошибки _execute_query
        logger.error(f"Не удалось сохранить отзыв от user {user_id} для msg {message_id}.")


# --- Функция закрытия соединений ---
def close_all_connections():
    """Закрывает соединение с БД для текущего потока (вызывается при остановке)."""
    close_db_connection()