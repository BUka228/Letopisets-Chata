# =============================================================================
# ФАЙЛ: localization.py
# (Финальная версия с учетом всех выбранных фич)
# =============================================================================
# localization.py
import logging
from typing import Optional, Dict, Tuple

# Импортируем настройки и константы
from config import (
    DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, COMMON_TIMEZONES, SUPPORTED_GENRES,
    SUPPORTED_PERSONALITIES, DEFAULT_PERSONALITY, SUPPORTED_OUTPUT_FORMATS,
    DEFAULT_OUTPUT_FORMAT,
    # Лимиты для отображения в настройках вмешательств
    INTERVENTION_MIN_COOLDOWN_MIN, INTERVENTION_MAX_COOLDOWN_MIN,
    INTERVENTION_MIN_MIN_MSGS, INTERVENTION_MAX_MIN_MSGS,
    INTERVENTION_MIN_TIMESPAN_MIN, INTERVENTION_MAX_TIMESPAN_MIN,
    # Дефолты для отображения
    INTERVENTION_DEFAULT_COOLDOWN_MIN, INTERVENTION_DEFAULT_MIN_MSGS,
    INTERVENTION_DEFAULT_TIMESPAN_MIN
)

logger = logging.getLogger(__name__)
chat_language_cache: Dict[int, str] = {}

# Словарь текстов
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
        "error_value_corrected": "⚠️ Значение скорректировано до {corrected_value} (пределы: {min_val}-{max_val})",
        "enabled_status": "✅ Включено",
        "disabled_status": "❌ Выключено",

        # --- Старт и Помощь ---
        "start_message": "Привет, {user_mention}! Я <b>Летописец</b> 📜 ({personality}) чата <i>'{chat_title}'</i>.\n{format_desc} ~ в {schedule_time} ({schedule_tz}).\nСтатус: {status}\n\nКоманды: /help, /story_settings",
        "start_format_desc_story": "Ежедневная история",
        "start_format_desc_digest": "Ежедневный дайджест",
        "help_message": """<b>Я бот-летописец {personality_name}!</b> 🧐

Анализирую чат, создаю уникальные сводки событий.

<b>🤖 Основные Команды:</b>
<code>/start</code> - Приветствие и статус
<code>/help</code> - Эта справка
<code>/story_settings</code> - ⚙️ Настройки Летописца (Админ)

<b>✍️ Генерация:</b>
<code>/generate_now</code> - {current_format_action_now}
<code>/regenerate_story</code> - {current_format_action_regen}
<code>/summarize</code> - 📝 Краткая выжимка чата

<b>📊 Аналитика:</b>
<code>/chat_stats</code> - 📈 Статистика активности чата

<b>🛠️ Администрирование:</b>
<code>/purge_history</code> - 🗑️ Очистить историю сообщений (Админ)

<b>ℹ️ Информация:</b>
<code>/status</code> - 📊 Статус бота (Владелец)""",
        "help_format_story_now": "Создать историю за сегодня",
        "help_format_story_regen": "Пересоздать историю дня",
        "help_format_digest_now": "Создать дайджест за сегодня",
        "help_format_digest_regen": "Пересоздать дайджест дня",

        # --- Генерация (Истории / Дайджесты) ---
        "generating_status_downloading": "⏳ Скачиваю фото ({count}/{total})...",
        "generating_status_contacting_ai": "🧠 Обращаюсь к ИИ...",
        "generating_status_formatting": "✍️ Форматирую {output_format_name}...",
        "generating_now": "⏳ Генерирую {output_format_name}...",
        "generating_now_no_messages": "🤷‍♀️ Нет сообщений для {output_format_name} за сегодня.",
        "generation_failed_user_friendly": "😔 Не удалось создать {output_format_name}: {reason}.",
        "generation_failed_no_reason": "😕 Не удалось создать {output_format_name}.",
        "story_ready_header": "✨ <b>{output_format_name_capital} дня (по запросу)</b>{photo_info}:\n",
        "story_sent": "{output_format_name_capital} отправлена.",
        "regenerate_no_data": "🤷‍♀️ Нечего перегенерировать.",
        "regenerating": "⏳ Пересоздаю {output_format_name}...",
        "daily_story_header": "📅 <b>{output_format_name_capital} за {date_str} в чате {chat_title}</b> ✨\n{photo_info}\n" + "-"*20 + "\n",
        "daily_job_failed_chat_user_friendly": "😔 Сегодня не удалось создать {output_format_name} ({reason}).",
        "photo_info_text": " <i>(с анализом до {count} фото)</i>",
        "output_format_name_story": "историю", "output_format_name_digest": "дайджест", # В винительном падеже
        "output_format_name_story_capital": "История", "output_format_name_digest_capital": "Дайджест", # Именительный

        # --- Саммари (для /summarize) ---
        "summarize_prompt_period": "📝 Выберите период для краткой выжимки:",
        "summarize_button_today": "За сегодня", "summarize_button_last_1h": "За час", "summarize_button_last_3h": "За 3 часа", "summarize_button_last_24h": "За 24 часа",
        "summarize_generating": "⏳ Готовлю выжимку...",
        "summarize_no_messages": "🤷‍♀️ Нет сообщений для выжимки за этот период.",
        "summarize_header": "📝 <b>Краткая выжимка: {period_name}</b>\n" + "-"*20 + "\n",
        "summarize_failed_user_friendly": "😔 Не удалось создать выжимку ({reason}).",
        "summarize_period_name_today": "сегодня", "summarize_period_name_last_1h": "последний час",
        "summarize_period_name_last_3h": "последние 3 часа", "summarize_period_name_last_24h": "последние 24 часа",

        # --- Ошибки Прокси/Сети (user-friendly) ---
        "proxy_note": "ℹ️ <i>Примечание: {note}</i>",
        "error_proxy_generic": "Сервис ИИ временно недоступен",
        "error_proxy_timeout": "Сервис ИИ не ответил вовремя",
        "error_proxy_connect": "Ошибка сети при подключении к сервису ИИ",
        "error_proxy_safety": "Запрос заблокирован настройками безопасности контента ИИ",
        "error_proxy_config_user": "Критическая ошибка конфигурации бота",
        "error_proxy_unknown_user": "Неизвестная ошибка сервиса ИИ",
        "error_proxy_empty_response": "Сервис ИИ вернул пустой ответ",

        # --- Настройки (Общие строки и лейблы) ---
        "settings_title": "⚙️ <b>Настройки Летописца ({chat_title})</b>",
        "settings_status_label": "Статус",
        "settings_language_label": "Язык",
        "settings_time_label": "Время",
        "settings_timezone_label": "Пояс",
        "settings_genre_label": "Жанр",
        "settings_personality_label": "Личность",
        "settings_output_format_label": "Формат",
        "settings_retention_label": "Хранение",
        "settings_interventions_label": "Вмешательства",
        "settings_time_custom": "{custom_time} (ваше)", # Отображает время вида "HH:MM TZ"
        "settings_time_default": "~{default_local_time} (стандарт)", # Отображает локальное стандартное время
        "settings_button_change": "Изменить",
        "settings_button_toggle_on": "❌ Выключить Летописца",
        "settings_button_toggle_off": "✅ Включить Летописца",
        "settings_saved_popup": "✅ Сохранено!",
        "retention_days_0": "Бессрочно", # Остается для логики, но не в кнопках
        "retention_days_7": "7 дней",     # Новая опция
        "retention_days_14": "14 дней",   # Новая опция
        "retention_days_N": "{N} дн.",
        "intervention_state_enabled": "Разрешены",
        "intervention_state_disabled": "Запрещены",
        "settings_interventions_enabled": "✅ Разрешены", # Для главного меню
        "settings_interventions_disabled": "❌ Запрещены", # Для главного меню

        # Подменю Языка
        "settings_select_language_title": "🌐 Выберите язык:",
        "settings_lang_selected": "✅ Язык изменен!",

        # Подменю Времени
        "settings_select_time_title": "⏰ <b>Настройка времени генерации</b>",
        "settings_time_current": "Текущее: {current_time_display}",
        "settings_time_prompt": "Отправьте новое время в формате <b>ЧЧ:ММ</b> (24ч) для вашего часового пояса (<b>{chat_timezone}</b>), или сбросьте на стандартное.",
        "settings_time_invalid_format": "❌ Неверный формат. Введите время как <b>ЧЧ:ММ</b> (например, <code>09:00</code>).",
        "settings_time_success": "✅ Время генерации установлено: {local_time} {tz_short} ({utc_time} UTC).",
        "settings_time_reset_success": "✅ Время генерации сброшено на стандартное (~{local_default_time}).",
        "settings_time_button_reset": "Сбросить на стандартное",
        "waiting_for_time_input": "⏳ Ожидаю ввода времени...",

        # Подменю Таймзоны
        "settings_select_timezone_title": "🌍 Выберите ваш часовой пояс:",
        "settings_tz_selected": "✅ Часовой пояс изменен!",

        # Подменю Жанра
        "settings_select_genre_title": "🎭 Выберите жанр для историй:",
        "settings_genre_selected": "✅ Жанр изменен!",

        # Подменю Личности
        "settings_select_personality_title": "👤 Выберите личность Летописца:",
        "settings_personality_selected": "✅ Личность изменена!",

        # Подменю Формата Вывода
        "settings_select_output_format_title": "📜 Выберите формат ежедневной сводки:",
        "settings_format_selected": "✅ Формат изменен!",

        # Подменю Срока Хранения (Изменен заголовок)
        "settings_select_retention_title": "💾 Срок хранения сообщений (выберите период):",
        "settings_retention_selected": "✅ Срок хранения изменен!",
        "settings_retention_button_days": "{days_text}", # Используется для всех опций дней

        # Подменю Вмешательств (Обновлено)
        "settings_interventions_title": "🤖 Настройка Вмешательств Летописца",
        "settings_interventions_description": "Здесь можно настроить, как часто и при каких условиях Летописец будет оставлять свои комментарии в чате.",
        "settings_button_toggle_interventions_on": "❌ Запретить Вмешательства",
        "settings_button_toggle_interventions_off": "✅ Разрешить Вмешательства",
        "settings_intervention_cooldown_label": "Интервал (пауза)",
        "settings_intervention_min_msgs_label": "Минимум сообщений",
        "settings_intervention_timespan_label": "Окно активности",
        "settings_intervention_current_value": "Текущее: <b>{value}</b>\n<i>(Пределы: {min_val}-{max_val} | Стандарт: {def_val})</i>",
        "settings_interventions_change_hint": "Выберите новое значение:",
        "settings_intervention_owner_note": "\n\n👑 <i>Как владелец, вы видите доп. опции для более частого интервала.</i>",
        "settings_interventions_saved_popup": "✅ Настройки вмешательств сохранены!",
        "settings_intervention_btn_cooldown": "{minutes} мин",
        "settings_intervention_btn_msgs": "{count} сообщ.",
        "settings_intervention_btn_timespan": "{minutes} мин",

        # Названия для локализации
        "genre_name_default": "Стандартный", "genre_name_humor": "Юмористический", "genre_name_detective": "Детективный", "genre_name_fantasy": "Фэнтезийный", "genre_name_news_report": "Новостной репортаж",
        "personality_name_neutral": "Нейтральный", "personality_name_wise": "Мудрый Старец", "personality_name_sarcastic": "Саркастичный Наблюдатель", "personality_name_poet": "Поэт-Романтик",
        "output_format_name_story": "История", "output_format_name_digest": "Дайджест", # Именительный падеж

        # Статус
        "status_command_reply": "<b>📊 Статус Бота</b>\nUptime: {uptime}\nАктивных чатов: {active_chats}\nПосл. запуск сводок: {last_job_run}\nПосл. ошибка сводок: <i>{last_job_error}</i>\nПосл. запуск очистки: {last_purge_run}\nПосл. ошибка очистки: <i>{last_purge_error}</i>\nВерсия PTB: {ptb_version}",

        # Очистка Истории
        "purge_prompt": "🗑️ Вы уверены, что хотите удалить сообщения?\nПериод: <b>{period_text}</b>\n\n<b>Это действие необратимо!</b>",
        "purge_period_all": "Вся история чата",
        "purge_period_days": "Старше {days} дней",
        "purge_period_days_7": "Старше 7 дней",   # Новый текст
        "purge_period_days_14": "Старше 14 дней", # Новый текст
        "purge_confirm": "Да, удалить",
        "purge_cancel": "Отмена",
        "purge_success": "✅ История чата успешно очищена (период: {period_text}).",
        "purge_no_args": "Укажите период: <code>/purge_history all</code> или <code>/purge_history days N</code> (где N - число дней).",
        "purge_invalid_days": "Укажите корректное число дней (N > 0).",
        "purge_cancelled": "Очистка истории отменена.",
        "purge_error": "😔 Не удалось очистить историю.",

        # Статистика Чата
        "stats_prompt_period": "📈 Выберите период для просмотра статистики:",
        "stats_button_today": "Сегодня", "stats_button_week": "Неделя", "stats_button_month": "Месяц",
        "stats_title": "📊 <b>Статистика Активности ({period_name})</b>",
        "stats_total_messages": "Всего сообщений: <b>{count}</b>",
        "stats_photos": "Изображений: {count}",
        "stats_stickers": "Стикеров: {count}",
        "stats_active_users": "Активных участников: <b>{count}</b>", # Добавил
        "stats_top_users_header": "Топ-3 участников по сообщениям:",
        "stats_user_entry": "  - {username}: {count}",
        "stats_no_data": "🤷‍♀️ Нет данных для статистики за этот период.",
        "stats_error": "😔 Не удалось собрать статистику.",
        "stats_period_name_today": "Сегодня", "stats_period_name_week": "Неделя", "stats_period_name_month": "Месяц",

        # Команды
        "cmd_start_desc": "👋 Статус и приветствие",
        "cmd_help_desc": "❓ Помощь по командам",
        "cmd_generate_now_desc": "✍️ Сводка за сегодня (История/Дайджест)",
        "cmd_regenerate_desc": "🔄 Пересоздать сводку дня",
        "cmd_summarize_desc": "📝 Краткая выжимка чата",
        "cmd_story_settings_desc": "⚙️ Настройки Летописца (Админ)",
        "cmd_chat_stats_desc": "📈 Статистика активности чата",
        "cmd_purge_history_desc": "🗑️ Очистить историю сообщений (Админ)",
        "cmd_status_desc": "📊 Статус бота (Владелец)",
    },
    "en": {
        # --- General ---
        "lang_name": "English 🇬🇧", "private_chat": "private chat", "admin_only": "🔐 Admins only.", "owner_only": "🔐 Owner only.",
        "error_db_generic": "😔 Database error.", "error_telegram": "😔 Telegram error: {error}.", "error_unexpected_send": "😔 Sending error.",
        "error_admin_check": "😔 Rights check error.", "feedback_thanks": "👍 Thanks for feedback!", "button_back": "⬅️ Back", "button_close": "❌ Close",
        "action_cancelled": "Action cancelled.", "error_value_corrected": "⚠️ Value adjusted to {corrected_value} (Limits: {min_val}-{max_val})",
        "enabled_status": "✅ Enabled", "disabled_status": "❌ Disabled",

        # --- Start & Help ---
        "start_message": "Hello, {user_mention}! I'm the <b>Chronicler</b> 📜 ({personality}) for chat <i>'{chat_title}'</i>.\n{format_desc} ~ at {schedule_time} ({schedule_tz}).\nStatus: {status}\n\nCommands: /help, /story_settings",
        "start_format_desc_story": "Daily story generated", "start_format_desc_digest": "Daily digest generated",
        "help_message": """<b>I'm the AI Chronicler {personality_name}!</b> 🧐

Analyzing chat, creating unique summaries.

<b>🤖 Core Commands:</b>
<code>/start</code> - Greeting & status
<code>/help</code> - This help
<code>/story_settings</code> - ⚙️ Chronicler Settings (Admin)

<b>✍️ Generation:</b>
<code>/generate_now</code> - {current_format_action_now}
<code>/regenerate_story</code> - {current_format_action_regen}
<code>/summarize</code> - 📝 Create brief chat summary

<b>📊 Analytics:</b>
<code>/chat_stats</code> - 📈 Chat activity statistics

<b>🛠️ Administration:</b>
<code>/purge_history</code> - 🗑️ Purge message history (Admin)

<b>ℹ️ Information:</b>
<code>/status</code> - 📊 Bot status (Owner)""",
        "help_format_story_now": "Generate today's story", "help_format_story_regen": "Regenerate today's story",
        "help_format_digest_now": "Generate today's digest", "help_format_digest_regen": "Regenerate today's digest",

        # --- Generation (Story / Digest) ---
        "generating_status_downloading": "⏳ Downloading photos ({count}/{total})...", "generating_status_contacting_ai": "🧠 Contacting AI...", "generating_status_formatting": "✍️ Formatting {output_format_name}...",
        "generating_now": "⏳ Generating {output_format_name}...", "generating_now_no_messages": "🤷‍♀️ No messages for {output_format_name} today.",
        "generation_failed_user_friendly": "😔 Failed to create {output_format_name}: {reason}.", "generation_failed_no_reason": "😕 Failed to create {output_format_name}.",
        "story_ready_header": "✨ <b>{output_format_name_capital} of the day (on request)</b>{photo_info}:\n",
        "story_sent": "{output_format_name_capital} sent.", "regenerate_no_data": "🤷‍♀️ Nothing to regenerate.", "regenerating": "⏳ Regenerating {output_format_name}...",
        "daily_story_header": "📅 <b>{output_format_name_capital} for {date_str} in {chat_title}</b> ✨\n{photo_info}\n" + "-"*20 + "\n",
        "daily_job_failed_chat_user_friendly": "😔 Failed to create today's {output_format_name} ({reason}).",
        "photo_info_text": " <i>(analyzed up to {count} photos)</i>",
        "output_format_name_story": "story", "output_format_name_digest": "digest",
        "output_format_name_story_capital": "Story", "output_format_name_digest_capital": "Digest",

        # --- Summary (for /summarize) ---
        "summarize_prompt_period": "📝 Select period for summary:",
        "summarize_button_today": "Today", "summarize_button_last_1h": "Last hour", "summarize_button_last_3h": "Last 3h", "summarize_button_last_24h": "Last 24h",
        "summarize_generating": "⏳ Preparing summary...", "summarize_no_messages": "🤷‍♀️ No messages for summary.",
        "summarize_header": "📝 <b>Summary: {period_name}</b>\n" + "-"*20 + "\n", "summarize_failed_user_friendly": "😔 Failed to create summary ({reason}).",
        "summarize_period_name_today": "Today", "summarize_period_name_last_1h": "Last hour", "summarize_period_name_last_3h": "Last 3 hours", "summarize_period_name_last_24h": "Last 24 hours",

        # --- Proxy/Network Errors (user-friendly) ---
        "proxy_note": "ℹ️ <i>Note: {note}</i>", "error_proxy_generic": "AI service unavailable", "error_proxy_timeout": "AI service timed out",
        "error_proxy_connect": "Network error (AI)", "error_proxy_safety": "Blocked by AI safety", "error_proxy_config_user": "Configuration error",
        "error_proxy_unknown_user": "Unknown AI error", "error_proxy_empty_response": "AI service returned empty response",

        # --- Settings ---
        "settings_title": "⚙️ <b>Chronicler Settings ({chat_title})</b>",
        "settings_status_label": "Status", "settings_language_label": "Language", "settings_time_label": "Time", "settings_timezone_label": "Zone",
        "settings_genre_label": "Genre", "settings_personality_label": "Personality", "settings_output_format_label": "Format",
        "settings_retention_label": "Retention", "settings_interventions_label": "Interventions",
        "settings_time_custom": "{custom_time} (yours)", "settings_time_default": "~{default_local_time} (default)",
        "settings_button_change": "Change", "settings_button_toggle_on": "❌ Disable Chronicler", "settings_button_toggle_off": "✅ Enable Chronicler",
        "settings_saved_popup": "✅ Saved!",
        "retention_days_0": "Forever", # Remains for logic, not buttons
        "retention_days_7": "7 days",   # New option
        "retention_days_14": "14 days", # New option
        "retention_days_N": "{N} days",
        "intervention_state_enabled": "Allowed",
        "intervention_state_disabled": "Forbidden",
        "settings_interventions_enabled": "✅ Allowed", # For main menu
        "settings_interventions_disabled": "❌ Forbidden", # For main menu

        # Submenus...
        "settings_select_language_title": "🌐 Select language:", "settings_lang_selected": "✅ Language changed!",
        "settings_select_time_title": "⏰ <b>Generation Time</b>", "settings_time_current": "Current: {current_time_display}",
        "settings_time_prompt": "Enter time (HH:MM) for {chat_timezone} or reset.", "settings_time_invalid_format": "❌ Invalid HH:MM format",
        "settings_time_success": "✅ Time set: {local_time} {tz_short} ({utc_time} UTC)", "settings_time_reset_success": "✅ Time reset (~{local_default_time})",
        "settings_time_button_reset": "Reset to Default", "waiting_for_time_input": "⏳ Waiting for time input...",
        "settings_select_timezone_title": "🌍 Select timezone:", "settings_tz_selected": "✅ Timezone changed!",
        "settings_select_genre_title": "🎭 Select genre:", "settings_genre_selected": "✅ Genre changed!",
        "settings_select_personality_title": "👤 Select Personality:", "settings_personality_selected": "✅ Personality changed!",
        "settings_select_output_format_title": "📜 Select Output Format:", "settings_format_selected": "✅ Format changed!",

        # Retention Submenu (Title changed)
        "settings_select_retention_title": "💾 Message Retention Period (select period):",
        "settings_retention_selected": "✅ Retention changed!",
        "settings_retention_button_days": "{days_text}", # Used for all day options

        # Interventions Submenu (Updated)
        "settings_interventions_title": "🤖 Chronicler Intervention Settings",
        "settings_interventions_description": "Configure how often and under what conditions the Chronicler comments in the chat.",
        "settings_button_toggle_interventions_on": "❌ Forbid Interventions",
        "settings_button_toggle_interventions_off": "✅ Allow Interventions",
        "settings_intervention_cooldown_label": "Interval (Pause)",
        "settings_intervention_min_msgs_label": "Minimum Messages",
        "settings_intervention_timespan_label": "Activity Window",
        "settings_intervention_current_value": "Current: <b>{value}</b>\n<i>(Range: {min_val}-{max_val} | Default: {def_val})</i>",
        "settings_interventions_change_hint": "Select a new value:",
        "settings_intervention_owner_note": "\n\n👑 <i>As the owner, you see extra options for more frequent intervals.</i>",
        "settings_interventions_saved_popup": "✅ Intervention settings saved!",
        "settings_intervention_btn_cooldown": "{minutes} min",
        "settings_intervention_btn_msgs": "{count} msgs",
        "settings_intervention_btn_timespan": "{minutes} min",

        # Names for localization
        "genre_name_default": "Standard", "genre_name_humor": "Humorous", "genre_name_detective": "Detective", "genre_name_fantasy": "Fantasy", "genre_name_news_report": "News Report",
        "personality_name_neutral": "Neutral", "personality_name_wise": "Wise Elder", "personality_name_sarcastic": "Sarcastic Observer", "personality_name_poet": "Romantic Poet",
        "output_format_name_story": "Story", "output_format_name_digest": "Digest",

        # Status
        "status_command_reply": "<b>📊 Bot Status</b>\nUptime: {uptime}\nActive Chats: {active_chats}\nLast Summary Run: {last_job_run}\nLast Summary Error: <i>{last_job_error}</i>\nLast Purge Run: {last_purge_run}\nLast Purge Error: <i>{last_purge_error}</i>\nPTB Version: {ptb_version}",

        # Purge History
        "purge_prompt": "🗑️ Are you sure you want to purge messages?\nPeriod: <b>{period_text}</b>\n\n<b>This action is irreversible!</b>",
        "purge_period_all": "All chat history",
        "purge_period_days": "Older than {days} days",
        "purge_period_days_7": "Older than 7 days",   # New text
        "purge_period_days_14": "Older than 14 days", # New text
        "purge_confirm": "Yes, Purge",
        "purge_cancel": "Cancel",
        "purge_success": "✅ Chat history successfully purged (Period: {period_text}).",
        "purge_no_args": "Specify period: <code>/purge_history all</code> or <code>/purge_history days N</code> (where N is days).",
        "purge_invalid_days": "Please specify a valid number of days (N > 0).",
        "purge_cancelled": "History purge cancelled.",
        "purge_error": "😔 Failed to purge history.",

        # Chat Stats
        "stats_prompt_period": "📈 Select period for statistics:",
        "stats_button_today": "Today", "stats_button_week": "Week", "stats_button_month": "Month",
        "stats_title": "📊 <b>Activity Statistics ({period_name})</b>",
        "stats_total_messages": "Total Messages: <b>{count}</b>",
        "stats_photos": "Photos: {count}",
        "stats_stickers": "Stickers: {count}",
        "stats_active_users": "Active Users: <b>{count}</b>", # Added
        "stats_top_users_header": "Top-3 Users by Messages:",
        "stats_user_entry": "  - {username}: {count}",
        "stats_no_data": "🤷‍♀️ No data available for statistics in this period.",
        "stats_error": "😔 Could not retrieve statistics.",
        "stats_period_name_today": "Today", "stats_period_name_week": "This Week", "stats_period_name_month": "This Month",

        # Commands
        "cmd_start_desc": "👋 Status & greeting",
        "cmd_help_desc": "❓ Help",
        "cmd_generate_now_desc": "✍️ Today's summary (Story/Digest)",
        "cmd_regenerate_desc": "🔄 Regenerate day's summary",
        "cmd_summarize_desc": "📝 Brief chat summary",
        "cmd_story_settings_desc": "⚙️ Chronicler Settings (Admin)",
        "cmd_chat_stats_desc": "📈 Chat activity statistics",
        "cmd_purge_history_desc": "🗑️ Purge message history (Admin)",
        "cmd_status_desc": "📊 Bot status (Owner)",
    }
}

# =======================================
# Функции работы с локализацией
# =======================================
async def get_chat_lang(chat_id: int) -> str:
    """Получает язык чата из кэша или БД (асинхронно)."""
    if chat_id in chat_language_cache:
        return chat_language_cache[chat_id]
    lang = DEFAULT_LANGUAGE # Значение по умолчанию
    try: 
        import data_manager as dm; settings = dm.get_chat_settings(chat_id); lang_from_db = settings.get('lang')
        if lang_from_db and lang_from_db in SUPPORTED_LANGUAGES: lang = lang_from_db
    except Exception as e: logger.error(f"Error get lang chat={chat_id}: {e}")
    chat_language_cache[chat_id] = lang; return lang

def update_chat_lang_cache(chat_id: int, lang: str):
    """Обновляет кэш языка чата."""
    if lang in SUPPORTED_LANGUAGES: chat_language_cache[chat_id] = lang
    else: logger.warning(f"Attempted cache unsupported lang '{lang}' chat={chat_id}")

def get_text(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Возвращает локализованный текст по ключу с форматированием."""
    effective_lang = lang if lang and lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    text_template = LOCALIZED_TEXTS.get(effective_lang, {}).get(key) or LOCALIZED_TEXTS.get(DEFAULT_LANGUAGE, {}).get(key)
    if text_template is None: logger.warning(f"Loc key '[{key}]' not found lang='{effective_lang}'"); return f"[{key}]"
    try: return text_template.format(**kwargs)
    except KeyError as e: logger.warning(f"Missing format key '{e}' text='{key}' lang='{effective_lang}' args={kwargs}"); return text_template
    except Exception as e: logger.error(f"Error formatting key='{key}' lang='{effective_lang}': {e}", exc_info=True); return f"[Fmt Err: {key}]"

def get_period_name(period_key: str, lang: str) -> str:
    """Возвращает имя периода для /summarize."""
    return get_text(f"summarize_period_name_{period_key}", lang)

def get_stats_period_name(period_key: str, lang: str) -> str:
     """Возвращает имя периода для /chat_stats."""
     return get_text(f"stats_period_name_{period_key}", lang)

def get_user_friendly_proxy_error(error_message: Optional[str], lang: str) -> str:
    """Преобразует техническую ошибку от прокси/ИИ в понятное сообщение."""
    if not error_message: return get_text("error_proxy_unknown_user", lang)
    error_lower = error_message.lower()
    if "safety settings" in error_lower or "blocked" in error_lower: return get_text("error_proxy_safety", lang)
    if "timeout" in error_lower: return get_text("error_proxy_timeout", lang)
    if any(sub in error_lower for sub in ["network", "connection", "502", "503", "504"]): return get_text("error_proxy_connect", lang)
    if "proxy url or auth token" in error_lower: return get_text("error_proxy_config_user", lang)
    if "429" in error_lower: return get_text("error_proxy_generic", lang) + " (too many requests)"
    if any(sub in error_lower for sub in ["invalid", "bad request", "400"]): return get_text("error_proxy_generic", lang) + " (bad request)"
    if "empty successful response" in error_lower or "missing text part" in error_lower : return get_text("error_proxy_empty_response", lang)
    return get_text("error_proxy_generic", lang) # Общая ошибка

def get_genre_name(genre_key: str, lang: str) -> str:
    """Возвращает локализованное имя жанра."""
    if genre_key not in SUPPORTED_GENRES: genre_key = 'default'
    return get_text(f"genre_name_{genre_key}", lang)

def get_personality_name(personality_key: str, lang: str) -> str:
    """Возвращает локализованное имя личности."""
    if personality_key not in SUPPORTED_PERSONALITIES: personality_key = DEFAULT_PERSONALITY
    return get_text(f"personality_name_{personality_key}", lang)

def get_output_format_name(format_key: str, lang: str, capital: bool = False) -> str:
    """Возвращает локализованное имя формата вывода."""
    if format_key not in SUPPORTED_OUTPUT_FORMATS: format_key = DEFAULT_OUTPUT_FORMAT
    loc_key = f"output_format_name_{format_key}"
    # Используем специальный ключ для формы винительного падежа ("историю", "дайджест")
    if not capital: pass # Для русского языка базовая форма в LOCALIZED_TEXTS - винительный падеж
    else: loc_key += "_capital" # Для заголовков используем именительный
    return get_text(loc_key, lang)

def format_retention_days(days: Optional[int], lang: str) -> str:
    """Форматирует срок хранения для отображения."""
    if days is None or days <= 0:
        # Используем ключ 'retention_days_0' для "Бессрочно"/"Forever"
        return get_text("retention_days_0", lang)
    elif days in [7, 14]: # Добавляем обработку новых периодов
        # Используем ключи 'retention_days_7', 'retention_days_14'
         return get_text(f"retention_days_{days}", lang)
    else:
        # Используем общий ключ 'retention_days_N' для остальных
        return get_text("retention_days_N", lang, N=days)

def get_intervention_value_limits(setting_key: str) -> Tuple[int, int, int]:
    """Возвращает кортеж (min, max, default) для настройки вмешательства."""
    if setting_key == 'intervention_cooldown_minutes': return (INTERVENTION_MIN_COOLDOWN_MIN, INTERVENTION_MAX_COOLDOWN_MIN, INTERVENTION_DEFAULT_COOLDOWN_MIN)
    elif setting_key == 'intervention_min_msgs': return (INTERVENTION_MIN_MIN_MSGS, INTERVENTION_MAX_MIN_MSGS, INTERVENTION_DEFAULT_MIN_MSGS)
    elif setting_key == 'intervention_timespan_minutes': return (INTERVENTION_MIN_TIMESPAN_MIN, INTERVENTION_MAX_TIMESPAN_MIN, INTERVENTION_DEFAULT_TIMESPAN_MIN)
    else: return (0, 99999, 0) # Fallback