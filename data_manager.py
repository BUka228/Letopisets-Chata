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

local_storage = threading.local()

def _get_db_connection() -> sqlite3.Connection:
    """Получает или создает соединение с БД SQLite для текущего потока."""
    if not hasattr(local_storage, 'connection') or local_storage.connection is None:
        retry_count = 0; max_retries = 3
        while retry_count < max_retries:
            try:
                logger.debug(f"Создание/получение SQLite соединения для потока {threading.current_thread().name} к '{DATA_FILE}'...")
                conn = sqlite3.connect(DATA_FILE, timeout=15, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout = 5000;")
                conn.execute("PRAGMA foreign_keys = ON;") # Включаем поддержку внешних ключей
                conn.row_factory = sqlite3.Row
                local_storage.connection = conn
                logger.debug("SQLite соединение успешно получено/создано.")
                return conn
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and retry_count < max_retries - 1:
                    retry_count += 1; wait_time = (retry_count ** 2) * 0.1
                    logger.warning(f"БД '{DATA_FILE}' заблокирована, повтор через {wait_time:.2f} сек...")
                    time.sleep(wait_time); continue
                else: logger.critical(f"Не удалось подключиться к БД SQLite '{DATA_FILE}' после {max_retries} попыток: {e}", exc_info=True); raise
            except sqlite3.Error as e: logger.critical(f"Ошибка SQLite при подключении к '{DATA_FILE}': {e}", exc_info=True); raise
    # Проверяем живо ли соединение перед возвратом (на случай если поток долго жил)
    try:
        # Простое быстрое чтение
        local_storage.connection.execute("PRAGMA schema_version;")
    except (sqlite3.ProgrammingError, sqlite3.OperationalError):
        logger.warning("Обнаружено закрытое соединение SQLite, пересоздаем...")
        close_db_connection() # Закрываем некорректное
        return _get_db_connection() # Рекурсивно вызываем для создания нового
    return local_storage.connection


def close_db_connection():
    """Закрывает соединение с БД для текущего потока."""
    if hasattr(local_storage, 'connection') and local_storage.connection is not None:
        logger.debug(f"Закрытие SQLite соединения для потока {threading.current_thread().name}")
        try: local_storage.connection.close()
        except sqlite3.Error as e: logger.error(f"Ошибка при закрытии SQLite соединения: {e}")
        finally: local_storage.connection = None

def _execute_query(sql: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False) -> Any:
    """Вспомогательная функция для выполнения SQL запросов."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if fetch_one: return cursor.fetchone()
        if fetch_all: return cursor.fetchall()
        conn.commit()
        return cursor.rowcount
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite: {sql} | {params} | {e}", exc_info=True)
        raise # Передаем исключение для обработки выше

def _init_db():
    """Инициализирует БД: создает/обновляет таблицы и индексы."""
    try:
        logger.info("Инициализация/проверка структуры БД...")
        # Таблица сообщений
        _execute_query("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                username TEXT, timestamp TEXT NOT NULL, message_type TEXT NOT NULL,
                content TEXT, file_id TEXT, file_unique_id TEXT, file_name TEXT,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        _execute_query("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id)")
        _execute_query("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp)")
        logger.info("Таблица 'messages' проверена/создана.")

        # Настройки чатов
        _execute_query(f"""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                lang TEXT DEFAULT '{DEFAULT_LANGUAGE}',
                enabled BOOLEAN DEFAULT 1,
                custom_schedule_time TEXT DEFAULT NULL -- Время HH:MM UTC или NULL
            )
        """)
        # Обновление таблицы, если поле еще не существует
        try:
            _execute_query("ALTER TABLE chat_settings ADD COLUMN custom_schedule_time TEXT DEFAULT NULL")
            logger.info("Добавлено поле 'custom_schedule_time' в таблицу 'chat_settings'.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e): pass
            else: raise
        logger.info("Таблица 'chat_settings' проверена/обновлена.")

        # Отзывы
        _execute_query("""
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rating INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        _execute_query("CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback (chat_id, message_id)")
        logger.info("Таблица 'feedback' проверена/создана.")

        logger.info(f"База данных '{DATA_FILE}' успешно инициализирована/проверена.")
    except Exception as e:
         logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации БД: {e}", exc_info=True)
         raise

# --- Функции для сообщений ---
def load_data(): _init_db()

def add_message(chat_id: int, message_data: Dict[str, Any]):
    if not isinstance(message_data, dict): logger.warning(f"Попытка добавить некорректные данные для чата {chat_id}"); return
    sql = """INSERT OR REPLACE INTO messages (chat_id, message_id, user_id, username, timestamp, message_type, content, file_id, file_unique_id, file_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (chat_id, message_data.get('message_id'), message_data.get('user_id'), message_data.get('username'), message_data.get('timestamp'), message_data.get('type'), message_data.get('content'), message_data.get('file_id'), message_data.get('file_unique_id'), message_data.get('file_name'))
    try: _execute_query(sql, params); logger.debug(f"Сообщение {message_data.get('message_id')} добавлено/заменено для чата {chat_id}.")
    except Exception: logger.exception(f"Не удалось добавить сообщение {message_data.get('message_id')} для чата {chat_id}.") # Используем exception для полного стека

def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    messages = []; sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp ASC"
    try:
        rows = _execute_query(sql, (chat_id,), fetch_all=True)
        if rows:
             messages = [dict(row) for row in rows]
             # Переименовываем ключ 'message_type' обратно в 'type'
             for msg in messages: msg['type'] = msg.pop('message_type', None)
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id}.")
    except Exception: logger.exception(f"Не удалось получить сообщения для чата {chat_id}.")
    return messages

def clear_messages_for_chat(chat_id: int):
    sql = "DELETE FROM messages WHERE chat_id = ?"
    try:
        deleted_rows = _execute_query(sql, (chat_id,))
        if deleted_rows > 0: logger.info(f"Удалено {deleted_rows} сообщений для чата {chat_id} из БД.")
    except Exception: logger.exception(f"Не удалось удалить сообщения для чата {chat_id}.")

# --- Функции для Настроек Чата ---
def get_chat_settings(chat_id: int) -> Dict[str, Any]:
    """Возвращает настройки для чата, включая timezone."""
    # --- ИЗМЕНЕНО: Выбираем timezone ---
    sql_select = "SELECT lang, enabled, custom_schedule_time, timezone FROM chat_settings WHERE chat_id = ?"
    sql_insert = f"INSERT OR IGNORE INTO chat_settings (chat_id, lang, enabled, custom_schedule_time, timezone) VALUES (?, ?, ?, ?, ?)"
    default_settings = {'lang': DEFAULT_LANGUAGE, 'enabled': True, 'custom_schedule_time': None, 'timezone': 'UTC'}
    try:
        row = _execute_query(sql_select, (chat_id,), fetch_one=True)
        if row: return dict(row)
        else:
            logger.info(f"Создание настроек по умолчанию для чата {chat_id}")
            # --- ИЗМЕНЕНО: Добавляем 'UTC' по умолчанию ---
            _execute_query(sql_insert, (chat_id, DEFAULT_LANGUAGE, 1, None, 'UTC'))
            return default_settings
    except Exception: logger.exception(f"Не удалось получить/создать настройки для чата {chat_id}."); return default_settings

def update_chat_setting(chat_id: int, setting_key: str, setting_value: Optional[str | bool | int]) -> bool:
    """Обновляет настройку, добавляет валидацию timezone."""
    # --- ИЗМЕНЕНО: Добавляем 'timezone' ---
    allowed_keys = ['lang', 'enabled', 'custom_schedule_time', 'timezone']
    if setting_key not in allowed_keys: logger.error(f"Неверный ключ настройки '{setting_key}' чат={chat_id}"); return False

    value_to_save: Optional[str | int] = None
    if setting_key == 'lang':
        # ... (валидация языка) ...
        if not isinstance(setting_value, str) or setting_value not in SUPPORTED_LANGUAGES: logger.error(f"Неподдерживаемый язык '{setting_value}' чат={chat_id}"); return False
        value_to_save = setting_value
    elif setting_key == 'enabled': value_to_save = 1 if bool(setting_value) else 0
    elif setting_key == 'custom_schedule_time':
        # ... (валидация времени) ...
        if setting_value is None: value_to_save = None
        elif isinstance(setting_value, str) and re.fullmatch(r"^(?:[01]\d|2[0-3]):[0-5]\d$", setting_value): value_to_save = setting_value
        else: logger.error(f"Неверный формат времени '{setting_value}' чат={chat_id}."); return False
    # --- НОВОЕ: Валидация таймзоны ---
    elif setting_key == 'timezone':
        if not isinstance(setting_value, str): logger.error(f"Неверный тип для timezone '{setting_value}' чат={chat_id}"); return False
        try:
            # Проверяем, что pytz знает такую таймзону
            pytz.timezone(setting_value)
            value_to_save = setting_value
        except pytz.exceptions.UnknownTimeZoneError:
            logger.error(f"Неизвестная таймзона '{setting_value}' для чата {chat_id}")
            return False
    # --------------------------------
    else: logger.error("Непредвиденный ключ настройки."); return False

    sql = f"INSERT INTO chat_settings (chat_id, {setting_key}) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET {setting_key}=excluded.{setting_key}"
    try:
        _execute_query(sql, (chat_id, value_to_save))
        logger.info(f"Настройка '{setting_key}' чат={chat_id} обновлена на '{value_to_save}'.")
        if setting_key == 'lang' and isinstance(value_to_save, str):
            try: from localization import update_chat_lang_cache; update_chat_lang_cache(chat_id, value_to_save)
            except ImportError: logger.error("Failed to import localization for cache update.")
        return True
    except Exception: logger.exception(f"Не удалось обновить '{setting_key}' чат={chat_id}."); return False

def get_chat_language(chat_id: int) -> str:
    """Получает язык чата из БД или кэша."""
    # Кэширование теперь внутри localization.py
    try:
         from localization import get_chat_lang as get_cached_lang
         return get_cached_lang(chat_id) # Используем кэшированную версию
    except ImportError: # На случай проблем с импортом
         logger.error("Не удалось импортировать get_chat_lang из localization. Получение напрямую из БД.")
         settings = get_chat_settings(chat_id)
         return settings.get('lang', DEFAULT_LANGUAGE)


def get_enabled_chats() -> List[int]:
    """Возвращает ID чатов, где enabled=1."""
    chat_ids = []; sql = "SELECT chat_id FROM chat_settings WHERE enabled = 1"
    try:
        rows = _execute_query(sql, fetch_all=True)
        if rows: chat_ids = [row['chat_id'] for row in rows]
        logger.debug(f"Найдено {len(chat_ids)} активных чатов для обработки.")
    except Exception: logger.exception(f"Не удалось получить список активных чатов.")
    return chat_ids

def get_chat_timezone(chat_id: int) -> str:
    """Получает строку таймзоны для чата из БД."""
    settings = get_chat_settings(chat_id)
    tz_str = settings.get('timezone', 'UTC')
    # Доп. проверка на валидность (на случай некорректных данных в БД)
    try:
        pytz.timezone(tz_str)
        return tz_str
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"В БД найдена невалидная таймзона '{tz_str}' для чата {chat_id}. Используется UTC.")
        # Можно опционально исправить в БД
        # update_chat_setting(chat_id, 'timezone', 'UTC')
        return 'UTC'

# --- Функции для Отзывов ---
def add_feedback(message_id: int, chat_id: int, user_id: int, rating: int):
    """Сохраняет отзыв пользователя."""
    # Используем datetime из стандартной библиотеки
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sql = "INSERT INTO feedback (message_id, chat_id, user_id, rating, timestamp) VALUES (?, ?, ?, ?, ?)"
    params = (message_id, chat_id, user_id, rating, timestamp)
    try: _execute_query(sql, params); logger.info(f"Отзыв (rating={rating}) от user {user_id} для msg {message_id} в чате {chat_id} сохранен.")
    except Exception: logger.exception(f"Не удалось сохранить отзыв от user {user_id} для msg {message_id}.")

# --- Функция закрытия соединений ---
def close_all_connections(): close_db_connection()