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
        "error_db_generic": "😔 Произошла ошибка базы данных. Попробуйте позже.",
        "error_telegram": "😔 Не удалось выполнить действие: Ошибка Telegram ({error}).",
        "error_unexpected_send": "😔 Произошла неожиданная ошибка при отправке.",
        "error_admin_check": "😔 Не удалось проверить права администратора.",
        "feedback_thanks": "👍 Спасибо за ваш отзыв!",
        "button_back": "⬅️ Назад",
        "button_close": "❌ Закрыть",
        "action_cancelled": "Действие отменено.",

        # --- Старт и Помощь ---
        "start_message": "Привет, {user_mention}! Я <b>Летописец</b> 📜\nСобираю события чата <i>'{chat_title}'</i>.\nЕжедневная история ~ в {schedule_time} ({schedule_tz} / ваше время).\nСтатус для этого чата: {status}\n\nКоманды: /help, /story_settings",
        "help_message": """<b>Я бот-летописец с ИИ!</b> 🧐

Анализирую текст и фото за день, создаю уникальную историю с помощью нейросети.

<b>🤖 Основные Команды:</b>
<code>/start</code> - Приветствие и статус
<code>/help</code> - Эта справка
<code>/story_settings</code> - ⚙️ Настройки историй (только админы)

<b>✍️ Генерация:</b>
<code>/generate_now</code> - Создать историю прямо сейчас (тест)
<code>/regenerate_story</code> - Пересоздать последнюю историю
<code>/summarize</code> - 📝 Сделать краткую выжимку чата

<b>🛠️ Настройки (также доступны через <code>/story_settings</code>):</b>
<code>/set_language</code> - 🌐 Выбрать язык бота
<code>/set_timezone</code> - 🌍 Установить часовой пояс (Админ)
    (Время и жанр меняются через меню настроек)

<b>ℹ️ Информация:</b>
<code>/status</code> - 📊 Статус бота (только владелец)""",

        # --- Генерация Историй ---
        "generating_status_downloading": "⏳ Скачиваю фото ({count}/{total})...",
        "generating_status_contacting_ai": "🧠 Отправляю данные ИИ...",
        "generating_status_formatting": "✍️ Форматирую историю...",
        "generating_now": "⏳ Генерирую историю для вас...", # Более общее начальное
        "generating_now_no_messages": "🤷‍♀️ В этом чате пока нет сообщений за сегодня для создания истории.",
        "generation_failed": "😕 Не удалось создать историю.\nПричина: <i>{error}</i>",
        "generation_failed_user_friendly": "😔 К сожалению, сервис генерации не смог создать историю ({reason}). Попробуйте позже.",
        "generation_failed_no_reason": "😕 Не удалось создать историю по неизвестной причине.",
        "story_ready_header": "✨ <b>История дня (по запросу)</b>{photo_info}:\n",
        "story_too_long": "История готова!{photo_info} Она получилась довольно длинной, отправляю по частям:",
        "story_sent": "История по запросу (generate_now) успешно отправлена/отредактирована.",
        "regenerate_no_data": "🤷‍♀️ Нечего перегенерировать, сообщения за сегодня уже обработаны или их не было.",
        "regenerating": "⏳ Пересоздаю историю...",
        "daily_story_header": "📅 <b>История за {date_str} в чате {chat_title}</b> ✨\n{photo_info}\n" + "-"*20 + "\n\n",
        "daily_story_header_long": "📅 <b>История за {date_str} в чате {chat_title}</b> ✨\n{photo_info}\n<i>(История длинная, разбита на части)</i>\n" + "-"*20 + "\n\n",
        "daily_job_failed_chat": "😕 Сегодня не удалось создать историю дня.\nПричина: <i>{error}</i>",
        "daily_job_failed_chat_user_friendly": "😔 Сегодня не удалось создать историю дня ({reason}). Следующая попытка завтра.",
        "photo_info_text": " <i>(с анализом до {count} фото)</i>",

        # --- Саммари ---
        "summarize_prompt_period": "📝 Выберите период для создания краткой выжимки:",
        "summarize_button_today": "За сегодня",
        "summarize_button_last_3h": "За последние 3 часа",
        "summarize_button_last_1h": "За последний час",
        "summarize_button_last_24h": "За последние 24 часа",
        "summarize_generating": "⏳ Готовлю краткую выжимку...",
        "summarize_no_messages": "🤷‍♀️ Не найдено сообщений для анализа за выбранный период.",
        "summarize_header": "📝 <b>Краткая выжимка за период: {period_name}</b>\n" + "-"*20 + "\n", # Убрал двойной \n, он будет после Markdown
        "summarize_failed": "😕 Не удалось создать выжимку.\nПричина: <i>{error}</i>",
        "summarize_failed_user_friendly": "😔 Не удалось создать выжимку ({reason}).",
        "summarize_period_name_today": "сегодня",
        "summarize_period_name_last_1h": "последний час",
        "summarize_period_name_last_3h": "последние 3 часа",
        "summarize_period_name_last_24h": "последние 24 часа",

        # --- Ошибки Прокси/Сети (для пользователя) ---
        "proxy_note": "ℹ️ <i>Примечание: {note}</i>",
        "error_proxy_generic": "Сервис генерации временно недоступен",
        "error_proxy_timeout": "Сервис генерации не ответил вовремя",
        "error_proxy_connect": "Ошибка сети при подключении к сервису генерации",
        "error_proxy_safety": "Запрос заблокирован настройками безопасности контента",
        "error_proxy_config_user": "Критическая ошибка конфигурации бота", # Если нужно показать пользователю
        "error_proxy_unknown_user": "Неизвестная ошибка сервиса генерации",

        # --- Настройки (Новый UI) ---
        "settings_title": "⚙️ <b>Настройки Летописца для {chat_title}</b>",
        "settings_status_label": "Статус",
        "settings_enabled": "✅ Включено",
        "settings_disabled": "❌ Выключено",
        "settings_language_label": "Язык",
        "settings_time_label": "Время", # Укорочено
        "settings_timezone_label": "Пояс", # Укорочено
        "settings_genre_label": "Жанр", # Укорочено
        "settings_time_custom": "{custom_time} (ваше)",
        "settings_time_default": "~{default_local_time} (стандартное)", # Показываем локальное время по умолчанию
        "settings_button_change": "Изменить", # Общая кнопка
        "settings_button_toggle_on": "❌ Выключить истории",
        "settings_button_toggle_off": "✅ Включить истории",
        "settings_current_value": "{value}", # Для отображения текущего значения
        "settings_saved_popup": "✅ Сохранено!", # Всплывающее уведомление

        # --- Подменю Языка ---
        "settings_select_language_title": "🌐 Выберите язык интерфейса:",
        "settings_lang_selected": "✅ Язык изменен!",

        # --- Подменю Времени ---
        "settings_select_time_title": "⏰ <b>Настройка времени генерации</b>",
        "settings_time_current": "Текущее время: {current_time_display}",
        "settings_time_prompt": "Отправьте новое время в формате <b>ЧЧ:ММ</b> (24ч) для вашего пояса (<b>{chat_timezone}</b>), или сбросьте на стандартное.",
        "settings_time_invalid_format": "❌ Неверный формат. Введите время как <b>ЧЧ:ММ</b> (например, <code>09:00</code> или <code>23:55</code>).",
        "settings_time_success": "✅ Время генерации установлено: {local_time} {tz_short} ({utc_time} UTC).",
        "settings_time_reset_success": "✅ Время генерации сброшено на стандартное ({local_default_time}).",
        "settings_time_button_reset": "Сбросить на стандартное",
        "waiting_for_time_input": "⏳ Ожидаю ввода времени...", # Сообщение пока пользователь печатает

        # --- Подменю Таймзоны ---
        "settings_select_timezone_title": "🌍 Выберите ваш часовой пояс:",
        "settings_tz_selected": "✅ Часовой пояс изменен!",

        # --- Подменю Жанра ---
        "settings_select_genre_title": "🎭 Выберите жанр для историй:",
        "settings_genre_selected": "✅ Жанр изменен!",

        # --- Названия жанров (для отображения) ---
        "genre_name_default": "Стандартный",
        "genre_name_humor": "Юмористический",
        "genre_name_detective": "Детективный",
        "genre_name_fantasy": "Фэнтезийный",
        "genre_name_news_report": "Новостной репортаж",
        
        "genre_select_button_text": "{genre_name}",

        # --- Статус ---
        "status_command_reply": "<b>📊 Статус Бота</b>\n\n<b>Uptime:</b> {uptime}\n<b>Активных чатов:</b> {active_chats}\n<b>Посл. запуск задачи:</b> {last_job_run}\n<b>Посл. ошибка задачи:</b> <i>{last_job_error}</i>\n<b>Версия PTB:</b> {ptb_version}",

        # --- Описания команд (то, что показывается в Telegram) ---
        "cmd_start_desc": "👋 Приветствие и статус",
        "cmd_help_desc": "❓ Помощь по командам",
        "cmd_generate_now_desc": "✍️ История за сегодня (тест)",
        "cmd_regenerate_desc": "🔄 Пересоздать историю дня",
        "cmd_summarize_desc": "📝 Создать краткую выжимку чата",
        "cmd_story_settings_desc": "⚙️ Настройки историй (Админ)",
        "cmd_set_language_desc": "🌐 Выбрать язык бота",
        "cmd_set_timezone_desc": "🌍 Установить часовой пояс (Админ)",
        "cmd_status_desc": "📊 Статус бота (Владелец)",
    },
    "en": {
        # --- General ---
        "lang_name": "English 🇬🇧",
        "private_chat": "private chat",
        "admin_only": "🔐 This function is only available to chat administrators.",
        "owner_only": "🔐 This command is only available to the bot owner.",
        "error_db_generic": "😔 A database error occurred. Please try again later.",
        "error_telegram": "😔 Failed to perform action: Telegram Error ({error}).",
        "error_unexpected_send": "😔 An unexpected error occurred while sending.",
        "error_admin_check": "😔 Failed to check admin privileges.",
        "feedback_thanks": "👍 Thank you for your feedback!",
        "button_back": "⬅️ Back",
        "button_close": "❌ Close",
        "action_cancelled": "Action cancelled.",

        # --- Start & Help ---
        "start_message": "Hello, {user_mention}! I'm the <b>Chronicler</b> 📜\nI collect events in the chat <i>'{chat_title}'</i>.\nDaily story is generated around {schedule_time} ({schedule_tz} / your time).\nStatus for this chat: {status}\n\nCommands: /help, /story_settings",
        "help_message": """<b>I'm the AI Chronicler Bot!</b> 🧐

I analyze text and photos from the day and create a unique story using AI.

<b>🤖 Core Commands:</b>
<code>/start</code> - Greeting and status
<code>/help</code> - This help message
<code>/story_settings</code> - ⚙️ Story settings (Admins only)

<b>✍️ Generation:</b>
<code>/generate_now</code> - Generate today's story now (test)
<code>/regenerate_story</code> - Regenerate the last story
<code>/summarize</code> - 📝 Create a brief chat summary

<b>🛠️ Settings (also via <code>/story_settings</code>):</b>
<code>/set_language</code> - 🌐 Choose bot language
<code>/set_timezone</code> - 🌍 Set timezone (Admin)
    (Time and Genre are changed via the settings menu)

<b>ℹ️ Information:</b>
<code>/status</code> - 📊 Bot status (Owner only)""",

        # --- Story Generation ---
        "generating_status_downloading": "⏳ Downloading photos ({count}/{total})...",
        "generating_status_contacting_ai": "🧠 Sending data to AI...",
        "generating_status_formatting": "✍️ Formatting story...",
        "generating_now": "⏳ Generating story for you...", # More general initial
        "generating_now_no_messages": "🤷‍♀️ No messages yet today in this chat to create a story.",
        "generation_failed": "😕 Failed to generate the story.\nReason: <i>{error}</i>",
        "generation_failed_user_friendly": "😔 Unfortunately, the generation service couldn't create the story ({reason}). Please try again later.",
        "generation_failed_no_reason": "😕 Failed to generate the story for an unknown reason.",
        "story_ready_header": "✨ <b>Story of the day (on request)</b>{photo_info}:\n",
        "story_too_long": "The story is ready!{photo_info} It's quite long, sending it in parts:",
        "story_sent": "Story (generate_now) successfully sent/edited.",
        "regenerate_no_data": "🤷‍♀️ Nothing to regenerate, today's messages have already been processed or there were none.",
        "regenerating": "⏳ Regenerating story...",
        "daily_story_header": "📅 <b>Story for {date_str} in chat {chat_title}</b> ✨\n{photo_info}\n" + "-"*20 + "\n\n",
        "daily_story_header_long": "📅 <b>Story for {date_str} in chat {chat_title}</b> ✨\n{photo_info}\n<i>(Story is long, sending in parts)</i>\n" + "-"*20 + "\n\n",
        "daily_job_failed_chat": "😕 Failed to create the story for today.\nReason: <i>{error}</i>",
        "daily_job_failed_chat_user_friendly": "😔 Failed to create today's story ({reason}). Next attempt tomorrow.",
        "photo_info_text": " <i>(with analysis of up to {count} photos)</i>",

        # --- Summarization ---
        "summarize_prompt_period": "📝 Select the period for the summary:",
        "summarize_button_today": "Today",
        "summarize_button_last_3h": "Last 3 hours",
        "summarize_button_last_1h": "Last 1 hour",
        "summarize_button_last_24h": "Last 24 hours",
        "summarize_generating": "⏳ Preparing summary...",
        "summarize_no_messages": "🤷‍♀️ No messages found to analyze for the selected period.",
        "summarize_header": "📝 <b>Summary for the period: {period_name}</b>\n" + "-"*20 + "\n", # Removed double \n
        "summarize_failed": "😕 Failed to create summary.\nReason: <i>{error}</i>",
        "summarize_failed_user_friendly": "😔 Failed to create summary ({reason}).",
        "summarize_period_name_today": "today",
        "summarize_period_name_last_1h": "last hour",
        "summarize_period_name_last_3h": "last 3 hours",
        "summarize_period_name_last_24h": "last 24 hours",

        # --- Proxy/Network Errors (for user) ---
        "proxy_note": "ℹ️ <i>Note: {note}</i>",
        "error_proxy_generic": "Generation service temporarily unavailable",
        "error_proxy_timeout": "Generation service did not respond in time",
        "error_proxy_connect": "Network error connecting to generation service",
        "error_proxy_safety": "Request blocked by content safety settings",
        "error_proxy_config_user": "Critical bot configuration error",
        "error_proxy_unknown_user": "Unknown generation service error",

        # --- Settings (New UI) ---
        "settings_title": "⚙️ <b>Chronicler Settings for {chat_title}</b>",
        "settings_status_label": "Status",
        "settings_enabled": "✅ Enabled",
        "settings_disabled": "❌ Disabled",
        "settings_language_label": "Language",
        "settings_time_label": "Time",
        "settings_timezone_label": "Zone",
        "settings_genre_label": "Genre",
        "settings_time_custom": "{custom_time} (custom)",
        "settings_time_default": "~{default_local_time} (default)",
        "settings_button_change": "Change",
        "settings_button_toggle_on": "❌ Disable stories",
        "settings_button_toggle_off": "✅ Enable stories",
        "settings_current_value": "{value}",
        "settings_saved_popup": "✅ Saved!",

        # --- Language Submenu ---
        "settings_select_language_title": "🌐 Select interface language:",
        "settings_lang_selected": "✅ Language changed!",

        # --- Time Submenu ---
        "settings_select_time_title": "⏰ <b>Set Generation Time</b>",
        "settings_time_current": "Current time: {current_time_display}",
        "settings_time_prompt": "Send the new time in <b>HH:MM</b> format (24h) for your timezone (<b>{chat_timezone}</b>), or reset to default.",
        "settings_time_invalid_format": "❌ Invalid format. Enter time as <b>HH:MM</b> (e.g., <code>09:00</code> or <code>23:55</code>).",
        "settings_time_success": "✅ Generation time set: {local_time} {tz_short} ({utc_time} UTC).",
        "settings_time_reset_success": "✅ Generation time reset to default ({local_default_time}).",
        "settings_time_button_reset": "Reset to Default",
        "waiting_for_time_input": "⏳ Waiting for time input...",

        # --- Timezone Submenu ---
        "settings_select_timezone_title": "🌍 Select your timezone:",
        "settings_tz_selected": "✅ Timezone changed!",

        # --- Genre Submenu ---
        "settings_select_genre_title": "🎭 Select story genre:",
        "settings_genre_selected": "✅ Genre changed!",

        # --- Genre Names (for display) ---
        "genre_name_default": "Standard",
        "genre_name_humor": "Humorous",
        "genre_name_detective": "Detective",
        "genre_name_fantasy": "Fantasy",
        "genre_name_news_report": "News Report",
        
        "genre_select_button_text": "{genre_name}", 

        # --- Status ---
        "status_command_reply": "<b>📊 Bot Status</b>\n\n<b>Uptime:</b> {uptime}\n<b>Active Chats:</b> {active_chats}\n<b>Last Job Run:</b> {last_job_run}\n<b>Last Job Error:</b> <i>{last_job_error}</i>\n<b>PTB Version:</b> {ptb_version}",

        # --- Command Descriptions ---
        "cmd_start_desc": "👋 Greeting and status",
        "cmd_help_desc": "❓ Help and commands",
        "cmd_generate_now_desc": "✍️ Today's story (test)",
        "cmd_regenerate_desc": "🔄 Regenerate today's story",
        "cmd_summarize_desc": "📝 Create a brief chat summary",
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
     
def get_genre_name(genre_key: str, lang: str) -> str:
    """Возвращает локализованное имя жанра по его ключу."""
    return get_text(f"genre_name_{genre_key}", lang)

# --- Функция для получения локализованного имени периода ---
def get_period_name(period_key: str, lang: str) -> str:
    """Возвращает локализованное имя периода по его ключу."""
    return get_text(f"summarize_period_name_{period_key}", lang)

# --- Функция для перевода ошибок прокси в user-friendly вид ---
def get_user_friendly_proxy_error(error_message: Optional[str], lang: str) -> str:
    """Преобразует техническую ошибку от прокси/Gemini в понятное сообщение."""
    if not error_message:
        return get_text("error_proxy_unknown_user", lang)

    error_lower = error_message.lower()

    if "safety settings" in error_lower or "blocked" in error_lower:
        return get_text("error_proxy_safety", lang)
    if "timeout" in error_lower:
        return get_text("error_proxy_timeout", lang)
    if "network error" in error_lower or "connection" in error_lower or "502" in error_lower or "503" in error_lower or "504" in error_lower:
         return get_text("error_proxy_connect", lang)
    if "proxy url or auth token" in error_lower:
        return get_text("error_proxy_config_user", lang) # Важно, если вдруг просочится
    if "429" in error_lower: # Too Many Requests
        return get_text("error_proxy_generic", lang) + " (слишком часто)"
    if "invalid" in error_lower or "bad request" in error_lower or "400" in error_lower:
        # Обычно это проблемы с запросом, которые не должны доходить до пользователя, но на всякий случай
        return get_text("error_proxy_generic", lang) + " (ошибка запроса)"

    # Если не подошло ни одно правило, возвращаем общую ошибку
    return get_text("error_proxy_generic", lang)