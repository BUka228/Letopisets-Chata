# jobs.py
import logging
import asyncio
import datetime
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, constants as tg_constants
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError, NetworkError, BadRequest
from typing import Dict, List, Any, Optional
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log

import data_manager as dm
import gemini_client as gc
from config import (
    BOT_OWNER_ID, SCHEDULE_HOUR, SCHEDULE_MINUTE,
    JOB_CHECK_INTERVAL_MINUTES
)
from localization import get_text, get_chat_lang # Используем get_chat_lang

logger = logging.getLogger(__name__)
retry_log = logging.getLogger(__name__ + '.retry')

MAX_PHOTOS_TO_ANALYZE = 5

# Глобальные переменные убраны, используются context.application.bot_data

# --- Уведомление владельцу (без изменений) ---
async def notify_owner(context: ContextTypes.DEFAULT_TYPE, message: str):
    if not context.bot: logger.error("Notify owner failed: bot object missing."); return
    if BOT_OWNER_ID and BOT_OWNER_ID != 0:
        try: max_len = 4000; truncated_message = message[:max_len] + ('...' if len(message) > max_len else ''); await context.bot.send_message(chat_id=BOT_OWNER_ID, text=f"🚨 Уведомление от Летописца:\n\n{truncated_message}"); logger.info(f"Уведомление отправлено владельцу (ID: {BOT_OWNER_ID})")
        except Exception as e: logger.exception(f"Не удалось отправить уведомление владельцу (ID: {BOT_OWNER_ID}): {e}")
    elif "ошибка" in message.lower() or "failed" in message.lower() or "error" in message.lower(): logger.warning("BOT_OWNER_ID не настроен, важное уведомление не отправлено.")

# --- Скачивание изображений с ретраями (без изменений) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(3), retry=retry_if_exception_type((TelegramError, NetworkError, TimeoutError)), before_sleep=before_sleep_log(retry_log, logging.WARNING))
async def download_single_image(context: ContextTypes.DEFAULT_TYPE, file_id: str, chat_id_for_log: int) -> Optional[bytes]:
    log_prefix = f"[Chat {chat_id_for_log}]"; 
    if context.bot is None: logger.error(f"{log_prefix} Bot object unavailable for download {file_id}"); return None
    try: 
        logger.debug(f"{log_prefix} Attempting download file_id={file_id}..."); file = await context.bot.get_file(file_id); image_bytearray = await asyncio.wait_for(file.download_as_bytearray(), timeout=30.0); MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024; 
        if len(image_bytearray) > MAX_FILE_SIZE_BYTES: logger.warning(f"{log_prefix} Photo {file_id} too large ({len(image_bytearray)} bytes), skipping."); return None; logger.debug(f"{log_prefix} Photo {file_id} ({len(image_bytearray)} bytes) downloaded."); return bytes(image_bytearray)
    except asyncio.TimeoutError: logger.error(f"{log_prefix} Timeout downloading file {file_id}."); return None
    except (TelegramError, NetworkError) as e: logger.error(f"{log_prefix} Telegram/Network error downloading file {file_id}: {e.__class__.__name__} - {e}"); raise
    except Exception as e: logger.exception(f"{log_prefix} Unexpected error downloading file {file_id}: {e}"); return None

# --- Функция download_images (без изменений) ---
async def download_images(context: ContextTypes.DEFAULT_TYPE, messages: List[Dict[str, Any]], chat_id: int, max_photos: int) -> Dict[str, bytes]:
    images_data: Dict[str, bytes] = {}; photo_messages = [m for m in messages if m.get('type') == 'photo' and m.get('file_id') and m.get('file_unique_id')]; 
    if not photo_messages: return images_data
    photo_messages.sort(key=lambda x: x.get('timestamp', '')); logger.info(f"[Chat {chat_id}] Found {len(photo_messages)} photos. Downloading up to {max_photos}..."); tasks = []; unique_ids_to_download = []; processed_unique_ids = set()
    for msg in photo_messages:
         if len(unique_ids_to_download) >= max_photos: break
         file_unique_id = msg.get('file_unique_id'); file_id = msg.get('file_id')
         if file_unique_id and file_id and file_unique_id not in processed_unique_ids: tasks.append(asyncio.create_task(download_single_image(context, file_id, chat_id))); unique_ids_to_download.append(file_unique_id); processed_unique_ids.add(file_unique_id)
    if not tasks: return images_data
    logger.debug(f"[Chat {chat_id}] Waiting for {len(tasks)} download tasks..."); results = await asyncio.gather(*tasks, return_exceptions=True); successful_downloads = 0
    for i, unique_id in enumerate(unique_ids_to_download): 
        result = results[i]
        if isinstance(result, bytes): images_data[unique_id] = result; successful_downloads += 1; logger.debug(f"[Chat {chat_id}] Photo {unique_id} ({len(result)} bytes) processed.")
        elif isinstance(result, Exception): logger.error(f"[Chat {chat_id}] Final download error for photo unique_id={unique_id}: {result.__class__.__name__}")
        else: logger.warning(f"[Chat {chat_id}] Download for {unique_id} returned None (timeout or error).")
    logger.info(f"[Chat {chat_id}] Successfully downloaded {successful_downloads}/{len(unique_ids_to_download)} requested photos."); return images_data

# --- Основная периодическая задача ---
async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускается периодически. Проверяет чаты и генерирует истории по расписанию."""
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

    bot = context.bot; 
    if bot is None: logger.error("Bot object missing!"); return
    bot_username = "UnknownBot"; 
    try: bot_username = (await bot.get_me()).username or "UnknownBot"; 
    except Exception as e: logger.error(f"Failed to get bot name: {e}")
    logger.info(f"[{bot_username}] Running scheduled check for story generation...")
    enabled_chat_ids = dm.get_enabled_chats(); 
    if not enabled_chat_ids: logger.info(f"[{bot_username}] No enabled chats found."); return
    now_utc = datetime.datetime.now(pytz.utc); current_hour_utc = now_utc.hour; current_minute_utc = now_utc.minute; current_minute_rounded = (current_minute_utc // JOB_CHECK_INTERVAL_MINUTES) * JOB_CHECK_INTERVAL_MINUTES
    logger.debug(f"UTC: {now_utc.strftime('%Y-%m-%d %H:%M')}, Slot: {current_hour_utc:02d}:{current_minute_rounded:02d}")
    processed_in_this_run = 0

    # Инициализация списка ПЕРЕД первым циклом
    chats_to_process = []

    # Шаг 1: Определяем, для каких чатов подошло время
    for chat_id in enabled_chat_ids:
        settings = dm.get_chat_settings(chat_id)
        target_hour, target_minute = SCHEDULE_HOUR, SCHEDULE_MINUTE
        custom_time_str = settings.get('custom_schedule_time')
        if custom_time_str:
            try: parts = custom_time_str.split(':'); target_hour, target_minute = int(parts[0]), int(parts[1])
            except (ValueError, IndexError, TypeError): logger.warning(f"[Chat {chat_id}] Invalid custom time '{custom_time_str}', using default."); target_hour, target_minute = SCHEDULE_HOUR, SCHEDULE_MINUTE
        # Сравниваем время
        if current_hour_utc == target_hour and current_minute_rounded == target_minute:
             chats_to_process.append(chat_id) # Добавляем в список
             logger.debug(f"[Chat {chat_id}] Scheduled time match. Added to process list.")
    # --- Конец ПЕРВОГО цикла ---

    # --- Начало ВТОРОГО цикла (с правильным отступом) ---
    if not chats_to_process:
        logger.info("No chats due for processing in this time slot.")
        return # Выходим, если обрабатывать нечего

    logger.info(f"Chats to process now ({current_hour_utc:02d}:{current_minute_rounded:02d}): {chats_to_process}")

    # Шаг 2: Обрабатываем отобранные чаты
    for chat_id in chats_to_process: # <-- Теперь эта переменная определена и содержит нужные ID
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id}]"
        logger.info(f"{current_chat_log_prefix} Processing...")
        story_sent = False
        # Получаем язык чата
        chat_lang = await get_chat_lang(chat_id)

        try:
            messages = dm.get_messages_for_chat(chat_id)
            if not messages:
                logger.info(f"{current_chat_log_prefix} No messages found in DB, skipping generation.")
                continue

            logger.info(f"{current_chat_log_prefix} Found {len(messages)} messages.")
            downloaded_images = await download_images(context, messages, chat_id, MAX_PHOTOS_TO_ANALYZE)
            prepared_content = gc.prepare_story_parts(messages, downloaded_images)
            if not prepared_content:
                logger.warning(f"{current_chat_log_prefix} Failed to prepare content for AI.")
                current_errors.append(f"Chat {chat_id}: Prepare Content Error")
                continue

            # Используем safe_generate_story
            story, error_msg = await gc.safe_generate_story(prepared_content)

            if story:
                # --- Отправка истории ---
                try:
                    date_str = job_start_time.strftime("%d %B %Y")
                    photo_note_str = get_text("photo_info_text", chat_lang, count=MAX_PHOTOS_TO_ANALYZE) if downloaded_images else ""
                    header_key = "daily_story_header"
                    chat_title_str = str(chat_id)
                    try: chat_info = await bot.get_chat(chat_id); chat_title_str = f"'{chat_info.title}'" if chat_info.title else str(chat_id)
                    except Exception: pass
                    final_message_header = get_text(header_key, chat_lang, date_str=date_str, chat_title=chat_title_str, photo_info=photo_note_str)

                    # Отправляем заголовок отдельно
                    await bot.send_message(chat_id=chat_id, text=final_message_header, parse_mode=ParseMode.HTML)
                    await asyncio.sleep(0.2)

                    # Отправляем историю с кнопками
                    sent_message = None
                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"), InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")]])
                    MAX_MSG_LEN = 4096
                    if len(story) > MAX_MSG_LEN:
                         logger.warning(f"{current_chat_log_prefix} Story too long, splitting.")
                         parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                         for k, part in enumerate(parts): current_reply_markup = keyboard if k == len(parts) - 1 else None; sent_message = await bot.send_message(chat_id=chat_id, text=part, reply_markup=current_reply_markup, parse_mode=ParseMode.MARKDOWN); await asyncio.sleep(0.5)
                    else: sent_message = await bot.send_message(chat_id=chat_id, text=story, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

                    # Обновляем кнопки
                    if sent_message:
                        keyboard_updated = InlineKeyboardMarkup([[ InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_message.message_id}"), InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_message.message_id}") ]])
                        try: await bot.edit_message_reply_markup(chat_id=chat_id, message_id=sent_message.message_id, reply_markup=keyboard_updated)
                        except BadRequest: pass; 
                        except TelegramError as e: logger.warning(f"Error updating buttons: {e}")

                    logger.info(f"{current_chat_log_prefix} Story sent successfully.")
                    story_sent = True

                    if error_msg: 
                        try: await bot.send_message(chat_id=chat_id, text=get_text("proxy_note", chat_lang, note=error_msg)); 
                        except Exception as e: logger.warning(f"{current_chat_log_prefix} Failed proxy note: {e}")

                except TelegramError as e:
                    logger.error(f"{current_chat_log_prefix} TG error sending story: {e}"); current_errors.append(f"Chat {chat_id}: TG Send Err ({e.__class__.__name__})"); error_str = str(e).lower();
                    if "blocked" in error_str or "deactivated" in error_str or "not found" in error_str or "kicked" in error_str or "forbidden" in error_str: logger.warning(f"{current_chat_log_prefix} Unrecoverable TG err. Clearing data."); dm.clear_messages_for_chat(chat_id); dm.update_chat_setting(chat_id, 'enabled', False)
                except Exception as e: logger.exception(f"{current_chat_log_prefix} Unexpected err sending story: {e}"); current_errors.append(f"Chat {chat_id}: Send Err ({e.__class__.__name__})")
            else: # Ошибка генерации
                logger.warning(f"{current_chat_log_prefix} Failed gen story. Reason: {error_msg}"); current_errors.append(f"Chat {chat_id}: Gen Err ({error_msg or 'Unknown'})")
                try: await bot.send_message(chat_id=chat_id, text=get_text("daily_job_failed_chat", chat_lang, error=error_msg or 'Unknown'))
                except Exception as e_err: logger.warning(f"{current_chat_log_prefix} Failed send gen failure notification: {e_err}"); error_str = str(e_err).lower()
                if "blocked" in error_str or "deactivated" in error_str or "not found" in error_str or "kicked" in error_str or "forbidden" in error_str: logger.warning(f"{current_chat_log_prefix} Clearing data: TG err sending fail notification."); dm.clear_messages_for_chat(chat_id); dm.update_chat_setting(chat_id, 'enabled', False)
                
        except Exception as e: # Глобальный обработчик ошибок чата
            logger.exception(f"{current_chat_log_prefix} CRITICAL error processing chat: {e}"); current_errors.append(f"Chat {chat_id}: Critical Err ({e.__class__.__name__})")

        # Очистка данных ТОЛЬКО при успешной отправке
        if story_sent:
            dm.clear_messages_for_chat(chat_id)
            processed_in_this_run += 1
        else:
            # Проверяем, остались ли сообщения (на случай, если фатальная ошибка TG их уже удалила)
            if dm.get_messages_for_chat(chat_id):
                 logger.warning(f"{current_chat_log_prefix} Data NOT cleared due to error.")

        await asyncio.sleep(2) # Пауза после обработки КАЖДОГО чата из списка
    # --- Конец ВТОРОГО цикла ---

    # --- Завершение задачи ---
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