# jobs.py
import logging
import asyncio
import datetime
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode # <-- Убедитесь, что этот импорт есть
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError, NetworkError, BadRequest
from typing import Dict, List, Any, Optional

import data_manager as dm
import gemini_client as gc
from config import (
    BOT_OWNER_ID, SCHEDULE_HOUR, SCHEDULE_MINUTE,
    JOB_CHECK_INTERVAL_MINUTES
)
from localization import get_text, get_chat_lang
# --- ИЗМЕНЕНО: Импортируем из utils.py ---
from utils import download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner

logger = logging.getLogger(__name__)

# --- Функция download_images (без изменений) ---
async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Запускается периодически. Проверяет чаты и генерирует истории по расписанию.
    """
    application: Optional[Application] = None
    if context.job and context.job.data and isinstance(context.job.data, dict):
         application = context.job.data.get('application')
    if not application:
        logger.error("Application object not found in job context!")
        if hasattr(context, 'application'): application = context.application
        else: logger.critical("Could not retrieve Application object. Aborting job."); return

    job_start_time = datetime.datetime.now(pytz.utc)
    application.bot_data['last_job_run_time'] = job_start_time
    application.bot_data['last_job_error'] = None # Сброс ошибки
    current_errors = []

    bot = context.bot
    if bot is None:
        logger.error("Bot object missing in daily_story_job context!")
        return

    bot_username = "UnknownBot"
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "UnknownBot"
    except Exception as e:
        logger.error(f"Failed to get bot name: {e}")

    logger.info(f"[{bot_username}] Running scheduled check for story generation...")

    enabled_chat_ids = dm.get_enabled_chats()
    if not enabled_chat_ids:
        logger.info(f"[{bot_username}] No enabled chats found.")
        return

    now_utc = datetime.datetime.now(pytz.utc)
    current_hour_utc = now_utc.hour
    current_minute_utc = now_utc.minute
    current_minute_rounded = (current_minute_utc // JOB_CHECK_INTERVAL_MINUTES) * JOB_CHECK_INTERVAL_MINUTES

    logger.debug(
        f"Current UTC: {now_utc.strftime('%Y-%m-%d %H:%M')}, "
        f"Checking for tasks scheduled around ~{current_hour_utc:02d}:{current_minute_rounded:02d}"
    )

    processed_in_this_run = 0
    chats_to_process = []

    # Шаг 1: Определяем, для каких чатов подошло время
    for chat_id in enabled_chat_ids:
        settings = dm.get_chat_settings(chat_id)
        target_hour, target_minute = SCHEDULE_HOUR, SCHEDULE_MINUTE
        custom_time_str = settings.get('custom_schedule_time')

        if custom_time_str:
            try:
                parts = custom_time_str.split(':')
                target_hour, target_minute = int(parts[0]), int(parts[1])
            except (ValueError, IndexError, TypeError):
                logger.warning(
                    f"[Chat {chat_id}] Invalid custom time format '{custom_time_str}'. "
                    f"Using default {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} UTC."
                )
                target_hour, target_minute = SCHEDULE_HOUR, SCHEDULE_MINUTE # Сброс на дефолт

        # Сравниваем ЧАС и ОКРУГЛЕННУЮ МИНУТУ
        if current_hour_utc == target_hour and current_minute_rounded == target_minute:
            chats_to_process.append(chat_id)
            logger.debug(
                f"[Chat {chat_id}] Scheduled time {target_hour:02d}:{target_minute:02d} "
                f"matches current slot. Added to process list."
            )

    if not chats_to_process:
        logger.info("No chats due for processing in this time slot.")
        return

    logger.info(
        f"Chats to process now ({current_hour_utc:02d}:{current_minute_rounded:02d}): "
        f"{chats_to_process}"
    )

    # Шаг 2: Обрабатываем отобранные чаты
    for chat_id in chats_to_process:
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id}]"
        logger.info(f"{current_chat_log_prefix} Processing...")
        story_sent = False
        chat_lang = await get_chat_lang(chat_id)

        try:
            messages = dm.get_messages_for_chat(chat_id)
            if not messages:
                logger.info(f"{current_chat_log_prefix} No messages found in DB, skipping generation.")
                continue # Переходим к следующему чату

            logger.info(f"{current_chat_log_prefix} Found {len(messages)} messages.")

            # Скачивание изображений
            downloaded_images = await download_images(
                context, messages, chat_id, MAX_PHOTOS_TO_ANALYZE
            )

            # Подготовка контента
            prepared_content = gc.prepare_story_parts(messages, downloaded_images)
            if not prepared_content:
                logger.warning(f"{current_chat_log_prefix} Failed to prepare content for AI.")
                current_errors.append(f"Chat {chat_id}: Prepare Content Error")
                continue # К следующему чату

            # Генерация истории
            story, error_msg = await gc.safe_generate_story(prepared_content)

            if story:
                # --- Отправка истории ---
                try:
                    date_str = job_start_time.strftime("%d %B %Y")
                    photo_note_str = get_text(
                        "photo_info_text", chat_lang, count=MAX_PHOTOS_TO_ANALYZE
                    ) if downloaded_images else ""
                    header_key = "daily_story_header"
                    chat_title_str = str(chat_id) # Fallback title
                    try:
                        chat_info = await bot.get_chat(chat_id)
                        if chat_info.title:
                             chat_title_str = f"'{chat_info.title}'"
                    except Exception as e_chat:
                        logger.warning(f"{current_chat_log_prefix} Could not get chat title: {e_chat}")

                    # Формируем и отправляем заголовок
                    final_message_header = get_text(
                        header_key, chat_lang,
                        date_str=date_str, chat_title=chat_title_str, photo_info=photo_note_str
                    )
                    await bot.send_message(
                        chat_id=chat_id, text=final_message_header,
                        parse_mode=ParseMode.HTML
                    )
                    await asyncio.sleep(0.2) # Маленькая пауза

                    # Отправляем тело истории с кнопками
                    sent_message = None
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"),
                        InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")
                    ]])
                    MAX_MSG_LEN = 4096

                    if len(story) > MAX_MSG_LEN:
                        logger.warning(f"{current_chat_log_prefix} Story is too long, splitting.")
                        parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                        for k, part in enumerate(parts):
                            # Кнопки только к последней части
                            current_reply_markup = keyboard if k == len(parts) - 1 else None
                            sent_message = await bot.send_message(
                                chat_id=chat_id, text=part,
                                reply_markup=current_reply_markup,
                                parse_mode=ParseMode.MARKDOWN # История в Markdown
                            )
                            await asyncio.sleep(0.5)
                    else:
                        sent_message = await bot.send_message(
                            chat_id=chat_id, text=story,
                            reply_markup=keyboard,
                            parse_mode=ParseMode.MARKDOWN # История в Markdown
                        )

                    # Обновляем ID в кнопках фидбэка
                    if sent_message:
                        keyboard_updated = InlineKeyboardMarkup([[
                            InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_message.message_id}"),
                            InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_message.message_id}")
                        ]])
                        try:
                            await bot.edit_message_reply_markup(
                                chat_id=chat_id, message_id=sent_message.message_id,
                                reply_markup=keyboard_updated
                            )
                        except BadRequest: pass # Игнорируем, если уже нажали/удалили
                        except TelegramError as e: logger.warning(f"Error updating buttons: {e}")

                    logger.info(f"{current_chat_log_prefix} Story sent successfully.")
                    story_sent = True # Успех!

                    # Отправляем примечание от прокси, если было
                    if error_msg:
                        try:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=get_text("proxy_note", chat_lang, note=error_msg)
                            )
                        except Exception as e:
                            logger.warning(f"{current_chat_log_prefix} Failed to send proxy note: {e}")

                except TelegramError as e:
                    logger.error(f"{current_chat_log_prefix} TG error sending story: {e}")
                    current_errors.append(f"Chat {chat_id}: TG Send Err ({e.__class__.__name__})")
                    error_str = str(e).lower()
                    is_fatal_tg_error = any(sub in error_str for sub in ["blocked", "deactivated", "not found", "kicked", "forbidden"])
                    if is_fatal_tg_error:
                        logger.warning(f"{current_chat_log_prefix} Unrecoverable TG error. Clearing data and disabling chat.")
                        dm.clear_messages_for_chat(chat_id)
                        dm.update_chat_setting(chat_id, 'enabled', False) # Отключаем чат
                except Exception as e:
                    logger.exception(f"{current_chat_log_prefix} Unexpected error sending story: {e}")
                    current_errors.append(f"Chat {chat_id}: Send Err ({e.__class__.__name__})")

            else: # Ошибка генерации (story is None)
                logger.warning(f"{current_chat_log_prefix} Failed to generate story. Reason: {error_msg}")
                current_errors.append(f"Chat {chat_id}: Gen Err ({error_msg or 'Unknown'})")
                try:
                    # Отправляем уведомление об ошибке в чат
                    await bot.send_message(
                        chat_id=chat_id,
                        text=get_text("daily_job_failed_chat", chat_lang, error=error_msg or 'Unknown')
                    )
                except Exception as e_err:
                    logger.warning(f"{current_chat_log_prefix} Failed send gen failure notification: {e_err}")
                    error_str = str(e_err).lower()
                    is_fatal_tg_error = any(sub in error_str for sub in ["blocked", "deactivated", "not found", "kicked", "forbidden"])
                    if is_fatal_tg_error:
                        logger.warning(f"{current_chat_log_prefix} Clearing data: TG err sending fail notification.")
                        dm.clear_messages_for_chat(chat_id)
                        dm.update_chat_setting(chat_id, 'enabled', False) # Отключаем чат

        except Exception as e: # Глобальный обработчик ошибок для цикла обработки чата
            logger.exception(f"{current_chat_log_prefix} CRITICAL error processing chat: {e}")
            current_errors.append(f"Chat {chat_id}: Critical Err ({e.__class__.__name__})")

        # --- Очистка данных ТОЛЬКО при успешной отправке ---
        if story_sent:
            dm.clear_messages_for_chat(chat_id)
            processed_in_this_run += 1
        else:
            # Проверяем, остались ли сообщения (на случай, если фатальная ошибка TG их уже удалила)
            if dm.get_messages_for_chat(chat_id):
                 logger.warning(f"{current_chat_log_prefix} Data NOT cleared due to error.")

        # Пауза после обработки КАЖДОГО чата из списка
        await asyncio.sleep(2)
    # --- Конец ВТОРОГО цикла ---

    # --- Завершение периодической задачи ---
    job_end_time = datetime.datetime.now(pytz.utc)
    duration = job_end_time - job_start_time
    final_error_summary = "\n".join(current_errors) if current_errors else None
    # Обновляем статус в bot_data
    application.bot_data['last_job_error'] = final_error_summary

    logger.info(
        f"[{bot_username}] Scheduled check finished in {duration}. "
        f"Processed: {processed_in_this_run}/{len(chats_to_process)}. "
        f"Errors this run: {len(current_errors)}."
    )
    if final_error_summary:
        await notify_owner(context, f"Errors during scheduled story generation:\n{final_error_summary}")