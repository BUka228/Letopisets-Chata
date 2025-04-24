# localization.py
import logging
from typing import Optional, Dict

# Импортируем настройки языка из config
from config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

# Словарь для хранения текстов
LOCALIZED_TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        "lang_name": "Русский 🇷🇺",
        "start_message": "Привет, {user_mention}! Я Летописец чата.\nЯ собираю сообщения и фото в этом чате ({chat_title}) и каждый день около {schedule_time} ({schedule_tz}) генерирую историю дня.\n\nИспользуйте /help для списка команд.\nБот сейчас: *{status}*.\nВаш язык: Русский /set_language",
        "help_message": "Я бот-летописец с ИИ!\nАнализирую текст и изображения за день, создаю историю с помощью нейросети.\n\nФункции:\n- Автосбор сообщений и фото.\n- Ежедневная генерация истории (~{schedule_time} {schedule_tz}).\n\nКоманды:\n/start - Приветствие\n/help - Эта справка\n/generate_now - История за сегодня немедленно (тест)\n/regenerate_story - Пересоздать историю дня\n/story_on - Вкл. ежедневные истории (Админ)\n/story_off - Выкл. ежедневные истории (Админ)\n/story_settings - Показать настройки чата (Админ)\n/set_story_time - Установить время истории (Админ)\n/set_language - Выбрать язык бота\n/status - Статус бота (Владелец)", # Добавлена команда
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
        "settings_custom_time": "Время генерации: {custom_time} UTC (пользовательское)", # Новый ключ
        "settings_default_time": "Время генерации: ~{default_hh}:{default_mm} UTC (стандартное)", # Новый ключ
        "set_time_prompt": "Введите желаемое время генерации ежедневной истории в формате *HH:MM* \\(UTC, 24\\-часовой формат\\)\\.\nНапример: `/set\\_story\\_time 21:30`\\.\nЧтобы использовать время по умолчанию \\({default_hh}:{default_mm} UTC\\), введите `/set\\_story\\_time default`\\.",
        "set_time_success": "✅ Время ежедневной генерации для этого чата установлено на *{new_time}* UTC\\.",
        "set_time_default_success": "✅ Время ежедневной генерации для этого чата сброшено на стандартное \\({default_hh}:{default_mm} UTC\\)\\.",
        "set_time_invalid_format": "❌ Неверный формат времени\\. Пожалуйста, используйте *HH:MM* \\(например, 09:00 или 23:55\\)\\.",
        "set_time_usage": "Использование: `/set\\_story\\_time HH:MM` или `/set\\_story\\_time default`\\.",
        "story_enabled": "✅ Ежедневные истории для этого чата включены.",
        "story_disabled": "❌ Ежедневные истории для этого чата выключены.",
        "status_command_reply": "Статус бота:\nUptime: {uptime}\nАктивных чатов (вкл.): {active_chats}\nПоследний запуск задачи: {last_job_run}\nПоследняя ошибка задачи: {last_job_error}\nВерсия PTB: {ptb_version}", # Уточнено про активные чаты
        "owner_only": "Эта команда доступна только владельцу бота.",
        "feedback_thanks": "Спасибо за ваш отзыв!",
        "daily_story_header": "📝 История дня{photo_info}:\n\n",
        "daily_story_header_long": "📝 История дня{photo_info} получилась объемной, вот она:",
        "daily_job_failed_chat": "😕 Сегодня не удалось создать историю дня.\nПричина: {error}",
        "photo_info_text": " (с анализом до {count} фото)",
        "error_telegram": "Не удалось отправить историю из-за ошибки Telegram: {error}.",
        "error_unexpected_send": "Произошла неожиданная ошибка при отправке.",
        "error_proxy_config": "Ошибка конфигурации: не задан URL прокси или токен.",
        "error_proxy_http": "Ошибка прокси ({status_code}). {body}",
        "error_proxy_connect": "Ошибка сети при подключении к прокси: {error}",
        "error_proxy_unknown": "Неизвестная ошибка при работе с прокси: {error}",
        "error_admin_check": "Не удалось проверить права администратора: {error}",
        "error_db_generic": "Произошла ошибка базы данных.",
        "private_chat": "личном чате",
        "cmd_start_desc": "Приветствие и информация",
        "cmd_help_desc": "Показать справку и команды",
        "cmd_generate_now_desc": "История за сегодня немедленно",
        "cmd_regenerate_desc": "Пересоздать историю дня",
        "cmd_story_on_desc": "Вкл. ежедневные истории (Админ)",
        "cmd_story_off_desc": "Выкл. ежедневные истории (Админ)",
        "cmd_settings_desc": "Показать настройки чата (Админ)",
        "cmd_set_story_time_desc": "Установить время истории (Админ)", # Новый ключ
        "cmd_language_desc": "Выбрать язык бота",
        "cmd_status_desc": "Статус бота (Владелец)",
    },
    "en": {
        "lang_name": "English 🇬🇧",
        "start_message": "Hello, {user_mention}! I'm the Chat Chronicler.\nI collect messages and photos in this chat ({chat_title}) and generate a story of the day around {schedule_time} ({schedule_tz}).\n\nUse /help for a list of commands.\nBot status: *{status}*.\nYour language: English /set_language",
        "help_message": "I'm the AI Chronicler Bot!\nI analyze text and images from the day and create a unique story using an AI.\n\nFeatures:\n- Automatic collection of messages and photos.\n- Daily story generation (~{schedule_time} {schedule_tz}).\n\nCommands:\n/start - Greeting\n/help - This help message\n/generate_now - Today's story immediately (test)\n/regenerate_story - Regenerate today's story\n/story_on - Enable daily stories (Admin)\n/story_off - Disable daily stories (Admin)\n/story_settings - Show chat settings (Admin)\n/set_story_time - Set story time (Admin)\n/set_language - Choose bot language\n/status - Bot status (Owner)", # Added command
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
        "settings_custom_time": "Generation time: {custom_time} UTC (custom)", # New key
        "settings_default_time": "Generation time: ~{default_hh}:{default_mm} UTC (default)", # New key
        "set_time_prompt": "Enter the desired daily story generation time in *HH:MM* format \\(UTC, 24\\-hour\\)\\.\nExample: `/set\\_story\\_time 21:30`\\.\nTo use the default time \\({default_hh}:{default_mm} UTC\\), enter `/set\\_story\\_time default`\\.",
        "set_time_success": "✅ Daily story generation time for this chat set to *{new_time}* UTC\\.",
        "set_time_default_success": "✅ Daily story generation time for this chat reset to default \\({default_hh}:{default_mm} UTC\\)\\.",
        "set_time_invalid_format": "❌ Invalid time format\\. Please use *HH:MM* \\(e\\.g\\., 09:00 or 23:55\\)\\.",
        "set_time_usage": "Usage: `/set\\_story\\_time HH:MM` or `/set\\_story\\_time default`\\.",
        "story_enabled": "✅ Daily stories for this chat have been enabled.",
        "story_disabled": "❌ Daily stories for this chat have been disabled.",
        "status_command_reply": "Bot Status:\nUptime: {uptime}\nActive chats (enabled): {active_chats}\nLast job run: {last_job_run}\nLast job error: {last_job_error}\nPTB Version: {ptb_version}", # Clarified active chats
        "owner_only": "This command is only available to the bot owner.",
        "feedback_thanks": "Thank you for your feedback!",
        "daily_story_header": "📝 Story of the day{photo_info}:\n\n",
        "daily_story_header_long": "📝 The story of the day{photo_info} is quite long, here it is:",
        "daily_job_failed_chat": "😕 Failed to create the story for today.\nReason: {error}",
        "photo_info_text": " (with analysis of up to {count} photos)",
        "error_telegram": "Failed to send the story due to a Telegram error: {error}.",
        "error_unexpected_send": "An unexpected error occurred while sending.",
        "error_proxy_config": "Configuration error: Proxy URL or Auth Token is not set.",
        "error_proxy_http": "Proxy error ({status_code}). {body}",
        "error_proxy_connect": "Network error connecting to proxy: {error}",
        "error_proxy_unknown": "Unknown error while communicating with proxy: {error}",
        "error_admin_check": "Failed to check admin privileges: {error}",
        "error_db_generic": "A database error occurred.",
        "private_chat": "private chat",
        "cmd_start_desc": "Greeting and info",
        "cmd_help_desc": "Show help and commands",
        "cmd_generate_now_desc": "Today's story immediately",
        "cmd_regenerate_desc": "Regenerate today's story",
        "cmd_story_on_desc": "Enable daily stories (Admin)",
        "cmd_story_off_desc": "Disable daily stories (Admin)",
        "cmd_settings_desc": "Show chat settings (Admin)",
        "cmd_set_story_time_desc": "Set story time (Admin)", # New key
        "cmd_language_desc": "Choose bot language",
        "cmd_status_desc": "Bot status (Owner)",
    }
}

# Кэш для языковых настроек чатов
chat_language_cache: Dict[int, str] = {}

# --- Функции get_chat_lang и update_chat_lang_cache без изменений ---
async def get_chat_lang(chat_id: int) -> str:
    if chat_id in chat_language_cache: return chat_language_cache[chat_id]
    lang = DEFAULT_LANGUAGE # Значение по умолчанию
    try:
        import data_manager as dm # Динамический импорт
        lang = dm.get_chat_language(chat_id) or DEFAULT_LANGUAGE
        if lang not in SUPPORTED_LANGUAGES: lang = DEFAULT_LANGUAGE
    except ImportError: logger.error("Failed to import data_manager in get_chat_lang.")
    except Exception as e: logger.error(f"Error getting lang for chat {chat_id} from DB: {e}")
    chat_language_cache[chat_id] = lang; return lang

def update_chat_lang_cache(chat_id: int, lang: str):
    if lang in SUPPORTED_LANGUAGES: chat_language_cache[chat_id] = lang

def get_text(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Возвращает локализованный текст по ключу с форматированием."""
    effective_lang = lang if lang and lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    # Сначала ищем в выбранном языке, потом в дефолтном, потом возвращаем ключ
    text_template = LOCALIZED_TEXTS.get(effective_lang, {}).get(key) or \
                    LOCALIZED_TEXTS.get(DEFAULT_LANGUAGE, {}).get(key) or \
                    f"[{key}]" # Возвращаем ключ, если перевод не найден
    try:
        return text_template.format(**kwargs)
    except KeyError as e:
        logger.warning(f"Missing format key '{e}' for text key '{key}' in lang '{effective_lang}'")
        # Попытаемся вернуть шаблон без форматирования, чтобы избежать падения
        return text_template
    except Exception as e:
         logger.error(f"Error formatting text key '{key}' in lang '{effective_lang}': {e}", exc_info=True)
         return f"[Formatting Error: {key}]" # Возвращаем явную ошибку