# bot_handlers.py
import logging
import datetime
import asyncio
import time
import re
import pytz
from typing import Optional, Dict, Any, Tuple, List

# Импорты из проекта
import data_manager as dm
import gemini_client as gc
from config import (
    SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE_STR, BOT_OWNER_ID,
    SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE, COMMON_TIMEZONES, DATA_FILE,
    MESSAGE_FILTERS, SUPPORTED_GENRES
)
from localization import (
    get_text, get_chat_lang, update_chat_lang_cache, LOCALIZED_TEXTS,
    get_genre_name, get_period_name, get_user_friendly_proxy_error
)
from utils import download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner, is_user_admin

# Импорты из Telegram
from telegram import (
    Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Chat, User
)
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from telegram.constants import ParseMode, ChatAction, ChatType
from telegram.error import TelegramError, BadRequest
from telegram.helpers import escape_markdown

logger = logging.getLogger(__name__)

# Ключ для хранения состояния ожидания ввода в user_data
PENDING_TIME_INPUT_KEY = 'pending_time_input_for_msg'

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (Локальные для bot_handlers)
# =============================================================================

def format_time_for_chat(utc_hour: int, utc_minute: int, target_tz_str: str) -> Tuple[str, str]:
    """Конвертирует время UTC HH:MM в строку (HH:MM Z) и краткое имя TZ."""
    try:
        target_tz = pytz.timezone(target_tz_str)
        now_utc = datetime.datetime.now(pytz.utc)
        # Используем текущую дату для корректного учета DST
        time_utc = now_utc.replace(hour=utc_hour, minute=utc_minute, second=0, microsecond=0)
        time_local = time_utc.astimezone(target_tz)
        # Возвращаем время и аббревиатуру таймзоны
        return time_local.strftime("%H:%M"), time_local.strftime("%Z")
    except Exception as e:
        logger.error(f"Ошибка форматирования времени {utc_hour}:{utc_minute} для TZ {target_tz_str}: {e}")
        return f"{utc_hour:02d}:{utc_minute:02d}", "UTC" # Возвращаем UTC при ошибке

# =============================================================================
# ОБРАБОТЧИКИ КОМАНД
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    chat_lang = await get_chat_lang(chat.id)
    settings = dm.get_chat_settings(chat.id)
    status_key = "settings_enabled" if settings.get('enabled', True) else "settings_disabled"
    status_text = get_text(status_key, chat_lang)

    chat_tz = dm.get_chat_timezone(chat.id)
    default_local_time, default_tz_short = format_time_for_chat(SCHEDULE_HOUR, SCHEDULE_MINUTE, chat_tz)

    logger.info(f"User {user.id} started bot in chat {chat.id} ({chat.type})")

    chat_title_text = f"'{chat.title}'" if chat.title else get_text('private_chat', chat_lang)
    if chat.type == ChatType.PRIVATE:
        chat_title_text = get_text('private_chat', chat_lang)

    await update.message.reply_html(
        get_text(
            "start_message", chat_lang,
            user_mention=user.mention_html(),
            chat_title=chat_title_text,
            schedule_time=default_local_time,
            schedule_tz=default_tz_short,
            status=f"<b>{status_text}</b>"
        )
        # Кнопки теперь в /help или /story_settings
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    chat = update.effective_chat
    if not chat: return
    chat_lang = await get_chat_lang(chat.id)
    logger.debug(f"Help cmd chat={chat.id}")
    await update.message.reply_html(get_text("help_message", chat_lang))

async def generate_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Генерирует историю по запросу пользователя."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message: return

    chat_lang = await get_chat_lang(chat.id)
    logger.info(f"User {user.id} requested /generate_now in chat {chat.id}")

    # 1. Получаем сообщения
    messages_current = dm.get_messages_for_chat(chat.id)
    if not messages_current:
        await update.message.reply_html(get_text("generating_now_no_messages", chat_lang))
        return

    # 2. Отправляем начальный статус
    status_message = await update.message.reply_html(get_text("generating_now", chat_lang))
    status_message_id = status_message.message_id if status_message else None

    story = None
    error_msg_friendly = None
    sent_story_message = None
    try:
        # 3. Скачиваем изображения с обновлением статуса
        photo_messages = [m for m in messages_current if m.get('type') == 'photo' and m.get('file_unique_id')]
        total_photos_to_download = min(len(photo_messages), MAX_PHOTOS_TO_ANALYZE)
        downloaded_images: Dict[str, bytes] = {}

        if total_photos_to_download > 0 and context.bot and status_message_id:
             try:
                 await context.bot.edit_message_text(
                     chat_id=chat.id, message_id=status_message_id,
                     text=get_text("generating_status_downloading", chat_lang, count=0, total=total_photos_to_download),
                     parse_mode=ParseMode.HTML
                 )
                 # Здесь можно было бы обновлять счетчик по мере скачивания, но download_images делает это внутри
             except BadRequest: pass # Игнорируем, если сообщение не изменилось
             except TelegramError as e: logger.warning(f"Error updating download status: {e}")

        downloaded_images = await download_images(context, messages_current, chat.id)

        # 4. Обращаемся к ИИ с обновлением статуса
        chat_genre = dm.get_chat_genre(chat.id)
        logger.info(f"[Chat {chat.id}] Generating story on demand with genre: {chat_genre}")

        if context.bot and status_message_id:
             try:
                 await context.bot.edit_message_text(
                     chat_id=chat.id, message_id=status_message_id,
                     text=get_text("generating_status_contacting_ai", chat_lang),
                     parse_mode=ParseMode.HTML
                 )
             except BadRequest: pass
             except TelegramError as e: logger.warning(f"Error updating AI status: {e}")

        # Передаем язык в функцию генерации для user-friendly ошибок
        story, error_msg_friendly = await gc.safe_generate_story(messages_current, downloaded_images, chat_genre, chat_lang)

        # 5. Отправляем результат или ошибку
        if story:
            if context.bot and status_message_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat.id, message_id=status_message_id,
                        text=get_text("generating_status_formatting", chat_lang),
                        parse_mode=ParseMode.HTML
                    )
                except BadRequest: pass
                except TelegramError as e: logger.warning(f"Error updating formatting status: {e}")

            # Отправляем заголовок
            photo_note_str_res = get_text("photo_info_text", chat_lang, count=len(downloaded_images)) if downloaded_images else ""
            final_message_header = get_text("story_ready_header", chat_lang, photo_info=photo_note_str_res)
            try:
                # Сначала пытаемся отредактировать статусное сообщение
                await status_message.edit_text(final_message_header, parse_mode=ParseMode.HTML)
            except (BadRequest, TelegramError):
                 # Если не вышло, удаляем старое и шлем новое
                 logger.warning("Could not edit status message for header, sending new.")
                 try: await status_message.delete()
                 except Exception: pass
                 await context.bot.send_message(chat_id=chat.id, text=final_message_header, parse_mode=ParseMode.HTML)

            # Отправляем тело истории с кнопками
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"),
                InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")]])
            MAX_MSG_LEN = 4096 # Telegram limit
            if len(story) > MAX_MSG_LEN:
                logger.warning(f"/generate_now story too long chat={chat.id}, splitting.")
                parts = [story[i:i+MAX_MSG_LEN] for i in range(0, len(story), MAX_MSG_LEN)]
                for k, part in enumerate(parts):
                    current_reply_markup = keyboard if k == len(parts) - 1 else None
                    # Используем escape_markdown если parse_mode=Markdown
                    sent_story_message = await context.bot.send_message(
                        chat_id=chat.id, text=part, reply_markup=current_reply_markup, parse_mode=ParseMode.MARKDOWN
                    )
                    await asyncio.sleep(0.5) # Небольшая пауза между частями
            else:
                sent_story_message = await context.bot.send_message(
                    chat_id=chat.id, text=story, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
                )

            # Обновляем ID в кнопках фидбека
            if sent_story_message:
                keyboard_updated = InlineKeyboardMarkup([[
                    InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_story_message.message_id}"),
                    InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_story_message.message_id}")]])
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=chat.id, message_id=sent_story_message.message_id, reply_markup=keyboard_updated
                    )
                except BadRequest: pass
                except TelegramError as e: logger.warning(f"Error updating feedback buttons: {e}")

            logger.info(f"Story sent successfully for chat {chat.id}")
            # Отправляем примечание, если оно было (но без ошибки)
            if error_msg_friendly and not story: # Это условие теперь не должно срабатывать, т.к. error_msg передается отдельно
                 pass # Логика обработки ошибки ниже
            elif error_msg_friendly: # Если есть примечание, но история сгенерилась (например, safety fallback)
                 try: await context.bot.send_message(chat_id=chat.id, text=get_text("proxy_note", chat_lang, note=error_msg_friendly), parse_mode=ParseMode.HTML)
                 except Exception as e_note: logger.warning(f"Failed proxy note: {e_note}")

        else: # Если story is None (ошибка генерации)
            logger.warning(f"Failed gen story chat={chat.id}. Reason: {error_msg_friendly}")
            final_error_msg = get_text("generation_failed_user_friendly", chat_lang, reason=error_msg_friendly or 'неизвестной')
            # Уведомляем владельца о серьезной ошибке
            await notify_owner(context=context, message=f"Ошибка генерации истории (по запросу): {error_msg_friendly}", chat_id=chat.id, operation="generate_now", important=True)
            try:
                if status_message: # Пытаемся отредактировать статус
                    await status_message.edit_text(final_error_msg, parse_mode=ParseMode.HTML)
                else: # Если статуса нет, отправляем новым
                    await update.message.reply_html(final_error_msg)
            except Exception as edit_e:
                logger.error(f"Failed to edit status message on error: {edit_e}")
                await update.message.reply_html(final_error_msg) # Fallback

    except Exception as e:
        logger.exception(f"General error in /generate_now chat={chat.id}: {e}")
        await notify_owner(context=context, message=f"Критическая ошибка в /generate_now", chat_id=chat.id, operation="generate_now", exception=e, important=True)
        # Сообщаем пользователю об общей ошибке
        final_error_msg = get_text("error_telegram", chat_lang, error=e.__class__.__name__)
        try:
            if status_message: await status_message.edit_text(final_error_msg, parse_mode=ParseMode.HTML)
            else: await update.message.reply_html(final_error_msg)
        except Exception: await update.message.reply_html(final_error_msg)


async def regenerate_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пересоздает последнюю историю дня."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message: return

    chat_lang = await get_chat_lang(chat.id)
    logger.info(f"User {user.id} requested /regenerate_story in chat {chat.id}")

    # Важно: Используем ВСЕ сообщения, сохраненные в БД для этого чата
    messages_current = dm.get_messages_for_chat(chat.id)
    if not messages_current:
        await update.message.reply_html(get_text("regenerate_no_data", chat_lang))
        return

    status_message = await update.message.reply_html(get_text("regenerating", chat_lang))
    status_message_id = status_message.message_id if status_message else None

    story = None
    error_msg_friendly = None
    sent_story_message = None
    try:
        # --- Логика аналогична /generate_now, но без очистки сообщений ---
        # Скачиваем изображения с обновлением статуса
        photo_messages = [m for m in messages_current if m.get('type') == 'photo' and m.get('file_unique_id')]
        total_photos_to_download = min(len(photo_messages), MAX_PHOTOS_TO_ANALYZE)
        downloaded_images: Dict[str, bytes] = {}
        if total_photos_to_download > 0 and context.bot and status_message_id:
             try: await context.bot.edit_message_text(chat_id=chat.id, message_id=status_message_id, text=get_text("generating_status_downloading", chat_lang, count=0, total=total_photos_to_download), parse_mode=ParseMode.HTML)
             except BadRequest: pass; 
             except TelegramError as e: logger.warning(f"Error updating regen download status: {e}")
        downloaded_images = await download_images(context, messages_current, chat.id)

        # Обращаемся к ИИ
        chat_genre = dm.get_chat_genre(chat.id)
        logger.info(f"[Chat {chat.id}] Regenerating story with genre: {chat_genre}")
        if context.bot and status_message_id:
             try: await context.bot.edit_message_text(chat_id=chat.id, message_id=status_message_id, text=get_text("generating_status_contacting_ai", chat_lang), parse_mode=ParseMode.HTML)
             except BadRequest: pass; 
             except TelegramError as e: logger.warning(f"Error updating regen AI status: {e}")
        story, error_msg_friendly = await gc.safe_generate_story(messages_current, downloaded_images, chat_genre, chat_lang)

        # Удаляем сообщение "Пересоздаю..."
        try: await status_message.delete()
        except Exception as e: logger.warning(f"Could not delete 'regenerating' message: {e}")

        # Отправляем результат или ошибку
        if story:
            # Заголовок
            photo_note_str = get_text("photo_info_text", chat_lang, count=len(downloaded_images)) if downloaded_images else ""
            final_message_header = get_text("story_ready_header", chat_lang, photo_info=photo_note_str) # Используем тот же заголовок
            await update.message.reply_html(final_message_header) # Отправляем новым сообщением после команды

            # Тело истории с кнопками
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"), InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")]])
            MAX_MSG_LEN = 4096
            if len(story) > MAX_MSG_LEN: logger.warning(f"/regenerate_story too long chat={chat.id}, splitting."); parts = [story[i:i+MAX_MSG_LEN] for i in range(0, len(story), MAX_MSG_LEN)]
            else: parts = [story]
            for k, part in enumerate(parts):
                current_reply_markup = keyboard if k == len(parts) - 1 else None
                sent_story_message = await context.bot.send_message(chat_id=chat.id, text=part, reply_markup=current_reply_markup, parse_mode=ParseMode.MARKDOWN)
                if k < len(parts) - 1: await asyncio.sleep(0.5)
            # Обновление кнопок
            if sent_story_message:
                keyboard_updated = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_story_message.message_id}"), InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_story_message.message_id}")]])
                try: await context.bot.edit_message_reply_markup(chat_id=chat.id, message_id=sent_story_message.message_id, reply_markup=keyboard_updated)
                except BadRequest: pass; 
                except TelegramError as e: logger.warning(f"Error updating regen feedback buttons: {e}")
            logger.info(f"Regenerated story sent successfully for chat {chat.id}")
            if error_msg_friendly: 
                try: await context.bot.send_message(chat_id=chat.id, text=get_text("proxy_note", chat_lang, note=error_msg_friendly), parse_mode=ParseMode.HTML); 
                except Exception as e: logger.warning(f"Failed regen proxy note: {e}")

        else: # Ошибка генерации
            logger.warning(f"Failed regenerate story chat={chat.id}. Reason: {error_msg_friendly}")
            final_error_msg = get_text("generation_failed_user_friendly", chat_lang, reason=error_msg_friendly or 'неизвестной')
            await notify_owner(context=context, message=f"Ошибка регенерации истории: {error_msg_friendly}", chat_id=chat.id, operation="regenerate_story", important=True)
            await update.message.reply_html(final_error_msg) # Отправляем новым сообщением

    except Exception as e:
        logger.exception(f"Error in /regenerate_story chat={chat.id}: {e}")
        await notify_owner(context=context, message=f"Критическая ошибка в /regenerate_story", chat_id=chat.id, operation="regenerate_story", exception=e, important=True)
        final_error_msg = get_text("error_telegram", chat_lang, error=e.__class__.__name__)
        try: await status_message.edit_text(final_error_msg, parse_mode=ParseMode.HTML) # Пытаемся отредактировать статус
        except Exception: await update.message.reply_html(final_error_msg) # Отправляем новым если не вышло


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статус бота (только владелец)."""
    user = update.effective_user
    if not user or user.id != BOT_OWNER_ID: return # Молча игнорируем не владельца

    # Получаем данные из bot_data
    bot_start_time = context.application.bot_data.get('bot_start_time', time.time())
    last_run_time_dt = context.application.bot_data.get('last_job_run_time')
    last_err = context.application.bot_data.get('last_job_error')

    uptime_seconds = time.time() - bot_start_time
    uptime_str = str(datetime.timedelta(seconds=int(uptime_seconds)))
    enabled_chats_list = dm.get_enabled_chats()
    last_run_str = last_run_time_dt.strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(last_run_time_dt, datetime.datetime) else "Ни разу"
    last_error_str = escape_markdown(last_err[:1000] + ('...' if len(last_err)>1000 else ''), version=2) if last_err else "Нет" # Экранируем и обрезаем

    # Используем PTB версию из __init__
    from telegram import __version__ as ptb_version

    status_text = get_text(
        "status_command_reply", DEFAULT_LANGUAGE, # Статус всегда на языке по умолчанию
        uptime=uptime_str,
        active_chats=len(enabled_chats_list),
        last_job_run=last_run_str,
        last_job_error=last_error_str,
        ptb_version=ptb_version
    )
    await update.message.reply_html(status_text)


async def summarize_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Предлагает пользователю выбрать период для саммари."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message: return
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]: return # Только в группах

    chat_lang = await get_chat_lang(chat.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text("summarize_button_today", chat_lang), callback_data="summary_today"),
            InlineKeyboardButton(get_text("summarize_button_last_1h", chat_lang), callback_data="summary_last_1h"),
        ],
        [
            InlineKeyboardButton(get_text("summarize_button_last_3h", chat_lang), callback_data="summary_last_3h"),
            InlineKeyboardButton(get_text("summarize_button_last_24h", chat_lang), callback_data="summary_last_24h"),
        ],
         [InlineKeyboardButton(get_text("button_close", chat_lang), callback_data="summary_cancel")] # Кнопка закрытия
    ])
    await update.message.reply_html(
        text=get_text("summarize_prompt_period", chat_lang),
        reply_markup=keyboard
    )


# =============================================================================
# ОБРАБОТЧИК ОСНОВНОГО МЕНЮ НАСТРОЕК И ЕГО КНОПОК
# =============================================================================

async def story_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для показа кнопок настроек (только админы)."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message or chat.type == ChatType.PRIVATE: return

    if not await is_user_admin(chat.id, user.id, context):
        await update.message.reply_html(get_text("admin_only", await get_chat_lang(chat.id)))
        return

    # Очищаем возможное состояние ожидания ввода времени от этого пользователя
    context.user_data.pop(PENDING_TIME_INPUT_KEY, None)

    await _display_settings_main(update, context, chat.id, user.id)


async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает ВСЕ нажатия кнопок в меню настроек."""
    query = update.callback_query
    if not query or not query.message: return
    await query.answer() # Отвечаем на колбек сразу

    user = query.from_user
    chat = query.message.chat
    if not user or not chat: return

    chat_id = chat.id
    user_id = user.id
    message_id = query.message.message_id
    data = query.data
    chat_lang = await get_chat_lang(chat_id)

    logger.info(f"Settings CB: user={user_id}, chat={chat_id}, data='{data}'")

    # --- Проверка прав админа ---
    if not await is_user_admin(chat_id, user_id, context):
        await query.answer(get_text("admin_only", chat_lang), show_alert=True)
        # Не меняем сообщение, просто показываем alert
        return

    # --- Маршрутизация по callback_data ---
    try:
        if data == 'settings_main':
            context.user_data.pop(PENDING_TIME_INPUT_KEY, None) # Сброс ожидания ввода
            await _display_settings_main(update, context, chat_id, user_id)
        elif data == 'settings_close':
             context.user_data.pop(PENDING_TIME_INPUT_KEY, None)
             await query.edit_message_reply_markup(reply_markup=None) # Убираем кнопки
             await query.answer(get_text("action_cancelled", chat_lang))
        elif data == 'settings_toggle_status':
            settings = dm.get_chat_settings(chat_id)
            new_status = not settings.get('enabled', True)
            success = dm.update_chat_setting(chat_id, 'enabled', new_status)
            if success:
                await _display_settings_main(update, context, chat_id, user_id)
                await query.answer(get_text("settings_saved_popup", chat_lang))
            else:
                await query.answer(get_text("error_db_generic", chat_lang), show_alert=True)
                await notify_owner(context=context, message="DB Error toggling status", chat_id=chat_id, user_id=user_id, operation="toggle_status", important=True)
        # --- Подменю ---
        elif data == 'settings_show_lang':
             await _display_settings_language(update, context, chat_id, user_id)
        elif data == 'settings_show_time':
             await _display_settings_time(update, context, chat_id, user_id)
        elif data == 'settings_show_tz':
             await _display_settings_timezone(update, context, chat_id, user_id)
        elif data == 'settings_show_genre':
             await _display_settings_genre(update, context, chat_id, user_id)
        # --- Установка значений ---
        elif data.startswith('settings_set_lang_'):
            lang_code = data.split('_')[-1]
            if lang_code in SUPPORTED_LANGUAGES:
                success = dm.update_chat_setting(chat_id, 'lang', lang_code)
                if success:
                    update_chat_lang_cache(chat_id, lang_code) # Обновляем кэш
                    # Обновляем команды на новый язык
                    await _update_bot_commands(context, chat_id, user_id, lang_code)
                    await _display_settings_main(update, context, chat_id, user_id) # Возврат в главное меню
                    await query.answer(get_text("settings_lang_selected", lang_code))
                else: await query.answer(get_text("error_db_generic", await get_chat_lang(chat_id)), show_alert=True)
            else: logger.warning(f"Invalid lang code in CB: {data}")
        elif data == 'settings_set_time_default':
            success = dm.update_chat_setting(chat_id, 'custom_schedule_time', None)
            if success:
                await _display_settings_main(update, context, chat_id, user_id) # Возврат
                await query.answer(get_text("settings_saved_popup", chat_lang))
            else: await query.answer(get_text("error_db_generic", chat_lang), show_alert=True)
        elif data.startswith('settings_set_tz_'):
            tz_id = data.split('settings_set_tz_', 1)[-1] # Берем все после префикса
            if tz_id in COMMON_TIMEZONES:
                success = dm.update_chat_setting(chat_id, 'timezone', tz_id)
                if success:
                    await _display_settings_main(update, context, chat_id, user_id) # Возврат
                    await query.answer(get_text("settings_tz_selected", chat_lang))
                else: await query.answer(get_text("error_db_generic", chat_lang), show_alert=True)
            else: logger.warning(f"Invalid timezone id in CB: {data}")
        elif data.startswith('settings_set_genre_'):
            genre_key = data.split('_')[-1]
            if genre_key in SUPPORTED_GENRES:
                success = dm.update_chat_setting(chat_id, 'story_genre', genre_key)
                if success:
                    await _display_settings_main(update, context, chat_id, user_id) # Возврат
                    await query.answer(get_text("settings_genre_selected", chat_lang))
                else: await query.answer(get_text("error_db_generic", chat_lang), show_alert=True)
            else: logger.warning(f"Invalid genre key in CB: {data}")
        else:
            logger.warning(f"Unknown settings callback data received: {data}")

    except BadRequest as e:
        if "Message is not modified" in str(e): logger.debug(f"Settings message not modified: {e}")
        else: logger.error(f"BadRequest in settings CB handler: {e}", exc_info=True)
    except TelegramError as e:
        logger.error(f"TelegramError in settings CB handler: {e}", exc_info=True)
    except Exception as e:
        logger.exception(f"Unexpected error in settings CB handler: {e}")
        await notify_owner(context=context, message="Critical error in settings_callback_handler", operation="settings_callback", exception=e, important=True)

# =============================================================================
# ФУНКЦИИ ОТОБРАЖЕНИЯ РАЗНЫХ ЭКРАНОВ НАСТРОЕК (Приватные)
# =============================================================================

async def _display_settings_main(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Отображает ГЛАВНОЕ меню настроек."""
    chat_lang = await get_chat_lang(chat_id)
    settings = dm.get_chat_settings(chat_id)
    chat = update.effective_chat or await context.bot.get_chat(chat_id)
    chat_title = f"'{chat.title}'" if chat.title else get_text('private_chat', chat_lang)

    # Формируем строки для отображения текущих значений
    status_text = get_text("settings_enabled", chat_lang) if settings.get('enabled', True) else get_text("settings_disabled", chat_lang)
    lang_name = LOCALIZED_TEXTS.get(settings.get('lang', DEFAULT_LANGUAGE), {}).get("lang_name", settings.get('lang', DEFAULT_LANGUAGE))
    chat_tz_str = settings.get('timezone', 'UTC')
    tz_display_name = COMMON_TIMEZONES.get(chat_tz_str, chat_tz_str)
    current_genre_key = settings.get('story_genre', 'default')
    genre_display_name = get_genre_name(current_genre_key, chat_lang)

    # Время
    custom_time_utc_str = settings.get('custom_schedule_time')
    if custom_time_utc_str:
        try: ch, cm = map(int, custom_time_utc_str.split(':')); local_time_str, tz_short = format_time_for_chat(ch, cm, chat_tz_str); time_display = get_text("settings_time_custom", chat_lang, custom_time=f"{local_time_str} {tz_short}")
        except ValueError: time_display = f"{custom_time_utc_str} UTC (invalid!)"
    else: local_time_str, tz_short = format_time_for_chat(SCHEDULE_HOUR, SCHEDULE_MINUTE, chat_tz_str); time_display = get_text("settings_time_default", chat_lang, default_local_time=f"{local_time_str} {tz_short}")

    # Текст сообщения
    text = get_text("settings_title", chat_lang, chat_title=chat_title) + "\n\n"
    text += f"▪️ {get_text('settings_status_label', chat_lang)}: {status_text}\n"
    text += f"▪️ {get_text('settings_language_label', chat_lang)}: {lang_name}\n"
    text += f"▪️ {get_text('settings_time_label', chat_lang)}: {time_display}\n"
    text += f"▪️ {get_text('settings_timezone_label', chat_lang)}: {tz_display_name}\n"
    text += f"▪️ {get_text('settings_genre_label', chat_lang)}: {genre_display_name}"

    # Кнопки
    toggle_btn_text = get_text("settings_button_toggle_on" if settings.get('enabled', True) else "settings_button_toggle_off", chat_lang)
    keyboard = [
        [InlineKeyboardButton(toggle_btn_text, callback_data='settings_toggle_status')],
        [
            InlineKeyboardButton(f"🌐 {get_text('settings_language_label', chat_lang)}", callback_data='settings_show_lang'),
            InlineKeyboardButton(f"🎭 {get_text('settings_genre_label', chat_lang)}", callback_data='settings_show_genre')
        ],
        [
            InlineKeyboardButton(f"⏰ {get_text('settings_time_label', chat_lang)}", callback_data='settings_show_time'),
            InlineKeyboardButton(f"🌍 {get_text('settings_timezone_label', chat_lang)}", callback_data='settings_show_tz')
        ],
         [InlineKeyboardButton(get_text('button_close', chat_lang), callback_data='settings_close')]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    # Отправляем или редактируем
    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    elif update.message: # Если вызвано командой /story_settings
        await update.message.reply_html(text, reply_markup=markup)

async def _display_settings_language(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Отображает подменю выбора языка."""
    chat_lang = await get_chat_lang(chat_id)
    settings = dm.get_chat_settings(chat_id)
    current_lang = settings.get('lang', DEFAULT_LANGUAGE)

    text = get_text("settings_select_language_title", chat_lang)
    buttons = []
    for code in SUPPORTED_LANGUAGES:
        lang_name = LOCALIZED_TEXTS.get(code, {}).get("lang_name", code)
        prefix = "✅ " if code == current_lang else ""
        buttons.append([InlineKeyboardButton(f"{prefix}{lang_name}", callback_data=f"settings_set_lang_{code}")])

    buttons.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

async def _display_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Отображает подменю настройки времени."""
    chat_lang = await get_chat_lang(chat_id)
    settings = dm.get_chat_settings(chat_id)
    chat_tz_str = settings.get('timezone', 'UTC')
    custom_time_utc_str = settings.get('custom_schedule_time')

    if custom_time_utc_str:
        try: ch, cm = map(int, custom_time_utc_str.split(':')); local_time, tz_short = format_time_for_chat(ch, cm, chat_tz_str); current_display = f"{local_time} {tz_short} ({custom_time_utc_str} UTC)"
        except ValueError: current_display = f"{custom_time_utc_str} UTC (invalid!)"
    else: local_time, tz_short = format_time_for_chat(SCHEDULE_HOUR, SCHEDULE_MINUTE, chat_tz_str); current_display = get_text("settings_time_default", chat_lang, default_local_time=f"{local_time} {tz_short}") + f" ({SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} UTC)"

    text = get_text("settings_select_time_title", chat_lang) + "\n"
    text += get_text("settings_time_current", chat_lang, current_time_display=current_display) + "\n\n"
    text += get_text("settings_time_prompt", chat_lang, chat_timezone=COMMON_TIMEZONES.get(chat_tz_str, chat_tz_str))

    keyboard = [
        [InlineKeyboardButton(get_text("settings_time_button_reset", chat_lang), callback_data="settings_set_time_default")],
        [InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    # Сохраняем ID сообщения, которое нужно будет обновить после ввода текста
    query = update.callback_query
    if query and query.message:
         context.user_data[PENDING_TIME_INPUT_KEY] = query.message.message_id
         logger.debug(f"Set pending time input for user {user_id} in chat {chat_id}, message {query.message.message_id}")
         await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
         # Отвечаем, чтобы убрать "часики" с кнопки
         await query.answer(get_text("waiting_for_time_input", chat_lang))

async def _display_settings_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Отображает подменю выбора таймзоны."""
    chat_lang = await get_chat_lang(chat_id)
    settings = dm.get_chat_settings(chat_id)
    current_tz = settings.get('timezone', 'UTC')

    text = get_text("settings_select_timezone_title", chat_lang)
    buttons = []
    sorted_tzs = sorted(COMMON_TIMEZONES.items(), key=lambda item: item[1]) # Сортируем по имени
    rows = []
    for tz_id, tz_name in sorted_tzs:
        prefix = "✅ " if tz_id == current_tz else ""
        button = InlineKeyboardButton(f"{prefix}{tz_name}", callback_data=f"settings_set_tz_{tz_id}")
        # Создаем по 2 кнопки в ряду
        if len(rows) == 0 or len(rows[-1]) == 2: rows.append([button])
        else: rows[-1].append(button)
    buttons.extend(rows) # Добавляем ряды кнопок таймзон
    buttons.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

async def _display_settings_genre(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Отображает подменю выбора жанра."""
    chat_lang = await get_chat_lang(chat_id)
    settings = dm.get_chat_settings(chat_id)
    current_genre = settings.get('story_genre', 'default')

    text = get_text("settings_select_genre_title", chat_lang)
    buttons = []
    for key in SUPPORTED_GENRES.keys():
        prefix = "✅ " if key == current_genre else ""
        button_text = get_text("genre_select_button_text", chat_lang, genre_name=get_genre_name(key, chat_lang))
        buttons.append([InlineKeyboardButton(f"{prefix}{button_text}", callback_data=f"settings_set_genre_{key}")])

    buttons.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def _update_bot_commands(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, lang_code: str):
     """Обновляет команды бота для текущего языка."""
     try:
         commands = [
             BotCommand("start", get_text("cmd_start_desc", lang_code)),
             BotCommand("help", get_text("cmd_help_desc", lang_code)),
             BotCommand("generate_now", get_text("cmd_generate_now_desc", lang_code)),
             BotCommand("regenerate_story", get_text("cmd_regenerate_desc", lang_code)),
             BotCommand("summarize", get_text("cmd_summarize_desc", lang_code)),
             BotCommand("story_settings", get_text("cmd_story_settings_desc", lang_code)),
             BotCommand("set_language", get_text("cmd_set_language_desc", lang_code)),
             BotCommand("set_timezone", get_text("cmd_set_timezone_desc", lang_code)),
         ]
         # Команды для владельца не меняем здесь, они глобальные
         if chat_id > 0: # Личный чат
             await context.bot.set_my_commands(commands)
         else: # Групповой чат
             # TODO: Возможно, стоит устанавливать команды для админов? Пока для всех в группе.
             await context.bot.set_my_commands(commands) # scope=BotCommandScopeChat(chat_id) - может требовать прав

         logger.info(f"Команды обновлены для языка '{lang_code}' в чате {chat_id}")
     except Exception as e:
         logger.warning(f"Не удалось обновить команды для языка {lang_code} в чате {chat_id}: {e}")

# =============================================================================
# ОБРАБОТЧИК КНОПОК ФИДБЕКА И САММАРИ (НЕ НАСТРОЙКИ)
# =============================================================================

async def feedback_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает кнопки обратной связи 👍 / 👎."""
    query = update.callback_query
    if not query or not query.message: return
    await query.answer() # Отвечаем сразу
    user = query.from_user; chat = query.message.chat
    if not user or not chat: return

    data = query.data; chat_lang = await get_chat_lang(chat.id)

    if data.startswith("feedback_"):
        parts = data.split("_")
        if len(parts) == 3:
            rating_type, original_message_id_str = parts[1], parts[2]
            try:
                original_message_id = int(original_message_id_str)
                rating_value = 1 if rating_type == "good" else -1 if rating_type == "bad" else 0
                if rating_value != 0:
                    dm.add_feedback(original_message_id, chat.id, user.id, rating_value)
                    try:
                        await query.edit_message_reply_markup(reply_markup=None) # Убираем кнопки
                        await query.answer(text=get_text("feedback_thanks", chat_lang)) # Всплывающее уведомление
                    except BadRequest as e:
                        if "message is not modified" in str(e).lower(): logger.debug(f"Feedback buttons already removed for msg {original_message_id}.")
                        else: logger.warning(f"BadRequest removing feedback buttons: {e}")
                    except TelegramError as e: logger.warning(f"Error removing feedback buttons: {e}")
                else: await query.answer("Invalid feedback type.", show_alert=True)
            except (ValueError, IndexError): logger.warning(f"Invalid feedback CB data: {data}"); await query.answer("Error processing feedback.")
        else: logger.warning(f"Incorrect feedback format: {data}"); await query.answer("Error processing feedback.")


async def summary_period_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие кнопок выбора периода для саммари."""
    query = update.callback_query
    if not query or not query.message: logger.warning("summary CB: invalid query/message"); return
    await query.answer()
    user = query.from_user; chat = query.message.chat
    if not user or not chat: logger.warning("summary CB: no user/chat"); return

    chat_lang = await get_chat_lang(chat.id)
    data = query.data

    # Обработка отмены
    if data == "summary_cancel":
        try: await query.edit_message_text(get_text("action_cancelled", chat_lang), reply_markup=None)
        except BadRequest: pass; 
        except TelegramError as e: logger.warning(f"Error editing summary cancel: {e}")
        return

    period_key = data.removeprefix("summary_")
    logger.info(f"User {user.id} requested summary for period '{period_key}' in chat {chat.id}")

    # 1. Получаем сообщения
    messages_to_summarize: List[Dict[str, Any]] = []
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    period_start_utc: Optional[datetime.datetime] = None
    try:
        if period_key == "today": period_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period_key == "last_1h": period_start_utc = now_utc - datetime.timedelta(hours=1)
        elif period_key == "last_3h": period_start_utc = now_utc - datetime.timedelta(hours=3)
        elif period_key == "last_24h": period_start_utc = now_utc - datetime.timedelta(hours=24)
        else: logger.error(f"Unknown summary period key: {period_key}"); await context.bot.send_message(chat.id, "Ошибка: Неизвестный период."); return
        messages_to_summarize = dm.get_messages_for_chat_since(chat.id, period_start_utc)
    except Exception as db_err:
        logger.exception(f"DB error getting messages for summary chat {chat.id}:"); await notify_owner(context=context, message="DB Error getting summary messages", chat_id=chat.id, operation="get_summary_messages", exception=db_err, important=True)
        try: await query.edit_message_text(get_text("error_db_generic", chat_lang), reply_markup=None)
        except BadRequest: pass
        return

    # 2. Проверка наличия сообщений
    if not messages_to_summarize:
        logger.info(f"No messages found for summary period '{period_key}' chat {chat.id}")
        try: await query.edit_message_text(get_text("summarize_no_messages", chat_lang), reply_markup=None)
        except BadRequest: pass
        return

    # 3. Удаляем кнопки и показываем статус
    original_message_id = query.message.message_id
    status_message = None
    try: await query.edit_message_text(get_text("summarize_generating", chat_lang), reply_markup=None); status_message = query.message # Сохраняем объект сообщения
    except BadRequest: pass; 
    except TelegramError as e: logger.warning(f"Failed to edit summary prompt msg {original_message_id}: {e}")

    if context.bot: 
        try: await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
        except Exception: pass

    # 4. Генерируем саммари
    summary_content, error_msg_friendly = await gc.safe_generate_summary(messages_to_summarize, chat_lang)

    # 5. Отправляем результат
    try:
        # Удаляем сообщение "Генерирую..." если оно было отредактировано
        if status_message and status_message.text == get_text("summarize_generating", chat_lang):
             try: await status_message.delete()
             except Exception: pass # Ошибки удаления не критичны

        if summary_content:
            period_name = get_period_name(period_key, chat_lang)
            header_html = get_text("summarize_header", chat_lang, period_name=period_name)
            # Отправляем заголовок и тело разными сообщениями
            await context.bot.send_message(chat.id, header_html, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.1) # Пауза
            try: await context.bot.send_message(chat.id, summary_content, parse_mode=ParseMode.MARKDOWN)
            except (BadRequest, TelegramError) as send_error:
                logger.error(f"Failed to send summary body as Markdown: {send_error}. Trying plain text.")
                try: await context.bot.send_message(chat.id, summary_content) # Fallback
                except Exception as fallback_err: logger.error(f"Failed to send summary body even as plain text: {fallback_err}"); raise # Передаем ошибку выше
            logger.info(f"Summary sent for period '{period_key}' in chat {chat.id}")
            if error_msg_friendly: 
                try: await context.bot.send_message(chat.id, get_text("proxy_note", chat_lang, note=error_msg_friendly), parse_mode=ParseMode.HTML); 
                except Exception as e: logger.warning(f"Failed summary proxy note: {e}")

        else: # Ошибка генерации
            logger.warning(f"Failed to generate summary for '{period_key}' chat={chat.id}. Reason: {error_msg_friendly}")
            final_error_text = get_text("summarize_failed_user_friendly", chat_lang, reason=error_msg_friendly or 'неизвестной')
            await notify_owner(context=context, message=f"Ошибка генерации саммари ({period_key}): {error_msg_friendly}", chat_id=chat.id, user_id=user.id, operation="generate_summary", important=True)
            await context.bot.send_message(chat.id, final_error_text, parse_mode=ParseMode.HTML) # Отправляем новым сообщением

    except Exception as e:
        logger.exception(f"Unexpected error processing summary result chat={chat.id}:")
        await notify_owner(context=context, message=f"Unexpected error processing summary result", chat_id=chat.id, user_id=user.id, operation="process_summary", exception=e, important=True)
        try: await context.bot.send_message(chat.id, get_text("error_db_generic", chat_lang))
        except Exception: pass


# =============================================================================
# ОБРАБОТЧИК СООБЩЕНИЙ (для сохранения и для ввода времени)
# =============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сохраняет сообщения в БД И обрабатывает ожидаемый ввод времени."""
    message = update.message
    if not message or not message.from_user or not message.chat: return
    if message.from_user.is_bot: return # Игнорируем ботов

    chat_id = message.chat_id
    user_id = message.from_user.id

    # --- Обработка ожидаемого ввода времени ---
    pending_msg_id = context.user_data.get(PENDING_TIME_INPUT_KEY)
    if pending_msg_id is not None and message.text and not message.text.startswith('/'):
        logger.debug(f"Handling expected time input from user {user_id} in chat {chat_id}")
        # Убираем флаг ожидания СРАЗУ
        context.user_data.pop(PENDING_TIME_INPUT_KEY, None)
        chat_lang = await get_chat_lang(chat_id)
        chat_tz_str = dm.get_chat_timezone(chat_id)
        input_time_str = message.text.strip()

        if not re.fullmatch(r"^(?:[01]\d|2[0-3]):[0-5]\d$", input_time_str):
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=pending_msg_id,
                    text=get_text("settings_time_invalid_format", chat_lang),
                    parse_mode=ParseMode.HTML
                    # Клавиатуру не возвращаем, пользователь должен снова нажать "Изменить время"
                )
                # Удаляем некорректное сообщение пользователя
                await message.delete()
            except Exception as e: logger.error(f"Error handling invalid time input message: {e}")
            return # Выходим, не сохраняя некорректное сообщение

        # Конвертация и сохранение
        utc_time_to_save = None
        local_time_saved = input_time_str
        tz_short_name = "???"
        try:
            hour_local, minute_local = map(int, input_time_str.split(':'))
            local_tz = pytz.timezone(chat_tz_str)
            now_local_naive = datetime.datetime.now() # Берем текущую дату для локализации
            time_local_naive = now_local_naive.replace(hour=hour_local, minute=minute_local, second=0, microsecond=0)
            time_local_aware = local_tz.localize(time_local_naive, is_dst=None) # Автоопределение летнего времени
            time_utc = time_local_aware.astimezone(pytz.utc)
            utc_time_to_save = time_utc.strftime("%H:%M")
            tz_short_name = time_local_aware.strftime('%Z')
            logger.info(f"Chat {chat_id}: User input {input_time_str} ({chat_tz_str}/{tz_short_name}) converted to {utc_time_to_save} UTC.")
        except Exception as e:
            logger.error(f"Error converting time chat={chat_id}: Input='{input_time_str}', TZ='{chat_tz_str}'. Error: {e}", exc_info=True)
            try:
                 # Сообщаем об ошибке конвертации в исходном сообщении настроек
                 await context.bot.edit_message_text(chat_id=chat_id, message_id=pending_msg_id, text=get_text("error_db_generic", chat_lang))
                 await message.delete() # Удаляем сообщение с некорректным временем
            except Exception: pass
            return

        # Сохраняем в БД
        success = dm.update_chat_setting(chat_id, 'custom_schedule_time', utc_time_to_save)
        if success:
            text_to_show = get_text("settings_time_success", chat_lang, local_time=local_time_saved, tz_short=tz_short_name, utc_time=utc_time_to_save)
            try:
                # Обновляем исходное сообщение настроек с сообщением об успехе
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=pending_msg_id,
                    text=text_to_show, parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")]]) # Кнопка Назад
                )
                await message.delete() # Удаляем сообщение пользователя с введенным временем
                await asyncio.sleep(5) # Пауза перед возвратом в главное меню
                # Автоматически возвращаемся в главное меню настроек
                await _display_settings_main(update, context, chat_id, user_id)
            except Exception as e: logger.error(f"Error updating settings message after time set: {e}")
        else:
            try: await context.bot.edit_message_text(chat_id=chat_id, message_id=pending_msg_id, text=get_text("error_db_generic", chat_lang))
            except Exception: pass
        return # Завершаем обработку, т.к. это было сообщение для установки времени

    # --- Сохранение обычного сообщения (если это не было вводом времени) ---
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]: # Сохраняем только из групп
        timestamp = message.date or datetime.datetime.now(datetime.timezone.utc)
        username = message.from_user.username or message.from_user.first_name or f"User_{user_id}"
        message_data = {
            'message_id': message.message_id, 'user_id': user_id, 'username': username,
            'timestamp': timestamp.isoformat(), 'type': 'unknown', 'content': None,
            'file_id': None, 'file_unique_id': None, 'file_name': None }
        file_info = None; msg_type = 'unknown'

        if message.text: msg_type = 'text'; message_data['content'] = message.text
        elif message.sticker: msg_type = 'sticker'; message_data['content'] = message.sticker.emoji; file_info = message.sticker
        elif message.photo: msg_type = 'photo'; message_data['content'] = message.caption; file_info = message.photo[-1] # Берем наибольшее разрешение
        elif message.video: msg_type = 'video'; message_data['content'] = message.caption; file_info = message.video
        elif message.audio: msg_type = 'audio'; message_data['content'] = message.caption; file_info = message.audio
        elif message.voice: msg_type = 'voice'; file_info = message.voice
        elif message.video_note: msg_type = 'video_note'; file_info = message.video_note
        elif message.document: msg_type = 'document'; message_data['content'] = message.caption; file_info = message.document
        elif message.caption and msg_type == 'unknown': msg_type = 'media_with_caption'; message_data['content'] = message.caption # Если подпись есть, а тип не определен

        message_data['type'] = msg_type
        if file_info:
            try: message_data['file_id'] = file_info.file_id; message_data['file_unique_id'] = file_info.file_unique_id; message_data['file_name'] = getattr(file_info, 'file_name', None)
            except AttributeError: logger.warning(f"Failed to get file info type={msg_type} chat={chat_id}")

        if message_data['type'] != 'unknown': dm.add_message(chat_id, message_data)
        # else: logger.debug(f"Ignored unknown message type from {username} in chat {chat_id}")