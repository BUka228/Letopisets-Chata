# bot_handlers.py
import logging
import datetime
import asyncio
import time
import re
import pytz
from typing import Optional, Dict, Any, Tuple
from utils import download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner
# Импорты из Telegram
from telegram import (
    Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
    constants as tg_constants # Используем псевдоним для констант
)
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters,
    ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest

# Импорты из проекта
import data_manager as dm
import gemini_client as gc
from jobs import (
    download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner,
)
from config import (
    SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE_STR, BOT_OWNER_ID,
    SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE, COMMON_TIMEZONES # <-- Добавлено сюда
)
from localization import get_text, get_chat_lang, update_chat_lang_cache, LOCALIZED_TEXTS
from telegram import __version__ as ptb_version

logger = logging.getLogger(__name__)
# bot_start_time убран отсюда, берем из context.application.bot_data

# --- Константы для состояний ConversationHandler ---
SELECTING_LANG, AWAITING_TIME, SELECTING_TZ = map(str, range(3)) 
# --- Константы для Callback Data ---
CB_TOGGLE_STATUS = "settings_toggle_status"
CB_CHANGE_LANG = "settings_change_lang"
CB_CHANGE_TIME = "settings_change_time"
CB_CHANGE_TZ = "settings_change_tz" 
CB_SET_TIME_DEFAULT = "set_time_default"
CB_CANCEL_CONV = "conv_cancel"
CB_SHOW_SETTINGS = "show_settings" # Для кнопки в /start

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

async def is_user_admin(
    chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Проверяет, является ли пользователь администратором или создателем чата."""
    if chat_id > 0: # В личных чатах пользователь всегда "админ"
        return True
    if not context.bot:
        logger.error(f"Bot object unavailable checking admin {user_id} in {chat_id}")
        return False
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in [
            tg_constants.ChatMemberStatus.ADMINISTRATOR,
            tg_constants.ChatMemberStatus.OWNER
        ]
    except TelegramError as e:
        # Логируем частые ошибки доступа чуть тише
        if "chat not found" in str(e).lower() or "user not found" in str(e).lower():
            logger.warning(f"Could not get member status {user_id} in chat {chat_id}: {e}")
        else:
            logger.error(f"Telegram error checking admin {user_id} in chat {chat_id}: {e}")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error checking admin {user_id} in chat {chat_id}: {e}")
        return False
    
def format_time_for_chat(utc_hour: int, utc_minute: int, target_tz_str: str) -> str:
    """Конвертирует время UTC HH:MM в строку для таймзоны чата."""
    try:
        target_tz = pytz.timezone(target_tz_str)
        # Создаем datetime в UTC с произвольной датой, чтобы применить смещение
        # Важно: Может не учитывать переход на летнее/зимнее время корректно без полной даты
        # Но для отображения HH:MM обычно достаточно
        now_utc = datetime.datetime.now(pytz.utc)
        time_utc = now_utc.replace(hour=utc_hour, minute=utc_minute, second=0, microsecond=0)
        time_local = time_utc.astimezone(target_tz)
        # Возвращаем время и аббревиатуру таймзоны (может быть неоднозначной)
        return time_local.strftime(f"%H:%M %Z") # (%z для смещения +/-HHMM)
    except Exception as e:
        logger.error(f"Ошибка форматирования времени {utc_hour}:{utc_minute} для TZ {target_tz_str}: {e}")
        return f"{utc_hour:02d}:{utc_minute:02d} UTC" # Возвращаем UTC при ошибке


async def get_settings_text_and_markup(chat_id: int, chat_title: Optional[str]) -> Tuple[str, InlineKeyboardMarkup]:
    """Генерирует текст и кнопки настроек, учитывая таймзону."""
    chat_lang = await get_chat_lang(chat_id)
    settings = dm.get_chat_settings(chat_id)
    chat_tz_str = settings.get('timezone', 'UTC') # Получаем таймзону чата

    is_enabled = settings.get('enabled', True)
    current_lang = settings.get('lang', DEFAULT_LANGUAGE)
    custom_time_utc_str = settings.get('custom_schedule_time')

    status_text = get_text("settings_enabled" if is_enabled else "settings_disabled", chat_lang)
    lang_name = LOCALIZED_TEXTS.get(current_lang, {}).get("lang_name", current_lang)
    lang_text = get_text("settings_language_label", chat_lang) + f": {lang_name}"

    # --- ИЗМЕНЕНО: Форматируем время с учетом таймзоны чата ---
    if custom_time_utc_str:
        try:
            ch, cm = map(int, custom_time_utc_str.split(':'))
            local_time_str = format_time_for_chat(ch, cm, chat_tz_str)
            time_text = get_text("settings_time_custom", chat_lang, custom_time=local_time_str) + f" ({custom_time_utc_str} UTC)"
        except ValueError:
             time_text = f"{custom_time_utc_str} UTC (неверный формат!)"
    else:
        local_time_str = format_time_for_chat(SCHEDULE_HOUR, SCHEDULE_MINUTE, chat_tz_str)
        time_text = get_text(
            "settings_default_time", chat_lang,
            default_hh=f"{SCHEDULE_HOUR:02d}",
            default_mm=f"{SCHEDULE_MINUTE:02d}"
        ) + f" (UTC)" # Добавляем UTC явно
    # -----------------------------------------------------------

    # --- НОВОЕ: Отображаем текущую таймзону ---
    tz_display_name = COMMON_TIMEZONES.get(chat_tz_str, chat_tz_str) # Отображаемое имя
    timezone_text = get_text("settings_timezone_label", chat_lang) + f" {tz_display_name}"
    # -----------------------------------------

    text = (
        f"{get_text('settings_title', chat_lang, chat_title=chat_title or 'Unknown')}\n\n"
        f"▪️ {get_text('settings_status_label', chat_lang)} {status_text}\n"
        f"▪️ {lang_text}\n"
        f"▪️ {time_text}\n"
        f"▪️ {timezone_text}" # Добавили строку таймзоны
    )

    # Кнопки
    status_button_text = get_text("settings_button_status_on" if is_enabled else "settings_button_status_off", chat_lang)
    keyboard = [
        [InlineKeyboardButton(status_button_text, callback_data=CB_TOGGLE_STATUS)],
        [InlineKeyboardButton(get_text("settings_button_language", chat_lang), callback_data=CB_CHANGE_LANG)],
        [
            InlineKeyboardButton(get_text("settings_button_time", chat_lang), callback_data=CB_CHANGE_TIME),
            InlineKeyboardButton(get_text("settings_button_timezone", chat_lang), callback_data=CB_CHANGE_TZ) # Добавили кнопку TZ
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    return text, markup

async def display_settings(
    update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: Optional[int] = None
):
    """Отображает или обновляет сообщение с настройками."""
    chat = await context.bot.get_chat(chat_id) # Получаем информацию о чате
    text, markup = await get_settings_text_and_markup(chat_id, chat.title)
    try:
        if message_id: # Если нужно обновить существующее сообщение
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text,
                reply_markup=markup, parse_mode=ParseMode.HTML
            )
            logger.debug(f"Settings message {message_id} updated for chat {chat_id}")
        elif update.message: # Если вызывается командой /story_settings
            await update.message.reply_html(text, reply_markup=markup)
    except BadRequest as e:
        # Игнорируем ошибку "Message is not modified"
        if "Message is not modified" in str(e):
            logger.debug(f"Settings message not modified for chat {chat_id}")
        else:
            logger.error(f"BadRequest updating settings message for chat {chat_id}: {e}")
    except TelegramError as e:
        logger.error(f"TelegramError updating/sending settings for chat {chat_id}: {e}")

# =============================================================================
# ОБРАБОТЧИКИ КОМАНД
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код без изменений, но обновим кнопки) ...
    user = update.effective_user; chat = update.effective_chat; 
    if not user or not chat: return
    chat_lang = await get_chat_lang(chat.id); settings = dm.get_chat_settings(chat.id); status_key = "settings_enabled" if settings.get('enabled', True) else "settings_disabled"; status_text = get_text(status_key, chat_lang).split(': ')[-1]
    # --- ИЗМЕНЕНО: Используем helper для форматирования времени ---
    chat_tz = dm.get_chat_timezone(chat.id)
    default_local_time = format_time_for_chat(SCHEDULE_HOUR, SCHEDULE_MINUTE, chat_tz)
    # ----------------------------------------------------------
    logger.info(f"User {user.id} started bot in chat {chat.id}")
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ " + get_text("cmd_story_settings_desc", chat_lang), callback_data=CB_SHOW_SETTINGS)],
        [InlineKeyboardButton("🌐 " + get_text("cmd_language_desc", chat_lang), callback_data=CB_CHANGE_LANG),
         InlineKeyboardButton("🌍 " + get_text("cmd_set_timezone_desc", chat_lang), callback_data=CB_CHANGE_TZ)] # Добавили кнопку TZ
    ])
    await update.message.reply_html(
        get_text("start_message", chat_lang, user_mention=user.mention_html(), chat_title=f"<i>'{chat.title}'</i>" if chat.title else get_text('private_chat', chat_lang),
                 # --- ИЗМЕНЕНО: Показываем локальное время ---
                 schedule_time=default_local_time,
                 # -----------------------------------------
                 schedule_tz=SCHEDULE_TIMEZONE_STR, # Оставляем UTC как базовый пояс
                 status=f"<b>{status_text}</b>"),
        reply_markup=markup
    )
    # Установка команд теперь в post_init

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код без изменений, но добавляем команду /set_timezone) ...
    chat = update.effective_chat; user = update.effective_user; 
    if not chat or not user: return
    chat_lang = await get_chat_lang(chat.id); logger.debug(f"Help cmd chat={chat.id} user={user.id}")
    # --- ИЗМЕНЕНО: Форматируем время ---
    chat_tz = dm.get_chat_timezone(chat.id)
    default_local_time = format_time_for_chat(SCHEDULE_HOUR, SCHEDULE_MINUTE, chat_tz)
    # ---------------------------------
    await update.message.reply_html(
        get_text("help_message", chat_lang,
                 schedule_time=default_local_time, # Показываем локальное время
                 schedule_tz=SCHEDULE_TIMEZONE_STR)
    )

async def generate_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message:
        return

    # ... (код получения сообщений, вычисления photo_info_str, msg_count_str) ...
    chat_lang = await get_chat_lang(chat.id)
    messages_current = dm.get_messages_for_chat(chat.id)
    photo_count = sum(1 for m in messages_current if m.get('type') == 'photo')
    photo_process_limit = min(photo_count, MAX_PHOTOS_TO_ANALYZE)
    photo_info_str = get_text("photo_info_text", chat_lang, count=photo_process_limit) if photo_count > 0 else ""
    msg_count_str = str(len(messages_current))

    if context.bot:
        await context.bot.send_chat_action(chat_id=chat.id, action=tg_constants.ChatAction.TYPING)
    status_message = await update.message.reply_html(
        get_text("generating_now", chat_lang, msg_count=msg_count_str, photo_info=photo_info_str)
    )

    story = None
    error_msg = None
    sent_story_message = None # Для хранения ID сообщения с историей
    try:
        downloaded_images = await download_images(
            context, messages_current, chat.id, MAX_PHOTOS_TO_ANALYZE
        )
        prepared_content = gc.prepare_story_parts(messages_current, downloaded_images)
        story, error_msg = await gc.safe_generate_story(prepared_content)

        if story:
            header_key = "story_ready_header"
            photo_note_str_res = get_text("photo_info_text", chat_lang, count=photo_process_limit) if downloaded_images else ""
            final_message_header = get_text(header_key, chat_lang, photo_info=photo_note_str_res)

            # --- ИЗМЕНЕНО ЗДЕСЬ: Отправляем/редактируем заголовок отдельно ---
            try:
                # Пытаемся отредактировать сообщение "Анализирую..." на заголовок
                await status_message.edit_text(final_message_header, parse_mode=ParseMode.HTML)
            except BadRequest: # Если не изменилось или ошибка
                 # Отправляем заголовок новым сообщением
                 await context.bot.send_message(chat_id=chat.id, text=final_message_header, parse_mode=ParseMode.HTML)
                 # Удаляем старое сообщение "Анализирую..."
                 try: await status_message.delete()
                 except Exception: pass # Игнорируем, если не удалось удалить
            # ---------------------------------------------------------------

            # --- Отправляем историю с кнопками (как отдельное сообщение) ---
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"),
                InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")
            ]])
            MAX_MSG_LEN = 4096
            if len(story) > MAX_MSG_LEN: # Разбиваем только историю
                logger.warning(f"/generate_now story too long chat={chat.id}, splitting.")
                parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                for k, part in enumerate(parts):
                    current_reply_markup = keyboard if k == len(parts) - 1 else None
                    # Отправляем часть истории как Markdown
                    sent_story_message = await context.bot.send_message(
                        chat_id=chat.id, text=part,
                        reply_markup=current_reply_markup,
                        parse_mode=ParseMode.MARKDOWN # Используем Markdown v1
                    )
                    await asyncio.sleep(0.5)
            else:
                # Отправляем историю одним сообщением как Markdown
                sent_story_message = await context.bot.send_message(
                    chat_id=chat.id, text=story,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN # Используем Markdown v1
                )

            # Обновляем кнопки с ID сообщения истории
            if sent_story_message:
                 keyboard_updated = InlineKeyboardMarkup([[
                     InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_story_message.message_id}"),
                     InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_story_message.message_id}")
                 ]])
                 try:
                     await context.bot.edit_message_reply_markup(
                         chat_id=chat.id, message_id=sent_story_message.message_id,
                         reply_markup=keyboard_updated
                     )
                 except BadRequest: pass # Игнорируем, если кнопки уже нажаты/удалены
                 except TelegramError as e: logger.warning(f"Error updating feedback buttons: {e}")
            # ---------------------------------------------------------------

            logger.info(f"Story sent chat={chat.id}")
            if error_msg:
                try: await context.bot.send_message(chat_id=chat.id, text=get_text("proxy_note", chat_lang, note=error_msg))
                except Exception as e_note: logger.warning(f"Failed proxy note: {e_note}")

        else: # Если story is None (ошибка генерации)
            logger.warning(f"Failed gen story chat={chat.id}. Reason: {error_msg}")
            await status_message.edit_text(
                get_text("generation_failed", chat_lang, error=error_msg or 'Unknown'),
                parse_mode=ParseMode.HTML # Ошибка в HTML
            )
    except Exception as e:
        logger.exception(f"General error in /generate_now chat={chat.id}: {e}")
        try:
             await status_message.edit_text(
                 get_text("error_db_generic", chat_lang), parse_mode=ParseMode.HTML
             )
        except Exception as edit_e: logger.error(f"Failed to edit status message on error: {edit_e}")


async def regenerate_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message:
        return

    chat_lang = await get_chat_lang(chat.id)
    logger.info(f"User {user.username} /regenerate_story chat={chat.id}")

    messages_current = dm.get_messages_for_chat(chat.id)
    if not messages_current:
        await update.message.reply_html(get_text("regenerate_no_data", chat_lang))
        return

    photo_count = sum(1 for m in messages_current if m.get('type') == 'photo')
    photo_process_limit = min(photo_count, MAX_PHOTOS_TO_ANALYZE)

    if context.bot:
        await context.bot.send_chat_action(chat_id=chat.id, action=tg_constants.ChatAction.TYPING)
    status_message = await update.message.reply_html(get_text("regenerating", chat_lang))

    story = None
    error_msg = None
    sent_story_message = None
    try:
        downloaded_images = await download_images(
            context, messages_current, chat.id, MAX_PHOTOS_TO_ANALYZE
        )
        photo_note_str = get_text(
            "photo_info_text", chat_lang, count=photo_process_limit
        ) if downloaded_images else ""
        prepared_content = gc.prepare_story_parts(messages_current, downloaded_images)
        story, error_msg = await gc.safe_generate_story(prepared_content)

        # Удаляем сообщение "Регенерирую..."
        try: await status_message.delete()
        except Exception as e: logger.warning(f"Could not delete 'regenerating' message: {e}")

        if story:
            header_key = "story_ready_header"
            final_message_header = get_text(header_key, chat_lang, photo_info=photo_note_str)

            # --- ИЗМЕНЕНО: Отправляем заголовок отдельно ---
            await update.message.reply_html(final_message_header)
            # -------------------------------------------

            # --- Отправляем историю с кнопками ---
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"),
                InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")
            ]])
            MAX_MSG_LEN = 4096
            if len(story) > MAX_MSG_LEN: # Разбиваем только историю
                logger.warning(f"/regenerate_story story too long chat={chat.id}, splitting.")
                parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                for k, part in enumerate(parts):
                    current_reply_markup = keyboard if k == len(parts) - 1 else None
                    sent_story_message = await context.bot.send_message(
                        chat_id=chat.id, text=part,
                        reply_markup=current_reply_markup,
                        parse_mode=ParseMode.MARKDOWN # Markdown v1
                    )
                    await asyncio.sleep(0.5)
            else:
                sent_story_message = await context.bot.send_message(
                    chat_id=chat.id, text=story,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN # Markdown v1
                )

            # Обновляем кнопки
            if sent_story_message:
                 keyboard_updated = InlineKeyboardMarkup([[
                     InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_story_message.message_id}"),
                     InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_story_message.message_id}")
                 ]])
                 try:
                     await context.bot.edit_message_reply_markup(
                         chat_id=chat.id, message_id=sent_story_message.message_id, reply_markup=keyboard_updated
                     )
                 except BadRequest: pass
                 except TelegramError as e: logger.warning(f"Error updating reply markup in regenerate: {e}")

            if error_msg:
                await context.bot.send_message(
                    chat_id=chat.id, text=get_text("proxy_note", chat_lang, note=error_msg)
                )
        else: # Если история не сгенерировалась
            await update.message.reply_html( # Отвечаем на исходное сообщение /regenerate_story
                get_text("generation_failed", chat_lang, error=error_msg or 'Unknown')
            )
    except Exception as e:
        logger.exception(f"Error in /regenerate_story chat={chat.id}: {e}")
        # Отвечаем на исходное сообщение /regenerate_story
        await update.message.reply_html(get_text("error_db_generic", chat_lang))

async def ask_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Начинает диалог выбора таймзоны (entry point)."""
    query = update.callback_query
    user = update.effective_user or (query.from_user if query else None)
    chat = update.effective_chat or (query.message.chat if query and query.message else None)
    if not user or not chat: return ConversationHandler.END
    context.user_data['conv_type'] = 'tz' # Помечаем тип диалога

    chat_lang = await get_chat_lang(chat.id)

    # Проверка прав админа (если вызвано кнопкой)
    if query:
        await query.answer()
        is_admin = await is_user_admin(chat.id, user.id, context)
        if not is_admin:
            await query.edit_message_text(get_text("admin_only", chat_lang), reply_markup=None)
            return ConversationHandler.END

    # Формируем кнопки из COMMON_TIMEZONES
    buttons = []
    # Сортируем по отображаемому имени для удобства
    sorted_tzs = sorted(COMMON_TIMEZONES.items(), key=lambda item: item[1])
    for tz_id, tz_name in sorted_tzs:
        buttons.append([InlineKeyboardButton(tz_name, callback_data=f"conv_settz_{tz_id}")])
    buttons.append([InlineKeyboardButton("🚫 " + get_text("timezone_set_cancel", chat_lang), callback_data=CB_CANCEL_CONV)])
    keyboard_markup = InlineKeyboardMarkup(buttons)
    text = get_text("timezone_select", chat_lang)

    if query: await query.edit_message_text(text=text, reply_markup=keyboard_markup, parse_mode=ParseMode.HTML)
    elif update.message: await update.message.reply_html(text, reply_markup=keyboard_markup) # Если вызвано командой

    return SELECTING_TZ # Переходим в состояние выбора TZ

async def set_timezone_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор таймзоны."""
    query = update.callback_query; 
    if not query or not query.message: return ConversationHandler.END
    await query.answer()
    user = query.from_user; chat = query.message.chat; 
    if not user or not chat: return ConversationHandler.END

    tz_id = query.data.split("_", 2)[-1]

    if tz_id in COMMON_TIMEZONES:
        success = dm.update_chat_setting(chat.id, 'timezone', tz_id)
        chat_lang = await get_chat_lang(chat.id) # Получаем язык ПОСЛЕ потенциального обновления
        if success:
            tz_name = COMMON_TIMEZONES[tz_id]
            await query.edit_message_text(
                text=get_text("timezone_set_success", chat_lang, tz_name=tz_name, tz_id=tz_id),
                reply_markup=None, parse_mode=ParseMode.HTML
            )
        else:
            await context.bot.send_message(chat_id=chat.id, text=get_text("error_db_generic", chat_lang))
            try: await query.edit_message_reply_markup(reply_markup=None)
            except BadRequest: pass
    else:
        await query.answer(text="Invalid timezone selected.", show_alert=True)

    context.user_data.pop('conv_type', None)
    return ConversationHandler.END

async def story_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для показа кнопок настроек (только админы)."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message or chat.type == tg_constants.ChatType.PRIVATE:
        return

    is_admin = await is_user_admin(chat.id, user.id, context)
    chat_lang = await get_chat_lang(chat.id)
    if not is_admin:
        await update.message.reply_html(get_text("admin_only", chat_lang))
        return

    await display_settings(update, context, chat.id) # Используем вспомогательную функцию

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статус бота (только владелец)."""
    user = update.effective_user
    if not user or user.id != BOT_OWNER_ID:
        # Владельцу не отвечаем ничего, если это не он
        return

    # Получаем данные из bot_data
    bot_start_time = context.application.bot_data.get('bot_start_time', time.time())
    last_run_time = context.application.bot_data.get('last_job_run_time')
    last_err = context.application.bot_data.get('last_job_error')

    uptime_seconds = time.time() - bot_start_time
    uptime_str = str(datetime.timedelta(seconds=int(uptime_seconds)))
    active_chats_list = dm.get_enabled_chats() # Список включенных чатов
    last_run_str = last_run_time.isoformat(sep=' ', timespec='seconds') + " UTC" if last_run_time else "Never"
    last_error_str = last_err if last_err else "None"

    status_text = get_text(
        "status_command_reply", DEFAULT_LANGUAGE, # Статус всегда на языке по умолчанию
        uptime=uptime_str,
        active_chats=len(active_chats_list),
        last_job_run=last_run_str,
        last_job_error=last_error_str,
        ptb_version=ptb_version
    )
    await update.message.reply_html(status_text)

# =============================================================================
# CONVERSATION HANDLERS (ДЛЯ ЯЗЫКА И ВРЕМЕНИ)
# =============================================================================

# --- Диалог выбора языка ---
async def ask_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Начинает диалог выбора языка (entry point)."""
    query = update.callback_query
    user = update.effective_user or (query.from_user if query else None)
    chat = update.effective_chat or (query.message.chat if query and query.message else None)
    if not user or not chat: return ConversationHandler.END
    context.user_data['conv_type'] = 'lang' # Помечаем тип диалога

    chat_lang = await get_chat_lang(chat.id)
    buttons = [
        [InlineKeyboardButton(
            LOCALIZED_TEXTS.get(lc, {}).get("lang_name", lc),
            callback_data=f"conv_setlang_{lc}"
        )] for lc in SUPPORTED_LANGUAGES
    ]
    buttons.append([InlineKeyboardButton(
        "🚫 " + get_text("set_language_cancel", chat_lang),
        callback_data=CB_CANCEL_CONV # Общая кнопка отмены
    )])
    keyboard_markup = InlineKeyboardMarkup(buttons)
    text = get_text("language_select", chat_lang)

    if query:
        await query.answer()
        await query.edit_message_text(text=text, reply_markup=keyboard_markup, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_html(text, reply_markup=keyboard_markup)

    return SELECTING_LANG # Переходим в состояние выбора

async def set_language_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор языка в диалоге."""
    query = update.callback_query
    if not query or not query.message: return ConversationHandler.END # Нужен query для ответа и редактирования
    await query.answer()
    user = query.from_user; chat = query.message.chat
    if not user or not chat: return ConversationHandler.END

    lang_code = query.data.split("_", 2)[-1]

    if lang_code in SUPPORTED_LANGUAGES:
        success = dm.update_chat_setting(chat.id, 'lang', lang_code)
        if success:
            update_chat_lang_cache(chat.id, lang_code) # Обновляем кэш
            await query.edit_message_text(
                text=get_text("language_set", lang_code), reply_markup=None
            )
            # Обновляем команды на новый язык
            try:
                commands = [
                    BotCommand("start", get_text("cmd_start_desc", lang_code)),
                    BotCommand("help", get_text("cmd_help_desc", lang_code)),
                    BotCommand("generate_now", get_text("cmd_generate_now_desc", lang_code)),
                    BotCommand("regenerate_story", get_text("cmd_regenerate_desc", lang_code)),
                    BotCommand("story_settings", get_text("cmd_story_settings_desc", lang_code)),
                    BotCommand("set_language", get_text("cmd_language_desc", lang_code)),
                ]
                if user.id == BOT_OWNER_ID:
                    commands.append(BotCommand("status", get_text("cmd_status_desc", lang_code)))
                if context.bot:
                    await context.bot.set_my_commands(commands)
            except Exception as e:
                logger.warning(f"Failed to update commands for lang {lang_code}: {e}")
        else:
            # Если ошибка БД, сообщаем пользователю
            current_lang = await get_chat_lang(chat.id) # Получаем текущий язык для сообщения об ошибке
            await context.bot.send_message(chat_id=chat.id, text=get_text("error_db_generic", current_lang))
            try: await query.edit_message_reply_markup(reply_markup=None) # Убираем кнопки в любом случае
            except BadRequest: pass
    else:
        await query.answer(text="Invalid language selected.", show_alert=True)

    context.user_data.pop('conv_type', None) # Очищаем состояние диалога
    return ConversationHandler.END # Завершаем диалог

# --- Диалог установки времени ---
async def ask_set_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Начинает диалог установки времени."""
    query = update.callback_query
    if not query or not query.message: return ConversationHandler.END
    user = query.from_user; chat = query.message.chat
    if not user or not chat: return ConversationHandler.END
    context.user_data['conv_type'] = 'time'
    await query.answer()
    chat_lang = await get_chat_lang(chat.id)

    is_admin = await is_user_admin(chat.id, user.id, context)
    if not is_admin:
        await query.edit_message_text(get_text("admin_only", chat_lang), reply_markup=None)
        return ConversationHandler.END

    # Получаем текущую таймзону чата для отображения в сообщении
    chat_tz_str = dm.get_chat_timezone(chat.id)
    tz_display_name = COMMON_TIMEZONES.get(chat_tz_str, chat_tz_str)
    
    default_time_text_for_button = get_text(
        "settings_default_time", chat_lang, # Правильный ключ
        default_hh=f"{SCHEDULE_HOUR:02d}",   # Правильный аргумент
        default_mm=f"{SCHEDULE_MINUTE:02d}"  # Правильный аргумент
    ).split(': ')[-1] # Берем только само время " HH:MM (стандартное)"
    

    keyboard = [
        [InlineKeyboardButton(f"⏰ {default_time_text_for_button}", callback_data=CB_SET_TIME_DEFAULT)],
        [InlineKeyboardButton("🚫 " + get_text("set_time_cancel", chat_lang), callback_data=CB_CANCEL_CONV)]
    ]
    await query.edit_message_text(
        # Передаем имя таймзоны в текст приглашения
        get_text("set_time_prompt_conv", chat_lang, chat_timezone=tz_display_name),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return AWAITING_TIME

async def set_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает введенное время, конвертирует в UTC и сохраняет."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message or not update.message.text:
        return AWAITING_TIME

    chat_lang = await get_chat_lang(chat.id)
    chat_tz_str = dm.get_chat_timezone(chat.id)
    tz_display_name = COMMON_TIMEZONES.get(chat_tz_str, chat_tz_str)
    input_time_str = update.message.text.strip() # Время, введенное пользователем

    if not re.fullmatch(r"^(?:[01]\d|2[0-3]):[0-5]\d$", input_time_str):
        await update.message.reply_html(
            get_text("set_time_invalid_format_conv", chat_lang, chat_timezone=tz_display_name)
        )
        return AWAITING_TIME # Остаемся ждать корректного ввода

    utc_time_to_save = None
    tz_short_name = chat_tz_str # Fallback для короткого имени таймзоны
    try:
        hour_local, minute_local = map(int, input_time_str.split(':'))
        local_tz = pytz.timezone(chat_tz_str)
        now_local_naive = datetime.datetime.now()
        time_local_naive = now_local_naive.replace(hour=hour_local, minute=minute_local, second=0, microsecond=0)
        time_local_aware = local_tz.localize(time_local_naive, is_dst=None)
        time_utc = time_local_aware.astimezone(pytz.utc)
        utc_time_to_save = time_utc.strftime("%H:%M")
        tz_short_name = time_local_aware.strftime('%Z') # Получаем краткое имя TZ
        logger.info(
            f"Chat {chat.id}: User input {input_time_str} ({chat_tz_str}/{tz_short_name}) "
            f"converted to {utc_time_to_save} UTC for saving."
        )
    except Exception as e:
        logger.error(f"Error converting time for chat {chat.id}: Input='{input_time_str}', TZ='{chat_tz_str}'. Error: {e}", exc_info=True)
        await update.message.reply_html(get_text("error_db_generic", chat_lang))
        return AWAITING_TIME # Остаемся ждать на всякий случай

    # --- Сохранение времени UTC в БД ---
    success = dm.update_chat_setting(chat.id, 'custom_schedule_time', utc_time_to_save)

    # --- ИСПРАВЛЕНО: Передаем правильные аргументы в get_text ---
    if success:
        text = get_text(
            "set_time_success_conv", chat_lang,
            input_time=input_time_str,        # Используем input_time_str
            chat_timezone_short=tz_short_name,# Используем tz_short_name
            utc_time=utc_time_to_save         # Используем utc_time_to_save
        )
    else:
        text = get_text("error_db_generic", chat_lang)
    # ---------------------------------------------------------

    await update.message.reply_html(text)
    context.user_data.pop('conv_type', None)
    return ConversationHandler.END

async def set_time_default_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает сброс на время по умолчанию."""
    query = update.callback_query
    if not query or not query.message: return ConversationHandler.END
    await query.answer()
    user = query.from_user; chat = query.message.chat
    if not user or not chat: return ConversationHandler.END

    chat_lang = await get_chat_lang(chat.id)
    # Получаем таймзону чата, чтобы показать время по умолчанию в ней
    chat_tz_str = dm.get_chat_timezone(chat.id)
    local_default_time_str = format_time_for_chat(SCHEDULE_HOUR, SCHEDULE_MINUTE, chat_tz_str)

    success = dm.update_chat_setting(chat.id, 'custom_schedule_time', None) # Сбрасываем время

    if success:
        text = get_text(
            "set_time_default_success_conv", chat_lang,
            local_default_time=local_default_time_str # Показываем локальное время по умолчанию
        )
    else:
        text = get_text("error_db_generic", chat_lang)

    await query.edit_message_text(text=text, reply_markup=None, parse_mode=ParseMode.HTML)
    context.user_data.pop('conv_type', None)
    return ConversationHandler.END

# --- Общая функция отмены диалога ---
async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущий диалог (установки языка или времени)."""
    query = update.callback_query
    user = update.effective_user or (query.from_user if query else None)
    chat = update.effective_chat or (query.message.chat if query and query.message else None)
    if not user or not chat: return ConversationHandler.END

    chat_lang = await get_chat_lang(chat.id)
    conv_type = context.user_data.get('conv_type')
    cancel_text_key = "set_time_cancelled" if conv_type == 'time' else "set_language_cancelled"
    cancel_text = get_text(cancel_text_key, chat_lang)

    if query:
        await query.answer()
        try:
            await query.edit_message_text(text=cancel_text, reply_markup=None)
        except BadRequest: # Сообщение могло быть удалено
            logger.debug("Failed to edit message on conversation cancel (message likely deleted).")
            # Отправим новое сообщение, если редактирование не удалось
            if update.effective_message: # Если есть к чему привязаться
                 await update.effective_message.reply_text(cancel_text)
    elif update.message:
        # Отмена командой /cancel
        await update.message.reply_text(cancel_text)

    context.user_data.pop('conv_type', None) # Очищаем состояние в любом случае
    logger.info(f"Conversation cancelled by user {user.id} in chat {chat.id}")
    return ConversationHandler.END


# =============================================================================
# ОБРАБОТЧИКИ КНОПОК (CallbackQueryHandler)
# =============================================================================

async def settings_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает кнопки из сообщения /story_settings (кроме тех, что начинают диалог)."""
    query = update.callback_query
    if not query or not query.message: return
    await query.answer()
    user = query.from_user; chat = query.message.chat
    if not user or not chat: return
    chat_lang = await get_chat_lang(chat.id); data = query.data

    logger.info(f"User {user.id} pressed settings button: {data} in chat {chat.id}")

    is_admin = await is_user_admin(chat.id, user.id, context)
    if not is_admin:
        await query.answer(text=get_text("admin_only", chat_lang), show_alert=True)
        return

    if data == CB_TOGGLE_STATUS:
        settings = dm.get_chat_settings(chat.id)
        new_status = not settings.get('enabled', True) # Инвертируем
        success = dm.update_chat_setting(chat.id, 'enabled', new_status)
        if success:
            # Обновляем сообщение с настройками
            await display_settings(update, context, chat.id, query.message.message_id)
            await query.answer( # Даем короткое подтверждение
                text=get_text("story_enabled", chat_lang) if new_status else get_text("story_disabled", chat_lang)
            )
        else:
            # Ошибка БД - сообщаем пользователю через alert
            await query.answer(text=get_text("error_db_generic", chat_lang), show_alert=True)

    elif data == CB_SHOW_SETTINGS: # Кнопка из /start
         await display_settings(update, context, chat.id)
         try: await query.message.delete() # Удаляем сообщение /start с кнопкой
         except Exception as e: logger.warning(f"Could not delete /start message: {e}")


async def feedback_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает кнопки обратной связи 👍 / 👎."""
    query = update.callback_query
    if not query or not query.message: return
    await query.answer()
    user = query.from_user; chat = query.message.chat
    if not user or not chat: return

    data = query.data; chat_lang = await get_chat_lang(chat.id)

    if data.startswith("feedback_"):
        parts = data.split("_");
        if len(parts) == 3:
            rating_type, original_message_id_str = parts[1], parts[2]
            try:
                original_message_id = int(original_message_id_str)
                rating_value = 1 if rating_type == "good" else -1 if rating_type == "bad" else 0
                if rating_value != 0:
                    dm.add_feedback(original_message_id, chat.id, user.id, rating_value)
                    try:
                        # Убираем кнопки после нажатия
                        await query.edit_message_reply_markup(reply_markup=None)
                        await query.answer(text=get_text("feedback_thanks", chat_lang))
                    # Игнорируем ошибку, если сообщение уже было изменено или удалено
                    except BadRequest as e:
                        if "message is not modified" in str(e).lower():
                             logger.debug(f"Feedback buttons already removed or message unchanged for msg {original_message_id}.")
                             await query.answer(text=get_text("feedback_thanks", chat_lang)) # Все равно подтверждаем
                        else:
                             logger.warning(f"BadRequest removing feedback buttons for msg {original_message_id}: {e}")
                    except TelegramError as e:
                        logger.warning(f"Error removing feedback buttons for msg {original_message_id}: {e}")
                else:
                    await query.answer(text="Invalid feedback type.") # Всплывающее уведомление
            except (ValueError, IndexError):
                logger.warning(f"Invalid feedback callback data: {data}")
                await query.answer(text="Error processing feedback.")
        else:
            logger.warning(f"Incorrect feedback format: {data}")
            await query.answer(text="Error processing feedback.")

# =============================================================================
# ОБРАБОТЧИК СООБЩЕНИЙ
# =============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сохраняет сообщения в БД (без изменений)."""
    message = update.message
    if not message or not message.from_user or not message.chat: return
    if message.from_user.is_bot: return

    chat_id = message.chat_id
    user = message.from_user
    timestamp = message.date or datetime.datetime.now(datetime.timezone.utc)
    username = user.username or user.first_name or f"User_{user.id}"

    message_data = {
        'message_id': message.message_id, 'user_id': user.id, 'username': username,
        'timestamp': timestamp.isoformat(), 'type': 'unknown', 'content': None,
        'file_id': None, 'file_unique_id': None, 'file_name': None
    }
    file_info = None

    if message.text: message_data['type'] = 'text'; message_data['content'] = message.text
    elif message.sticker: message_data['type'] = 'sticker'; message_data['content'] = message.sticker.emoji; file_info = message.sticker
    elif message.photo: message_data['type'] = 'photo'; message_data['content'] = message.caption; file_info = message.photo[-1]
    elif message.video: message_data['type'] = 'video'; message_data['content'] = message.caption; file_info = message.video
    elif message.audio: message_data['type'] = 'audio'; message_data['content'] = message.caption; file_info = message.audio
    elif message.voice: message_data['type'] = 'voice'; file_info = message.voice
    elif message.video_note: message_data['type'] = 'video_note'; file_info = message.video_note
    elif message.document: message_data['type'] = 'document'; message_data['content'] = message.caption; file_info = message.document
    elif message.caption and message_data['type'] == 'unknown': message_data['type'] = 'media_with_caption'; message_data['content'] = message.caption

    if file_info:
        try:
            message_data['file_id'] = file_info.file_id
            message_data['file_unique_id'] = file_info.file_unique_id
            message_data['file_name'] = getattr(file_info, 'file_name', None)
        except AttributeError:
            logger.warning(f"Failed to get file info for type {message_data['type']} in chat {chat_id}")

    if message_data['type'] != 'unknown':
        dm.add_message(chat_id, message_data)
    # else: logger.debug(f"Ignored unknown message type from {username} in chat {chat_id}")