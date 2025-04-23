import logging
import datetime
import asyncio
import time
from typing import Optional
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, constants as tg_constants
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest

# Импорты из проекта
import data_manager as dm
import gemini_client as gc
# --- ИЗМЕНЕНО: Импортируем статус ИЗ jobs.py ---
# Не нужно импортировать last_job..., они обновляются в jobs.py и используются в /status
from jobs import download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner
# -------------------------------------------
from config import SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE_STR, BOT_OWNER_ID, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE
# --- ИЗМЕНЕНО: Импортируем LOCALIZED_TEXTS ---
from localization import get_text, get_chat_lang, update_chat_lang_cache, LOCALIZED_TEXTS
# ---------------------------------------
from telegram import __version__ as ptb_version


logger = logging.getLogger(__name__)

# --- Переменные для статуса ---
bot_start_time = time.time()
last_job_run_time: Optional[datetime.datetime] = None
last_job_error: Optional[str] = None # Можно обновлять в конце jobs.py

# --- Вспомогательная функция для проверки прав администратора ---
async def is_user_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, является ли пользователь администратором или создателем чата."""
    if chat_id > 0: # В личных чатах пользователь всегда "админ"
        return True
    if not context.bot: # Проверка на случай, если бот недоступен
        logger.error(f"Объект бота недоступен при проверке админа {user_id} в чате {chat_id}")
        return False
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        # --- ИЗМЕНЕНО ЗДЕСЬ: Используем OWNER вместо CREATOR ---
        return chat_member.status in [
            tg_constants.ChatMemberStatus.ADMINISTRATOR,
            tg_constants.ChatMemberStatus.OWNER # <-- Правильный статус для создателя
        ]
        # ------------------------------------------------------
    except TelegramError as e:
        # Логируем частые ошибки доступа
        if "chat not found" in str(e).lower() or "user not found" in str(e).lower():
            logger.warning(f"Не удалось получить статус участника {user_id} в чате {chat_id}: {e}")
        else:
            logger.error(f"Ошибка Telegram при проверке админа {user_id} в чате {chat_id}: {e}")
        return False
    except Exception as e:
         logger.error(f"Неожиданная ошибка при проверке админа {user_id} в чате {chat_id}: {e}", exc_info=True)
         return False

# --- Обработчики команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat: return

    chat_lang = await get_chat_lang(chat.id)
    settings = dm.get_chat_settings(chat.id) # Получаем статус enabled
    status_text = get_text("settings_enabled", chat_lang) if settings.get('enabled', True) else get_text("settings_disabled", chat_lang)

    logger.info(f"User {user.id} started bot in chat {chat.id}")
    await update.message.reply_html(
        get_text("start_message", chat_lang,
                 user_mention=user.mention_html(),
                 chat_title=f"'{chat.title}'" if chat.title else get_text('private_chat', chat_lang), # Уточняем для личных чатов
                 schedule_time=f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}",
                 schedule_tz=SCHEDULE_TIMEZONE_STR,
                 status=status_text.split(': ')[1] # Берем только статус
                 ),
        # Добавляем кнопку выбора языка
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🌐 Language / Язык", callback_data="select_language")
        ]])
    )
    commands = [
        BotCommand("start", get_text("cmd_start_desc", chat_lang)),
        BotCommand("help", get_text("cmd_help_desc", chat_lang)),
        BotCommand("generate_now", get_text("cmd_generate_now_desc", chat_lang)),
        BotCommand("regenerate_story", get_text("cmd_regenerate_desc", chat_lang)),
        BotCommand("story_on", get_text("cmd_story_on_desc", chat_lang)),
        BotCommand("story_off", get_text("cmd_story_off_desc", chat_lang)),
        BotCommand("story_settings", get_text("cmd_settings_desc", chat_lang)),
        BotCommand("set_language", get_text("cmd_language_desc", chat_lang)),
    ]
    # Добавляем команду /status только для владельца
    if user.id == BOT_OWNER_ID:
         commands.append(BotCommand("status", get_text("cmd_status_desc", chat_lang)))
    try:
        await context.bot.set_my_commands(commands) #, language_code=chat_lang[:2] if chat_lang else None) # Можно задать язык для команд
    except TelegramError as e:
        logger.warning(f"Failed to set bot commands for chat {chat.id}: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat; user = update.effective_user
    if not chat or not user: return
    chat_lang = await get_chat_lang(chat.id)
    logger.debug(f"Help command called in chat {chat.id} by user {user.id}")
    await update.message.reply_text(
        get_text("help_message", chat_lang,
                 schedule_time=f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}",
                 schedule_tz=SCHEDULE_TIMEZONE_STR),
        parse_mode=ParseMode.HTML # Используем HTML для потенциального форматирования
    )

async def generate_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat: return
    chat_lang = await get_chat_lang(chat.id)
    logger.info(f"User {user.username} requested /generate_now for chat {chat.id}")
    messages_current = dm.get_messages_for_chat(chat.id)
    if not messages_current:
        await update.message.reply_text(get_text("generating_now_no_messages", chat_lang))
        return

    photo_count = sum(1 for m in messages_current if m.get('type') == 'photo')
    photo_process_limit = min(photo_count, MAX_PHOTOS_TO_ANALYZE)
    photo_info_str = get_text("photo_info_text", chat_lang, count=photo_process_limit) if photo_count > 0 else ""
    msg_count_str = str(len(messages_current))

    msg = await update.message.reply_text(get_text("generating_now", chat_lang, msg_count=msg_count_str, photo_info=photo_info_str))

    try:
        downloaded_images = await download_images(context, messages_current, chat.id, MAX_PHOTOS_TO_ANALYZE)
        prepared_content = gc.prepare_story_parts(messages_current, downloaded_images)
        # --- ИЗМЕНЕНО: Используем safe_generate_story ---
        story, error_msg = await gc.safe_generate_story(prepared_content)

        if story:
            final_message = ""; header_key = "story_ready_header"
            photo_note_str_res = get_text("photo_info_text", chat_lang, count=photo_process_limit) if downloaded_images else ""
            final_message_header = get_text(header_key, chat_lang, photo_info=photo_note_str_res)

            # --- НОВОЕ: Добавляем кнопки фидбэка ---
            # Callback data будет содержать ID сообщения, которое мы сейчас отправим (или отредактируем)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("👍", callback_data=f"feedback_good_{msg.message_id}"),
                InlineKeyboardButton("👎", callback_data=f"feedback_bad_{msg.message_id}")
            ]])

            try:
                MAX_MSG_LEN = 4096
                if len(final_message_header + story) > MAX_MSG_LEN:
                     logger.warning(f"/generate_now story too long for chat {chat.id}, splitting.")
                     await msg.edit_text(get_text("story_too_long", chat_lang, photo_info=photo_note_str_res))
                     await asyncio.sleep(0.5); parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                     sent_msg = None
                     for k, part in enumerate(parts):
                         current_reply_markup = keyboard if k == len(parts) - 1 else None
                         sent_msg = await context.bot.send_message(chat_id=chat.id, text=part, reply_markup=current_reply_markup)
                         await asyncio.sleep(0.5)
                     # Обновим ID в callback_data для кнопок последнего сообщения
                     if sent_msg:
                          keyboard = InlineKeyboardMarkup([[
                              InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_msg.message_id}"),
                              InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_msg.message_id}")
                          ]]); await context.bot.edit_message_reply_markup(chat_id=chat.id, message_id=sent_msg.message_id, reply_markup=keyboard)
                else:
                     final_message = final_message_header + story
                     await msg.edit_text(final_message, reply_markup=keyboard)
                logger.info(get_text("story_sent", chat_lang) + f" Chat ID: {chat.id}")
                if error_msg: # Примечание от прокси (если есть)
                     try: await context.bot.send_message(chat_id=chat.id, text=get_text("proxy_note", chat_lang, note=error_msg))
                     except Exception as e_note: logger.warning(f"Failed to send proxy note for /generate_now: {e_note}")
            except TelegramError as e: logger.error(f"Telegram error sending/editing story (generate_now): {e}"); await update.message.reply_text(get_text("error_telegram", chat_lang, error=e))
            except Exception as e: logger.error(f"Unexpected error sending story (generate_now): {e}", exc_info=True); await update.message.reply_text(get_text("error_unexpected_send", chat_lang))
        else:
            logger.warning(f"Failed to generate story (generate_now) for chat {chat.id}. Reason: {error_msg}")
            await msg.edit_text(get_text("generation_failed", chat_lang, error=error_msg or 'Unknown'))
    except Exception as e:
         logger.error(f"General error in /generate_now for chat {chat.id}: {e}", exc_info=True)
         await msg.edit_text(get_text("error_db_generic", chat_lang)) # Или другая общая ошибка


# --- НОВЫЕ КОМАНДЫ ---

async def regenerate_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Повторно генерирует историю за сегодня (если сообщения еще не удалены)."""
    user = update.effective_user; chat = update.effective_chat;
    if not user or not chat: return
    chat_lang = await get_chat_lang(chat.id); logger.info(f"User {user.username} requested /regenerate_story for chat {chat.id}")
    messages_current = dm.get_messages_for_chat(chat.id)
    if not messages_current: await update.message.reply_text(get_text("regenerate_no_data", chat_lang)); return

    # --- Определение photo_count и photo_process_limit ЗДЕСЬ ---
    photo_count = sum(1 for m in messages_current if m.get('type') == 'photo')
    photo_process_limit = min(photo_count, MAX_PHOTOS_TO_ANALYZE)
    # ----------------------------------------------------------

    msg = await update.message.reply_text(get_text("regenerating", chat_lang))

    try:
        # --- Сначала скачиваем изображения ---
        downloaded_images = await download_images(context, messages_current, chat.id, MAX_PHOTOS_TO_ANALYZE)

        # --- ИЗМЕНЕНО: Определяем photo_note_str ПОСЛЕ скачивания ---
        photo_note_str = get_text("photo_info_text", chat_lang, count=photo_process_limit) if downloaded_images else ""
        # -------------------------------------------------------------

        prepared_content = gc.prepare_story_parts(messages_current, downloaded_images)
        story, error_msg = await gc.safe_generate_story(prepared_content)
        if story:
            header_key = "story_ready_header"
            # Теперь photo_note_str определена корректно
            final_message_header = get_text(header_key, chat_lang, photo_info=photo_note_str)
            new_msg = await update.message.reply_text(final_message_header + story)
            keyboard = InlineKeyboardMarkup([[ InlineKeyboardButton("👍", callback_data=f"feedback_good_{new_msg.message_id}"), InlineKeyboardButton("👎", callback_data=f"feedback_bad_{new_msg.message_id}") ]])
            try: await context.bot.edit_message_reply_markup(chat_id=chat.id, message_id=new_msg.message_id, reply_markup=keyboard)
            except BadRequest: pass
            except TelegramError as e: logger.warning(f"Error updating reply markup in regenerate: {e}")

            try: await msg.delete()
            except Exception as e: logger.warning(f"Could not delete 'regenerating' message: {e}")

            if error_msg: await context.bot.send_message(chat_id=chat.id, text=get_text("proxy_note", chat_lang, note=error_msg))
        else: await msg.edit_text(get_text("generation_failed", chat_lang, error=error_msg or 'Unknown'))
    except Exception as e: logger.error(f"Error in /regenerate_story chat {chat.id}: {e}", exc_info=True); await msg.edit_text(get_text("error_db_generic", chat_lang))

async def story_on_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Включает или выключает ежедневную генерацию (только админы)."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or not update.message or not update.message.text \
       or chat.type == tg_constants.ChatType.PRIVATE: return # Добавили проверки сообщения
    chat_lang = await get_chat_lang(chat.id)

    is_admin = await is_user_admin(chat.id, user.id, context)
    if not is_admin:
        await update.message.reply_text(get_text("admin_only", chat_lang))
        return

    # --- ИЗМЕНЕНО: Извлекаем чистую команду ---
    # Получаем текст сообщения
    message_text = update.message.text
    # Разбиваем по '@' и берем первую часть (саму команду)
    command_part = message_text.split('@')[0]
    # Разбиваем по пробелу (на случай, если есть аргументы) и берем первую часть
    command = command_part.split()[0].lower()
    # -----------------------------------------

    # Определяем статус на основе чистой команды
    new_status = (command == "/story_on")

    logger.info(f"Получен текст '{message_text}'. Извлечена команда '{command}'. Определен new_status = {new_status} (тип: {type(new_status)})")

    # Вызываем обновление в БД
    dm.update_chat_setting(chat.id, 'enabled', new_status)

    # Отправляем ответ пользователю
    reply_text = get_text("story_enabled", chat_lang) if new_status else get_text("story_disabled", chat_lang)
    await update.message.reply_text(reply_text)

async def story_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает настройки для чата (только админы)."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat or chat.type == tg_constants.ChatType.PRIVATE: return
    chat_lang = await get_chat_lang(chat.id)

    is_admin = await is_user_admin(chat.id, user.id, context)
    if not is_admin:
        await update.message.reply_text(get_text("admin_only", chat_lang))
        return

    settings = dm.get_chat_settings(chat.id)
    status_text = get_text("settings_enabled", chat_lang) if settings.get('enabled', True) else get_text("settings_disabled", chat_lang)
    lang_text = get_text("settings_language", chat_lang) + f" ({settings.get('lang', DEFAULT_LANGUAGE)})"

    await update.message.reply_text(
        f"{get_text('settings_title', chat_lang, chat_title=chat.title)}\n"
        f"- {status_text}\n"
        f"- {lang_text}"
    )

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Предлагает выбрать язык."""
    user = update.effective_user; chat = update.effective_chat
    if not user or not chat: return

    buttons = []
    for lang_code in SUPPORTED_LANGUAGES:
        # Показываем название языка на самом языке
        lang_name = LOCALIZED_TEXTS.get(lang_code, {}).get("lang_name", lang_code)
        buttons.append(InlineKeyboardButton(lang_name, callback_data=f"setlang_{lang_code}"))

    # Создаем строки кнопок (например, по 2 в ряд)
    keyboard_markup = InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])
    current_lang = await get_chat_lang(chat.id)
    await update.message.reply_text(get_text("language_select", current_lang), reply_markup=keyboard_markup)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статус бота (только владелец)."""
    user = update.effective_user
    if not user or user.id != BOT_OWNER_ID:
        # Можно либо ничего не отвечать, либо вежливо отказать
        # await update.message.reply_text("Access denied.")
        return

    uptime_seconds = time.time() - bot_start_time
    uptime_str = str(datetime.timedelta(seconds=int(uptime_seconds)))
    active_chats_list = dm.get_enabled_chats() # Получаем список активных чатов

    # Получаем данные о последнем запуске задачи (нужно их где-то хранить)
    last_run_str = last_job_run_time.isoformat() if last_job_run_time else "Never"
    last_error_str = last_job_error if last_job_error else "None"

    status_text = get_text("status_command_reply", DEFAULT_LANGUAGE, # Статус всегда на дефолтном языке?
                           uptime=uptime_str,
                           active_chats=len(active_chats_list),
                           last_job_run=last_run_str,
                           last_job_error=last_error_str,
                           ptb_version=ptb_version
                           )
    await update.message.reply_text(status_text)


# --- Обработчики кнопок ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия на инлайн-кнопки."""
    query = update.callback_query
    if not query or not query.message: # Добавим проверку query.message
        logger.warning("Получен CallbackQuery без сообщения.")
        if query: await query.answer() # Отвечаем на коллбэк, даже если нет сообщения
        return

    await query.answer() # Обязательно отвечаем на колбэк
    user = query.from_user; chat = query.message.chat
    if not user or not chat: return

    data = query.data
    logger.info(f"User {user.id} pressed button with data: {data} in chat {chat.id}")
    chat_lang = await get_chat_lang(chat.id) # Получаем язык

    # --- ИЗМЕНЕНО: Обработка нажатия кнопки выбора языка ---
    if data == "select_language":
        buttons = []
        for lang_code in SUPPORTED_LANGUAGES:
            lang_name = LOCALIZED_TEXTS.get(lang_code, {}).get("lang_name", lang_code)
            buttons.append(InlineKeyboardButton(lang_name, callback_data=f"setlang_{lang_code}"))
        keyboard_markup = InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])

        try:
            # Редактируем исходное сообщение, показывая выбор языка
            await query.edit_message_text(
                text=get_text("language_select", chat_lang),
                reply_markup=keyboard_markup
            )
        except BadRequest as e:
            # Ошибка может возникнуть, если сообщение не изменилось или слишком старое
            logger.warning(f"Не удалось отредактировать сообщение для выбора языка: {e}")
        except TelegramError as e:
            logger.error(f"Ошибка Telegram при редактировании сообщения для выбора языка: {e}")
        return # Завершаем обработку этого коллбэка

    # Обработка выбора конкретного языка (setlang_...)
    if data.startswith("setlang_"):
        lang_code = data.split("_", 1)[1]
        if lang_code in SUPPORTED_LANGUAGES:
            dm.update_chat_setting(chat.id, 'lang', lang_code)
            update_chat_lang_cache(chat.id, lang_code)
            try:
                # Редактируем сообщение, подтверждая смену языка
                await query.edit_message_text(text=get_text("language_set", lang_code), reply_markup=None) # Убираем кнопки
            except BadRequest as e: logger.warning(f"Не удалось отредактировать сообщение после смены языка: {e}")
            except TelegramError as e: logger.error(f"Ошибка Telegram при редактировании сообщения после смены языка: {e}")
            # Обновляем команды для нового языка (опционально)
            try:
                commands = [ BotCommand(c.command, get_text(f"cmd_{c.command}_desc", lang_code)) for c in await context.bot.get_my_commands() if c.command != 'status']
                if user.id == BOT_OWNER_ID: commands.append(BotCommand("status", get_text("cmd_status_desc", lang_code)))
                await context.bot.set_my_commands(commands)
            except Exception as e: logger.warning(f"Failed to update commands for lang {lang_code}: {e}")
        else:
             await query.answer(text="Invalid language selected.", show_alert=True) # Показываем alert
        return

    # Обработка кнопок обратной связи
    if data.startswith("feedback_"):
        parts = data.split("_")
        if len(parts) == 3:
            rating_type = parts[1]; original_message_id_str = parts[2]
            try:
                original_message_id = int(original_message_id_str)
                rating_value = 1 if rating_type == "good" else -1 if rating_type == "bad" else 0
                if rating_value != 0:
                    dm.add_feedback(original_message_id, chat.id, user.id, rating_value)
                    await query.edit_message_reply_markup(reply_markup=None) # Убираем кнопки
                    await query.answer(text=get_text("feedback_thanks", chat_lang))
                else: await query.answer(text="Invalid feedback type.")
            except (ValueError, IndexError): logger.warning(f"Invalid feedback callback data: {data}"); await query.answer(text="Error processing feedback.")
            except BadRequest as e: logger.debug(f"Could not edit feedback buttons (maybe already removed): {e}") # Игнорируем BadRequest
            except TelegramError as e: logger.warning(f"Error removing feedback buttons: {e}"); await query.answer(text="Error processing feedback.")
        else: logger.warning(f"Incorrect feedback callback data format: {data}"); await query.answer(text="Error processing feedback.")
        return


# --- Обработчик сообщений (без изменений) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (Код функции handle_message остается прежним) ...
    message = update.message
    if not message or not message.from_user or not message.chat: return
    if message.from_user.is_bot: return
    chat_id = message.chat_id; user = message.from_user; timestamp = message.date or datetime.datetime.now(datetime.timezone.utc); username = user.username or user.first_name or f"User_{user.id}"
    message_data = {'message_id': message.message_id, 'user_id': user.id, 'username': username, 'timestamp': timestamp.isoformat(), 'type': 'unknown', 'content': None, 'file_id': None, 'file_unique_id': None, 'file_name': None}
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
        try: message_data['file_id'] = file_info.file_id; message_data['file_unique_id'] = file_info.file_unique_id; message_data['file_name'] = getattr(file_info, 'file_name', None)
        except AttributeError: logger.warning(f"Failed to get file info for type {message_data['type']} in chat {chat_id}")
    if message_data['type'] != 'unknown': dm.add_message(chat_id, message_data)

# --- Добавляем описания команд для локализации ---
# Ключи должны совпадать с командами + '_desc'
LOCALIZED_TEXTS["ru"].update({
    "cmd_start_desc": "Приветствие и информация",
    "cmd_help_desc": "Показать справку и команды",
    "cmd_generate_now_desc": "История за сегодня немедленно",
    "cmd_regenerate_desc": "Пересоздать историю дня",
    "cmd_story_on_desc": "Вкл. ежедневные истории (Админ)",
    "cmd_story_off_desc": "Выкл. ежедневные истории (Админ)",
    "cmd_settings_desc": "Показать настройки чата (Админ)",
    "cmd_language_desc": "Выбрать язык бота",
    "cmd_status_desc": "Статус бота (Владелец)",
    "private_chat": "личном чате"
})
LOCALIZED_TEXTS["en"].update({
    "cmd_start_desc": "Greeting and info",
    "cmd_help_desc": "Show help and commands",
    "cmd_generate_now_desc": "Today's story immediately",
    "cmd_regenerate_desc": "Regenerate today's story",
    "cmd_story_on_desc": "Enable daily stories (Admin)",
    "cmd_story_off_desc": "Disable daily stories (Admin)",
    "cmd_settings_desc": "Show chat settings (Admin)",
    "cmd_language_desc": "Choose bot language",
    "cmd_status_desc": "Bot status (Owner)",
    "private_chat": "private chat"
})