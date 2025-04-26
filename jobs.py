# jobs.py
import logging
import asyncio
import datetime
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode # <-- Убедитесь, что этот импорт есть
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError, NetworkError, BadRequest
from typing import Dict, List, Any, Optional

import data_manager as dm
import gemini_client as gc
from config import (
    BOT_OWNER_ID, SCHEDULE_HOUR, SCHEDULE_MINUTE,
    JOB_CHECK_INTERVAL_MINUTES, DATA_FILE # Добавили DATA_FILE
)
from localization import get_text, get_chat_lang
from utils import download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner


logger = logging.getLogger(__name__)

# --- Функция download_images (без изменений) ---
async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Запускается периодически. Проверяет чаты и генерирует истории по расписанию, учитывая жанр.
    """
    application: Optional[Application] = context.application
    if not application:
        logger.critical("Application object not found in job context! Aborting job.")
        return

    job_start_time = datetime.datetime.now(pytz.utc)
    application.bot_data['last_job_run_time'] = job_start_time
    application.bot_data['last_job_error'] = None # Сброс ошибки
    current_errors: List[Tuple[int, str, Optional[BaseException]]] = [] # (chat_id, message, exception)

    bot: Optional[Bot] = context.bot
    if bot is None:
        logger.error("Bot object missing in daily_story_job context!")
        return

    # ... (получение bot_username, enabled_chat_ids, now_utc, current_hour_utc, etc. - без изменений) ...
    try: bot_info = await bot.get_me(); bot_username = bot_info.username or f"Bot{bot_info.id}"
    except Exception as e: bot_username = "UnknownBot"; logger.error(f"Failed to get bot name: {e}")

    logger.info(f"[{bot_username}] Running scheduled check for story generation...")
    enabled_chat_ids = dm.get_enabled_chats()
    if not enabled_chat_ids: logger.info(f"[{bot_username}] No enabled chats found."); return

    now_utc = datetime.datetime.now(pytz.utc)
    current_hour_utc = now_utc.hour
    current_minute_utc = now_utc.minute
    current_minute_rounded = (current_minute_utc // JOB_CHECK_INTERVAL_MINUTES) * JOB_CHECK_INTERVAL_MINUTES
    logger.debug(f"Current UTC: {now_utc.strftime('%Y-%m-%d %H:%M')}, Checking for tasks scheduled around ~{current_hour_utc:02d}:{current_minute_rounded:02d}")

    processed_in_this_run = 0
    chats_to_process = []
    # Шаг 1: Определяем, для каких чатов подошло время
    for chat_id in enabled_chat_ids:
        settings = dm.get_chat_settings(chat_id)
        # --- ИЗМЕНЕНО: Учитываем таймзону чата при определении времени запуска ---
        target_hour_utc, target_minute_utc = SCHEDULE_HOUR, SCHEDULE_MINUTE
        custom_time_utc_str = settings.get('custom_schedule_time')
        chat_tz_str = settings.get('timezone', 'UTC')

        if custom_time_utc_str:
            try:
                parts = custom_time_utc_str.split(':')
                target_hour_utc, target_minute_utc = int(parts[0]), int(parts[1])
            except (ValueError, IndexError, TypeError):
                logger.warning(f"[Chat {chat_id}] Invalid custom time format '{custom_time_utc_str}'. Using default {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} UTC.")
                target_hour_utc, target_minute_utc = SCHEDULE_HOUR, SCHEDULE_MINUTE

        # Сравниваем ЧАС и ОКРУГЛЕННУЮ МИНУТУ в UTC
        if current_hour_utc == target_hour_utc and current_minute_rounded == target_minute_utc:
            chats_to_process.append(chat_id)
            logger.debug(f"[Chat {chat_id}] Scheduled UTC time {target_hour_utc:02d}:{target_minute_utc:02d} matches current slot. Added to process list.")
        # ----- Конец изменений в определении времени -----

    if not chats_to_process: logger.info("No chats due for processing in this time slot."); return
    logger.info(f"Chats to process now ({current_hour_utc:02d}:{current_minute_rounded:02d}): {chats_to_process}")

    # Шаг 2: Обрабатываем отобранные чаты
    for chat_id in chats_to_process:
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id}]"
        logger.info(f"{current_chat_log_prefix} Processing...")
        story_sent = False
        chat_lang = await get_chat_lang(chat_id)
        error_for_owner: Optional[Tuple[str, Optional[BaseException]]] = None # (message, exception)

        try:
            messages = dm.get_messages_for_chat(chat_id)
            if not messages:
                logger.info(f"{current_chat_log_prefix} No messages found in DB, skipping generation.")
                continue

            logger.info(f"{current_chat_log_prefix} Found {len(messages)} messages.")

            # --- ИЗМЕНЕНО: Получаем жанр чата ---
            chat_genre = dm.get_chat_genre(chat_id)
            logger.info(f"{current_chat_log_prefix} Using genre: {chat_genre}")

            # Скачивание изображений
            downloaded_images = await download_images(context, messages, chat_id) # MAX_PHOTOS_TO_ANALYZE используется по умолчанию

            # --- ИЗМЕНЕНО: Передаем жанр в safe_generate_story ---
            story, error_msg = await gc.safe_generate_story(messages, downloaded_images, chat_genre)

            if story:
                # --- Отправка истории (без изменений в логике отправки) ---
                try:
                    # ... (код получения date_str, photo_note_str, chat_title_str) ...
                    date_str = job_start_time.strftime("%d %B %Y") # Или можно взять дату из сообщений?
                    photo_note_str = get_text("photo_info_text", chat_lang, count=len(downloaded_images)) if downloaded_images else "" # Точное кол-во
                    header_key = "daily_story_header"
                    chat_title_str = str(chat_id)
                    try:
                        chat_info = await bot.get_chat(chat_id); chat_title_str = f"'{chat_info.title}'" if chat_info.title else str(chat_id)
                    except Exception as e_chat: logger.warning(f"{current_chat_log_prefix} Could not get chat title: {e_chat}")

                    final_message_header = get_text(header_key, chat_lang, date_str=date_str, chat_title=chat_title_str, photo_info=photo_note_str)
                    await bot.send_message(chat_id=chat_id, text=final_message_header, parse_mode=ParseMode.HTML)
                    await asyncio.sleep(0.2)

                    # ... (код отправки тела истории с кнопками, разбиение на части, обновление кнопок - без изменений) ...
                    # ----- НАЧАЛО БЛОКА ОТПРАВКИ (без изменений) -----
                    sent_message = None
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"),
                        InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")]])
                    MAX_MSG_LEN = 4096
                    if len(story) > MAX_MSG_LEN:
                        logger.warning(f"{current_chat_log_prefix} Story is too long, splitting.")
                        parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                        for k, part in enumerate(parts):
                            current_reply_markup = keyboard if k == len(parts) - 1 else None
                            sent_message = await bot.send_message(chat_id=chat_id, text=part, reply_markup=current_reply_markup, parse_mode=ParseMode.MARKDOWN)
                            await asyncio.sleep(0.5)
                    else:
                        sent_message = await bot.send_message(chat_id=chat_id, text=story, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

                    if sent_message:
                         keyboard_updated = InlineKeyboardMarkup([[
                            InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_message.message_id}"),
                            InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_message.message_id}")]])
                         try: await bot.edit_message_reply_markup(chat_id=chat_id, message_id=sent_message.message_id, reply_markup=keyboard_updated)
                         except BadRequest: pass
                         except TelegramError as e: logger.warning(f"Error updating buttons: {e}")
                    # ----- КОНЕЦ БЛОКА ОТПРАВКИ (без изменений) -----

                    logger.info(f"{current_chat_log_prefix} Story sent successfully.")
                    story_sent = True # Успех!

                    # Отправляем примечание от прокси, если было
                    if error_msg:
                        try: await bot.send_message(chat_id=chat_id, text=get_text("proxy_note", chat_lang, note=error_msg))
                        except Exception as e: logger.warning(f"{current_chat_log_prefix} Failed to send proxy note: {e}")

                except TelegramError as e:
                    logger.error(f"{current_chat_log_prefix} TG error sending story: {e}")
                    error_for_owner = (f"TG Send Err ({e.__class__.__name__})", e)
                    error_str = str(e).lower()
                    is_fatal_tg_error = any(sub in error_str for sub in ["blocked", "deactivated", "not found", "kicked", "forbidden", "chat not found", "bot was kicked"])
                    if is_fatal_tg_error:
                        logger.warning(f"{current_chat_log_prefix} Unrecoverable TG error: {e}. Clearing data and disabling chat.")
                        dm.clear_messages_for_chat(chat_id) # Очищаем данные сразу
                        dm.update_chat_setting(chat_id, 'enabled', False)
                        error_for_owner = (f"Disabled chat due to fatal TG error: {e.__class__.__name__}", e)
                except Exception as e:
                    logger.exception(f"{current_chat_log_prefix} Unexpected error sending story: {e}")
                    error_for_owner = (f"Send Err ({e.__class__.__name__})", e)

            else: # Ошибка генерации (story is None)
                logger.warning(f"{current_chat_log_prefix} Failed to generate story. Reason: {error_msg}")
                error_for_owner = (f"Gen Err ({error_msg or 'Unknown'})", None)
                try:
                    await bot.send_message(chat_id=chat_id, text=get_text("daily_job_failed_chat", chat_lang, error=error_msg or 'Unknown'))
                except TelegramError as e_err:
                    logger.warning(f"{current_chat_log_prefix} Failed send gen failure notification: {e_err}")
                    error_for_owner = (f"Gen Err + Failed Notify ({e_err.__class__.__name__})", e_err)
                    error_str = str(e_err).lower()
                    is_fatal_tg_error = any(sub in error_str for sub in ["blocked", "deactivated", "not found", "kicked", "forbidden", "chat not found", "bot was kicked"])
                    if is_fatal_tg_error:
                        logger.warning(f"{current_chat_log_prefix} Clearing data: TG err sending fail notification.")
                        dm.clear_messages_for_chat(chat_id)
                        dm.update_chat_setting(chat_id, 'enabled', False)
                        error_for_owner = (f"Disabled chat due to fatal TG error on notify fail: {e_err.__class__.__name__}", e_err)
                except Exception as e_notify:
                     logger.exception(f"{current_chat_log_prefix} Unexpected error sending fail notification: {e_notify}")
                     error_for_owner = (f"Gen Err + Unexpected Notify Err ({e_notify.__class__.__name__})", e_notify)

        except Exception as e: # Глобальный обработчик ошибок для чата
            logger.exception(f"{current_chat_log_prefix} CRITICAL error processing chat: {e}")
            error_for_owner = (f"Critical Err ({e.__class__.__name__})", e)

        # --- Очистка данных и запись ошибки ---
        if story_sent:
            dm.clear_messages_for_chat(chat_id)
            processed_in_this_run += 1
        elif error_for_owner:
            # Записываем ошибку для этого чата
            current_errors.append((chat_id, error_for_owner[0], error_for_owner[1]))
            logger.warning(f"{current_chat_log_prefix} Data NOT cleared due to error: {error_for_owner[0]}")
        else:
            # Если не было ошибки, но история не отправлена (странная ситуация)
             logger.warning(f"{current_chat_log_prefix} Data NOT cleared, story not sent but no specific error recorded.")

        # Пауза после обработки КАЖДОГО чата
        await asyncio.sleep(2)
    # --- Конец цикла по чатам ---

    # --- Завершение периодической задачи ---
    job_end_time = datetime.datetime.now(pytz.utc)
    duration = job_end_time - job_start_time
    final_error_summary = None
    if current_errors:
        error_lines = [f"Chat {cid}: {msg}" for cid, msg, _ in current_errors]
        final_error_summary = "\n".join(error_lines)
        application.bot_data['last_job_error'] = final_error_summary # Сохраняем сводку
        # Отправляем детальное уведомление владельцу
        await notify_owner(
            bot=bot, # Передаем bot напрямую
            message=f"Errors occurred during scheduled story generation:\n{final_error_summary}",
            operation="daily_story_job",
            important=True
            # Можно добавить детализацию по каждой ошибке, если нужно
            # exception=current_errors[0][2] if current_errors else None
        )

    logger.info(
        f"[{bot_username}] Scheduled check finished in {duration}. "
        f"Processed: {processed_in_this_run}/{len(chats_to_process)}. "
        f"Errors this run: {len(current_errors)}."
    )