# localization.py
import logging
from typing import Optional, Dict

# Импортируем настройки языка и список таймзон из config
from config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, COMMON_TIMEZONES

logger = logging.getLogger(__name__)

# Словарь для хранения текстов
LOCALIZED_TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        # --- Общие ---
        "lang_name": "Русский 🇷🇺",
        "private_chat": "личном чате",
        "admin_only": "🔐 Эта функция доступна только администраторам чата.",
        "owner_only": "🔐 Эта команда доступна только владельцу бота.",
        "error_db_generic": "Произошла ошибка базы данных. Попробуйте позже.",
        "error_telegram": "Не удалось отправить сообщение: Ошибка Telegram ({error}).",
        "error_unexpected_send": "Произошла неожиданная ошибка при отправке.",
        "error_admin_check": "Не удалось проверить права администратора.",
        "feedback_thanks": "👍 Спасибо за ваш отзыв!",

        # --- Старт и Помощь ---
        "start_message": "Привет, {user_mention}! Я <b>Летописец</b> 📜\nСобираю события чата <i>'{chat_title}'</i>.\nЕжедневная история ~ в {schedule_time} ({schedule_tz} / ваше время).\nСтатус для этого чата: {status}\n\n<code>/help</code> - помощь\n<code>/story_settings</code> - настройки", # Убрали /set_language, т.к. есть кнопка
        "help_message": """<b>Я бот-летописец с ИИ!</b> 🧐

Анализирую текст и фото за день, создаю уникальную историю с помощью нейросети.

<b>Функции:</b>
- Автосбор сообщений и фото.
- Ежедневная история (~{schedule_time} / ваше время).

<b>Команды:</b>
<code>/start</code> - Приветствие и статус
<code>/help</code> - Эта справка
<code>/generate_now</code> - История за сегодня (тест)
<code>/regenerate_story</code> - Пересоздать историю дня
<code>/story_settings</code> - Настройки историй (Админ)
<code>/set_language</code> - Выбрать язык бота
<code>/status</code> - Статус бота (Владелец)""",

        # --- Генерация Историй ---
        "generating_now": "⏳ Анализирую {msg_count} сообщ.{photo_info} и обращаюсь к ИИ... Минутку...",
        "generating_now_no_messages": "🤷‍♀️ В этом чате пока нет сообщений за сегодня для создания истории.",
        "generation_failed": "😕 Не удалось создать историю.\nПричина: <i>{error}</i>",
        "generation_failed_no_reason": "😕 Не удалось создать историю по неизвестной причине.",
        "story_ready_header": "✨ <b>История дня (по запросу)</b>{photo_info}:\n", # Заголовок для /generate_now
        "story_too_long": "История готова!{photo_info} Она получилась довольно длинной, отправляю по частям:",
        "story_sent": "История по запросу (generate_now) успешно отправлена/отредактирована.",
        "regenerate_no_data": "🤷‍♀️ Нечего перегенерировать, сообщения за сегодня уже обработаны или их не было.",
        "regenerating": "⏳ Пытаюсь пересоздать историю за сегодня...",
        "daily_story_header": "📅 <b>История за {date_str} в чате {chat_title}</b> ✨\n{photo_info}\n" + "-"*20 + "\n\n", # Заголовок ежедневной истории
        "daily_story_header_long": "📅 <b>История за {date_str} в чате {chat_title}</b> ✨\n{photo_info}\n<i>(История длинная, разбита на части)</i>\n" + "-"*20 + "\n\n",
        "daily_job_failed_chat": "😕 Сегодня не удалось создать историю дня.\nПричина: <i>{error}</i>",
        "photo_info_text": " <i>(с анализом до {count} фото)</i>",

        # --- Ошибки Прокси/Сети ---
        "proxy_note": "ℹ️ <i>Примечание: {note}</i>",
        "error_proxy_config": "Критическая ошибка конфигурации прокси!",
        "error_proxy_http": "Ошибка от сервиса ИИ ({status_code}).",
        "error_proxy_connect": "Ошибка сети при подключении к сервису ИИ.",
        "error_proxy_unknown": "Неизвестная ошибка при работе с сервисом ИИ.",

        # --- Настройки ---
        "settings_title": "⚙️ <b>Настройки Летописца для {chat_title}</b>", # Название чата теперь параметр
        "settings_status_label": "Статус",
        "settings_enabled": "✅ Включено",
        "settings_disabled": "❌ Выключено",
        "settings_language_label": "Язык",
        "settings_time_label": "Время генерации",
        "settings_timezone_label": "Часовой пояс",
        "settings_time_custom": "{custom_time} (ваше)",
        "settings_time_default": "~{default_time} (стандарт)", # Используем отформатированное время
        "settings_button_status_on": "❌ Выключить истории",
        "settings_button_status_off": "✅ Включить истории",
        "settings_button_language": "🌐 Сменить язык",
        "settings_button_time": "⏰ Сменить время",
        "settings_button_timezone": "🌍 Сменить пояс",
        "settings_updated": "✅ Настройки обновлены.",

        # --- Диалог установки времени ---
        "set_time_cancel": "Отмена",
        "set_time_cancelled": "Установка времени отменена.",
        "set_time_prompt_conv": "⏰ Введите желаемое время генерации в формате <b>HH:MM</b> (UTC, 24ч) или /cancel.",
        "set_time_invalid_format_conv": "❌ Неверный формат. Введите время как <b>HH:MM</b> (например, <code>09:00</code>) или /cancel.",
        "set_time_success_conv": "✅ Время генерации (UTC) установлено на <b>{new_time}</b>.",
        "set_time_default_success_conv": "✅ Время генерации сброшено на стандартное (<b>~{default_hh}:{default_mm} UTC</b>).",
        "set_time_reset_button": "⏰ Сбросить на стандартное", # Текст для кнопки сброса

        # --- Диалог выбора языка ---
        "language_select": "🌐 Выберите язык:",
        "language_set": "✅ Язык установлен на Русский.",
        "set_language_cancel": "Отмена", # Используем ту же кнопку
        "set_language_cancelled": "Выбор языка отменен.",

        # --- Диалог выбора таймзоны ---
        "timezone_select": "🌍 Выберите ваш часовой пояс из списка:",
        "timezone_set_success": "✅ Часовой пояс для этого чата установлен на <b>{tz_name}</b> ({tz_id})",
        "timezone_set_cancel": "🚫 Выбор часового пояса отменен.", # Кнопка отмены общая

        # --- Статус ---
        "status_command_reply": "<b>📊 Статус Бота</b>\n\n<b>Uptime:</b> {uptime}\n<b>Активных чатов:</b> {active_chats}\n<b>Посл. запуск задачи:</b> {last_job_run}\n<b>Посл. ошибка задачи:</b> <i>{last_job_error}</i>\n<b>Версия PTB:</b> {ptb_version}",

        # --- Описания команд (то, что показывается в Telegram) ---
        "cmd_start_desc": "Приветствие и статус",
        "cmd_help_desc": "Помощь по командам",
        "cmd_generate_now_desc": "История за сегодня (тест)",
        "cmd_regenerate_desc": "Пересоздать историю дня",
        "cmd_story_settings_desc": "⚙️ Настройки историй (Админ)",
        "cmd_set_language_desc": "🌐 Выбрать язык бота",
        "cmd_set_timezone_desc": "🌍 Установить часовой пояс (Админ)",
        "cmd_status_desc": "📊 Статус бота (Владелец)",
        # Убрали set_story_time, story_on, story_off из видимых команд
    },
    "en": {
        # --- Общие ---
        "lang_name": "English 🇬🇧",
        "private_chat": "private chat",
        "admin_only": "🔐 This function is only available to chat administrators.",
        "owner_only": "🔐 This command is only available to the bot owner.",
        "error_db_generic": "A database error occurred. Please try again later.",
        "error_telegram": "Failed to send message: Telegram Error ({error}).",
        "error_unexpected_send": "An unexpected error occurred while sending.",
        "error_admin_check": "Failed to check admin privileges.",
        "feedback_thanks": "👍 Thank you for your feedback!",

        # --- Старт и Помощь ---
        "start_message": "Hello, {user_mention}! I'm the <b>Chronicler</b> 📜\nI collect events in the chat <i>'{chat_title}'</i>.\nDaily story is generated around {schedule_time} ({schedule_tz} / your time).\nStatus for this chat: {status}\n\n<code>/help</code> - help\n<code>/story_settings</code> - settings",
        "help_message": """<b>I'm the AI Chronicler Bot!</b> 🧐

I analyze text and photos from the day and create a unique story using AI.

<b>Features:</b>
- Automatic collection of messages and photos.
- Daily story generation (~{schedule_time} / your time).

<b>Commands:</b>
<code>/start</code> - Greeting and status
<code>/help</code> - This help message
<code>/generate_now</code> - Today's story (test)
<code>/regenerate_story</code> - Regenerate today's story
<code>/story_settings</code> - Story settings (Admin)
<code>/set_language</code> - Choose bot language
<code>/set_timezone</code> - Set timezone (Admin)
<code>/status</code> - Bot status (Owner)""",

        # --- Генерация Историй ---
        "generating_now": "⏳ Analyzing {msg_count} messages{photo_info} and contacting AI... One moment...",
        "generating_now_no_messages": "🤷‍♀️ No messages yet today in this chat to create a story.",
        "generation_failed": "😕 Failed to generate the story.\nReason: <i>{error}</i>",
        "generation_failed_no_reason": "😕 Failed to generate the story for an unknown reason.",
        "story_ready_header": "✨ <b>Story of the day (on request)</b>{photo_info}:\n",
        "story_too_long": "The story is ready!{photo_info} It's quite long, sending it in parts:",
        "story_sent": "Story (generate_now) successfully sent/edited.",
        "regenerate_no_data": "🤷‍♀️ Nothing to regenerate, today's messages have already been processed or there were none.",
        "regenerating": "⏳ Attempting to regenerate today's story...",
        "daily_story_header": "📅 <b>Story for {date_str} in chat {chat_title}</b> ✨\n{photo_info}\n" + "-"*20 + "\n\n",
        "daily_story_header_long": "📅 <b>Story for {date_str} in chat {chat_title}</b> ✨\n{photo_info}\n<i>(Story is long, sending in parts)</i>\n" + "-"*20 + "\n\n",
        "daily_job_failed_chat": "😕 Failed to create the story for today.\nReason: <i>{error}</i>",
        "photo_info_text": " <i>(with analysis of up to {count} photos)</i>",

        # --- Ошибки Прокси/Сети ---
        "proxy_note": "ℹ️ <i>Note: {note}</i>",
        "error_proxy_config": "Critical proxy configuration error!",
        "error_proxy_http": "AI Service Error ({status_code}).",
        "error_proxy_connect": "Network error connecting to AI service.",
        "error_proxy_unknown": "Unknown error communicating with AI service.",

        # --- Настройки ---
        "settings_title": "⚙️ <b>Chronicler Settings for {chat_title}</b>",
        "settings_status_label": "Status",
        "settings_enabled": "✅ Enabled",
        "settings_disabled": "❌ Disabled",
        "settings_language_label": "Language",
        "settings_time_label": "Generation Time",
        "settings_timezone_label": "Timezone",
        "settings_time_custom": "{custom_time} (custom)",
        "settings_time_default": "~{default_time} (default)",
        "settings_button_status_on": "❌ Disable stories",
        "settings_button_status_off": "✅ Enable stories",
        "settings_button_language": "🌐 Change language",
        "settings_button_time": "⏰ Change time",
        "settings_button_timezone": "🌍 Change timezone",
        "settings_updated": "✅ Settings updated.",

        # --- Диалог установки времени ---
        "set_time_cancel": "Cancel",
        "set_time_cancelled": "Set time cancelled.",
        "set_time_prompt_conv": "⏰ Enter the desired generation time in <b>HH:MM</b> format (UTC, 24h) or send /cancel.",
        "set_time_invalid_format_conv": "❌ Invalid format. Enter time as <b>HH:MM</b> (e.g., <code>09:00</code>) or /cancel.",
        "set_time_success_conv": "✅ Generation time (UTC) set to <b>{new_time}</b>.",
        "set_time_default_success_conv": "✅ Generation time reset to default (<b>~{default_hh}:{default_mm} UTC</b>).",
        "set_time_reset_button": "⏰ Reset to Default",

        # --- Диалог выбора языка ---
        "language_select": "🌐 Select language:",
        "language_set": "✅ Language set to English.",
        "set_language_cancel": "Cancel",
        "set_language_cancelled": "Language selection cancelled.",

        # --- Диалог выбора таймзоны ---
        "timezone_select": "🌍 Select your timezone from the list:",
        "timezone_set_success": "✅ Timezone for this chat set to <b>{tz_name}</b> ({tz_id})",
        "timezone_set_cancel": "🚫 Timezone selection cancelled.",

        # --- Статус ---
        "status_command_reply": "<b>📊 Bot Status</b>\n\n<b>Uptime:</b> {uptime}\n<b>Active Chats:</b> {active_chats}\n<b>Last Job Run:</b> {last_job_run}\n<b>Last Job Error:</b> <i>{last_job_error}</i>\n<b>PTB Version:</b> {ptb_version}",

        # --- Описания команд ---
        "cmd_start_desc": "Greeting and status",
        "cmd_help_desc": "Help and commands",
        "cmd_generate_now_desc": "Today's story (test)",
        "cmd_regenerate_desc": "Regenerate today's story",
        "cmd_story_settings_desc": "⚙️ Story settings (Admin)",
        "cmd_set_language_desc": "🌐 Choose bot language",
        "cmd_set_timezone_desc": "🌍 Set timezone (Admin)",
        "cmd_status_desc": "📊 Bot status (Owner)",
    }
}

# Кэш для языковых настроек чатов
chat_language_cache: Dict[int, str] = {}

# --- Функции работы с локализацией ---
async def get_chat_lang(chat_id: int) -> str:
    """Получает язык чата из кэша или БД (асинхронно)."""
    if chat_id in chat_language_cache:
        return chat_language_cache[chat_id]

    lang = DEFAULT_LANGUAGE # Значение по умолчанию
    try:
        # Используем динамический импорт, чтобы избежать циклических зависимостей
        # если localization импортируется из data_manager
        import data_manager as dm
        settings = dm.get_chat_settings(chat_id) # Получаем полные настройки
        lang_from_db = settings.get('lang')
        if lang_from_db and lang_from_db in SUPPORTED_LANGUAGES:
            lang = lang_from_db
        else:
            # Если язык в БД некорректен или отсутствует, используем дефолтный
            lang = DEFAULT_LANGUAGE
    except ImportError:
        logger.error("Failed to import data_manager within get_chat_lang.")
    except Exception as e:
        logger.error(f"Error getting language for chat {chat_id} from DB: {e}")
        # В случае любой ошибки используем язык по умолчанию

    chat_language_cache[chat_id] = lang # Сохраняем в кэш
    return lang

def update_chat_lang_cache(chat_id: int, lang: str):
    """Обновляет кэш языка чата."""
    if lang in SUPPORTED_LANGUAGES:
        chat_language_cache[chat_id] = lang
    else:
        logger.warning(f"Attempted to cache unsupported language '{lang}' for chat {chat_id}")

def get_text(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """
    Возвращает локализованный текст по ключу с форматированием.
    Сначала ищет в указанном языке, потом в языке по умолчанию.
    Если ключ не найден, возвращает сам ключ в квадратных скобках.
    """
    effective_lang = lang if lang and lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    # Ищем сначала в нужном языке, потом в дефолтном
    text_template = LOCALIZED_TEXTS.get(effective_lang, {}).get(key) or \
                    LOCALIZED_TEXTS.get(DEFAULT_LANGUAGE, {}).get(key)

    if text_template is None:
        logger.warning(f"Localization key '[{key}]' not found for languages '{effective_lang}' or '{DEFAULT_LANGUAGE}'")
        return f"[{key}]" # Возвращаем ключ как индикатор отсутствия перевода

    try:
        # Форматируем строку с переданными аргументами
        return text_template.format(**kwargs)
    except KeyError as e:
        # Ошибка, если в шаблоне есть {переменная}, а она не передана в kwargs
        logger.warning(f"Missing format key '{e}' for text key '{key}' in lang '{effective_lang}' with args {kwargs}")
        return text_template # Возвращаем шаблон без форматирования
    except Exception as e:
         # Другие возможные ошибки форматирования
         logger.error(f"Error formatting text key '{key}' in lang '{effective_lang}': {e}", exc_info=True)
         return f"[Formatting Error: {key}]" # Возвращаем явную ошибку форматирования