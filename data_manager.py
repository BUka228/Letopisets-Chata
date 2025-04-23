# data_manager.py
import logging
import sqlite3
import threading
import time

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
                else: logger.critical(f"Не удалось подключиться к БД SQLite '{DATA_FILE}': {e}", exc_info=True); raise
            except sqlite3.Error as e: logger.critical(f"Ошибка SQLite при подключении к '{DATA_FILE}': {e}", exc_info=True); raise
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
    """Инициализирует БД: создает таблицы и индексы."""
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

        # --- НОВОЕ: Таблица настроек чатов ---
        _execute_query(f"""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                lang TEXT DEFAULT '{DEFAULT_LANGUAGE}', -- Язык для ответов бота в этом чате
                enabled BOOLEAN DEFAULT 1 -- Включена ли генерация для этого чата
            )
        """)
        logger.info("Таблица 'chat_settings' проверена/создана.")

        # --- НОВОЕ: Таблица для обратной связи ---
        _execute_query("""
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, -- Уникальный ID отзыва
                message_id INTEGER NOT NULL,  -- ID сообщения *с историей*, на которое дан отзыв
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,     -- ID пользователя, оставившего отзыв
                rating INTEGER NOT NULL,      -- Оценка (например, 1 для 👍, -1 для 👎)
                timestamp TEXT NOT NULL     -- Время отзыва (ISO 8601 UTC)
            )
        """)
        _execute_query("CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback (chat_id, message_id)")
        logger.info("Таблица 'feedback' проверена/создана.")

        logger.info(f"База данных '{DATA_FILE}' успешно инициализирована/проверена.")
    except Exception as e:
         logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации БД: {e}", exc_info=True)
         raise

# --- Функции для сообщений (без изменений) ---
def load_data(): _init_db()
def add_message(chat_id: int, message_data: Dict[str, Any]):
    sql = """INSERT OR REPLACE INTO messages (chat_id, message_id, user_id, username, timestamp, message_type, content, file_id, file_unique_id, file_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (chat_id, message_data.get('message_id'), message_data.get('user_id'), message_data.get('username'), message_data.get('timestamp'), message_data.get('type'), message_data.get('content'), message_data.get('file_id'), message_data.get('file_unique_id'), message_data.get('file_name'))
    try: _execute_query(sql, params); logger.debug(f"Сообщение {message_data.get('message_id')} добавлено/заменено для чата {chat_id}.")
    except Exception: logger.error(f"Не удалось добавить сообщение {message_data.get('message_id')} для чата {chat_id}.")
def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    messages = []; sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp ASC"
    try:
        rows = _execute_query(sql, (chat_id,), fetch_all=True)
        if rows: messages = [dict(row) for row in rows]; [m.update({'type': m.pop('message_type')}) for m in messages]
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id}.")
    except Exception: logger.error(f"Не удалось получить сообщения для чата {chat_id}.")
    return messages
def clear_messages_for_chat(chat_id: int):
    sql = "DELETE FROM messages WHERE chat_id = ?"
    try:
        deleted_rows = _execute_query(sql, (chat_id,))
        if deleted_rows > 0: logger.info(f"Удалено {deleted_rows} сообщений для чата {chat_id} из БД.")
    except Exception: logger.error(f"Не удалось удалить сообщения для чата {chat_id}.")
# def get_all_chat_ids() -> List[int]: # Переименуем для ясности
#     chat_ids = []; sql = "SELECT DISTINCT chat_id FROM messages"
#     try:
#         rows = _execute_query(sql, fetch_all=True)
#         if rows: chat_ids = [row['chat_id'] for row in rows]
#         logger.debug(f"Найдено {len(chat_ids)} уникальных chat_id в БД.")
#     except Exception: logger.error(f"Не удалось получить список chat_id из БД.")
#     return chat_ids

# --- НОВЫЕ ФУНКЦИИ для Настроек Чата ---
def get_chat_settings(chat_id: int) -> Dict[str, Any]:
    """Возвращает настройки для чата (язык, статус). Создает запись по умолчанию, если нет."""
    sql_select = "SELECT lang, enabled FROM chat_settings WHERE chat_id = ?"
    sql_insert = f"INSERT OR IGNORE INTO chat_settings (chat_id, lang, enabled) VALUES (?, ?, ?)"
    default_settings = {'lang': DEFAULT_LANGUAGE, 'enabled': True}
    try:
        row = _execute_query(sql_select, (chat_id,), fetch_one=True)
        if row:
            return dict(row)
        else:
            # Если настроек нет, создаем по умолчанию
            logger.info(f"Создание настроек по умолчанию для чата {chat_id}")
            _execute_query(sql_insert, (chat_id, DEFAULT_LANGUAGE, 1))
            return default_settings
    except Exception:
        logger.error(f"Не удалось получить/создать настройки для чата {chat_id}.")
        return default_settings # Возвращаем дефолт при ошибке

def update_chat_setting(chat_id: int, setting_key: str, setting_value: Any):
    """Обновляет конкретную настройку для чата."""
    if setting_key not in ['lang', 'enabled']:
        logger.error(f"Попытка обновить неверный ключ настройки '{setting_key}' для чата {chat_id}")
        return
    # Валидация значения
    if setting_key == 'lang' and setting_value not in SUPPORTED_LANGUAGES:
        logger.error(f"Неподдерживаемый язык '{setting_value}' для чата {chat_id}")
        return
    if setting_key == 'enabled':
        setting_value = 1 if bool(setting_value) else 0 # Приводим к 0 или 1

    sql = f"INSERT INTO chat_settings (chat_id, {setting_key}) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET {setting_key}=excluded.{setting_key}"
    try:
        _execute_query(sql, (chat_id, setting_value))
        logger.info(f"Настройка '{setting_key}' для чата {chat_id} обновлена на '{setting_value}'.")
        # Обновляем кэш языка, если меняли язык
        if setting_key == 'lang':
            from localization import update_chat_lang_cache # Избегаем циклического импорта
            update_chat_lang_cache(chat_id, setting_value)
    except Exception:
         logger.error(f"Не удалось обновить настройку '{setting_key}' для чата {chat_id}.")

def get_chat_language(chat_id: int) -> str:
    """Получает язык чата из БД."""
    settings = get_chat_settings(chat_id)
    return settings.get('lang', DEFAULT_LANGUAGE)

def get_enabled_chats() -> List[int]:
    """Возвращает список ID чатов, для которых включена генерация историй."""
    chat_ids = []
    # Выбираем чаты, где enabled=1 или где настроек еще нет (по умолчанию включено)
    sql = "SELECT chat_id FROM chat_settings WHERE enabled = 1 UNION SELECT DISTINCT m.chat_id FROM messages m LEFT JOIN chat_settings cs ON m.chat_id = cs.chat_id WHERE cs.chat_id IS NULL"
    # Альтернативно: просто выбираем все чаты с сообщениями и фильтруем в jobs.py
    # sql_alt = "SELECT DISTINCT chat_id FROM messages"
    try:
        rows = _execute_query(sql, fetch_all=True)
        if rows: chat_ids = [row['chat_id'] for row in rows]
        logger.debug(f"Найдено {len(chat_ids)} активных чатов для обработки.")
    except Exception:
        logger.error(f"Не удалось получить список активных чатов.")
    return chat_ids

# --- НОВЫЕ ФУНКЦИИ для Обратной Связи ---
def add_feedback(message_id: int, chat_id: int, user_id: int, rating: int):
    """Сохраняет отзыв пользователя."""
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat() # Используем импортированный datetime
    sql = "INSERT INTO feedback (message_id, chat_id, user_id, rating, timestamp) VALUES (?, ?, ?, ?, ?)"
    params = (message_id, chat_id, user_id, rating, timestamp)
    try:
        _execute_query(sql, params)
        logger.info(f"Отзыв (rating={rating}) от user {user_id} для msg {message_id} в чате {chat_id} сохранен.")
    except Exception:
         logger.error(f"Не удалось сохранить отзыв от user {user_id} для msg {message_id}.")

# --- Функция закрытия соединений ---
def close_all_connections(): close_db_connection()