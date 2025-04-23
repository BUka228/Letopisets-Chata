# data_manager.py
import logging
import os
import psycopg2 # Используем psycopg2
from psycopg2 import sql # Для безопасного формирования SQL запросов
from psycopg2.extras import DictCursor # Для получения строк как словарей
import urllib.parse as urlparse # Для парсинга DATABASE_URL
from typing import List, Dict, Any, Optional
import asyncio # Для run_in_executor

# Импортируем DATABASE_URL из конфига
from config import DATABASE_URL

logger = logging.getLogger(__name__)

# --- Парсинг DATABASE_URL ---
_db_params = None
if DATABASE_URL:
    try:
        url = urlparse.urlparse(DATABASE_URL)
        _db_params = {
            'database': url.path[1:],
            'user': url.username,
            'password': url.password,
            'host': url.hostname,
            'port': url.port
        }
        logger.info("Параметры подключения к PostgreSQL успешно извлечены.")
    except Exception as e:
        logger.critical(f"Ошибка парсинга DATABASE_URL: {e}", exc_info=True)
        _db_params = None
else:
    logger.warning("DATABASE_URL не задан.")

# --- Функции для работы с БД (блокирующие) ---
# Эти функции будут вызываться через run_in_executor

def _get_db_connection_sync():
    """Создает и возвращает НОВОЕ блокирующее соединение psycopg2."""
    if not _db_params:
        raise ConnectionError("Параметры подключения к БД не инициализированы.")
    try:
        conn = psycopg2.connect(**_db_params)
        logger.debug("Новое соединение PostgreSQL установлено.")
        return conn
    except psycopg2.Error as e:
        logger.error(f"Ошибка подключения к PostgreSQL: {e}", exc_info=True)
        raise # Передаем исключение дальше

def _init_db_sync():
    """Инициализирует БД: создает таблицы и индексы (блокирующая версия)."""
    conn = None
    try:
        conn = _get_db_connection_sync()
        cursor = conn.cursor()

        # Используем BIGSERIAL для автоинкрементного ID в PostgreSQL
        # Используем TIMESTAMP WITH TIME ZONE для времени
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username TEXT,
                timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                message_type TEXT NOT NULL,
                content TEXT,
                file_id TEXT,
                file_unique_id TEXT,
                file_name TEXT,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        logger.info("Таблица 'messages' проверена/создана в PostgreSQL.")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_id_pg ON messages (chat_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp_pg ON messages (timestamp)")
        logger.info("Индексы для таблицы 'messages' проверены/созданы в PostgreSQL.")

        # Добавьте другие таблицы здесь при необходимости

        conn.commit()
        cursor.close()
        logger.info(f"База данных PostgreSQL успешно инициализирована.")
    except psycopg2.Error as e:
        logger.critical(f"Ошибка при инициализации базы данных PostgreSQL: {e}", exc_info=True)
        if conn:
            conn.rollback() # Откатываем изменения в случае ошибки
        raise
    finally:
        if conn:
            conn.close()
            logger.debug("Соединение PostgreSQL закрыто после инициализации.")

def _add_message_sync(chat_id: int, message_data: Dict[str, Any]):
    """Добавляет или заменяет сообщение в БД (блокирующая версия)."""
    # Используем INSERT ... ON CONFLICT для аналога INSERT OR REPLACE
    sql = """
        INSERT INTO messages (
            chat_id, message_id, user_id, username, timestamp,
            message_type, content, file_id, file_unique_id, file_name
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chat_id, message_id) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            username = EXCLUDED.username,
            timestamp = EXCLUDED.timestamp,
            message_type = EXCLUDED.message_type,
            content = EXCLUDED.content,
            file_id = EXCLUDED.file_id,
            file_unique_id = EXCLUDED.file_unique_id,
            file_name = EXCLUDED.file_name;
    """
    # Используем %s как плейсхолдер для psycopg2
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

    conn = None
    try:
        conn = _get_db_connection_sync()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        cursor.close()
        logger.debug(f"Сообщение (ID: {message_data.get('message_id')}) добавлено/заменено для чата {chat_id} в PostgreSQL.")
    except psycopg2.Error as e:
        logger.error(f"Ошибка PostgreSQL при добавлении сообщения для чата {chat_id}: {e}", exc_info=True)
        if conn: conn.rollback()
        raise # Чтобы внешний код знал об ошибке
    finally:
        if conn: conn.close()

def _get_messages_for_chat_sync(chat_id: int) -> List[Dict[str, Any]]:
    """Получает сообщения для чата из БД (блокирующая версия)."""
    messages = []
    sql = "SELECT * FROM messages WHERE chat_id = %s ORDER BY timestamp ASC"
    conn = None
    try:
        conn = _get_db_connection_sync()
        # Используем DictCursor для получения результата в виде словаря
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute(sql, (chat_id,))
        rows = cursor.fetchall()
        messages = [dict(row) for row in rows] # Преобразуем в обычные словари
        cursor.close()
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id} из PostgreSQL.")
    except psycopg2.Error as e:
        logger.error(f"Ошибка PostgreSQL при получении сообщений для чата {chat_id}: {e}", exc_info=True)
        # Не бросаем исключение, возвращаем пустой список в случае ошибки чтения
    finally:
        if conn: conn.close()
    return messages

def _clear_messages_for_chat_sync(chat_id: int):
    """Удаляет сообщения для чата из БД (блокирующая версия)."""
    sql = "DELETE FROM messages WHERE chat_id = %s"
    deleted_rows = 0
    conn = None
    try:
        conn = _get_db_connection_sync()
        cursor = conn.cursor()
        cursor.execute(sql, (chat_id,))
        deleted_rows = cursor.rowcount
        conn.commit()
        cursor.close()
        if deleted_rows > 0:
            logger.info(f"Удалено {deleted_rows} сообщений для чата {chat_id} из PostgreSQL.")
        else:
            logger.debug(f"Нет сообщений для удаления для чата {chat_id} в PostgreSQL.")
    except psycopg2.Error as e:
        logger.error(f"Ошибка PostgreSQL при удалении сообщений для чата {chat_id}: {e}", exc_info=True)
        if conn: conn.rollback()
        raise
    finally:
        if conn: conn.close()

def _get_all_chat_ids_sync() -> List[int]:
    """Получает список уникальных chat_id из БД (блокирующая версия)."""
    chat_ids = []
    sql = "SELECT DISTINCT chat_id FROM messages"
    conn = None
    try:
        conn = _get_db_connection_sync()
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        chat_ids = [row[0] for row in rows] # Получаем первый элемент из кортежа
        cursor.close()
        logger.debug(f"Найдено {len(chat_ids)} уникальных chat_id в PostgreSQL.")
    except psycopg2.Error as e:
        logger.error(f"Ошибка PostgreSQL при получении списка chat_id: {e}", exc_info=True)
    finally:
        if conn: conn.close()
    return chat_ids


# --- Асинхронные обертки для вызова блокирующих функций ---

async def run_db_operation(func, *args, **kwargs):
    """Запускает блокирующую функцию БД в отдельном потоке."""
    loop = asyncio.get_running_loop()
    # None в run_in_executor использует ThreadPoolExecutor по умолчанию
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

async def load_data():
    """Асинхронная функция инициализации БД."""
    logger.info("Инициализация хранилища данных (PostgreSQL)...")
    try:
        await run_db_operation(_init_db_sync)
    except Exception as e:
         logger.critical(f"Критическая ошибка при асинхронной инициализации PostgreSQL: {e}", exc_info=True)
         raise # Прерываем запуск бота

async def add_message(chat_id: int, message_data: Dict[str, Any]):
    """Асинхронно добавляет сообщение в БД."""
    if not isinstance(message_data, dict):
         logger.warning(f"Попытка добавить некорректные данные сообщения для чата {chat_id}")
         return
    try:
        await run_db_operation(_add_message_sync, chat_id, message_data)
    except Exception as e:
        # Логируем ошибку, но не прерываем работу бота из-за одной ошибки записи
        logger.error(f"Не удалось асинхронно добавить сообщение для чата {chat_id}: {e}", exc_info=True)

async def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    """Асинхронно получает сообщения для чата."""
    try:
        return await run_db_operation(_get_messages_for_chat_sync, chat_id)
    except Exception as e:
        logger.error(f"Не удалось асинхронно получить сообщения для чата {chat_id}: {e}", exc_info=True)
        return [] # Возвращаем пустой список в случае ошибки

async def clear_messages_for_chat(chat_id: int):
    """Асинхронно удаляет сообщения для чата."""
    try:
        await run_db_operation(_clear_messages_for_chat_sync, chat_id)
    except Exception as e:
        logger.error(f"Не удалось асинхронно очистить сообщения для чата {chat_id}: {e}", exc_info=True)

async def get_all_chat_ids() -> List[int]:
    """Асинхронно получает список всех chat_id."""
    try:
        return await run_db_operation(_get_all_chat_ids_sync)
    except Exception as e:
        logger.error(f"Не удалось асинхронно получить все chat_id: {e}", exc_info=True)
        return []

# Функция close_all_connections больше не нужна, т.к. соединения
# открываются и закрываются для каждой операции в _sync функциях.