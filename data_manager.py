# data_manager.py
import logging
import sqlite3
import threading
import os # <--- Добавлен импорт os
from typing import List, Dict, Any, Optional

from config import DATA_FILE, DATA_DIR # <-- Импортируем и DATA_DIR

logger = logging.getLogger(__name__)
local_storage = threading.local()

def _get_db_connection() -> sqlite3.Connection:
    if not hasattr(local_storage, 'connection') or local_storage.connection is None:
        logger.debug(f"Создание нового SQLite соединения для потока {threading.current_thread().name}")
        try:
            # --- ИЗМЕНЕНИЕ: Убедимся, что директория существует ПЕРЕД connect ---
            # Это может помочь, если диск монтируется с задержкой
            if not os.path.exists(DATA_DIR):
                 logger.warning(f"Директория '{DATA_DIR}' не найдена перед подключением к БД. Попытка создать...")
                 os.makedirs(DATA_DIR, exist_ok=True)
                 logger.info(f"Директория '{DATA_DIR}' проверена/создана для БД.")
            # -----------------------------------------------------------------

            conn = sqlite3.connect(DATA_FILE, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.row_factory = sqlite3.Row
            local_storage.connection = conn
            logger.debug(f"Успешно подключено к SQLite БД: {DATA_FILE}")
        except sqlite3.Error as e:
            logger.critical(f"Не удалось подключиться к базе данных SQLite '{DATA_FILE}': {e}", exc_info=True)
            raise
        except OSError as e: # Ловим ошибки создания директории
             logger.critical(f"Ошибка файловой системы при подготовке к подключению к БД '{DATA_FILE}': {e}", exc_info=True)
             raise
    return local_storage.connection

# ... остальная часть data_manager.py без изменений ...

def close_db_connection():
    """Закрывает соединение с БД для текущего потока/задачи, если оно было открыто."""
    if hasattr(local_storage, 'connection') and local_storage.connection is not None:
        logger.debug(f"Закрытие SQLite соединения для потока {threading.current_thread().name}")
        local_storage.connection.close()
        local_storage.connection = None

def _init_db():
    """Инициализирует базу данных: создает таблицы и индексы, если их нет."""
    try:
        # Получаем соединение (которое теперь должно быть успешным, т.к. директория проверена)
        conn = _get_db_connection()
        cursor = conn.cursor()
        # Создаем таблицу для сообщений
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                timestamp TEXT NOT NULL,
                message_type TEXT NOT NULL,
                content TEXT,
                file_id TEXT,
                file_unique_id TEXT,
                file_name TEXT,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        logger.info("Таблица 'messages' проверена/создана.")
        # Создаем индексы
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp)")
        logger.info("Индексы для таблицы 'messages' проверены/созданы.")
        conn.commit()
        logger.info(f"База данных '{DATA_FILE}' успешно инициализирована.")
    except sqlite3.Error as e:
        logger.critical(f"Ошибка при инициализации базы данных SQLite: {e}", exc_info=True)
        close_db_connection()
        raise
    except Exception as e: # Ловим и другие возможные ошибки на этапе инициализации
        logger.critical(f"Неожиданная ошибка при инициализации БД: {e}", exc_info=True)
        close_db_connection()
        raise

def load_data():
    """Инициализирует БД."""
    logger.info("Инициализация хранилища данных (SQLite)...")
    _init_db()

def add_message(chat_id: int, message_data: Dict[str, Any]):
    """Добавляет или заменяет сообщение в базе данных."""
    if not isinstance(message_data, dict): return
    sql = """
        INSERT OR REPLACE INTO messages (
            chat_id, message_id, user_id, username, timestamp,
            message_type, content, file_id, file_unique_id, file_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        chat_id, message_data.get('message_id'), message_data.get('user_id'),
        message_data.get('username'), message_data.get('timestamp'), message_data.get('type'),
        message_data.get('content'), message_data.get('file_id'),
        message_data.get('file_unique_id'), message_data.get('file_name')
    )
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        logger.debug(f"Сообщение (ID: {message_data.get('message_id')}) доб./зам. для чата {chat_id}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при доб. сообщения (чат {chat_id}): {e}", exc_info=True)

def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    """Возвращает список словарей сообщений для указанного чата."""
    messages = []
    sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp ASC"
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, (chat_id,))
        rows = cursor.fetchall()
        for row in rows:
            msg_dict = dict(row)
            msg_dict['type'] = msg_dict.pop('message_type', None)
            messages.append(msg_dict)
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при получении сообщений (чат {chat_id}): {e}", exc_info=True)
    return messages

def clear_messages_for_chat(chat_id: int):
    """Удаляет все сообщения для указанного чата из базы данных."""
    sql = "DELETE FROM messages WHERE chat_id = ?"
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, (chat_id,))
        deleted_rows = cursor.rowcount
        conn.commit()
        if deleted_rows > 0:
            logger.info(f"Удалено {deleted_rows} сообщений для чата {chat_id} из БД.")
        else:
            logger.debug(f"Нет сообщений для удаления (чат {chat_id}) в БД.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при удалении сообщений (чат {chat_id}): {e}", exc_info=True)

def get_all_chat_ids() -> List[int]:
    """Возвращает список уникальных ID чатов из базы."""
    chat_ids = []
    sql = "SELECT DISTINCT chat_id FROM messages"
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        chat_ids = [row['chat_id'] for row in rows]
        logger.debug(f"Найдено {len(chat_ids)} уникальных chat_id в БД.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при получении списка chat_id: {e}", exc_info=True)
    return chat_ids

def close_all_connections():
    """Закрывает соединение с БД, если оно было открыто в текущем потоке."""
    close_db_connection()