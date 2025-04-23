# data_manager.py
import logging
import sqlite3
import threading # Для thread-local storage соединений (оптимизация)
from typing import List, Dict, Any, Optional

from config import DATA_FILE # Импортируем путь к файлу БД из конфига

logger = logging.getLogger(__name__)

# Используем thread-local storage для хранения соединения с БД в каждом потоке/задаче asyncio
# Это немного эффективнее, чем открывать/закрывать соединение на каждую операцию
local_storage = threading.local()

def _get_db_connection() -> sqlite3.Connection:
    """
    Получает соединение с БД для текущего потока/задачи.
    Создает новое соединение, если его еще нет.
    Включает WAL mode для лучшей производительности записи.
    """
    if not hasattr(local_storage, 'connection') or local_storage.connection is None:
        logger.debug(f"Создание нового SQLite соединения для потока {threading.current_thread().name}")
        try:
            # connect_timeout - время ожидания снятия блокировки БД (в секундах)
            conn = sqlite3.connect(DATA_FILE, timeout=10, check_same_thread=False)
            # Включаем WAL mode (Write-Ahead Logging)
            # Улучшает параллелизм: чтение не блокирует запись, запись не блокирует чтение.
            conn.execute("PRAGMA journal_mode=WAL;")
            # Возвращаем строки как объекты, доступные по имени колонки
            conn.row_factory = sqlite3.Row
            local_storage.connection = conn
        except sqlite3.Error as e:
            logger.critical(f"Не удалось подключиться к базе данных SQLite '{DATA_FILE}': {e}", exc_info=True)
            raise # Передаем исключение дальше, т.к. без БД работать нельзя
    return local_storage.connection

def close_db_connection():
    """Закрывает соединение с БД для текущего потока/задачи, если оно было открыто."""
    if hasattr(local_storage, 'connection') and local_storage.connection is not None:
        logger.debug(f"Закрытие SQLite соединения для потока {threading.current_thread().name}")
        local_storage.connection.close()
        local_storage.connection = None

def _init_db():
    """Инициализирует базу данных: создает таблицы и индексы, если их нет."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()

        # Создаем таблицу для сообщений
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                timestamp TEXT NOT NULL, -- ISO 8601 format UTC
                message_type TEXT NOT NULL,
                content TEXT,
                file_id TEXT,
                file_unique_id TEXT,
                file_name TEXT,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        logger.info("Таблица 'messages' проверена/создана.")

        # Создаем индекс для быстрого поиска по chat_id (если его еще нет)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id)")
        # Можно добавить индекс по времени, если будут запросы с фильтрацией по времени
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp)")
        logger.info("Индексы для таблицы 'messages' проверены/созданы.")

        # Здесь можно добавить создание других таблиц, например, для настроек чатов
        # cursor.execute("""
        #    CREATE TABLE IF NOT EXISTS chat_settings (
        #        chat_id INTEGER PRIMARY KEY,
        #        story_enabled BOOLEAN DEFAULT 1,
        #        custom_time TEXT -- Можно хранить HH:MM
        #    )
        # """)
        # logger.info("Таблица 'chat_settings' проверена/создана.")

        conn.commit()
        logger.info(f"База данных '{DATA_FILE}' успешно инициализирована.")
    except sqlite3.Error as e:
        logger.critical(f"Ошибка при инициализации базы данных SQLite: {e}", exc_info=True)
        # Закрываем соединение в случае ошибки инициализации
        close_db_connection()
        raise

def load_data():
    """
    Заменяет старую функцию загрузки. Теперь просто инициализирует БД.
    Вызывается один раз при старте бота.
    """
    logger.info("Инициализация хранилища данных (SQLite)...")
    _init_db()

def add_message(chat_id: int, message_data: Dict[str, Any]):
    """Добавляет или заменяет сообщение в базе данных."""
    if not isinstance(message_data, dict):
        logger.warning(f"Попытка добавить некорректные данные сообщения для чата {chat_id}")
        return

    sql = """
        INSERT OR REPLACE INTO messages (
            chat_id, message_id, user_id, username, timestamp,
            message_type, content, file_id, file_unique_id, file_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        chat_id,
        message_data.get('message_id'),
        message_data.get('user_id'),
        message_data.get('username'),
        message_data.get('timestamp'),
        message_data.get('type'), # Используем ключ 'type' из словаря
        message_data.get('content'),
        message_data.get('file_id'),
        message_data.get('file_unique_id'),
        message_data.get('file_name')
    )

    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        logger.debug(f"Сообщение (ID: {message_data.get('message_id')}) добавлено/заменено для чата {chat_id}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при добавлении сообщения для чата {chat_id}: {e}", exc_info=True)
        # Не закрываем соединение здесь, оно управляется thread-local storage

def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    """Возвращает список словарей сообщений для указанного чата, отсортированных по времени."""
    messages = []
    sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp ASC"
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, (chat_id,))
        rows = cursor.fetchall()
        # Преобразуем sqlite3.Row в словари, переименовывая 'message_type' обратно в 'type'
        for row in rows:
            msg_dict = dict(row)
            msg_dict['type'] = msg_dict.pop('message_type', None) # Переименовываем ключ обратно
            messages.append(msg_dict)
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при получении сообщений для чата {chat_id}: {e}", exc_info=True)
    return messages

def clear_messages_for_chat(chat_id: int):
    """Удаляет все сообщения для указанного чата из базы данных."""
    sql = "DELETE FROM messages WHERE chat_id = ?"
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, (chat_id,))
        deleted_rows = cursor.rowcount # Сколько строк было удалено
        conn.commit()
        if deleted_rows > 0:
            logger.info(f"Удалено {deleted_rows} сообщений для чата {chat_id} из БД.")
        else:
            logger.debug(f"Нет сообщений для удаления для чата {chat_id} в БД.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при удалении сообщений для чата {chat_id}: {e}", exc_info=True)

def get_all_chat_ids() -> List[int]:
    """Возвращает список уникальных ID чатов, для которых есть сообщения в базе."""
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

# Важно: Добавляем функцию для закрытия соединений при остановке бота
# Её нужно будет вызывать из main.py
def close_all_connections():
    """Закрывает соединение с БД, если оно было открыто в текущем потоке."""
    close_db_connection()