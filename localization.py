# localization.py
import logging # Добавим импорт logging для предупреждений

from typing import Optional
from config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__) # Получаем логгер

# Простой словарь для хранения текстов на разных языках
# В реальном приложении лучше использовать gettext или fluent
LOCALIZED_TEXTS = {
    "ru": {
        "lang_name": "Русский 🇷🇺", # Добавляем ключ для имени языка
        "start_message": (
            "Привет, {user_mention}! Я Летописец чата.\n"
            "Я собираю сообщения и фото в этом чате ({chat_title}) и каждый день около "
            "{schedule_time} ({schedule_tz}) генерирую историю дня.\n\n"
            "Используйте /help для списка команд.\n"
            "Бот сейчас: *{status}*.\n"
            "Ваш язык: Русский /set_language"
        ),
        "help_message": (
            "Я бот-летописец с ИИ!\n"
            "Анализирую текст и изображения за день, создаю историю с помощью нейросети.\n\n"
            "Функции:\n"
            "- Автосбор сообщений и фото.\n"
            "- Ежедневная генерация истории (~{schedule_time} {schedule_tz}).\n\n"
            "Команды:\n"
            "/start - Приветствие\n"
            "/help - Эта справка\n"
            "/generate_now - История за сегодня немедленно (тест)\n"
            "/regenerate_story - Попробовать пересоздать сегодняшнюю историю (если не удалось)\n"
            "/story_on - Включить ежедневные истории для этого чата (только админы)\n"
            "/story_off - Выключить ежедневные истории для этого чата (только админы)\n"
            "/story_settings - Показать текущие настройки для чата (только админы)\n"
            "/set_language - Выбрать язык бота\n"
            "/status - Статус бота (только владелец)"
        ),
        "language_select": "Выберите язык:",
        "language_set": "Язык установлен на Русский.",
        "generating_now": "⏳ Анализирую {msg_count} сообщ.{photo_info} и обращаюсь к прокси... Ожидайте.",
        "generating_now_no_messages": "В этом чате пока нет сообщений за сегодня для создания истории.",
        "generation_failed": "😕 Не удалось сгенерировать историю.\nПричина: {error}",
        "generation_failed_no_reason": "😕 Не удалось сгенерировать историю по неизвестной причине.",
        "story_ready_header": "✨ История дня (по запросу){photo_info}:\n\n",
        "story_too_long": "История готова!{photo_info} Она получилась довольно длинной, отправляю по частям:",
        "story_sent": "История по запросу (generate_now) успешно отправлена/отредактирована.",
        "proxy_note": "ℹ️ Примечание: {note}",
        "regenerate_no_data": "Нечего перегенерировать, сообщения за сегодня уже обработаны или их не было.",
        "regenerating": "⏳ Пытаюсь пересоздать историю за сегодня...",
        "admin_only": "Эта команда доступна только администраторам чата.",
        "settings_title": "Настройки Летописца для чата '{chat_title}':",
        "settings_enabled": "Ежедневные истории: ✅ Включены",
        "settings_disabled": "Ежедневные истории: ❌ Выключены",
        "settings_language": "Язык чата: Русский",
        "story_enabled": "✅ Ежедневные истории для этого чата включены.",
        "story_disabled": "❌ Ежедневные истории для этого чата выключены.",
        "status_command_reply": (
            "Статус бота:\n"
            "Uptime: {uptime}\n"
            "Активных чатов (сообщения за день): {active_chats}\n"
            "Последний запуск задачи: {last_job_run}\n"
            "Последняя ошибка задачи: {last_job_error}\n"
            "Версия PTB: {ptb_version}"
        ),
        "owner_only": "Эта команда доступна только владельцу бота.",
        "feedback_thanks": "Спасибо за ваш отзыв!",
        "daily_story_header": "📝 История дня{photo_info}:\n\n",
        "daily_story_header_long": "📝 История дня{photo_info} получилась объемной, вот она:",
        "daily_job_failed_chat": "😕 Сегодня не удалось создать историю дня.\nПричина: {error}",
        "photo_info_text": " (с анализом до {count} фото)",
        # Ошибки
        "error_telegram": "Не удалось отправить историю из-за ошибки Telegram: {error}.",
        "error_unexpected_send": "Произошла неожиданная ошибка при отправке.",
        "error_proxy_config": "Ошибка конфигурации: не задан URL прокси или токен.",
        "error_proxy_http": "Ошибка прокси ({status_code}). {body}",
        "error_proxy_connect": "Ошибка сети при подключении к прокси: {error}",
        "error_proxy_unknown": "Неизвестная ошибка при работе с прокси: {error}",
        "error_admin_check": "Не удалось проверить права администратора: {error}",
        "error_db_generic": "Произошла ошибка базы данных.",
        "private_chat": "личном чате", # Текст для личного чата
        # Описания команд
        "cmd_start_desc": "Приветствие и информация",
        "cmd_help_desc": "Показать справку и команды",
        "cmd_generate_now_desc": "История за сегодня немедленно",
        "cmd_regenerate_desc": "Пересоздать историю дня",
        "cmd_story_on_desc": "Вкл. ежедневные истории (Админ)",
        "cmd_story_off_desc": "Выкл. ежедневные истории (Админ)",
        "cmd_settings_desc": "Показать настройки чата (Админ)",
        "cmd_language_desc": "Выбрать язык бота",
        "cmd_status_desc": "Статус бота (Владелец)",
    },
    "en": {
        "lang_name": "English 🇬🇧", # Добавляем ключ для имени языка
        "start_message": (
            "Hello, {user_mention}! I'm the Chat Chronicler.\n"
            "I collect messages and photos in this chat ({chat_title}) and generate a story of the day around "
            "{schedule_time} ({schedule_tz}).\n\n"
            "Use /help for a list of commands.\n"
            "Bot status: *{status}*.\n"
            "Your language: English /set_language"
        ),
        "help_message": (
            "I'm the AI Chronicler Bot!\n"
            "I analyze text and images from the day and create a unique story using an AI.\n\n"
            "Features:\n"
            "- Automatic collection of messages and photos.\n"
            "- Daily story generation (~{schedule_time} {schedule_tz}).\n\n"
            "Commands:\n"
            "/start - Greeting\n"
            "/help - This help message\n"
            "/generate_now - Today's story immediately (test)\n"
            "/regenerate_story - Try regenerating today's story (if failed)\n"
            "/story_on - Enable daily stories for this chat (Admins only)\n"
            "/story_off - Disable daily stories for this chat (Admins only)\n"
            "/story_settings - Show current settings for this chat (Admins only)\n"
            "/set_language - Choose bot language\n"
            "/status - Bot status (Owner only)"
        ),
        "language_select": "Select language:",
        "language_set": "Language set to English.",
        "generating_now": "⏳ Analyzing {msg_count} messages{photo_info} and contacting the proxy... Please wait.",
        "generating_now_no_messages": "There are no messages yet today in this chat to create a story.",
        "generation_failed": "😕 Failed to generate the story.\nReason: {error}",
        "generation_failed_no_reason": "😕 Failed to generate the story for an unknown reason.",
        "story_ready_header": "✨ Story of the day (on request){photo_info}:\n\n",
        "story_too_long": "The story is ready!{photo_info} It's quite long, sending it in parts:",
        "story_sent": "Story (generate_now) successfully sent/edited.",
        "proxy_note": "ℹ️ Note: {note}",
        "regenerate_no_data": "Nothing to regenerate, today's messages have already been processed or there were none.",
        "regenerating": "⏳ Attempting to regenerate today's story...",
        "admin_only": "This command is only available to chat administrators.",
        "settings_title": "Chronicler Settings for chat '{chat_title}':",
        "settings_enabled": "Daily stories: ✅ Enabled",
        "settings_disabled": "Daily stories: ❌ Disabled",
        "settings_language": "Chat language: English",
        "story_enabled": "✅ Daily stories for this chat have been enabled.",
        "story_disabled": "❌ Daily stories for this chat have been disabled.",
        "status_command_reply": (
            "Bot Status:\n"
            "Uptime: {uptime}\n"
            "Active chats (today's messages): {active_chats}\n"
            "Last job run: {last_job_run}\n"
            "Last job error: {last_job_error}\n"
            "PTB Version: {ptb_version}"
        ),
        "owner_only": "This command is only available to the bot owner.",
        "feedback_thanks": "Thank you for your feedback!",
        "daily_story_header": "📝 Story of the day{photo_info}:\n\n",
        "daily_story_header_long": "📝 The story of the day{photo_info} is quite long, here it is:",
        "daily_job_failed_chat": "😕 Failed to create the story for today.\nReason: {error}",
        "photo_info_text": " (with analysis of up to {count} photos)",
        # Errors
        "error_telegram": "Failed to send the story due to a Telegram error: {error}.",
        "error_unexpected_send": "An unexpected error occurred while sending.",
        "error_proxy_config": "Configuration error: Proxy URL or Auth Token is not set.",
        "error_proxy_http": "Proxy error ({status_code}). {body}",
        "error_proxy_connect": "Network error connecting to proxy: {error}",
        "error_proxy_unknown": "Unknown error while communicating with proxy: {error}",
        "error_admin_check": "Failed to check admin privileges: {error}",
        "error_db_generic": "A database error occurred.",
        "private_chat": "private chat", # Текст для личного чата
        # Command descriptions
        "cmd_start_desc": "Greeting and info",
        "cmd_help_desc": "Show help and commands",
        "cmd_generate_now_desc": "Today's story immediately",
        "cmd_regenerate_desc": "Regenerate today's story",
        "cmd_story_on_desc": "Enable daily stories (Admin)",
        "cmd_story_off_desc": "Disable daily stories (Admin)",
        "cmd_settings_desc": "Show chat settings (Admin)",
        "cmd_language_desc": "Choose bot language",
        "cmd_status_desc": "Bot status (Owner)",
    }
}

# Кэш для языковых настроек чатов
chat_language_cache = {}

# --- ИЗМЕНЕНО: Импорт data_manager теперь внутри функции для избежания цикла ---
# (Хотя это не идеальное решение, но простое для исправления ImportError)
async def get_chat_lang(chat_id: int) -> str:
    """Получает язык чата из кэша или БД."""
    if chat_id in chat_language_cache:
        return chat_language_cache[chat_id]
    else:
        try:
            # Динамический импорт для получения языка из БД
            import data_manager as dm
            lang = dm.get_chat_language(chat_id) or DEFAULT_LANGUAGE # Получаем из БД
            if lang not in SUPPORTED_LANGUAGES: # Проверка на валидность
                lang = DEFAULT_LANGUAGE
        except ImportError:
            logger.error("Не удалось импортировать data_manager в get_chat_lang.")
            lang = DEFAULT_LANGUAGE
        except Exception as e:
            logger.error(f"Ошибка получения языка для чата {chat_id} из БД: {e}")
            lang = DEFAULT_LANGUAGE

        chat_language_cache[chat_id] = lang
        return lang

def update_chat_lang_cache(chat_id: int, lang: str):
    """Обновляет кэш языка чата."""
    if lang in SUPPORTED_LANGUAGES:
        chat_language_cache[chat_id] = lang

# --- Функция get_text теперь использует импортированные константы ---
def get_text(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Возвращает локализованный текст по ключу."""
    effective_lang = lang if lang and lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    text_template = LOCALIZED_TEXTS.get(effective_lang, {}).get(key, f"[{key}]")
    try:
        return text_template.format(**kwargs)
    except KeyError as e:
        logger.warning(f"Missing format key '{e}' for text key '{key}' in lang '{effective_lang}'")
        return text_template