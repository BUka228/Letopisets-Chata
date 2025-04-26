# data_manager.py
import logging
import sqlite3
import threading
import time
import datetime
import pytz
import re
from typing import List, Dict, Any, Optional, Tuple, Union

# Импорты из конфига (как раньше)
from config import (
    DATA_FILE, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, SUPPORTED_GENRES,
    SUPPORTED_PERSONALITIES, DEFAULT_PERSONALITY, SUPPORTED_OUTPUT_FORMATS,
    DEFAULT_OUTPUT_FORMAT, INTERVENTION_ENABLED_DEFAULT,
    INTERVENTION_MIN_COOLDOWN_MIN, INTERVENTION_MAX_COOLDOWN_MIN,
    INTERVENTION_MIN_MIN_MSGS, INTERVENTION_MAX_MIN_MSGS,
    INTERVENTION_MIN_TIMESPAN_MIN, INTERVENTION_MAX_TIMESPAN_MIN,
    INTERVENTION_DEFAULT_COOLDOWN_MIN, INTERVENTION_DEFAULT_MIN_MSGS,
    INTERVENTION_DEFAULT_TIMESPAN_MIN, DEFAULT_RETENTION_DAYS
)

logger = logging.getLogger(__name__)
local_storage = threading.local()

# --- _get_db_connection, close_db_connection, _execute_query, _init_db ---
# (Эти функции остаются без изменений, как в последней предоставленной версии)
# ... (вставить код _get_db_connection, close_db_connection, _execute_query, _init_db) ...
def _get_db_connection() -> sqlite3.Connection:
    """Получает или создает соединение с БД SQLite для текущего потока."""
    connection = getattr(local_storage, 'connection', None)
    if connection:
        try:
            connection.execute("PRAGMA schema_version;")
            return connection
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            logger.warning("Обнаружено закрытое соединение SQLite, пересоздаем...")
            close_db_connection()

    retry_count = 0
    max_retries = 3
    while retry_count < max_retries:
        try:
            logger.debug(f"Создание/получение SQLite соединения для потока {threading.current_thread().name} к '{DATA_FILE}'...")
            conn = sqlite3.connect(DATA_FILE, timeout=15, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.row_factory = sqlite3.Row
            local_storage.connection = conn
            logger.debug("SQLite соединение успешно получено/создано.")
            return conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and retry_count < max_retries - 1:
                retry_count += 1
                wait_time = (retry_count ** 2) * 0.1 + (retry_count * 0.05)
                logger.warning(f"БД '{DATA_FILE}' заблокирована, повтор через {wait_time:.2f} сек... (попытка {retry_count}/{max_retries})")
                time.sleep(wait_time)
                continue
            else: logger.critical(f"Не удалось подключиться к БД '{DATA_FILE}' после {max_retries} попыток: {e}", exc_info=True); raise
        except sqlite3.Error as e: logger.critical(f"Ошибка SQLite при подключении к '{DATA_FILE}': {e}", exc_info=True); raise
    raise sqlite3.Error(f"Не удалось получить соединение к БД после {max_retries} попыток.")

def close_db_connection():
    """Закрывает соединение с БД для текущего потока, если оно существует."""
    connection = getattr(local_storage, 'connection', None)
    if connection:
        logger.debug(f"Закрытие SQLite соединения для потока {threading.current_thread().name}")
        try: connection.close()
        except sqlite3.Error as e: logger.error(f"Ошибка при закрытии SQLite соединения: {e}")
        finally: local_storage.connection = None

def _execute_query(sql: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False) -> Any:
    """Вспомогательная функция для выполнения SQL запросов с обработкой блокировок."""
    try:
        conn = _get_db_connection(); cursor = conn.cursor()
        cursor.execute(sql, params)
        if fetch_one: return cursor.fetchone()
        if fetch_all: return cursor.fetchall()
        conn.commit()
        return cursor.rowcount # Для INSERT, UPDATE, DELETE
    except sqlite3.Error as e:
        logger.error(f"Ошибка выполнения SQLite запроса:\nSQL: {sql}\nParams: {params}\nError: {e}", exc_info=True) # Добавлено больше инфо в лог
        try:
            if hasattr(local_storage, 'connection') and local_storage.connection: local_storage.connection.rollback(); logger.warning("Транзакция SQLite отменена из-за ошибки.")
        except sqlite3.Error as rollback_e: logger.error(f"Ошибка при откате транзакции SQLite: {rollback_e}")
        raise # Передаем исходное исключение

def _init_db():
    """Инициализирует БД: создает/обновляет таблицы и индексы."""
    try:
        logger.info("Инициализация/проверка структуры БД...")
        # Таблица messages
        _execute_query("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                username TEXT, timestamp TEXT NOT NULL, message_type TEXT NOT NULL,
                content TEXT, file_id TEXT, file_unique_id TEXT, file_name TEXT,
                PRIMARY KEY (chat_id, message_id)
            ) WITHOUT ROWID;
        """) # Добавил WITHOUT ROWID для возможной экономии места
        _execute_query("CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages (chat_id, timestamp)") # Объединенный индекс
        _execute_query("DROP INDEX IF EXISTS idx_messages_chat_id") # Удаляем старые отдельные индексы, если были
        _execute_query("DROP INDEX IF EXISTS idx_messages_timestamp")
        logger.info("Таблица 'messages' проверена/создана.")

        # Таблица chat_settings
        default_retention = "NULL" if DEFAULT_RETENTION_DAYS <= 0 else str(DEFAULT_RETENTION_DAYS)
        intervention_default_enabled_db = 1 if INTERVENTION_ENABLED_DEFAULT else 0
        _execute_query(f"""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY, lang TEXT DEFAULT '{DEFAULT_LANGUAGE}',
                enabled BOOLEAN DEFAULT 1, custom_schedule_time TEXT DEFAULT NULL,
                timezone TEXT DEFAULT 'UTC', story_genre TEXT DEFAULT 'default',
                retention_days INTEGER DEFAULT {default_retention}, output_format TEXT DEFAULT '{DEFAULT_OUTPUT_FORMAT}',
                story_personality TEXT DEFAULT '{DEFAULT_PERSONALITY}', allow_interventions BOOLEAN DEFAULT {intervention_default_enabled_db},
                last_intervention_ts INTEGER DEFAULT 0, intervention_cooldown_minutes INTEGER DEFAULT NULL,
                intervention_min_msgs INTEGER DEFAULT NULL, intervention_timespan_minutes INTEGER DEFAULT NULL
            )
        """)
        # Проверка и добавление колонок
        conn = _get_db_connection(); cursor = conn.cursor()
        def column_exists(table, column): cursor.execute(f"PRAGMA table_info({table})"); return column in [r['name'] for r in cursor.fetchall()]
        new_columns = {
            "retention_days": f"INTEGER DEFAULT {default_retention}", "output_format": f"TEXT DEFAULT '{DEFAULT_OUTPUT_FORMAT}'",
            "story_personality": f"TEXT DEFAULT '{DEFAULT_PERSONALITY}'", "allow_interventions": f"BOOLEAN DEFAULT {intervention_default_enabled_db}",
            "last_intervention_ts": "INTEGER DEFAULT 0", "intervention_cooldown_minutes": "INTEGER DEFAULT NULL",
            "intervention_min_msgs": "INTEGER DEFAULT NULL", "intervention_timespan_minutes": "INTEGER DEFAULT NULL",
            # Добавляем старые на всякий случай, если кто-то обновляется с древней версии
            "custom_schedule_time": "TEXT DEFAULT NULL", "timezone": "TEXT DEFAULT 'UTC'", "story_genre": f"TEXT DEFAULT 'default'"
        }
        for col, definition in new_columns.items():
             if not column_exists('chat_settings', col): _execute_query(f"ALTER TABLE chat_settings ADD COLUMN {col} {definition}"); logger.info(f"Added column '{col}' to chat_settings.")
        logger.info("Таблица 'chat_settings' проверена/обновлена.")

        # Таблица feedback
        _execute_query(""" CREATE TABLE IF NOT EXISTS feedback (feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rating INTEGER NOT NULL, timestamp TEXT NOT NULL) """)
        _execute_query("CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback (chat_id, message_id)")
        logger.info("Таблица 'feedback' проверена/создана.")
        logger.info(f"База данных '{DATA_FILE}' успешно инициализирована/проверена.")
    except Exception as e: logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации БД: {e}", exc_info=True); raise

def load_data(): _init_db()

# --- Функции для сообщений ---
# --- add_message (без изменений) ---
def add_message(chat_id: int, message_data: Dict[str, Any]):
    if not isinstance(message_data, dict): logger.warning(f"Bad data type for chat {chat_id}"); return
    req=['message_id','user_id','timestamp','type'];
    if not all(f in message_data for f in req): logger.warning(f"Msg missing req fields chat={chat_id} keys={message_data.keys()}"); return
    sql="INSERT OR REPLACE INTO messages(chat_id,message_id,user_id,username,timestamp,message_type,content,file_id,file_unique_id,file_name)VALUES(?,?,?,?,?,?,?,?,?,?)"
    p=(chat_id, message_data.get('message_id'), message_data.get('user_id'), message_data.get('username'), message_data.get('timestamp'), message_data.get('type'), message_data.get('content'), message_data.get('file_id'), message_data.get('file_unique_id'), message_data.get('file_name'))
    try:_execute_query(sql,p);logger.debug(f"Msg {message_data.get('message_id')} added/replaced chat={chat_id}.")
    except Exception: logger.error(f"Failed add/replace msg {message_data.get('message_id')} chat={chat_id}.")


# --- ИСПРАВЛЕНО: Явный SELECT ---
def get_messages_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    """Возвращает все сообщения для указанного чата."""
    messages = []
    # --- ИСПРАВЛЕНО: Явно указываем нужные колонки ---
    sql = """
        SELECT message_id, user_id, username, timestamp, message_type,
               content, file_id, file_unique_id, file_name
        FROM messages
        WHERE chat_id = ? ORDER BY timestamp ASC
    """
    # -------------------------------------------
    try:
        rows = _execute_query(sql, (chat_id,), fetch_all=True)
        if rows:
            messages = []
            for row in rows:
                msg_dict = dict(row)
                # Переименовываем обратно для консистентности с add_message
                msg_dict['type'] = msg_dict.pop('message_type', None)
                messages.append(msg_dict)
        logger.debug(f"Извлечено {len(messages)} сообщений для чата {chat_id}.")
    except Exception:
        logger.error(f"Не удалось получить сообщения для чата {chat_id}.") # Лог ошибки уже есть в _execute_query
    return messages

# --- ИСПРАВЛЕНО: Явный SELECT ---
def get_messages_for_chat_since(chat_id: int, since_datetime_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Возвращает сообщения из чата, начиная с указанной даты/времени UTC."""
    messages = []
    if since_datetime_utc.tzinfo is None: since_datetime_utc = pytz.utc.localize(since_datetime_utc)
    else: since_datetime_utc = since_datetime_utc.astimezone(pytz.utc)
    since_iso_str = since_datetime_utc.isoformat()
    # --- ИСПРАВЛЕНО: Явно указываем нужные колонки ---
    sql = """
        SELECT message_id, user_id, username, timestamp, message_type,
               content, file_id, file_unique_id, file_name
        FROM messages
        WHERE chat_id = ? AND timestamp >= ? ORDER BY timestamp ASC
    """
    # -------------------------------------------
    try:
        rows = _execute_query(sql, (chat_id, since_iso_str), fetch_all=True)
        if rows:
            messages = []
            for row in rows: msg_dict = dict(row); msg_dict['type'] = msg_dict.pop('message_type', None); messages.append(msg_dict)
        logger.debug(f"Извлечено {len(messages)} сообщений чата {chat_id} с {since_iso_str}.")
    except Exception:
        logger.error(f"Не удалось получить сообщения чата {chat_id} с {since_iso_str}.")
    return messages

# --- ИСПРАВЛЕНО: Явный SELECT ---
def get_messages_for_chat_last_n(chat_id: int, limit: int, only_text: bool = False) -> List[Dict[str, Any]]:
    """Возвращает последние N сообщений, опционально только текст."""
    messages = []
    if limit <= 0: return messages
    # --- ИСПРАВЛЕНО: Явно указываем нужные колонки ---
    select_cols = """
        SELECT message_id, user_id, username, timestamp, message_type,
               content, file_id, file_unique_id, file_name
        FROM messages
    """
    # -------------------------------------------
    where_clause = " WHERE chat_id = ?"
    params = [chat_id]
    if only_text: where_clause += " AND message_type = 'text'"
    where_clause += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    sql = select_cols + where_clause

    try:
        rows = _execute_query(sql, tuple(params), fetch_all=True)
        if rows:
            messages = []
            for row in reversed(rows): msg_dict = dict(row); msg_dict['type'] = msg_dict.pop('message_type', None); messages.append(msg_dict)
        logger.debug(f"Извл {len(messages)} посл {'text ' if only_text else ''}сообщ chat={chat_id} limit={limit}.")
    except Exception:
        logger.error(f"Не уд извл посл {limit} {'text ' if only_text else ''}сообщ chat={chat_id}.")
    return messages

# --- count_messages_since (без изменений) ---
def count_messages_since(chat_id: int, since_datetime_utc: datetime.datetime) -> int:
    """Считает количество сообщений с указанного времени UTC."""
    if since_datetime_utc.tzinfo is None: since_datetime_utc = pytz.utc.localize(since_datetime_utc)
    else: since_datetime_utc = since_datetime_utc.astimezone(pytz.utc)
    since_iso_str = since_datetime_utc.isoformat()
    sql = "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND timestamp >= ?"
    try: row = _execute_query(sql, (chat_id, since_iso_str), fetch_one=True); count = row[0] if row else 0; logger.debug(f"Found {count} msgs chat={chat_id} since {since_iso_str}."); return count
    except Exception: logger.error(f"Failed count msgs chat={chat_id} since {since_iso_str}."); return 0

# --- clear_messages_for_chat (без изменений) ---
def clear_messages_for_chat(chat_id: int):
    sql = "DELETE FROM messages WHERE chat_id = ?"
    try: deleted_rows = _execute_query(sql, (chat_id,)); logger.info(f"Purge ALL: Deleted {deleted_rows or 0} messages chat={chat_id}.")
    except Exception: logger.error(f"Failed purge ALL chat={chat_id}.")

# --- delete_messages_older_than (без изменений) ---
def delete_messages_older_than(chat_id: int, days: int):
    if days <= 0: logger.warning(f"Attempt del msgs with invalid period ({days}) chat={chat_id}"); return
    try: 
        cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days); cutoff_iso = cutoff_dt.isoformat(); sql = "DELETE FROM messages WHERE chat_id = ? AND timestamp < ?"; deleted_rows = _execute_query(sql, (chat_id, cutoff_iso))
        if deleted_rows is not None and deleted_rows > 0: logger.info(f"Auto-Purge: Deleted {deleted_rows} messages older {days}d chat={chat_id}.")
        else: logger.debug(f"Auto-Purge: No old messages ({days}d) found/deleted chat={chat_id}.")
    except Exception: logger.error(f"Failed Auto-Purge ({days}d) chat={chat_id}.")


# --- Функции для Настроек Чата ---
# --- get_chat_settings (без изменений) ---
def get_chat_settings(chat_id: int) -> Dict[str, Any]:
    fields = "lang, enabled, custom_schedule_time, timezone, story_genre, retention_days, output_format, story_personality, allow_interventions, last_intervention_ts, intervention_cooldown_minutes, intervention_min_msgs, intervention_timespan_minutes"
    sql_select = f"SELECT {fields} FROM chat_settings WHERE chat_id = ?"
    # Дефолты для словаря и для INSERT
    default_retention_val = None if DEFAULT_RETENTION_DAYS <= 0 else DEFAULT_RETENTION_DAYS
    default_retention_db = "NULL" if DEFAULT_RETENTION_DAYS <= 0 else str(DEFAULT_RETENTION_DAYS)
    intervention_enabled_db = 1 if INTERVENTION_ENABLED_DEFAULT else 0
    default_settings = {'lang': DEFAULT_LANGUAGE, 'enabled': True, 'custom_schedule_time': None, 'timezone': 'UTC', 'story_genre': 'default', 'retention_days': default_retention_val, 'output_format': DEFAULT_OUTPUT_FORMAT, 'story_personality': DEFAULT_PERSONALITY, 'allow_interventions': INTERVENTION_ENABLED_DEFAULT, 'last_intervention_ts': 0, 'intervention_cooldown_minutes': None, 'intervention_min_msgs': None, 'intervention_timespan_minutes': None }
    sql_insert = f"INSERT OR IGNORE INTO chat_settings({','.join(default_settings.keys())}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    try:
        row = _execute_query(sql_select, (chat_id,), fetch_one=True)
        if row: s = dict(row); s['enabled'] = bool(s.get('enabled',1)); s['allow_interventions'] = bool(s.get('allow_interventions',INTERVENTION_ENABLED_DEFAULT)); return s
        else: # Create defaults
            logger.info(f"Creating default settings for chat={chat_id}")
            params = (chat_id, DEFAULT_LANGUAGE, 1, None, 'UTC', 'default', default_retention_db, DEFAULT_OUTPUT_FORMAT, DEFAULT_PERSONALITY, intervention_enabled_db, 0, None, None, None)
            _execute_query(sql_insert, params)
            return default_settings
    except Exception: logger.exception(f"Failed get/create settings chat={chat_id}. Returning defaults."); return default_settings


# --- update_chat_setting (без изменений) ---
def update_chat_setting(chat_id: int, setting_key: str, setting_value: Optional[Union[str, bool, int]]) -> bool:
    """
    Обновляет одну настройку чата с валидацией и коррекцией для числовых параметров.
    Возвращает True при успехе, False при ошибке.
    """
    allowed_keys = [
        'lang', 'enabled', 'custom_schedule_time', 'timezone', 'story_genre',
        'retention_days', 'output_format', 'story_personality', 'allow_interventions',
        'last_intervention_ts', # Обычно не меняется пользователем, но может через код
        'intervention_cooldown_minutes', 'intervention_min_msgs', 'intervention_timespan_minutes'
    ]
    if setting_key not in allowed_keys:
        logger.error(f"Попытка обновить неверный ключ настройки '{setting_key}' для чата {chat_id}")
        return False

    value_to_save: Optional[Union[str, int]] = None # Значение для сохранения в БД (TEXT или INTEGER)
    was_corrected = False # Флаг, показывающий, было ли значение скорректировано

    # --- Блок Валидации и Коррекции Значений ---
    try:
        if setting_key == 'lang':
            if not isinstance(setting_value, str) or setting_value not in SUPPORTED_LANGUAGES:
                raise ValueError(f"Недопустимый язык: {setting_value}")
            value_to_save = setting_value

        elif setting_key in ['enabled', 'allow_interventions']:
            # Преобразуем любое значение в 1 или 0 для SQLite BOOLEAN
            value_to_save = 1 if bool(setting_value) else 0

        elif setting_key == 'custom_schedule_time':
            if setting_value is None:
                value_to_save = None
            elif isinstance(setting_value, str) and re.fullmatch(r"^(?:[01]\d|2[0-3]):[0-5]\d$", setting_value):
                value_to_save = setting_value
            else:
                raise ValueError(f"Неверный формат времени HH:MM: {setting_value}")

        elif setting_key == 'timezone':
            if not isinstance(setting_value, str):
                raise ValueError(f"Таймзона должна быть строкой: {setting_value}")
            try:
                pytz.timezone(setting_value) # Проверка существования
                value_to_save = setting_value
            except pytz.exceptions.UnknownTimeZoneError:
                raise ValueError(f"Неизвестная таймзона: {setting_value}")

        elif setting_key == 'story_genre':
            if not isinstance(setting_value, str) or setting_value not in SUPPORTED_GENRES:
                raise ValueError(f"Недопустимый жанр: {setting_value}")
            value_to_save = setting_value

        elif setting_key == 'output_format':
             if not isinstance(setting_value, str) or setting_value not in SUPPORTED_OUTPUT_FORMATS:
                 raise ValueError(f"Недопустимый формат вывода: {setting_value}")
             value_to_save = setting_value

        elif setting_key == 'story_personality':
             if not isinstance(setting_value, str) or setting_value not in SUPPORTED_PERSONALITIES:
                 raise ValueError(f"Недопустимая личность: {setting_value}")
             value_to_save = setting_value

        elif setting_key == 'retention_days':
             if setting_value is None: # Разрешаем сброс на NULL
                 value_to_save = None
             elif isinstance(setting_value, int) and setting_value >= 0:
                 value_to_save = None if setting_value == 0 else setting_value # 0 означает вечно (NULL)
             else:
                 raise ValueError("Срок хранения должен быть целым неотрицательным числом (0 = вечно)")

        elif setting_key == 'last_intervention_ts':
             if isinstance(setting_value, int) and setting_value >= 0:
                 value_to_save = setting_value
             else:
                 raise ValueError("Timestamp должен быть целым неотрицательным числом")

        # Настройки Вмешательств с коррекцией в пределах MIN/MAX
        elif setting_key == 'intervention_cooldown_minutes':
            if setting_value is None: value_to_save = None
            elif isinstance(setting_value, int):
                corrected = max(INTERVENTION_MIN_COOLDOWN_MIN, min(setting_value, INTERVENTION_MAX_COOLDOWN_MIN))
                if corrected != setting_value: was_corrected = True
                value_to_save = corrected # Сохраняем скорректированное значение
            else: raise ValueError("Cooldown должен быть целым числом (в минутах) или None")

        elif setting_key == 'intervention_min_msgs':
            if setting_value is None: value_to_save = None
            elif isinstance(setting_value, int):
                corrected = max(INTERVENTION_MIN_MIN_MSGS, min(setting_value, INTERVENTION_MAX_MIN_MSGS))
                if corrected != setting_value: was_corrected = True
                value_to_save = corrected # Сохраняем скорректированное значение
            else: raise ValueError("Мин. сообщений должно быть целым числом или None")

        elif setting_key == 'intervention_timespan_minutes':
            if setting_value is None: value_to_save = None
            elif isinstance(setting_value, int):
                corrected = max(INTERVENTION_MIN_TIMESPAN_MIN, min(setting_value, INTERVENTION_MAX_TIMESPAN_MIN))
                if corrected != setting_value: was_corrected = True
                value_to_save = corrected # Сохраняем скорректированное значение
            else: raise ValueError("Окно активности должно быть целым числом (в минутах) или None")

    except Exception as validation_e:
        logger.error(f"Ошибка валидации: ключ='{setting_key}', значение='{setting_value}', чат={chat_id}: {validation_e}")
        return False # Ошибка валидации - не сохраняем

    # --- Блок Сохранения в БД ---
    # Используем UPSERT (INSERT или UPDATE)
    sql = f"""
        INSERT INTO chat_settings (chat_id, {setting_key}) VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET {setting_key} = excluded.{setting_key}
    """
    params = (chat_id, value_to_save)

    try:
        _execute_query(sql, params)
        log_suffix = " (скорректировано)" if was_corrected else ""
        logger.info(f"Настройка '{setting_key}' для чата={chat_id} обновлена на '{value_to_save}'{log_suffix}.")

        # Обновляем кэш языка, если изменился язык
        if setting_key == 'lang' and isinstance(value_to_save, str):
            try:
                from localization import update_chat_lang_cache
                update_chat_lang_cache(chat_id, value_to_save)
            except ImportError:
                logger.error("Не удалось импортировать localization для обновления кэша языка.")
            except Exception as cache_e:
                logger.error(f"Ошибка при обновлении кэша языка для чата {chat_id}: {cache_e}")

        # Не возвращаем was_corrected напрямую, бот узнает об этом через алерт в UI
        return True # Успех сохранения

    except Exception:
        # Ошибка уже залогирована в _execute_query
        logger.error(f"Не удалось сохранить настройку '{setting_key}'='{setting_value}' для чата={chat_id} в БД.")
        return False # Ошибка сохранения


# --- Функции Получения Настроек (без изменений) ---
def get_chat_language(chat_id: int) -> str: # Без изменений логики
    try: from localization import get_chat_lang as get_cached_lang; return get_cached_lang(chat_id)
    except ImportError: settings = get_chat_settings(chat_id); return settings.get('lang', DEFAULT_LANGUAGE)

def get_enabled_chats() -> List[int]: # Без изменений логики
    chat_ids = []; sql = "SELECT chat_id FROM chat_settings WHERE enabled = 1"; 
    try: rows = _execute_query(sql, fetch_all=True); chat_ids = [row['chat_id'] for row in rows] if rows else []; 
    except Exception: logger.error("Failed get enabled chats."); logger.debug(f"Found {len(chat_ids)} enabled chats."); return chat_ids

def get_chat_timezone(chat_id: int) -> str: # Без изменений логики
    settings = get_chat_settings(chat_id); tz_str = settings.get('timezone', 'UTC'); 
    try: pytz.timezone(tz_str); return tz_str; 
    except pytz.exceptions.UnknownTimeZoneError: return 'UTC'

def get_chat_genre(chat_id: int) -> str: # Без изменений логики
    settings = get_chat_settings(chat_id); genre = settings.get('story_genre', 'default'); return genre if genre in SUPPORTED_GENRES else 'default'

def get_chat_output_format(chat_id: int) -> str: # Без изменений логики
    settings = get_chat_settings(chat_id); fmt = settings.get('output_format', DEFAULT_OUTPUT_FORMAT); return fmt if fmt in SUPPORTED_OUTPUT_FORMATS else DEFAULT_OUTPUT_FORMAT

def get_chat_personality(chat_id: int) -> str: # Без изменений логики
    settings = get_chat_settings(chat_id); pers = settings.get('story_personality', DEFAULT_PERSONALITY); return pers if pers in SUPPORTED_PERSONALITIES else DEFAULT_PERSONALITY

def get_chat_retention_days(chat_id: int) -> Optional[int]: # Без изменений логики
    settings = get_chat_settings(chat_id); days = settings.get('retention_days'); return days if isinstance(days, int) and days > 0 else None

def get_chats_with_retention() -> List[Tuple[int, int]]: # Без изменений логики
    chats = []; sql = "SELECT chat_id, retention_days FROM chat_settings WHERE retention_days IS NOT NULL AND retention_days > 0"; 
    try: 
        rows = _execute_query(sql, fetch_all=True); 
        if rows: chats = [(row['chat_id'], row['retention_days']) for row in rows]; 
    except Exception: logger.error("Failed get chats for purge."); logger.debug(f"Found {len(chats)} chats with retention."); return chats

def get_intervention_settings(chat_id: int) -> Dict[str, Any]: # Без изменений логики
    settings = get_chat_settings(chat_id); return {'allow_interventions': settings.get('allow_interventions', INTERVENTION_ENABLED_DEFAULT), 'last_intervention_ts': settings.get('last_intervention_ts', 0), 'cooldown_minutes': settings.get('intervention_cooldown_minutes') or INTERVENTION_DEFAULT_COOLDOWN_MIN, 'min_msgs': settings.get('intervention_min_msgs') or INTERVENTION_DEFAULT_MIN_MSGS, 'timespan_minutes': settings.get('intervention_timespan_minutes') or INTERVENTION_DEFAULT_TIMESPAN_MIN }

# --- Функция Статистики (без изменений) ---
def get_chat_stats(chat_id: int, since_datetime_utc: datetime.datetime) -> Optional[Dict[str, Any]]: # <-- ИЗМЕНЕНО: Возвращаем Optional[Dict]
    """Собирает статистику чата с указанного времени UTC. Возвращает None при ошибке."""
    stats = {'active_users': 0, 'total_messages': 0, 'photos': 0, 'stickers': 0, 'top_users': []}
    if since_datetime_utc.tzinfo is None: since_datetime_utc = pytz.utc.localize(since_datetime_utc)
    else: since_datetime_utc = since_datetime_utc.astimezone(pytz.utc)
    since_iso_str = since_datetime_utc.isoformat()
    params = (chat_id, since_iso_str)
    logger.debug(f"Собираю статистику для чата {chat_id} с {since_iso_str}") # <-- Лог начала

    try:
        # 1. Общее кол-во сообщений и типы
        sql_counts = "SELECT message_type, COUNT(*) as count FROM messages WHERE chat_id = ? AND timestamp >= ? GROUP BY message_type"
        rows_counts = _execute_query(sql_counts, params, fetch_all=True)
        logger.debug(f"Stats query 1 (counts): Found {len(rows_counts) if rows_counts else '0'} rows.") # <-- Лог 1
        if rows_counts: # Проверяем, что результат не None
            for row in rows_counts:
                stats['total_messages'] += row['count']
                msg_type = row['message_type']
                if msg_type == 'photo': stats['photos'] = row['count']
                elif msg_type == 'sticker': stats['stickers'] = row['count']
                # Добавить другие типы по желанию

        # 2. Топ активных пользователей
        sql_top_users = "SELECT username, COUNT(*) as msg_count FROM messages WHERE chat_id = ? AND timestamp >= ? GROUP BY user_id ORDER BY msg_count DESC LIMIT 3" # Group by user_id for count
        rows_top = _execute_query(sql_top_users, params, fetch_all=True)
        logger.debug(f"Stats query 2 (top users): Found {len(rows_top) if rows_top else '0'} rows.") # <-- Лог 2
        if rows_top: # Проверяем, что результат не None
            stats['top_users'] = [(row['username'], row['msg_count']) for row in rows_top]

        # 3. Количество уникальных активных пользователей
        sql_active_users = "SELECT COUNT(DISTINCT user_id) as unique_users FROM messages WHERE chat_id = ? AND timestamp >= ?"
        active_users_row = _execute_query(sql_active_users, params, fetch_one=True)
        logger.debug(f"Stats query 3 (active users): Found row - {bool(active_users_row)}") # <-- Лог 3
        if active_users_row: # Проверяем, что результат не None
            stats['active_users'] = active_users_row['unique_users']

        logger.debug(f"Статистика для чата {chat_id} собрана: {stats}") # <-- Итоговый лог перед return
        return stats

    except Exception as e:
        logger.exception(f"Ошибка сбора статистики чата {chat_id}")
        # --- ИЗМЕНЕНО: Возвращаем None при любой ошибке внутри try ---
        return None

# --- add_feedback, close_all_connections (без изменений) ---
def add_feedback(message_id: int, chat_id: int, user_id: int, rating: int):
    """Сохраняет отзыв пользователя (1 для 👍, -1 для 👎)."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sql = "INSERT INTO feedback (message_id, chat_id, user_id, rating, timestamp) VALUES (?, ?, ?, ?, ?)"
    params = (message_id, chat_id, user_id, rating, ts)
    try:
        _execute_query(sql, params)
        logger.info(f"FB r={rating} u={user_id} m={message_id} c={chat_id} saved.")
    except Exception:
        # Лог ошибки уже будет в _execute_query, можно добавить специфичное сообщение
        logger.error(f"Failed to save feedback u={user_id} m={message_id}.")

# --- Функция закрытия соединений (ВОССТАНОВЛЕНО ФОРМАТИРОВАНИЕ) ---
def close_all_connections():
    """Закрывает соединение с БД для текущего потока (вызывается при остановке)."""
    close_db_connection()