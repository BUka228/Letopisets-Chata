# =============================================================================
# ФАЙЛ: bot_handlers.py (ПОЛНАЯ ВЕРСИЯ)
# Реализует команды, колбэки, обработку сообщений, включая новый UI настроек
# и логику вмешательств Летописца.
# =============================================================================
# bot_handlers.py
import logging
import datetime
import asyncio
import time
import re
import pytz
import html
import prompt_builder as pb
from typing import Optional, Dict, Any, Tuple, List

# Импорты проекта
import data_manager as dm
import gemini_client as gc
from config import (
    
    SCHEDULE_HOUR, SCHEDULE_MINUTE, DEFAULT_LANGUAGE, COMMON_TIMEZONES,
    SUPPORTED_LANGUAGES, SUPPORTED_GENRES, SUPPORTED_PERSONALITIES,
    SUPPORTED_OUTPUT_FORMATS, BOT_OWNER_ID, DEFAULT_RETENTION_DAYS,  DEFAULT_OUTPUT_FORMAT, DEFAULT_PERSONALITY,
    JOB_CHECK_INTERVAL_MINUTES,
    # Лимиты и дефолты для вмешательств
    INTERVENTION_MIN_COOLDOWN_MIN, INTERVENTION_MAX_COOLDOWN_MIN, INTERVENTION_DEFAULT_COOLDOWN_MIN,
    INTERVENTION_MIN_MIN_MSGS, INTERVENTION_MAX_MIN_MSGS, INTERVENTION_DEFAULT_MIN_MSGS,
    INTERVENTION_MIN_TIMESPAN_MIN, INTERVENTION_MAX_TIMESPAN_MIN, INTERVENTION_DEFAULT_TIMESPAN_MIN, INTERVENTION_PROMPT_MESSAGE_COUNT,
     INTERVENTION_CONTEXT_HOURS
)
from localization import (
    get_intervention_value_limits, get_text, get_chat_lang, update_chat_lang_cache, get_genre_name,
    get_personality_name, get_output_format_name, format_retention_days,
    get_user_friendly_proxy_error, get_stats_period_name, LOCALIZED_TEXTS
)
from utils import (
    download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner, is_user_admin
)

# Импорты Telegram
from telegram import (
    Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Chat, User, Message
)
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from telegram.constants import ParseMode, ChatAction, ChatType
from telegram.error import TelegramError, BadRequest
from telegram.helpers import escape_markdown # Только escape_markdown
from telegram.constants import ParseMode, ChatAction, ChatType

logger = logging.getLogger(__name__)

# Ключ для ожидания ввода времени
PENDING_TIME_INPUT_KEY = 'pending_time_input_for_msg'
PENDING_INTERVENTION_INPUT_KEY = 'pending_intervention_setting_input_details' 
ACTIVE_INTERVENTION_CHAIN_MESSAGE_ID_KEY = 'active_intervention_chain_message_id'

# =======================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ОБРАБОТЧИКОВ
# =======================================
def format_time_for_chat(utc_hour: int, utc_minute: int, target_tz_str: str) -> Tuple[str, str]:
    """Конвертирует время UTC в HH:MM и короткое имя TZ."""
    try:
        target_tz = pytz.timezone(target_tz_str)
        time_utc = datetime.datetime.now(pytz.utc).replace(hour=utc_hour, minute=utc_minute, second=0, microsecond=0)
        time_local = time_utc.astimezone(target_tz)
        return time_local.strftime("%H:%M"), time_local.strftime("%Z")
    except Exception as e:
        logger.error(f"Ошибка форматирования времени UTC={utc_hour}:{utc_minute} для TZ={target_tz_str}: {e}")
        return f"{utc_hour:02d}:{utc_minute:02d}", "UTC"

async def get_chat_info(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, str]:
    chat_lang = await get_chat_lang(chat_id); chat_key = f"chat_title_{chat_id}"; cached_title = context.bot_data.get(chat_key)
    if cached_title: return chat_lang, cached_title
    try:
        chat = await context.bot.get_chat(chat_id);
        # --- ИСПРАВЛЕНО ---
        chat_title = f"'{html.escape(chat.title)}'" if chat.title else get_text('private_chat', chat_lang)
        # -------------------
        context.bot_data[chat_key] = chat_title; return chat_lang, chat_title
    except Exception as e: logger.warning(f"Failed get chat info {chat_id}: {e}"); return chat_lang, f"Chat ID: <code>{chat_id}</code>"

# =======================================
# ОБРАБОТЧИКИ КОМАНД
# =======================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    chat_id = chat.id
    chat_lang, chat_title_safe = await get_chat_info(chat_id, context)
    settings = dm.get_chat_settings(chat_id)
    status_text = get_text("enabled_status" if settings.get('enabled', True) else "disabled_status", chat_lang)
    personality_key = settings.get('story_personality', 'neutral')
    personality_name = get_personality_name(personality_key, chat_lang)
    format_key = settings.get('output_format', 'story')
    format_desc = get_text(f"start_format_desc_{format_key}", chat_lang)

    chat_tz = dm.get_chat_timezone(chat_id)
    custom_time = settings.get('custom_schedule_time')
    schedule_h, schedule_m = (int(t) for t in custom_time.split(':')) if custom_time else (SCHEDULE_HOUR, SCHEDULE_MINUTE)
    schedule_local_time, schedule_tz_short = format_time_for_chat(schedule_h, schedule_m, chat_tz)

    await update.message.reply_html(
        get_text("start_message", chat_lang,
                 user_mention=user.mention_html(), chat_title=chat_title_safe,
                 personality=personality_name, format_desc=format_desc,
                 schedule_time=schedule_local_time, schedule_tz=schedule_tz_short, status=status_text)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    chat = update.effective_chat
    if not chat: return

    chat_lang, _ = await get_chat_info(chat.id, context)
    settings = dm.get_chat_settings(chat.id)
    personality_name = get_personality_name(settings.get('story_personality', 'neutral'), chat_lang)
    output_format = settings.get('output_format', 'story')
    format_action_now = get_text(f"help_format_{output_format}_now", chat_lang)
    format_action_regen = get_text(f"help_format_{output_format}_regen", chat_lang)

    await update.message.reply_html(
        get_text("help_message", chat_lang, personality_name=personality_name,
                 current_format_action_now=format_action_now,
                 current_format_action_regen=format_action_regen)
    )

async def generate_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Генерирует историю/дайджест по запросу."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message: return
    chat_id = chat.id; chat_lang, _ = await get_chat_info(chat_id, context)
    logger.info(f"User {user.id} /generate_now chat={chat_id}")

    messages_current = dm.get_messages_for_chat(chat_id)
    settings = dm.get_chat_settings(chat_id)
    output_format = settings.get('output_format', 'story')
    output_format_name = get_output_format_name(output_format, chat_lang)
    output_format_name_capital = get_output_format_name(output_format, chat_lang, capital=True)

    if not messages_current:
        await update.message.reply_html(get_text("generating_now_no_messages", chat_lang, output_format_name=output_format_name)); return

    status_msg: Optional[Message] = await update.message.reply_html(get_text("generating_now", chat_lang, output_format_name=output_format_name))
    status_msg_id = status_msg.message_id if status_msg else None
    output_text, error_msg_friendly = None, None

    try:
        downloaded_images = {}
        if output_format == 'story':
            # Status update for downloading (optional but nice)
            photo_count = sum(1 for m in messages_current if m.get('type') == 'photo')
            limit = MAX_PHOTOS_TO_ANALYZE
            if photo_count > 0 and status_msg_id:
                try: await context.bot.edit_message_text(chat_id, status_msg_id, get_text("generating_status_downloading", chat_lang, count=0, total=min(photo_count,limit)), parse_mode=ParseMode.HTML)
                except Exception: pass
            downloaded_images = await download_images(context, messages_current, chat_id, limit)

        # Status update for AI call
        chat_genre = settings.get('story_genre', 'default')
        personality_key = settings.get('story_personality', 'neutral')
        logger.info(f"[Chat {chat_id}] Gen on demand: Format={output_format}, Genre={chat_genre}, Personality={personality_key}")
        if status_msg_id:
            try: await context.bot.edit_message_text(chat_id, status_msg_id, get_text("generating_status_contacting_ai", chat_lang), parse_mode=ParseMode.HTML)
            except Exception: pass

        output_text, error_msg_friendly = await gc.safe_generate_output(
            messages_current, downloaded_images, output_format, chat_genre, personality_key, chat_lang
        )

        # Status update for formatting
        if output_text and status_msg_id:
            try: await context.bot.edit_message_text(chat_id, status_msg_id, get_text("generating_status_formatting", chat_lang, output_format_name=output_format_name), parse_mode=ParseMode.HTML)
            except Exception: pass

        # Sending result
        if output_text:
            photo_note = get_text("photo_info_text", chat_lang, count=len(downloaded_images)) if downloaded_images else ""
            header_key = "story_ready_header" # Generic key for on-demand header
            final_header = get_text(header_key, chat_lang, output_format_name_capital=output_format_name_capital, photo_info=photo_note)
            # Try editing status first, fallback to deleting and sending new
            try: await status_msg.edit_text(final_header, parse_mode=ParseMode.HTML)
            except Exception:
                logger.warning("Failed to edit status for header, deleting and sending new.")
                try: await status_msg.delete(); 
                except Exception: pass
                try: await context.bot.send_message(chat_id, final_header, parse_mode=ParseMode.HTML)
                except Exception as send_err: logger.error(f"Failed to send gen header: {send_err}"); raise # Propagate error

            # Send body parts
            sent_message = None
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data="feedback_good_p"), InlineKeyboardButton("👎", callback_data="feedback_bad_p")]])
            MAX_LEN = 4096
            parts = [output_text[i:i+MAX_LEN] for i in range(0, len(output_text), MAX_LEN)]
            for k, part in enumerate(parts):
                reply_markup = keyboard if k == len(parts)-1 else None
                sent_message = await context.bot.send_message(chat_id, part, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
                await asyncio.sleep(0.2) # Prevent rate limiting

            # Update feedback buttons with message ID
            if sent_message:
                 keyboard_updated = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_message.message_id}"), InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_message.message_id}")]])
                 try: await context.bot.edit_message_reply_markup(chat_id, sent_message.message_id, reply_markup=keyboard_updated)
                 except Exception as e: logger.warning(f"Err update feedback btns: {e}")

            logger.info(f"Generated {output_format} sent chat={chat_id}")
            if error_msg_friendly: # Send note if generation was successful but had notes (e.g., safety)
                 try: await context.bot.send_message(chat_id, get_text("proxy_note", chat_lang, note=error_msg_friendly), parse_mode=ParseMode.HTML)
                 except Exception as e: logger.warning(f"Failed proxy note: {e}")

        else: # Generation failed
            logger.warning(f"Failed gen {output_format} chat={chat_id}. Reason: {error_msg_friendly}")
            final_err_msg = get_text("generation_failed_user_friendly", chat_lang, output_format_name=output_format_name, reason=error_msg_friendly or 'Unknown')
            await notify_owner(context=context, message=f"Ошибка /generate_now ({output_format}): {error_msg_friendly}", chat_id=chat_id, important=True)
            try: await status_msg.edit_text(final_err_msg, parse_mode=ParseMode.HTML)
            except Exception: await update.message.reply_html(final_err_msg)

    except Exception as e: # General error handler
        logger.exception(f"Error in /generate_now chat={chat_id}: {e}")
        await notify_owner(context=context, message=f"Крит. ошибка /generate_now", chat_id=chat_id, exception=e, important=True)
        err_msg = get_text("error_telegram", chat_lang, error=e.__class__.__name__)
        try: await status_msg.edit_text(err_msg, parse_mode=ParseMode.HTML)
        except Exception: await update.message.reply_html(err_msg)

async def regenerate_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пересоздает последнюю историю/дайджест дня."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message: return
    chat_id = chat.id; chat_lang, _ = await get_chat_info(chat_id, context)
    logger.info(f"User {user.id} /regenerate_story chat={chat_id}")

    messages_current = dm.get_messages_for_chat(chat_id)
    settings = dm.get_chat_settings(chat_id)
    output_format = settings.get('output_format', 'story')
    output_format_name = get_output_format_name(output_format, chat_lang)
    output_format_name_capital = get_output_format_name(output_format, chat_lang, capital=True)

    if not messages_current:
        await update.message.reply_html(get_text("regenerate_no_data", chat_lang)); return

    status_msg = await update.message.reply_html(get_text("regenerating", chat_lang, output_format_name=output_format_name))
    output_text, error_msg_friendly = None, None
    try:
        downloaded_images = {}
        if output_format == 'story':
            downloaded_images = await download_images(context, messages_current, chat_id)

        chat_genre = settings.get('story_genre', 'default')
        personality_key = settings.get('story_personality', 'neutral')
        logger.info(f"[Chat {chat_id}] Regen: Format={output_format}, Genre={chat_genre}, Personality={personality_key}")

        output_text, error_msg_friendly = await gc.safe_generate_output(
            messages_current, downloaded_images, output_format, chat_genre, personality_key, chat_lang
        )
        try: await status_msg.delete()
        except Exception: pass # Delete "Regenerating..." message

        if output_text:
             # Send header as a new reply to the command
             photo_note = get_text("photo_info_text", chat_lang, count=len(downloaded_images)) if downloaded_images else ""
             final_header = get_text("story_ready_header", chat_lang, output_format_name_capital=output_format_name_capital, photo_info=photo_note)
             await update.message.reply_html(final_header)
             # Send body parts
             sent_msg=None; kbd=InlineKeyboardMarkup([[InlineKeyboardButton("👍",callback_data="feedback_good_p"), InlineKeyboardButton("👎", callback_data="feedback_bad_p")]])
             MAX_LEN=4096; parts=[output_text[i:i+MAX_LEN] for i in range(0,len(output_text),MAX_LEN)]
             for k,p in enumerate(parts): mkup=kbd if k==len(parts)-1 else None; sent_msg=await context.bot.send_message(chat_id,p,reply_markup=mkup, parse_mode=ParseMode.MARKDOWN); await asyncio.sleep(0.2)
             # Update feedback buttons
             if sent_msg: kbd_upd=InlineKeyboardMarkup([[InlineKeyboardButton("👍",callback_data=f"feedback_good_{sent_msg.message_id}"), InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_msg.message_id}")]]); 
             try: await context.bot.edit_message_reply_markup(chat_id, sent_msg.message_id, reply_markup=kbd_upd); 
             except Exception: pass
             logger.info(f"Regenerated {output_format} sent chat={chat_id}")
             if error_msg_friendly: 
                 try: await context.bot.send_message(chat_id, get_text("proxy_note", chat_lang, note=error_msg_friendly), parse_mode=ParseMode.HTML); 
                 except Exception as e: logger.warning(f"Failed regen proxy note: {e}")
        else: # Generation failed
            logger.warning(f"Failed regen {output_format} chat={chat_id}. Reason: {error_msg_friendly}")
            final_err_msg = get_text("generation_failed_user_friendly", chat_lang, output_format_name=output_format_name, reason=error_msg_friendly or 'Unknown')
            await notify_owner(context=context, message=f"Ошибка /regenerate ({output_format}): {error_msg_friendly}", chat_id=chat_id, important=True)
            await update.message.reply_html(final_err_msg)

    except Exception as e: # General error handler
        logger.exception(f"Error in /regenerate_story chat={chat_id}: {e}")
        await notify_owner(context=context, message=f"Крит. ошибка /regenerate", chat_id=chat_id, exception=e, important=True)
        err_msg = get_text("error_telegram", chat_lang, error=e.__class__.__name__)
        try: await status_msg.edit_text(err_msg, parse_mode=ParseMode.HTML); 
        except Exception: await update.message.reply_html(err_msg)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status (только владелец)."""
    user = update.effective_user
    if not user or user.id != BOT_OWNER_ID: return

    bd = context.application.bot_data
    start_time = bd.get('bot_start_time', time.time())
    # Получаем данные о задачах (могут быть None)
    last_job_dt = bd.get('last_daily_story_job_run_time')
    last_job_e = bd.get('last_daily_story_job_error')
    last_prg_dt = bd.get('last_purge_job_run_time')
    last_prg_e = bd.get('last_purge_job_error')

    # Расчет uptime
    uptime_str = str(datetime.timedelta(seconds=int(time.time() - start_time))) # <-- Правильное имя uptime_str
    enabled_chats = dm.get_enabled_chats()

    # Форматирование дат
    ljr_str = last_job_dt.strftime("%Y-%m-%d %H:%M:%S UTC") if last_job_dt else "Ни разу"
    lpr_str = last_prg_dt.strftime("%Y-%m-%d %H:%M:%S UTC") if last_prg_dt else "Ни разу"

    # Безопасное форматирование ошибок
    lje_safe = "Нет"
    if last_job_e and isinstance(last_job_e, str): lje_safe = html.escape(last_job_e[:500] + ('...' if len(last_job_e) > 500 else ''))
    elif last_job_e: lje_safe = html.escape(str(last_job_e)[:500] + '...')

    lpe_safe = "Нет"
    if last_prg_e and isinstance(last_prg_e, str): lpe_safe = html.escape(last_prg_e[:500] + ('...' if len(last_prg_e) > 500 else ''))
    elif last_prg_e: lpe_safe = html.escape(str(last_prg_e)[:500] + '...')

    from telegram import __version__ as ptb_version # Получаем версию библиотеки

    # --- ИСПРАВЛЕНО: Используем uptime=uptime_str ---
    status_text = get_text(
        "status_command_reply", DEFAULT_LANGUAGE,
        uptime=uptime_str, # <-- ИЗМЕНЕНО ЗДЕСЬ
        active_chats=len(enabled_chats),
        last_job_run=ljr_str,
        last_job_error=lje_safe,
        last_purge_run=lpr_str,
        last_purge_error=lpe_safe,
        ptb_version=ptb_version
    )
    # -----------------------------------------------

    await update.message.reply_html(status_text)

async def summarize_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /summarize - выбор периода."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message or chat.type == ChatType.PRIVATE: return
    chat_lang = await get_chat_lang(chat.id)
    # Используем префикс summary_period_ для колбэков
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text("summarize_button_today", chat_lang), callback_data="summary_period_today"), InlineKeyboardButton(get_text("summarize_button_last_1h", chat_lang), callback_data="summary_period_last_1h")],
        [InlineKeyboardButton(get_text("summarize_button_last_3h", chat_lang), callback_data="summary_period_last_3h"), InlineKeyboardButton(get_text("summarize_button_last_24h", chat_lang), callback_data="summary_period_last_24h")],
        [InlineKeyboardButton(get_text("button_close", chat_lang), callback_data="summary_period_cancel")]]) # Добавляем _cancel
    await update.message.reply_html(text=get_text("summarize_prompt_period", chat_lang), reply_markup=kbd)

async def chat_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """НОВАЯ: Команда /chat_stats - выбор периода."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message or chat.type == ChatType.PRIVATE: return
    chat_lang = await get_chat_lang(chat.id)
    # Используем префикс stats_period_
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text("stats_button_today", chat_lang), callback_data="stats_period_today"),
         InlineKeyboardButton(get_text("stats_button_week", chat_lang), callback_data="stats_period_week"),
         InlineKeyboardButton(get_text("stats_button_month", chat_lang), callback_data="stats_period_month")],
        [InlineKeyboardButton(get_text("button_close", chat_lang), callback_data="stats_period_cancel")]])
    await update.message.reply_html(text=get_text("stats_prompt_period", chat_lang), reply_markup=kbd)

async def purge_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """НОВАЯ: Команда /purge_history (админы)."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message or chat.type == ChatType.PRIVATE: return
    chat_id = chat.id; user_id = user.id
    chat_lang, _ = await get_chat_info(chat_id, context)

    if not await is_user_admin(chat_id, user_id, context): await update.message.reply_html(get_text("admin_only", chat_lang)); return

    args = context.args
    if not args: await update.message.reply_html(get_text("purge_no_args", chat_lang)); return

    period_key = args[0].lower(); days = 0; purge_param = ""; period_text = ""
    try:
        if period_key == "all": purge_param = "all"; period_text = get_text("purge_period_all", chat_lang)
        elif period_key == "days" and len(args) == 2 and (days := int(args[1])) > 0: purge_param = f"days_{days}"; period_text = get_text("purge_period_days", chat_lang, days=days)
        else: raise ValueError("Invalid args")
    except ValueError: await update.message.reply_html(get_text("purge_invalid_days", chat_lang)); return

    text = get_text("purge_prompt", chat_lang, period_text=period_text)
    # Используем префикс purge_ для колбэков
    kbd = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("purge_confirm", chat_lang), callback_data=f"purge_confirm_{purge_param}")],[InlineKeyboardButton(get_text("purge_cancel", chat_lang), callback_data="purge_cancel")]])
    await update.message.reply_html(text, reply_markup=kbd)


async def story_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /story_settings - вызывает главное меню настроек."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message or chat.type == ChatType.PRIVATE: return
    if not await is_user_admin(chat.id, user.id, context): await update.message.reply_html(get_text("admin_only", await get_chat_lang(chat.id))); return
    context.user_data.pop(PENDING_TIME_INPUT_KEY, None) # Сброс ожидания
    await _display_settings_main(update, context, chat.id, user.id)

# ==============================
# ОБРАБОТЧИКИ КОЛБЭКОВ
# ==============================

async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Единый обработчик ВСЕХ кнопок меню настроек (с префиксом 'settings_').
    Маршрутизирует вызовы к соответствующим функциям отображения,
    напрямую обновляет настройки, или инициирует ручной ввод для владельца.
    """
    query = update.callback_query
    if not query or not query.message: return
    await query.answer() # Ответ на колбек обязателен

    user = query.from_user
    chat = query.message.chat
    if not user or not chat: return 
    chat_id = chat.id
    user_id = user.id
    message_id = query.message.message_id
    data = query.data
    chat_lang = await get_chat_lang(chat_id)
    logger.info(f"Settings CB: user={user_id} chat={chat_id} data='{data}' msg={message_id}")

    # Проверка прав администратора перед любыми действиями (кроме 'settings_close')
    if data != 'settings_close' and not await is_user_admin(chat_id, user_id, context):
        await query.answer(get_text("admin_only", chat_lang), show_alert=True)
        return

    try:
        # =============================================
        # == Маршрутизация по основному действию ========
        # =============================================

        # --- Навигация и базовые переключатели ---
        if data == 'settings_main':
            context.user_data.pop(PENDING_TIME_INPUT_KEY, None) 
            context.user_data.pop(PENDING_INTERVENTION_INPUT_KEY, None) # Сброс ожидания ввода для вмешательств
            await _display_settings_main(update, context, chat_id, user_id)
        elif data == 'settings_close':
            context.user_data.pop(PENDING_TIME_INPUT_KEY, None)
            context.user_data.pop(PENDING_INTERVENTION_INPUT_KEY, None)
            try:
                await query.delete_message() 
            except (BadRequest, TelegramError) as e:
                 logger.warning(f"Failed to delete settings message on close: {e}")
        elif data == 'settings_toggle_status': 
             settings=dm.get_chat_settings(chat_id)
             success=dm.update_chat_setting(chat_id,'enabled', not settings.get('enabled',True))
             if success:
                 await _display_settings_main(update,context,chat_id,user_id)
                 await query.answer(get_text("settings_saved_popup",chat_lang))
             else: await query.answer(get_text("error_db_generic",chat_lang),show_alert=True)

        # --- Отображение Подменю ---
        elif data == 'settings_show_lang': await _display_settings_language(update, context, chat_id, user_id)
        elif data == 'settings_show_time': await _display_settings_time(update, context, chat_id, user_id)
        elif data == 'settings_show_tz': await _display_settings_timezone(update, context, chat_id, user_id)
        elif data == 'settings_show_genre': await _display_settings_genre(update, context, chat_id, user_id)
        elif data == 'settings_show_personality': await _display_settings_personality(update, context, chat_id, user_id)
        elif data == 'settings_show_format': await _display_settings_output_format(update, context, chat_id, user_id)
        elif data == 'settings_show_retention': await _display_settings_retention(update, context, chat_id, user_id)
        
        # Вмешательства
        elif data == 'settings_toggle_interventions':
             settings=dm.get_chat_settings(chat_id)
             new_state = not settings.get('allow_interventions',False)
             success=dm.update_chat_setting(chat_id,'allow_interventions', new_state)
             if success:
                 # Если отключаем вмешательства, то и настройки вмешательств не показываем, а идем в главное меню
                 await _display_settings_main(update,context,chat_id,user_id)
                 await query.answer(get_text("settings_saved_popup",chat_lang))
             else: await query.answer(get_text("error_db_generic",chat_lang),show_alert=True)
        
        elif data == 'settings_show_interventions': # Показать настройки вмешательств (главная кнопка или после изменения toggle)
            await _display_settings_interventions(update, context, chat_id, user_id)

        # --- Ручной ввод настроек Вмешательств (Только Владелец) ---
        elif data.startswith('settings_manual_') and user_id == BOT_OWNER_ID:
            setting_type_to_input = data.split('_')[-1] # cooldown, minmsgs, timespan
            db_key_map = {
                'cooldown': 'intervention_cooldown_minutes',
                'minmsgs': 'intervention_min_msgs',
                'timespan': 'intervention_timespan_minutes'
            }
            if setting_type_to_input not in db_key_map:
                logger.warning(f"Invalid manual input type: {setting_type_to_input} from callback {data}")
                await query.answer("Ошибка: неизвестный тип параметра для ввода.", show_alert=True)
                return

            # Сохраняем ID сообщения с меню и тип ожидаемого ввода
            context.user_data[PENDING_INTERVENTION_INPUT_KEY] = {
                'type': db_key_map[setting_type_to_input],
                'menu_message_id': query.message.message_id,
                'chat_id_for_menu': chat_id 
            }
            
            prompt_text_key = f"settings_intervention_manual_prompt_{setting_type_to_input}"
            min_val, max_val, _ = (0,0,0) # Инициализация
            if setting_type_to_input == 'cooldown': limits_tuple = get_intervention_value_limits('intervention_cooldown_minutes')
            elif setting_type_to_input == 'minmsgs': limits_tuple = get_intervention_value_limits('intervention_min_msgs')
            elif setting_type_to_input == 'timespan': limits_tuple = get_intervention_value_limits('intervention_timespan_minutes')
            else: limits_tuple = (0,0,0) 
            min_val, max_val = limits_tuple[0], limits_tuple[1]

            prompt_text = get_text(prompt_text_key, chat_lang, min_val=min_val, max_val=max_val)
            
            await query.answer(prompt_text, show_alert=True) 
            logger.info(f"Owner {user_id} in chat {chat_id} (menu_msg_id {query.message.message_id}) requested manual input for {db_key_map[setting_type_to_input]}. Waiting...")


        # =============================================
        # == Обработка УСТАНОВКИ ЗНАЧЕНИЙ через кнопки ==
        # =============================================
        elif data.startswith('settings_set_'):
            parts = data.split('_')
            # Формат 'settings_set_TYPE_VALUE' или 'settings_set_TYPE_SUBTYPE_VALUE'
            # Минимальное количество частей - 4 (settings_set_type_value)
            if len(parts) < 4: 
                logger.warning(f"Invalid set CB format (too few parts): {data}")
                return

            setting_type = parts[2] # lang, tz, genre, personality, format, retention, time, cooldown, minmsgs, timespan
            
            # Для таймзоны значение может содержать '_'
            if setting_type == 'tz':
                value_str = '_'.join(parts[3:])
            else:
                value_str = parts[-1] # Последняя часть как значение для большинства

            db_key = "" 
            db_value: Any = None 
            popup_message = get_text("settings_saved_popup", chat_lang) 
            needs_display_main = True 
            alert_on_error = True 
            was_corrected = False
            limits_tuple_for_correction = (0,0,0) # Для сообщения о коррекции (min, max, default)

            # Определяем ключ БД и значение в зависимости от типа
            if setting_type == 'lang' and value_str in SUPPORTED_LANGUAGES:
                 db_key = 'lang'; db_value = value_str; popup_message = get_text("settings_lang_selected", value_str)
            elif setting_type == 'tz': # Обрабатывается выше для value_str
                 db_key = 'timezone'; db_value = value_str
                 if db_value not in COMMON_TIMEZONES: logger.warning(f"Invalid TZ value: {db_value}"); return
                 popup_message = get_text("settings_tz_selected", chat_lang)
            elif setting_type == 'genre' and value_str in SUPPORTED_GENRES:
                 db_key = 'story_genre'; db_value = value_str; popup_message = get_text("settings_genre_selected", chat_lang)
            elif setting_type == 'personality' and value_str in SUPPORTED_PERSONALITIES:
                 db_key = 'story_personality'; db_value = value_str; popup_message = get_text("settings_personality_selected", chat_lang)
            elif setting_type == 'format' and value_str in SUPPORTED_OUTPUT_FORMATS:
                 db_key = 'output_format'; db_value = value_str; popup_message = get_text("settings_format_selected", chat_lang)
            elif setting_type == 'retention':
                 db_key = 'retention_days'; db_value = None if value_str == 'inf' else int(value_str)
                 popup_message = get_text("settings_retention_selected", chat_lang)
            elif setting_type == 'time' and value_str == 'default': 
                 db_key = 'custom_schedule_time'; db_value = None
                 # Можно сделать сообщение о сбросе времени специфичным
                 # popup_message = get_text("settings_time_reset_success_specific", chat_lang) 
            
            # Настройки Вмешательств (только через кнопки, не ручной ввод)
            elif setting_type == 'cooldown':
                 db_key = 'intervention_cooldown_minutes'
                 limits_tuple_for_correction = get_intervention_value_limits(db_key)
                 val_int = int(value_str); db_value = max(limits_tuple_for_correction[0], min(val_int, limits_tuple_for_correction[1]))
                 if db_value != val_int: was_corrected = True
                 needs_display_main = False # Остаемся в подменю вмешательств
                 popup_message = get_text("settings_interventions_saved_popup", chat_lang)
            elif setting_type == 'minmsgs':
                 db_key = 'intervention_min_msgs'
                 limits_tuple_for_correction = get_intervention_value_limits(db_key)
                 val_int = int(value_str); db_value = max(limits_tuple_for_correction[0], min(val_int, limits_tuple_for_correction[1]))
                 if db_value != val_int: was_corrected = True
                 needs_display_main = False
                 popup_message = get_text("settings_interventions_saved_popup", chat_lang)
            elif setting_type == 'timespan':
                 db_key = 'intervention_timespan_minutes'
                 limits_tuple_for_correction = get_intervention_value_limits(db_key)
                 val_int = int(value_str); db_value = max(limits_tuple_for_correction[0], min(val_int, limits_tuple_for_correction[1]))
                 if db_value != val_int: was_corrected = True
                 needs_display_main = False
                 popup_message = get_text("settings_interventions_saved_popup", chat_lang)
            
            else: 
                logger.warning(f"Unhandled 'settings_set_' type/value in callback: {data}")
                return 

            # Сохранение в БД
            if db_key: 
                success = dm.update_chat_setting(chat_id, db_key, db_value)
                if success:
                    if db_key == 'lang' and isinstance(db_value, str):
                        update_chat_lang_cache(chat_id, db_value)
                        # Здесь можно обновить команды бота, если они зависят от языка
                        # await _update_bot_commands_for_chat(context.bot, chat_id, db_value)

                    # Перерисовываем меню и показываем уведомление
                    if needs_display_main:
                        await _display_settings_main(update, context, chat_id, user_id)
                    else: 
                        await _display_settings_interventions(update, context, chat_id, user_id)

                    if was_corrected:
                        corrected_popup_text = get_text("error_value_corrected", chat_lang, 
                                                        corrected_value=db_value, 
                                                        min_val=limits_tuple_for_correction[0], 
                                                        max_val=limits_tuple_for_correction[1])
                        await query.answer(corrected_popup_text, show_alert=True)
                    else:
                        await query.answer(popup_message)
                else: 
                    await query.answer(get_text("error_db_generic", chat_lang), show_alert=alert_on_error)
            else:
                 logger.error(f"DB key was not determined for callback data: {data}") 

        else:
             logger.warning(f"Unknown settings callback data prefix or structure: {data}")

    except BadRequest as e:
        if "Message is not modified" in str(e): logger.debug(f"Settings CB BadRequest (not modified): {e} for data {data}")
        else: logger.error(f"BadRequest in settings CB handler for data {data}: {e}", exc_info=True)
    except TelegramError as e:
        logger.error(f"TelegramError in settings CB handler for data {data}: {e}", exc_info=True)
        try:
            await query.answer(get_text("error_telegram", chat_lang, error=e.__class__.__name__), show_alert=True)
        except Exception as answer_e: # Если даже ответ на коллбэк не удался
            logger.error(f"Failed to answer callback query after TelegramError: {answer_e}")
    except Exception as e:
        logger.exception(f"Unexpected error in settings CB handler for data {data}: {e}")
        try:
            await query.answer(get_text("error_db_generic", chat_lang), show_alert=True) 
        except Exception as answer_e:
            logger.error(f"Failed to answer callback query after unexpected error: {answer_e}")
        await notify_owner(context=context, message="Critical error in settings_callback_handler", chat_id=chat_id, user_id=user_id, operation=f"settings_callback: {data}", exception=e, important=True)


async def feedback_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопок фидбэка (^feedback_)."""
    query = update.callback_query
    if not query or not query.message: return
    await query.answer() # Ответ обязателен

    user = query.from_user; chat = query.message.chat
    if not user or not chat: return
    data = query.data; chat_lang = await get_chat_lang(chat.id)

    # Игнорируем плейсхолдеры
    if data.endswith('_p'): return

    if data.startswith("feedback_") and len(parts := data.split("_")) == 3:
        rtype, mid_str = parts[1], parts[2]
        try: mid = int(mid_str); rating = 1 if rtype=="good" else -1 if rtype=="bad" else 0
        except ValueError: logger.warning(f"Invalid feedback mid: {mid_str}"); return

        if rating != 0:
            dm.add_feedback(mid, chat.id, user.id, rating)
            try: await query.edit_message_reply_markup(reply_markup=None); await query.answer(get_text("feedback_thanks", chat_lang));
            except Exception: pass # Ignore errors removing buttons
        else: await query.answer("Invalid feedback type.", show_alert=True)
    else: logger.warning(f"Invalid feedback data: {data}")

async def summary_period_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик выбора периода для /summarize (^summary_period_)."""
    query = update.callback_query; await query.answer(); user = query.from_user; chat = query.message.chat
    if not user or not chat: return
    chat_lang, _ = await get_chat_info(chat.id, context); data = query.data

    if data == "summary_period_cancel": await query.edit_message_text(get_text("action_cancelled", chat_lang), reply_markup=None); return
    period_key = data.removeprefix("summary_period_")
    logger.info(f"User {user.id} summary period='{period_key}' chat={chat.id}")

    messages = []; now = datetime.datetime.now(pytz.utc); start_dt = None
    try:
        if period_key == "today": start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period_key == "last_1h": start_dt = now - datetime.timedelta(hours=1)
        elif period_key == "last_3h": start_dt = now - datetime.timedelta(hours=3)
        elif period_key == "last_24h": start_dt = now - datetime.timedelta(hours=24)
        else: logger.error(f"Unknown summary key: {period_key}"); return
        messages = dm.get_messages_for_chat_since(chat.id, start_dt)
    except Exception as db_err: logger.exception("DB err sum get msgs"); await query.edit_message_text(get_text("error_db_generic", chat_lang), reply_markup=None); return
    if not messages: await query.edit_message_text(get_text("summarize_no_messages", chat_lang), reply_markup=None); return

    status_msg = None; 
    try: await query.edit_message_text(get_text("summarize_generating", chat_lang), reply_markup=None); status_msg=query.message; 
    except Exception:pass
    if context.bot:
        try: await context.bot.send_chat_action(chat.id, ChatAction.TYPING); 
        except Exception: pass

    summary, err_msg = await gc.safe_generate_summary(messages, chat_lang)
    try: # Send result
        if status_msg: 
            try: await status_msg.delete(); 
            except Exception:pass
        if summary:
             period_name = get_text(f"summarize_period_name_{period_key}", chat_lang)
             header = get_text("summarize_header", chat_lang, period_name=period_name)
             await context.bot.send_message(chat.id, header, parse_mode=ParseMode.HTML)
             try: await context.bot.send_message(chat.id, summary, parse_mode=ParseMode.MARKDOWN)
             except Exception as md_err: logger.error(f"Fail send summary MD: {md_err}, try plain"); await context.bot.send_message(chat.id, summary)
             logger.info(f"Summary sent p={period_key} c={chat.id}")
             if err_msg: 
                 try: await context.bot.send_message(chat.id, get_text("proxy_note", chat_lang, note=err_msg), parse_mode=ParseMode.HTML); 
                 except Exception: pass
        else: # Gen fail
            reason = err_msg or 'Unknown'
            logger.warning(f"Fail gen sum p={period_key} c={chat.id}: {reason}")
            err_txt = get_text("summarize_failed_user_friendly", chat_lang, reason=reason)
            await context.bot.send_message(chat.id, err_txt, parse_mode=ParseMode.HTML)
    except Exception as e: logger.exception(f"Err proc summary res c={chat.id}: {e}"); await context.bot.send_message(chat.id, get_text("error_db_generic", chat_lang))


async def stats_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """НОВЫЙ: Обработка выбора периода для /chat_stats (^stats_period_)."""
    query = update.callback_query; await query.answer(); user = query.from_user; chat = query.message.chat
    if not user or not chat: return
    chat_id = chat.id # Сохраняем ID
    chat_lang, _ = await get_chat_info(chat_id, context); data = query.data # Используем chat_id

    if data == "stats_period_cancel": await query.edit_message_text(get_text("action_cancelled", chat_lang), reply_markup=None); return
    period_key = data.removeprefix("stats_period_"); now = datetime.datetime.now(pytz.utc); start_dt = None;

    if period_key == "today": start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period_key == "week": start_dt = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period_key == "month": start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else: logger.error(f"Unknown stats key: {period_key}"); return

    stats_data = None; error_msg = None
    try:
        stats_data = dm.get_chat_stats(chat_id, start_dt) # Используем сохраненный chat_id
    except Exception as e:
        # Ловим ошибки уровня выше, если get_chat_stats не обработал
        logger.exception(f"Failed get stats c={chat_id} p={period_key}")
        error_msg = get_text("stats_error", chat_lang)

    # --- ИСПРАВЛЕНО: Упрощенная проверка ---
    # Если произошла ошибка ИЛИ get_chat_stats вернул None ИЛИ сообщений 0
    if error_msg or not stats_data or stats_data.get('total_messages', 0) == 0:
        # -----------------------------------------
        final_message = error_msg or get_text("stats_no_data", chat_lang) # Показываем ошибку или "нет данных"
        logger.info(f"Stats result for chat {chat_id} p={period_key}: No data or error ({error_msg=})")
        await query.edit_message_text(final_message, reply_markup=None);
        return

    # Форматируем результат (как раньше)
    period_name = get_stats_period_name(period_key, chat_lang)
    text = get_text("stats_title", chat_lang, period_name=period_name) + "\n\n"
    text += get_text("stats_total_messages", chat_lang, count=stats_data['total_messages']) + "\n"
    text += get_text("stats_photos", chat_lang, count=stats_data['photos']) + "\n"
    text += get_text("stats_stickers", chat_lang, count=stats_data['stickers']) + "\n"
    text += get_text("stats_active_users", chat_lang, count=stats_data['active_users']) + "\n" # Добавили отображение active_users
    if stats_data.get('top_users'): # Проверка, что список не пустой
        text += "\n" + get_text("stats_top_users_header", chat_lang) + "\n"
        for username, count in stats_data['top_users']:
             safe_username = html.escape(username or '??')
             text += get_text("stats_user_entry", chat_lang, username=safe_username, count=count) + "\n"

    try:
        await query.edit_message_text(text.strip(), reply_markup=None, parse_mode=ParseMode.HTML)
        logger.info(f"Stats displayed for chat {chat_id} p={period_key}")
    except BadRequest as e: # Обработка ошибки, если сообщение не изменилось
        if "Message is not modified" in str(e): logger.debug(f"Stats message not modified chat={chat_id}")
        else: logger.error(f"Failed to edit stats message chat={chat_id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error editing stats message chat={chat_id}: {e}")
        
        
async def purge_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """НОВЫЙ: Обработка подтверждения очистки (^purge_)."""
    query = update.callback_query; await query.answer(); user = query.from_user; chat = query.message.chat
    if not user or not chat: return
    chat_id = chat.id; user_id = user.id
    chat_lang, _ = await get_chat_info(chat_id, context); data = query.data

    if not await is_user_admin(chat_id, user_id, context): await query.answer(get_text("admin_only", chat_lang), show_alert=True); return

    if data == "purge_cancel": await query.edit_message_text(get_text("purge_cancelled", chat_lang), reply_markup=None); return

    if data.startswith("purge_confirm_"):
        param = data.removeprefix("purge_confirm_"); period_text = ""
        try:
            if param == "all": dm.clear_messages_for_chat(chat_id); period_text = get_text("purge_period_all", chat_lang)
            elif param.startswith("days_") and (days := int(param.split('_')[-1])) > 0: dm.delete_messages_older_than(chat_id, days); period_text = get_text("purge_period_days", chat_lang, days=days)
            else: raise ValueError("Invalid purge param")
            await query.edit_message_text(get_text("purge_success", chat_lang, period_text=period_text), reply_markup=None)
            await notify_owner(context=context, message=f"Очистка истории ({param})", chat_id=chat_id, user_id=user_id, important=True)
        except Exception as e: logger.error(f"Err purging c={chat_id} p={param}: {e}"); await query.edit_message_text(get_text("purge_error", chat_lang), reply_markup=None); await notify_owner(context=context, message=f"Ошибка очистки ({param})", chat_id=chat_id, user_id=user_id, exception=e, important=True)
    else: logger.warning(f"Unknown purge CB: {data}")

# ==============================
# ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ==============================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.from_user or message.from_user.is_bot: return
    chat = message.chat
    user = message.from_user
    if not chat or not user: return
    chat_id = chat.id
    user_id = user.id

    # --- 1. Обработка ожидаемого ввода ВРЕМЕНИ для настроек ---
    pending_time_input_msg_id = context.user_data.get(PENDING_TIME_INPUT_KEY)
    if pending_time_input_msg_id is not None and message.text and not message.text.startswith('/'):
        await _handle_time_input(update, context, chat_id, user_id, pending_time_input_msg_id, message)
        return

    # --- 2. Обработка ожидаемого ввода НАСТРОЙКИ ВМЕШАТЕЛЬСТВА от владельца ---
    # PENDING_INTERVENTION_INPUT_KEY хранит {'type': ..., 'menu_message_id': ..., 'chat_id_for_menu': ...}
    pending_intervention_details = context.user_data.get(PENDING_INTERVENTION_INPUT_KEY)
    if pending_intervention_details and \
       user_id == BOT_OWNER_ID and \
       message.text and \
       not message.text.startswith('/'):
        
        menu_chat_id_for_setting = pending_intervention_details.get('chat_id_for_menu')
        # Убедимся, что владелец отвечает в том же чате, где было открыто меню,
        # или в ЛС боту (если это разрешено и chat_id == user_id).
        # Пока считаем, что владелец должен отвечать в том же чате, где меню.
        if chat_id == menu_chat_id_for_setting:
            await _handle_intervention_setting_input(
                update, context, chat_id, user_id, # chat_id = menu_chat_id_for_setting
                pending_intervention_details['type'], 
                pending_intervention_details['menu_message_id'], 
                message
            )
            return 

    # --- 3. Обработка ответа на СООБЩЕНИЕ БОТА (возможно, на вмешательство) ---
    if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
        active_intervention_msg_id = context.chat_data.get(ACTIVE_INTERVENTION_CHAIN_MESSAGE_ID_KEY)
        if active_intervention_msg_id and message.reply_to_message.message_id == active_intervention_msg_id:
            logger.info(f"User {user_id} in chat {chat_id} replied to an active intervention chain message {active_intervention_msg_id}.")
            asyncio.create_task(_handle_reply_to_intervention_chain(
                context, chat_id, message.reply_to_message, message
            ))
            # Сообщение пользователя (ответ на вмешательство) НЕ сохраняем в БД и не триггерим на него новое обычное вмешательство.
            return 

    # --- 4. Сохранение обычного сообщения (только из групп) ---
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        timestamp = message.date or datetime.datetime.now(pytz.utc)
        username = user.username or user.first_name or f"User_{user_id}"
        m_data = {'message_id': message.message_id, 'user_id': user_id, 'username': username, 'timestamp': timestamp.isoformat(), 'type': 'unknown', 'content': None,'file_id': None, 'file_unique_id': None, 'file_name': None }
        f_info=None; m_type='unknown'
        if message.text: m_type='text'; m_data['content']=message.text
        elif message.sticker: m_type='sticker'; m_data['content']=message.sticker.emoji; f_info=message.sticker
        elif message.photo: m_type='photo'; m_data['content']=message.caption; f_info=message.photo[-1]
        elif message.video: m_type = 'video'; m_data['content'] = message.caption; f_info = message.video
        elif message.audio: m_type = 'audio'; m_data['content'] = message.caption; f_info = message.audio
        elif message.voice: m_type = 'voice'; f_info = message.voice
        elif message.video_note: m_type = 'video_note'; f_info = message.video_note
        elif message.document: m_type = 'document'; m_data['content'] = message.caption; f_info = message.document
        elif message.caption: m_type = 'media_with_caption'; m_data['content'] = message.caption
        m_data['type'] = m_type
        if f_info: 
            try: m_data['file_id']=f_info.file_id; m_data['file_unique_id']=f_info.file_unique_id; m_data['file_name']=getattr(f_info,'file_name',None); 
            except Exception:pass
        
        if m_data['type'] != 'unknown': 
            dm.add_message(chat_id, m_data)

        # --- 5. Проверка на ОБЫЧНОЕ Вмешательство ---
        if m_data['type'] == 'text' and m_data['content']:
            await _check_and_trigger_intervention(chat_id, context)


# ==============================
# ВНУТРЕННИЕ ОБРАБОТЧИКИ UI и Логики
# ==============================

async def _handle_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, pending_msg_id: int, user_message: Message):
    """
    Внутр: Обрабатывает ввод времени, ОКРУГЛЯЕТ и сохраняет.
    (Основано на вашей версии + добавлено округление)
    """
    logger.debug(f"Handle time input u={user_id} c={chat_id} for msg_id={pending_msg_id}")
    context.user_data.pop(PENDING_TIME_INPUT_KEY, None) # Снимаем флаг ожидания

    chat_lang, _ = await get_chat_info(chat_id, context)
    chat_tz_str = dm.get_chat_timezone(chat_id)
    input_time_str = user_message.text.strip()
    back_button_kbd = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")]])

    # 1. Валидация формата
    match = re.fullmatch(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$", input_time_str)
    if not match:
        error_text = get_text("settings_time_invalid_format", chat_lang)
        try: await context.bot.edit_message_text(chat_id, pending_msg_id, error_text, ParseMode.HTML, reply_markup=back_button_kbd);
        except (BadRequest, TelegramError) as e: logger.warning(f"Failed edit msg invalid format: {e}")
        try: await user_message.delete()
        except Exception: pass
        return

    hour_local = int(match.group("hour"))
    minute_local = int(match.group("minute"))

    # --- ДОБАВЛЕНО: Логика Округления ---
    rounded_minute = minute_local
    rounded_time_str = input_time_str # Время для отображения пользователю (округленное)
    interval = JOB_CHECK_INTERVAL_MINUTES
    if minute_local % interval != 0:
        rounded_minute = (minute_local // interval) * interval
        rounded_time_str = f"{hour_local:02d}:{rounded_minute:02d}"
        logger.info(f"Input time {input_time_str} rounded down to {rounded_time_str} c={chat_id} (int {interval})")
    # --- КОНЕЦ Добавленной Логики Округления ---

    # 3. Конвертация (Округленного) времени в UTC
    utc_time_save = None; tz_short = '??'
    try:
        local_tz = pytz.timezone(chat_tz_str); now_naive = datetime.datetime.now(local_tz).replace(tzinfo=None)
        # --- ИСПОЛЬЗУЕТСЯ rounded_minute ---
        time_naive = now_naive.replace(hour=hour_local, minute=rounded_minute, second=0, microsecond=0)
        # ----------------------------------
        time_aware = local_tz.localize(time_naive, is_dst=None); t_utc=time_aware.astimezone(pytz.utc)
        utc_time_save=t_utc.strftime("%H:%M"); tz_short=time_aware.strftime('%Z')
        logger.info(f"C={chat_id} Input='{input_time_str}' Rounded='{rounded_time_str}' TZ={chat_tz_str}->UTC={utc_time_save}")
    except Exception as e:
         logger.error(f"Err conv time c={chat_id} rounded_t='{rounded_time_str}': {e}", exc_info=True)
         err_txt = get_text("error_db_generic", chat_lang)
         try: await context.bot.edit_message_text(chat_id,pending_msg_id,err_txt,reply_markup=back_button_kbd); await user_message.delete();
         except Exception: pass
         return

    # 4. Сохранение UTC времени в БД
    success = dm.update_chat_setting(chat_id, 'custom_schedule_time', utc_time_save)

    # 5. Формирование ответного сообщения
    if success:
        # --- Используем округленное время для сообщения ---
        success_text = get_text(
            "settings_time_success", chat_lang,
            local_time=rounded_time_str, # <-- Показываем округленное
            tz_short=tz_short,
            utc_time=utc_time_save
        )
        # -----------------------------------------------
    else: # Ошибка сохранения БД
         success_text = get_text("error_db_generic", chat_lang)

    # 6. Обновление сообщения настроек (с улучшенной обработкой ошибок)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=pending_msg_id, text=success_text,
            parse_mode=ParseMode.HTML, reply_markup=back_button_kbd
        )
    except (BadRequest, TelegramError) as e:
        error_text_lower = str(e).lower()
        if "message is not modified" in error_text_lower:
            logger.debug(f"Time setting message {pending_msg_id} not modified.")
        elif "chat not found" in error_text_lower or "message to edit not found" in error_text_lower:
            logger.warning(f"Orig settings msg {pending_msg_id} not found c={chat_id}. Sending new confirmation.")
            try: await update.effective_message.reply_html(success_text, reply_markup=back_button_kbd)
            except Exception as send_e: logger.error(f"Failed to send fallback time set confirmation: {send_e}")
        else:
            logger.error(f"Failed to edit settings msg {pending_msg_id} c={chat_id}: {e}")
            # Опционально: Fallback на отправку нового сообщения при других ошибках
            # try: await update.effective_message.reply_html(success_text, reply_markup=back_button_kbd); logger.info(f"Sent new confirmation for msg {pending_msg_id} due to edit error: {e.__class__.__name__}")
            # except Exception as send_e: logger.error(f"Failed to send fallback confirmation after edit error: {send_e}")

    # Удаляем сообщение пользователя в любом случае
    try: await user_message.delete()
    except (BadRequest, TelegramError) as e: logger.warning(f"Failed del user time input msg post-process: {e}")
    except Exception as e: logger.error(f"Unexp err del user time input msg: {e}")

async def _check_and_trigger_intervention(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
     """Внутр: Проверяет условия и запускает фоновую задачу вмешательства."""
     inter_settings = dm.get_intervention_settings(chat_id)
     if not inter_settings.get('allow_interventions'): return

     now_ts = int(time.time()); last_ts = inter_settings.get('last_intervention_ts', 0); cooldown_sec = inter_settings.get('cooldown_minutes', INTERVENTION_DEFAULT_COOLDOWN_MIN) * 60
     if now_ts < last_ts + cooldown_sec: logger.debug(f"Chat {chat_id}: Intervention cooldown."); return

     timespan_min = inter_settings.get('timespan_minutes', INTERVENTION_DEFAULT_TIMESPAN_MIN); min_msgs_req = inter_settings.get('min_msgs', INTERVENTION_DEFAULT_MIN_MSGS)
     since_dt = datetime.datetime.now(pytz.utc) - datetime.timedelta(minutes=timespan_min)
     recent_msg_count = dm.count_messages_since(chat_id, since_dt)
     if recent_msg_count < min_msgs_req: logger.debug(f"Chat {chat_id}: Interv skip - only {recent_msg_count}/{min_msgs_req} msgs in {timespan_min}m."); return

     logger.info(f"Chat {chat_id}: Intervention conditions met. Creating task...")
     asyncio.create_task(_try_send_intervention(chat_id, context)) # Fire and forget


async def _try_send_intervention(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Внутренняя ФОНОВАЯ ЗАДАЧА: Генерирует и пытается отправить
    комментарий-вмешательство в чат, используя контекст последних N сообщений
    и информацию об авторах. При успехе обновляет chat_data для отслеживания цепочки.
    """
    if not context.bot:
        logger.error(f"Intervention task c={chat_id}: Bot object not found in context.")
        return

    log_prefix = f"Intervention task c={chat_id}:"

    try:
        # 1. Получаем настройки
        settings = dm.get_chat_settings(chat_id) # Получаем все настройки один раз
        if not settings.get('allow_interventions', False):
            logger.debug(f"{log_prefix} Interventions disabled during processing.")
            return

        personality = settings.get('story_personality', DEFAULT_PERSONALITY)
        lang = settings.get('lang', DEFAULT_LANGUAGE)

        # 2. Получаем ПОСЛЕДНИЕ N сообщений (с полной информацией)
        last_n_messages = []
        try:
            limit_msgs = INTERVENTION_PROMPT_MESSAGE_COUNT
            # Получаем сообщения, включая разные типы, чтобы корректно извлечь username
            last_n_messages = dm.get_messages_for_chat_last_n(
                chat_id,
                limit=limit_msgs,
                only_text=False # Нам нужны имена и типы для формирования context_log_entries
            )
            logger.debug(f"{log_prefix} Fetched last {len(last_n_messages)} messages (limit: {limit_msgs}).")
            if not last_n_messages:
                logger.debug(f"{log_prefix} No recent messages found for intervention context.")
                return 
        except Exception as db_err:
            logger.error(f"{log_prefix} Failed to fetch last N messages: {db_err}", exc_info=True)
            return

        # 3. Формирование КОНТЕКСТА С ИМЕНАМИ для промпта
        context_log_entries = [] 
        if last_n_messages:
            logger.debug(f"{log_prefix} Filtering {len(last_n_messages)} records for prompt context log...")
            try:
                for m in last_n_messages:
                    if not isinstance(m, dict): continue 

                    username = m.get('username', 'Неизвестный') 
                    msg_type = m.get('type')
                    content = m.get('content', '')

                    log_line = ""
                    if msg_type == 'text' and content:
                        text_preview = content[:100].strip() + ('...' if len(content) > 100 else '')
                        log_line = f"{username}: {text_preview}"
                    elif msg_type == 'photo':
                        caption_preview = (': ' + content[:50].strip() + ('...' if len(content) > 50 else '')) if content else ''
                        log_line = f"{username}: [отправил(а) фото]{caption_preview}"
                    elif msg_type == 'sticker':
                         emoji = f" ({content})" if content else ""
                         log_line = f"{username}: [отправил(а) стикер]{emoji}"
                    # Добавьте другие типы по необходимости для более богатого контекста
                    # else:
                    #     log_line = f"{username}: [отправил(а) сообщение типа {msg_type}]"
                    if log_line:
                        context_log_entries.append(log_line)
                logger.info(f"{log_prefix} Created {len(context_log_entries)} log entries for prompt.")
            except Exception as filter_err:
                logger.error(f"{log_prefix} Error during message filtering for context log: {filter_err}", exc_info=True)
                return 

        logger.debug(f"{log_prefix} -----> context_log_entries list BEFORE prompt build (sample): {context_log_entries[:5]}")

        if not context_log_entries:
            logger.debug(f"{log_prefix} context_log_entries list is empty after filtering, skipping intervention.")
            return

        # 4. Построение промпта
        intervention_prompt_string = None
        try:
            intervention_prompt_string = pb.build_intervention_prompt(context_log_entries, personality)
            if not intervention_prompt_string:
                 logger.warning(f"{log_prefix} Prompt builder returned None/empty string for intervention.")
                 return
            logger.debug(f"{log_prefix} Intervention prompt built (length: {len(intervention_prompt_string)}).")
        except Exception as build_err:
            logger.error(f"{log_prefix} Error building intervention prompt: {build_err}", exc_info=True)
            return

        logger.debug(f"{log_prefix} Generated prompt sample for intervention: {intervention_prompt_string[:200]}...")

        # 5. Вызов Gemini
        # Используем safe_generate_intervention, который принимает строку промпта
        intervention_text = await gc.safe_generate_intervention(intervention_prompt_string, lang)

        # 6. Обработка ответа и отправка
        if intervention_text:
            # Повторно проверяем кулдаун на случай, если генерация была долгой
            # Используем свежие настройки вмешательств, т.к. они могли измениться
            inter_settings_recheck = dm.get_intervention_settings(chat_id) 
            # И проверяем, что вмешательства все еще разрешены
            if not inter_settings_recheck.get('allow_interventions', False):
                logger.info(f"{log_prefix} Interventions were disabled while AI was generating. Skipped sending.")
                return

            now_ts_for_send = int(time.time())
            last_ts_from_db = inter_settings_recheck.get('last_intervention_ts', 0)
            cd_minutes = inter_settings_recheck.get('cooldown_minutes', INTERVENTION_DEFAULT_COOLDOWN_MIN)
            cd_sec = cd_minutes * 60

            if now_ts_for_send >= last_ts_from_db + cd_sec:
                logger.info(f"{log_prefix} Sending intervention '{intervention_text[:50]}...' (Personality: {personality})")
                try:
                    sent_intervention_msg = await context.bot.send_message(chat_id=chat_id, text=intervention_text)
                    # Обновляем время последнего *успешного* вмешательства
                    dm.update_chat_setting(chat_id, 'last_intervention_ts', now_ts_for_send)
                    logger.info(f"{log_prefix} Last intervention timestamp updated to {now_ts_for_send} in DB.")
                    
                    # Сохраняем ID этого вмешательства как начало/продолжение активной цепочки
                    # Это сообщение, на которое пользователь может ответить, и бот продолжит диалог.
                    if context.chat_data is not None:
                        context.chat_data[ACTIVE_INTERVENTION_CHAIN_MESSAGE_ID_KEY] = sent_intervention_msg.message_id
                        logger.info(f"{log_prefix} Set active intervention chain message_id to {sent_intervention_msg.message_id} in chat_data.")
                    else:
                        # Это маловероятно, но лучше проверить
                        logger.warning(f"{log_prefix} context.chat_data is None. Cannot set active intervention chain message_id.")

                except TelegramError as send_err:
                     logger.error(f"{log_prefix} Failed to send intervention message: {send_err}")
                     # Здесь не сбрасываем ACTIVE_INTERVENTION_CHAIN_MESSAGE_ID_KEY, т.к. сообщение не было отправлено
                except Exception as send_generic_err:
                     logger.error(f"{log_prefix} Unexpected error sending intervention message: {send_generic_err}", exc_info=True)
            else:
                # Кулдаун активировался во время генерации, или настройки изменились так, что кулдаун еще не прошел
                logger.info(f"{log_prefix} Cooldown became active during AI generation or settings changed. Cooldown ends at {last_ts_from_db + cd_sec} (current: {now_ts_for_send}). Skipped sending intervention.")
        else:
            logger.debug(f"{log_prefix} No intervention text generated by AI (or AI indicated no reply needed).")

    except Exception as e:
        # Это ловит ошибки, не пойманные внутри блоков try/except (например, критические ошибки в dm)
        logger.error(f"CRITICAL Error in intervention task main try-block for chat c={chat_id}: {e}", exc_info=True)


# ==================
# ФУНКЦИИ ОТОБРАЖЕНИЯ МЕНЮ НАСТРОЕК (Редактирование сообщения)
# ==================

# --- ГЛАВНОЕ МЕНЮ ---
async def _display_settings_main(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Отображает ГЛАВНОЕ меню настроек."""
    chat_lang, chat_title_safe = await get_chat_info(chat_id, context)
    settings = dm.get_chat_settings(chat_id)

    # --- БОЛЕЕ БЕЗОПАСНОЕ ПОЛУЧЕНИЕ ЧАСОВОГО ПОЯСА ---
    # 1. Получаем строку таймзоны из настроек
    chat_tz_str = settings.get('timezone')
    # 2. Если она None или пустая, используем 'UTC' как дефолт
    if not chat_tz_str:
        logger.warning(f"Chat {chat_id} has missing timezone in settings, defaulting to UTC.")
        chat_tz_str = 'UTC'
        # Опционально: можно попытаться исправить это в БД для будущих вызовов
        # try:
        #     dm.update_chat_setting(chat_id, 'timezone', 'UTC')
        # except Exception as db_fix_e:
        #     logger.error(f"Failed to fix missing timezone in DB for chat {chat_id}: {db_fix_e}")

    # 3. Получаем отображаемое имя, используя гарантированно не-None chat_tz_str
    #    Если chat_tz_str нет в словаре COMMON_TIMEZONES, просто покажем саму строку (например, 'Asia/Yakutsk')
    tz_display_name = COMMON_TIMEZONES.get(chat_tz_str, chat_tz_str)
    # --- КОНЕЦ БЕЗОПАСНОГО ПОЛУЧЕНИЯ ЧАСОВОГО ПОЯСА ---

    # Получение и форматирование остальных текущих значений для отображения
    status_text = get_text("enabled_status" if settings.get('enabled', True) else "disabled_status", chat_lang)
    # --- Язык ---
    lang_code = settings.get('lang', DEFAULT_LANGUAGE)
    lang_name = LOCALIZED_TEXTS.get(lang_code, {}).get("lang_name", lang_code) # Безопасное получение имени языка
    # --- Жанр ---
    genre_key = settings.get('story_genre', 'default')
    genre_display_name = get_genre_name(genre_key, chat_lang)
    # --- Личность ---
    personality_key = settings.get('story_personality', DEFAULT_PERSONALITY)
    personality_display_name = get_personality_name(personality_key, chat_lang)
    # --- Формат ---
    output_format_key = settings.get('output_format', DEFAULT_OUTPUT_FORMAT)
    output_format_display_name = get_output_format_name(output_format_key, chat_lang)
    # --- Хранение ---
    retention_display = format_retention_days(settings.get('retention_days'), chat_lang)
    # --- Вмешательства ---
    interventions_allowed = settings.get('allow_interventions', False)
    intervention_status_text = get_text("settings_interventions_enabled" if interventions_allowed else "settings_interventions_disabled", chat_lang)

    # Форматирование времени генерации (теперь использует безопасный chat_tz_str)
    custom_time_utc_str = settings.get('custom_schedule_time')
    time_display = "" # Инициализируем
    try:
        schedule_h_utc, schedule_m_utc = SCHEDULE_HOUR, SCHEDULE_MINUTE # Дефолтные UTC
        if custom_time_utc_str:
            ch, cm = map(int, custom_time_utc_str.split(':'))
            schedule_h_utc, schedule_m_utc = ch, cm # Используем кастомные UTC

        # Вызываем форматирование с гарантированно не-None chat_tz_str
        local_time_str, tz_short = format_time_for_chat(schedule_h_utc, schedule_m_utc, chat_tz_str)

        time_display = f"{local_time_str} {tz_short}"
        if custom_time_utc_str:
            time_display += f" ({custom_time_utc_str} UTC)"
        else:
            time_display = f"~{time_display} ({schedule_h_utc:02d}:{schedule_m_utc:02d} UTC)" # Добавляем тильду для дефолта

    except Exception as time_fmt_e:
         logger.error(f"Error formatting display time for chat {chat_id}: {time_fmt_e}")
         # Отображаем запасной вариант, если форматирование не удалось
         time_display = f"{custom_time_utc_str} UTC" if custom_time_utc_str else f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} UTC (Default)"


    # Сборка текста сообщения настроек (без изменений)
    text = get_text("settings_title", chat_lang, chat_title=chat_title_safe) + "\n\n"
    text += f"▪️ {get_text('settings_status_label', chat_lang)}: {status_text}\n"
    text += f"▪️ {get_text('settings_language_label', chat_lang)}: {lang_name}\n"
    text += f"▪️ {get_text('settings_output_format_label', chat_lang)}: {output_format_display_name}\n"
    text += f"▪️ {get_text('settings_personality_label', chat_lang)}: {personality_display_name}\n"
    text += f"▪️ {get_text('settings_genre_label', chat_lang)}: {genre_display_name}\n"
    text += f"▪️ {get_text('settings_time_label', chat_lang)}: {time_display}\n"
    text += f"▪️ {get_text('settings_timezone_label', chat_lang)}: {tz_display_name}\n" # Использует безопасный tz_display_name
    text += f"▪️ {get_text('settings_retention_label', chat_lang)}: {retention_display}\n"
    text += f"▪️ {get_text('settings_interventions_label', chat_lang)}: {intervention_status_text}"

    # Сборка клавиатуры (без изменений в логике рядов, но использует безопасные переменные)
    # Ряд 1: Статус
    row1 = [InlineKeyboardButton(
        get_text("settings_button_toggle_on" if settings.get('enabled', True) else "settings_button_toggle_off", chat_lang),
        callback_data='settings_toggle_status'
    )]
    # Ряд 2: Язык, Формат
    row2 = [
        InlineKeyboardButton(f"🌐 {lang_name.split(' ')[0]}", callback_data='settings_show_lang'),
        InlineKeyboardButton(f"📜 {output_format_display_name}", callback_data='settings_show_format')
    ]
    # Ряд 3: Личность, Жанр
    row3 = [
        InlineKeyboardButton(f"👤 {personality_display_name}", callback_data='settings_show_personality'),
        InlineKeyboardButton(f"🎭 {genre_display_name}", callback_data='settings_show_genre')
    ]
    # Ряд 4: Время, Таймзона (теперь безопасно)
    # Безопасное отображение времени (избегаем split если time_display - запасной вариант)
    time_button_text = time_display.split(' ')[0] if ' ' in time_display else time_display
    # Безопасное отображение таймзоны (tz_display_name гарантированно не None)
    tz_button_text = tz_display_name.split(' ')[0]
    row4 = [
        InlineKeyboardButton(f"⏰ {time_button_text}", callback_data='settings_show_time'),
        InlineKeyboardButton(f"🌍 {tz_button_text}", callback_data='settings_show_tz')
    ]
    # Ряд 5: Хранение, Вмешательства (без изменений)
    row5 = [
        InlineKeyboardButton(f"💾 {retention_display}", callback_data='settings_show_retention')
    ]
    if interventions_allowed:
        inter_btn_text = get_text("settings_interventions_label", chat_lang)
        inter_cb = 'settings_show_interventions'
        row5.append(InlineKeyboardButton(f"⚙️ {inter_btn_text}", callback_data=inter_cb))
    else:
        inter_btn_text = get_text("settings_button_toggle_interventions_off", chat_lang)
        inter_cb = 'settings_toggle_interventions'
        row5.append(InlineKeyboardButton(f"🤖 {inter_btn_text}", callback_data=inter_cb))

    # Ряд 6: Закрыть
    row6 = [InlineKeyboardButton(get_text("button_close", chat_lang), callback_data='settings_close')]

    keyboard_markup = InlineKeyboardMarkup([row1, row2, row3, row4, row5, row6])

    # Отправка или Редактирование сообщения (без изменений)
    query = update.callback_query
    if query and query.message:
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard_markup,
                parse_mode=ParseMode.HTML
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                logger.error(f"BadRequest editing settings message: {e}", exc_info=True)
        except TelegramError as e:
             logger.error(f"TelegramError editing settings message: {e}", exc_info=True)

    elif update.message:
        try:
            await update.message.reply_html(text=text, reply_markup=keyboard_markup)
        except TelegramError as e:
             logger.error(f"TelegramError sending new settings message: {e}", exc_info=True)

# --- ПОДМЕНЮ ЯЗЫКА ---
async def _display_settings_language(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Подменю выбора языка."""
    chat_lang = await get_chat_lang(chat_id); current_lang = dm.get_chat_settings(chat_id).get('lang')
    text = get_text("settings_select_language_title", chat_lang); btns = []
    for code in SUPPORTED_LANGUAGES: pre = "✅ " if code == current_lang else ""; name = LOCALIZED_TEXTS.get(code, {}).get("lang_name", code); btns.append([InlineKeyboardButton(f"{pre}{name}", callback_data=f"settings_set_lang_{code}")])
    btns.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)

# --- ПОДМЕНЮ ВРЕМЕНИ ---
async def _display_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
     """Подменю настройки времени."""
     chat_lang, _ = await get_chat_info(chat_id, context); settings=dm.get_chat_settings(chat_id); tz=settings['timezone']
     custom_time=settings.get('custom_schedule_time'); sch_h,sch_m=(int(t) for t in custom_time.split(':')) if custom_time else (SCHEDULE_HOUR,SCHEDULE_MINUTE)
     local_t,tz_s=format_time_for_chat(sch_h,sch_m,tz); current_display = f"{local_t} {tz_s}"+ (f" ({custom_time} UTC)" if custom_time else "")
     def_local_t, _ = format_time_for_chat(SCHEDULE_HOUR,SCHEDULE_MINUTE,tz)

     text = f"{get_text('settings_select_time_title', chat_lang)}\n"
     text += f"{get_text('settings_time_current', chat_lang, current_time_display=current_display)}\n\n"
     text += get_text('settings_time_prompt', chat_lang, chat_timezone=COMMON_TIMEZONES.get(tz,tz))
     kbd = [[InlineKeyboardButton(get_text("settings_time_button_reset", chat_lang, default_local_time=def_local_t), callback_data="settings_set_time_default")], [InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")]]
     query = update.callback_query
     context.user_data[PENDING_TIME_INPUT_KEY] = query.message.message_id # Запоминаем ID сообщения для обновления
     logger.debug(f"Set pending time input msg={query.message.message_id}")
     await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kbd), parse_mode=ParseMode.HTML)
     await query.answer(get_text("waiting_for_time_input", chat_lang)) # Всплывающая подсказка

# --- ПОДМЕНЮ ЧАСОВОГО ПОЯСА ---
async def _display_settings_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Подменю выбора таймзоны."""
    chat_lang = await get_chat_lang(chat_id)
    current_tz = dm.get_chat_settings(chat_id).get('timezone', 'UTC')
    text = get_text("settings_select_timezone_title", chat_lang)
    rows = []
    btns = [] # Список всех кнопок для клавиатуры

    # Сортируем по имени для лучшего отображения
    try:
        # Используем COMMON_TIMEZONES напрямую из импорта config
        sorted_tzs = sorted(COMMON_TIMEZONES.items(), key=lambda item: item[1])
    except Exception as e:
        logger.error(f"Error sorting COMMON_TIMEZONES: {e}")
        sorted_tzs = [] # Используем пустой список при ошибке

    button_count = 0
    # ----- НАЧАЛО ЦИКЛА -----
    for tz_id, tz_name in sorted_tzs: # Итерируемся по отсортированному списку пар (ключ, значение)
        prefix = "✅ " if tz_id == current_tz else ""
        btn = InlineKeyboardButton(f"{prefix}{tz_name}", callback_data=f"settings_set_tz_{tz_id}")
        button_count += 1

        # Логика добавления кнопок в ряды (по 2 в ряд)
        if not rows or len(rows[-1]) == 2:
            rows.append([btn]) # Начинаем новый ряд
        else:
            rows[-1].append(btn) # Добавляем во второй столбец текущего ряда
    # ----- КОНЕЦ ЦИКЛА -----

    logger.debug(f"Created {button_count} timezone buttons. Number of rows: {len(rows)}") # Добавим лог

    # Собираем финальный список рядов для клавиатуры
    btns.extend(rows) # Добавляем все созданные ряды с кнопками таймзон
    btns.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")]) # Добавляем кнопку "Назад"

    # Редактируем сообщение
    try:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" not in str(e): logger.error(f"Failed to edit timezone menu: {e}")
    except Exception as e:
        logger.error(f"Unexpected error editing timezone menu: {e}")

# --- ПОДМЕНЮ ЖАНРА ---
async def _display_settings_genre(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Подменю выбора жанра."""
    chat_lang = await get_chat_lang(chat_id); current = dm.get_chat_settings(chat_id).get('story_genre', 'default')
    text = get_text("settings_select_genre_title", chat_lang); btns = []
    for key in SUPPORTED_GENRES.keys(): pre = "✅ " if key == current else ""; name = get_genre_name(key, chat_lang); btns.append([InlineKeyboardButton(f"{pre}{name}", callback_data=f"settings_set_genre_{key}")])
    btns.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)

# --- ПОДМЕНЮ ЛИЧНОСТИ ---
async def _display_settings_personality(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Подменю выбора личности."""
    chat_lang = await get_chat_lang(chat_id); current = dm.get_chat_settings(chat_id).get('story_personality', DEFAULT_PERSONALITY)
    text = get_text("settings_select_personality_title", chat_lang); btns = []
    for key in SUPPORTED_PERSONALITIES.keys(): pre = "✅ " if key == current else ""; name = get_personality_name(key, chat_lang); btns.append([InlineKeyboardButton(f"{pre}{name}", callback_data=f"settings_set_personality_{key}")])
    btns.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)

# --- ПОДМЕНЮ ФОРМАТА ВЫВОДА ---
async def _display_settings_output_format(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Подменю выбора формата сводки."""
    chat_lang = await get_chat_lang(chat_id); current = dm.get_chat_settings(chat_id).get('output_format', DEFAULT_OUTPUT_FORMAT)
    text = get_text("settings_select_output_format_title", chat_lang); btns = []
    for key in SUPPORTED_OUTPUT_FORMATS.keys(): pre = "✅ " if key == current else ""; name = get_output_format_name(key, chat_lang, capital=True); btns.append([InlineKeyboardButton(f"{pre}{name}", callback_data=f"settings_set_format_{key}")])
    btns.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)

# --- ПОДМЕНЮ СРОКА ХРАНЕНИЯ ---
async def _display_settings_retention(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Подменю выбора срока хранения (без опций 0 и 365, добавлены короткие)."""
    chat_lang = await get_chat_lang(chat_id)
    current = dm.get_chat_settings(chat_id).get('retention_days') # Может быть None

    text = get_text("settings_select_retention_title", chat_lang) # Используем обновленный заголовок
    btns = []

    options_days = [7, 14, 30, 90, 180]
    # -------------------------------------------

    for days in options_days:
        val = days # val теперь всегда > 0
        pre = "✅ " if val == current else ""
        # Используем обновленную format_retention_days, которая знает про 7 и 14
        btn_text = format_retention_days(val, chat_lang)
        cb_data = f"settings_set_retention_{days}" # Callback остается прежним
        btns.append([InlineKeyboardButton(f"{pre}{btn_text}", callback_data=cb_data)])

    # Добавляем кнопку "Назад"
    btns.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)

async def _display_settings_interventions(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """
    Отображает улучшенное подменю настроек Вмешательств Летописца.
    Для владельца добавляет опции ручного ввода значений.
    """
    query = update.callback_query
    # target_message_id_for_edit используется для определения, какое сообщение редактировать.
    # Оно может прийти из query.message.message_id или из user_data (после ручного ввода).
    target_message_id_for_edit = None
    if query and query.message:
        target_message_id_for_edit = query.message.message_id
    elif PENDING_INTERVENTION_INPUT_KEY in context.user_data and \
         context.user_data[PENDING_INTERVENTION_INPUT_KEY].get('menu_message_id') and \
         context.user_data[PENDING_INTERVENTION_INPUT_KEY].get('chat_id_for_menu') == chat_id:
        target_message_id_for_edit = context.user_data[PENDING_INTERVENTION_INPUT_KEY]['menu_message_id']
        # Этот ключ будет очищен в _handle_intervention_setting_input после успешного сохранения.

    if not target_message_id_for_edit and not update.message:
        logger.error(f"Cannot display intervention settings for chat {chat_id}: no target message_id and no originating command message.")
        if query: await query.answer("Ошибка отображения меню.", show_alert=True)
        return

    chat_lang = await get_chat_lang(chat_id)
    inter_settings = dm.get_intervention_settings(chat_id)
    current_cooldown = inter_settings['cooldown_minutes']
    current_min_msgs = inter_settings['min_msgs']
    current_timespan = inter_settings['timespan_minutes']

    is_owner = (user_id == BOT_OWNER_ID)

    text = f"<b>{get_text('settings_interventions_title', chat_lang)}</b>\n"
    text += f"<i>{get_text('settings_interventions_description', chat_lang)}</i>\n\n"

    limits_cd = {'min_val': INTERVENTION_MIN_COOLDOWN_MIN, 'max_val': INTERVENTION_MAX_COOLDOWN_MIN, 'def_val': INTERVENTION_DEFAULT_COOLDOWN_MIN}
    text += f"▪️ <b>{get_text('settings_intervention_cooldown_label', chat_lang)}:</b>\n"
    text += f"   {get_text('settings_intervention_current_value', chat_lang, value=current_cooldown, **limits_cd)}\n\n"

    limits_mm = {'min_val': INTERVENTION_MIN_MIN_MSGS, 'max_val': INTERVENTION_MAX_MIN_MSGS, 'def_val': INTERVENTION_DEFAULT_MIN_MSGS}
    text += f"▪️ <b>{get_text('settings_intervention_min_msgs_label', chat_lang)}:</b>\n"
    text += f"   {get_text('settings_intervention_current_value', chat_lang, value=current_min_msgs, **limits_mm)}\n\n"

    limits_ts = {'min_val': INTERVENTION_MIN_TIMESPAN_MIN, 'max_val': INTERVENTION_MAX_TIMESPAN_MIN, 'def_val': INTERVENTION_DEFAULT_TIMESPAN_MIN}
    text += f"▪️ <b>{get_text('settings_intervention_timespan_label', chat_lang)}:</b>\n"
    text += f"   {get_text('settings_intervention_current_value', chat_lang, value=current_timespan, **limits_ts)}\n\n"

    text += f"<i>{get_text('settings_interventions_change_hint', chat_lang)}</i>"

    if is_owner:
        text += get_text("settings_intervention_owner_note", chat_lang)
        text += f"\n<i>{get_text('settings_intervention_manual_input_hint', chat_lang)}</i>"

    button_rows = []
    # Кнопка Вкл/Выкл вмешательств
    interventions_currently_enabled = dm.get_chat_settings(chat_id).get('allow_interventions', False)
    toggle_button_text_key = "settings_button_toggle_interventions_on" if interventions_currently_enabled else "settings_button_toggle_interventions_off"
    button_rows.append([InlineKeyboardButton(get_text(toggle_button_text_key, chat_lang), callback_data="settings_toggle_interventions")])

    # Cooldown
    cd_options = [60, 120, 240, 480]
    if is_owner: cd_options = [15, 30] + cd_options; cd_options.sort()
    cd_btns_row = []
    for cd_val in cd_options:
        if INTERVENTION_MIN_COOLDOWN_MIN <= cd_val <= INTERVENTION_MAX_COOLDOWN_MIN:
            cd_btns_row.append(InlineKeyboardButton(
                f"{'✅ ' if cd_val == current_cooldown else ''}{get_text('settings_intervention_btn_cooldown', chat_lang, minutes=cd_val)}",
                callback_data=f"settings_set_cooldown_{cd_val}"
            ))
    if is_owner:
        cd_btns_row.append(InlineKeyboardButton(get_text("settings_intervention_manual_button", chat_lang), callback_data="settings_manual_cooldown"))
    if cd_btns_row: button_rows.append(cd_btns_row) # Могут быть 2 ряда, если много опций; для простоты в один

    # Min Msgs
    mm_options = [5, 10, 15, 25]
    if is_owner: mm_options = [3] + mm_options; mm_options.sort()
    mm_btns_row = []
    for mm_val in mm_options:
        if INTERVENTION_MIN_MIN_MSGS <= mm_val <= INTERVENTION_MAX_MIN_MSGS:
            mm_btns_row.append(InlineKeyboardButton(
                f"{'✅ ' if mm_val == current_min_msgs else ''}{get_text('settings_intervention_btn_msgs', chat_lang, count=mm_val)}",
                callback_data=f"settings_set_minmsgs_{mm_val}"
            ))
    if is_owner:
        mm_btns_row.append(InlineKeyboardButton(get_text("settings_intervention_manual_button", chat_lang), callback_data="settings_manual_minmsgs"))
    if mm_btns_row: button_rows.append(mm_btns_row)

    # Timespan
    ts_options = [5, 10, 15, 30, 60]
    ts_btns_row = []
    for ts_val in ts_options:
        if INTERVENTION_MIN_TIMESPAN_MIN <= ts_val <= INTERVENTION_MAX_TIMESPAN_MIN:
            ts_btns_row.append(InlineKeyboardButton(
                f"{'✅ ' if ts_val == current_timespan else ''}{get_text('settings_intervention_btn_timespan', chat_lang, minutes=ts_val)}",
                callback_data=f"settings_set_timespan_{ts_val}"
            ))
    if is_owner:
        ts_btns_row.append(InlineKeyboardButton(get_text("settings_intervention_manual_button", chat_lang), callback_data="settings_manual_timespan"))
    if ts_btns_row: button_rows.append(ts_btns_row)

    button_rows.append([InlineKeyboardButton(get_text("button_back", chat_lang), callback_data="settings_main")])
    keyboard_markup = InlineKeyboardMarkup(button_rows)

    if target_message_id_for_edit:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=target_message_id_for_edit,
                text=text, reply_markup=keyboard_markup, parse_mode=ParseMode.HTML
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e): logger.error(f"Failed intervention settings edit (msg_id {target_message_id_for_edit}): {e}")
        except Exception as e:
            logger.error(f"Unexpected error editing intervention settings (msg_id {target_message_id_for_edit}): {e}", exc_info=True)
    elif update.message and query is None: # Если вызвано командой /story_settings -> settings_show_interventions (маловероятно)
        try:
            sent_msg = await update.message.reply_html(text=text, reply_markup=keyboard_markup)
            logger.info(f"Sent new intervention settings menu to chat {chat_id} with msg_id {sent_msg.message_id} (unexpected flow).")
        except Exception as e:
             logger.error(f"Error sending new intervention settings message for chat {chat_id}: {e}", exc_info=True)
    else:
        # Ситуация, когда не можем ни отредактировать, ни отправить новое.
        logger.error(f"Cannot display intervention settings for chat {chat_id}: No message to edit or reply to.")
        if query: await query.answer("Ошибка отображения меню.", show_alert=True)
        
        
        
        
async def _handle_intervention_setting_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    chat_id: int, user_id: int, 
    setting_key_to_set: str, menu_message_id_to_update: int, 
    user_message: Message 
):
    """Обрабатывает ручной ввод числового значения для настройки вмешательства от владельца."""
    logger.debug(f"Owner {user_id} (chat {chat_id}) entered value for intervention setting '{setting_key_to_set}': '{user_message.text}' for menu_msg_id {menu_message_id_to_update}")
    
    menu_chat_id = context.user_data.get(PENDING_INTERVENTION_INPUT_KEY, {}).get('chat_id_for_menu', chat_id)
    chat_lang = await get_chat_lang(menu_chat_id)
    pending_details_backup = context.user_data.pop(PENDING_INTERVENTION_INPUT_KEY, None)

    raw_value_str = user_message.text.strip()
    value_int: int

    try:
        value_int = int(raw_value_str)
    except ValueError:
        await user_message.reply_html(get_text("error_intervention_manual_not_number", chat_lang))
        try: await user_message.delete()
        except Exception: pass
        return
    
    corrected_value = value_int
    value_is_valid_for_parameter = True

    if setting_key_to_set in ['intervention_cooldown_minutes', 'intervention_min_msgs', 'intervention_timespan_minutes']:
        if value_int <= 0: # Эти параметры должны быть положительными
            value_is_valid_for_parameter = False
            await user_message.reply_html(get_text("error_intervention_manual_positive_only", chat_lang, setting_name=get_text(f"setting_name_{setting_key_to_set.split('_')[1]}", chat_lang))) # Нужны новые строки локализации
            try: await user_message.delete()
            except Exception: pass
            return # Не сохраняем, если не положительное

    if not value_is_valid_for_parameter: # Если проверка выше не прошла
        return

    success = dm.update_chat_setting(menu_chat_id, setting_key_to_set, corrected_value)

    if success:
        # Сообщение об успехе без упоминания коррекции по min/max
        await user_message.reply_html(get_text("settings_intervention_manual_set_owner", chat_lang, value=corrected_value))
        
        if pending_details_backup: 
            context.user_data[PENDING_INTERVENTION_INPUT_KEY] = pending_details_backup
        
        await _display_settings_interventions(update, context, menu_chat_id, user_id) 
        
        if pending_details_backup: 
             context.user_data.pop(PENDING_INTERVENTION_INPUT_KEY, None)
    else:
        await user_message.reply_html(get_text("error_db_generic", chat_lang))

    try: await user_message.delete()
    except Exception: pass


# НОВАЯ ФУНКЦИЯ _handle_reply_to_intervention_chain
async def _handle_reply_to_intervention_chain(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    bot_message_replied_to: Message, 
    user_reply_message: Message      
):
    chat_lang, _ = await get_chat_info(chat_id, context)
    settings = dm.get_chat_settings(chat_id)
    personality_key = settings.get('story_personality', DEFAULT_PERSONALITY)

    logger.info(f"Chat {chat_id}: User {user_reply_message.from_user.id} replied to intervention chain message {bot_message_replied_to.message_id}. Bot personality: {personality_key}.")
    
    if not user_reply_message.text:
        logger.debug(f"Chat {chat_id}: User reply is not text, not generating bot response in chain.")
        # Не сбрасываем активную цепочку, если ответ не текст, вдруг следующее сообщение будет текстом
        return

    try: await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception: pass

    original_bot_text = bot_message_replied_to.text or "" # Вмешательства обычно текстовые
    user_reply_text = user_reply_message.text

    reply_prompt_string = pb.build_reply_to_intervention_prompt(
        original_bot_text=original_bot_text,
        user_reply_text=user_reply_text,
        personality_key=personality_key,
        # chat_history_for_context=chat_history_for_reply_context # Если решим использовать
    )

    if not reply_prompt_string:
        logger.warning(f"Chat {chat_id}: Failed to build prompt for intervention reply.")
        return

    bot_response_text = await gc.safe_generate_reply_to_intervention(
        reply_prompt_string, lang=chat_lang
    )

    if bot_response_text:
        try:
            sent_bot_reply_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=bot_response_text,
                reply_to_message_id=user_reply_message.message_id
            )
            # Обновляем ID "активного вмешательства" на ID нового сообщения бота
            context.chat_data[ACTIVE_INTERVENTION_CHAIN_MESSAGE_ID_KEY] = sent_bot_reply_msg.message_id
            logger.info(f"Chat {chat_id}: Bot replied in intervention chain (msg_id {sent_bot_reply_msg.message_id}) to user {user_reply_message.from_user.id}.")
        except TelegramError as e:
            logger.error(f"Chat {chat_id}: Failed to send bot's reply in intervention chain: {e}")
            context.chat_data.pop(ACTIVE_INTERVENTION_CHAIN_MESSAGE_ID_KEY, None) # Сброс при ошибке
        except Exception as e:
            logger.exception(f"Chat {chat_id}: Unexpected error sending bot's reply: {e}")
            context.chat_data.pop(ACTIVE_INTERVENTION_CHAIN_MESSAGE_ID_KEY, None) # Сброс при ошибке
    else:
        logger.info(f"Chat {chat_id}: Gemini did not generate a response for intervention reply. Chain might be broken.")
