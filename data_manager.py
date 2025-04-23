# data_manager.py
import logging
import sqlite3
import threading
import time # Для повторных попыток подключения
from typing import List, Dict, Any, Optional

from config import DATA_FILE # Импортируем путь к файлу БД из конфига

logger = logging.getLogger(__name__)

# Thread-local storage для соединений
local_storage = threading.local()

def _get_db_connection() -> sqlite3.Connection:
    """Получает или создает соединение с БД SQLite для текущего потока."""
    if not hasattr(local_storage, 'connection') or local_storage.connection is None:
        retry_count = 0
        max_retries = 3
        while retry_count < max_retries:
            try:
                logger.debug(f"Попытка {retry_count+1}/{max_retries} создания SQLite соединения для потока {threading.current_thread().name} к файлу '{DATA_FILE}'...")
                # check_same_thread=False нужно для работы с asyncio/многопоточностью
                conn = sqlite3.connect(DATA_FILE, timeout=15, check_same_thread=False) # Увеличим таймаут
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout = 5000;") # Ждать 5 сек при блокировке
                conn.row_factory = sqlite3.Row
                local_storage.connection = conn
                logger.debug(f"SQLite соединение успешно создано для потока {threading.current_thread().name}")
                return conn
            except sqlite3.OperationalError as e:
                # 'database is locked' - частая ошибка при WAL mode и высокой нагрузке
                if "database is locked" in str(e) and retry_count < max_retries - 1:
                    retry_count += 1
                    wait_time = (retry_count ** 2) * 0.1 # Экспоненциальная задержка
                    logger.warning(f"База данных '{DATA_FILE}' заблокирована, повторная попытка через {wait_time:.2f} сек...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.critical(f"Не удалось подключиться к базе данных SQLite '{DATA_FILE}' после {max_retries} попыток: {e}", exc_info=True)
                    raise
            except sqlite3.Error as e:
                logger.critical(f"Непредвиденная ошибка SQLite при подключении к '{DATA_FILE}': {e}", exc_info=True)
                raise
    return local_storage.connection

def close_db_connection():
    """Закрывает соединение с БД для текущего потока/задачи."""
    if hasattr(local_storage, 'connection') and local_storage.connection is not None:
        logger.debug(f"Закрытие SQLite соединения для потока {threading.current_thread().name}")
        try:
            local_storage.connection.close()
        except sqlite3.Error as e:
             logger.error(f"Ошибка при закрытии SQLite соединения: {e}")
        finally:
            local_storage.connection = None

def _execute_query(sql: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False) -> Any:
    """Вспомогательная функция для выполнения SQL запросов с обработкой ошибок."""
    conn = None # Инициализируем conn
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if fetch_one:
            return cursor.fetchone()
        if fetch_all:
            return cursor.fetchall()
        conn.commit()
        return cursor.rowcount # Для INSERT/UPDATE/DELETE возвращаем кол-во измененных строк
    except sqlite3.Error as e:
        logger.error(f"Ошибка SQLite при выполнении запроса: {sql} с параметрами {params} - {e}", exc_info=True)
        # Важно: Не закрываем соединение здесь, оно управляется централизованно
        raise # Передаем исключение дальше, чтобы вызывающая функция знала об ошибке
    # finally: # Не закрываем соединение здесь

def _init_db():
    """Инициализирует базу данных: создает таблицы и индексы."""
    try:
        logger.info(f"Проверка/создание таблицы 'messages' в '{DATA_FILE}'...")
        _execute_query("""
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
        logger.info("Таблица 'messages' готова.")

        logger.info("Проверка/создание индексов для 'messages'...")
        _execute_query("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id)")
        _execute_query("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp)")
        logger.info("Индексы для 'messages' готовы.")

        # Можно добавить другие таблицы здесь
        # logger.info("Проверка/создание таблицы 'chat_settings'...")
        # _execute_query("""
        #    CREATE TABLE IF NOT EXISTS chat_settings ( ... )
        # """)
        # logger.info("Таблица 'chat_settings' готова.")

        logger.info(f"База данных '{DATA_FILE}' успешно инициализирована/проверена.")
    except Exception as e: # Ловим и другие возможные ошибки _execute_query
         logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации базы данных SQLite: {e}", exc_info=True)
         raise # Прерываем запуск, т.к. без БД нельзя

def load_data():
    """Инициализирует БД при старте."""
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
        message_data.get('type'),
        message_data.get('content'),
        message_data.get('file_id'),
        message_data.get('file_unique_id'),
        message_data.get('file_name')
    )
    try:
        _execute_query(sql, params)
        logger.debug(f"Сообщение (ID: {message_data.get('message_id')}) добавлено/заменено для чата {chat_id}.")
    except Exception as e:
        # Ошибка уже залогирована в _execute_query
        logger.error(f"Не удалось добавить сообщение {message_data.get('message_id')} для чата {chat_id}.")

def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    """Возвращает список словарей сообщений для чата, отсортированных по времени."""
    messages = []
    sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp ASC"
    try:
        rows = _execute_query(sql, (chat_id,), fetch_all=True)
        if rows:
            # Преобразуем sqlite3.Row в обычные словари
            messages = [dict(row) for row in rows]
            # Переименовываем ключ 'message_type' обратно в 'type' для совместимости
            for msg in messages:
                msg['type'] = msg.pop('message_type', None)
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id} из БД.")
    except Exception as e:
        logger.error(f"Не удалось получить сообщения для чата {chat_id}.")
    return messages

def clear_messages_for_chat(chat_id: int):
    """Удаляет все сообщения для указанного чата из базы данных."""
    sql = "DELETE FROM messages WHERE chat_id = ?"
    try:
        deleted_rows = _execute_query(sql, (chat_id,))
        if deleted_rows > 0:
            logger.info(f"Удалено {deleted_rows} сообщений для чата {chat_id} из БД.")
        else:
            logger.debug(f"Нет сообщений для удаления для чата {chat_id} в БД.")
    except Exception as e:
        logger.error(f"Не удалось удалить сообщения для чата {chat_id}.")

def get_all_chat_ids() -> List[int]:
    """Возвращает список уникальных ID чатов из базы."""
    chat_ids = []
    sql = "SELECT DISTINCT chat_id FROM messages"
    try:
        rows = _execute_query(sql, fetch_all=True)
        if rows:
            chat_ids = [row['chat_id'] for row in rows]
        logger.debug(f"Найдено {len(chat_ids)} уникальных chat_id в БД.")
    except Exception as e:
         logger.error(f"Не удалось получить список chat_id из БД.")
    return chat_ids

def close_all_connections():
    """Закрывает соединение с БД, если оно было открыто в текущем потоке."""
    # Эта функция вызывается при остановке, поэтому просто закрываем для текущего основного потока
    close_db_connection()