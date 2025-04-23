# bot_handlers.py
import logging
import datetime
import asyncio
from telegram import Update, BotCommand
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

import data_manager as dm
import gemini_client as gc
from jobs import download_images, MAX_PHOTOS_TO_ANALYZE # Импортируем из jobs
from config import SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE_STR

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return
    logger.info(f"Пользователь {user.id} ({user.username}) запустил /start в чате {chat.id} ({getattr(chat, 'title', 'Private')})")
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Я собираю сообщения в этом чате ({getattr(chat, 'title', 'личном')}) "
        f"и каждый день в ~{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} (по времени {SCHEDULE_TIMEZONE_STR}) "
        "генерирую краткую историю дня, теперь **с анализом изображений**! \n\n"
        "Используйте /help для списка команд.",
    )
    commands = [
        BotCommand("start", "Приветствие и информация"),
        BotCommand("help", "Показать справку и команды"),
        BotCommand("generate_now", "Сгенерировать историю дня немедленно (тест)"),
    ]
    try:
        # Устанавливаем команды глобально или для чата
        await context.bot.set_my_commands(commands)
    except TelegramError as e:
        logger.warning(f"Не удалось установить команды бота для чата {chat.id}: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat: return
    logger.debug(f"Команда /help вызвана в чате {chat.id}")
    await update.message.reply_text(
        "Я бот-летописец с ИИ!\n"
        "Я анализирую **текстовые сообщения и изображения** за день "
        "и создаю на их основе уникальную историю дня с помощью нейросети Google Gemini.\n\n"
        "Функции:\n"
        "- Автоматический сбор сообщений и фото.\n"
        f"- Ежедневная генерация истории около {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} (по времени {SCHEDULE_TIMEZONE_STR}).\n\n"
        "Команды:\n"
        "/start - Приветствие и информация\n"
        "/help - Эта справка\n"
        "/generate_now - Сгенерировать историю для этого чата прямо сейчас (с анализом фото)\n\n"
        "Просто добавьте меня в группу!"
    )

async def generate_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return
    logger.info(f"Пользователь {user.username} ({user.id}) запросил /generate_now для чата {chat.id}")
    messages_current = dm.get_messages_for_chat(chat.id)
    if not messages_current:
        await update.message.reply_text("В этом чате пока нет сообщений за сегодня для создания истории.")
        return
    msg_count_text = f"{len(messages_current)} сообщ."
    photo_count = sum(1 for m in messages_current if m.get('type') == 'photo')
    photo_process_limit = min(photo_count, MAX_PHOTOS_TO_ANALYZE)
    if photo_count > 0:
        msg_count_text += f" и {photo_count} фото (до {photo_process_limit} будут проанализированы)"
    msg = await update.message.reply_text(f"⏳ Анализирую {msg_count_text} и генерирую историю дня с помощью Gemini... Ожидайте.")
    try:
        downloaded_images = await download_images(context, messages_current, chat.id, MAX_PHOTOS_TO_ANALYZE)
        gemini_input_content = gc.prepare_story_parts(messages_current, downloaded_images)
        story, note_or_error = await gc.generate_story_from_proxy(gemini_input_content)
        if story:
            final_message = ""
            try:
                MAX_MESSAGE_LENGTH = 4000
                photo_note = f" (с анализом до {photo_process_limit} фото)" if downloaded_images else ""
                final_message_header = f"✨ История дня (по запросу){photo_note}:\n\n"
                if len(final_message_header + story) > MAX_MESSAGE_LENGTH:
                     logger.warning(f"История (generate_now) для чата {chat.id} слишком длинная, разбиваем.")
                     await msg.edit_text(f"История готова!{photo_note} Она получилась довольно длинной, отправляю по частям:")
                     await asyncio.sleep(0.5)
                     parts = [story[j:j+MAX_MESSAGE_LENGTH] for j in range(0, len(story), MAX_MESSAGE_LENGTH)]
                     for k, part in enumerate(parts):
                         await context.bot.send_message(chat_id=chat.id, text=part)
                         await asyncio.sleep(0.5)
                else:
                     final_message = final_message_header + story
                     await msg.edit_text(final_message)
                logger.info(f"История по запросу (generate_now) успешно отправлена/отредактирована в чате {chat.id}.")
                if note_or_error:
                     try: await context.bot.send_message(chat_id=chat.id, text=f"ℹ️ Примечание: {note_or_error}")
                     except TelegramError as e_note: logger.warning(f"Не удалось отправить примечание для /generate_now: {e_note}")
            except TelegramError as e:
                logger.error(f"Ошибка Telegram при отправке/ред. истории (generate_now): {e}")
                if "message to edit not found" in str(e).lower() and final_message:
                     try: await context.bot.send_message(chat_id=chat.id, text=final_message)
                     except TelegramError as e_send: logger.error(f"Попытка отправить как новое сообщение тоже не удалась: {e_send}")
                else:
                     await update.message.reply_text(f"Не удалось отправить историю: {e}.")
            except Exception as e:
                logger.error(f"Неожиданная ошибка при отправке истории (generate_now): {e}", exc_info=True)
                await update.message.reply_text(f"Произошла неожиданная ошибка при отправке.")
        else:
            logger.warning(f"Не удалось сгенерировать историю (generate_now) для чата {chat.id}. Причина: {note_or_error}")
            reply_error = note_or_error or "Неизвестная ошибка."
            await msg.edit_text(f"😕 Не удалось сгенерировать историю.\nПричина: {reply_error}")
    except Exception as e:
         logger.error(f"Общая ошибка в /generate_now для чата {chat.id}: {e}", exc_info=True)
         await msg.edit_text("Произошла внутренняя ошибка при обработке запроса.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.from_user or not message.chat: return
    if message.from_user.is_bot: return
    chat_id = message.chat_id
    user = message.from_user
    timestamp = message.date or datetime.datetime.now(datetime.timezone.utc)
    username = user.username or user.first_name or f"User_{user.id}"
    message_data = {
        'message_id': message.message_id,
        'user_id': user.id,
        'username': username,
        'timestamp': timestamp.isoformat(),
        'type': 'unknown',
        'content': None,
        'file_id': None,
        'file_unique_id': None,
        'file_name': None
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
            if hasattr(file_info, 'file_name'): message_data['file_name'] = file_info.file_name
        except AttributeError:
             logger.warning(f"Не удалось получить file_id/unique_id для типа {message_data['type']} в чате {chat_id}")
    if message_data['type'] != 'unknown':
        dm.add_message(chat_id, message_data)
    else:
        pass