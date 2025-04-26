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

async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускается периодически. Проверяет чаты и генерирует истории по расписанию."""
    application: Optional[Application] = context.application
    if not application: logger.critical("Application object not found in job context! Aborting job."); return

    job_start_time = datetime.datetime.now(pytz.utc)
    application.bot_data['last_job_run_time'] = job_start_time
    application.bot_data['last_job_error'] = None
    current_errors: List[Tuple[int, str, Optional[BaseException]]] = []

    bot: Optional[Bot] = context.bot
    if bot is None: logger.error("Bot object missing in daily_story_job context!"); return

    try: bot_info = await bot.get_me(); bot_username = bot_info.username or f"Bot{bot_info.id}"
    except Exception as e: bot_username = "UnknownBot"; logger.error(f"Failed to get bot name: {e}")

    logger.info(f"[{bot_username}] Running scheduled check for story generation...")
    enabled_chat_ids = dm.get_enabled_chats()
    if not enabled_chat_ids: logger.info(f"[{bot_username}] No enabled chats found."); return

    now_utc = datetime.datetime.now(pytz.utc)
    current_hour_utc = now_utc.hour
    current_minute_utc = now_utc.minute
    current_minute_rounded = (current_minute_utc // JOB_CHECK_INTERVAL_MINUTES) * JOB_CHECK_INTERVAL_MINUTES
    logger.debug(f"Current UTC: {now_utc.strftime('%Y-%m-%d %H:%M')}, Checking for tasks ~{current_hour_utc:02d}:{current_minute_rounded:02d}")

    processed_in_this_run = 0
    chats_to_process = []
    # Шаг 1: Определяем, для каких чатов подошло время
    for chat_id in enabled_chat_ids:
        settings = dm.get_chat_settings(chat_id)
        target_hour_utc, target_minute_utc = SCHEDULE_HOUR, SCHEDULE_MINUTE
        custom_time_utc_str = settings.get('custom_schedule_time')

        if custom_time_utc_str:
            try: th, tm = map(int, custom_time_utc_str.split(':')); target_hour_utc, target_minute_utc = th, tm
            except (ValueError, IndexError, TypeError): logger.warning(f"[Chat {chat_id}] Invalid custom time '{custom_time_utc_str}'. Using default."); target_hour_utc, target_minute_utc = SCHEDULE_HOUR, SCHEDULE_MINUTE

        # Сравниваем ЧАС и ОКРУГЛЕННУЮ МИНУТУ в UTC
        if current_hour_utc == target_hour_utc and current_minute_rounded == target_minute_utc:
            chats_to_process.append(chat_id); logger.debug(f"[Chat {chat_id}] Scheduled UTC {target_hour_utc:02d}:{target_minute_utc:02d} matches. Added to process.")

    if not chats_to_process: logger.info("No chats due for processing in this time slot."); return
    logger.info(f"Chats to process now ({len(chats_to_process)}): {chats_to_process}")

    # Шаг 2: Обрабатываем отобранные чаты
    for chat_id in chats_to_process:
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id}]"
        logger.info(f"{current_chat_log_prefix} Processing...")
        story_sent = False
        chat_lang = await get_chat_lang(chat_id)
        error_for_owner: Optional[Tuple[str, Optional[BaseException]]] = None # (message, exception)

        try:
            messages = dm.get_messages_for_chat(chat_id)
            if not messages: logger.info(f"{current_chat_log_prefix} No messages found, skipping."); continue
            logger.info(f"{current_chat_log_prefix} Found {len(messages)} messages.")

            chat_genre = dm.get_chat_genre(chat_id)
            logger.info(f"{current_chat_log_prefix} Using genre: {chat_genre}")

            downloaded_images = await download_images(context, messages, chat_id)
            # Передаем язык для user-friendly ошибок
            story, error_msg_friendly = await gc.safe_generate_story(messages, downloaded_images, chat_genre, chat_lang)

            if story:
                try:
                    date_str = job_start_time.strftime("%d %B %Y")
                    photo_note_str = get_text("photo_info_text", chat_lang, count=len(downloaded_images)) if downloaded_images else ""
                    chat_title_str = str(chat_id)
                    try: chat_info = await bot.get_chat(chat_id); chat_title_str = f"'{chat_info.title}'" if chat_info.title else str(chat_id)
                    except Exception as e_chat: logger.warning(f"{current_chat_log_prefix} Could not get chat title: {e_chat}")

                    final_message_header = get_text("daily_story_header", chat_lang, date_str=date_str, chat_title=chat_title_str, photo_info=photo_note_str)
                    await bot.send_message(chat_id=chat_id, text=final_message_header, parse_mode=ParseMode.HTML)
                    await asyncio.sleep(0.2)

                    # Отправка тела и кнопок
                    sent_message = None; keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"), InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")]])
                    MAX_MSG_LEN = 4096
                    if len(story) > MAX_MSG_LEN: logger.warning(f"{current_chat_log_prefix} Story too long, splitting."); parts = [story[i:i+MAX_MSG_LEN] for i in range(0, len(story), MAX_MSG_LEN)]
                    else: parts = [story]
                    for k, part in enumerate(parts):
                        current_reply_markup = keyboard if k == len(parts) - 1 else None
                        sent_message = await bot.send_message(chat_id=chat_id, text=part, reply_markup=current_reply_markup, parse_mode=ParseMode.MARKDOWN)
                        if k < len(parts) - 1: await asyncio.sleep(0.5)
                    if sent_message: # Обновляем ID в кнопках
                        keyboard_updated = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_message.message_id}"), InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_message.message_id}")]])
                        try: await bot.edit_message_reply_markup(chat_id=chat_id, message_id=sent_message.message_id, reply_markup=keyboard_updated)
                        except BadRequest: pass; 
                        except TelegramError as e: logger.warning(f"Error updating feedback buttons: {e}")

                    logger.info(f"{current_chat_log_prefix} Story sent successfully.")
                    story_sent = True
                    if error_msg_friendly: 
                        try: await bot.send_message(chat_id=chat.id, text=get_text("proxy_note", chat_lang, note=error_msg_friendly), parse_mode=ParseMode.HTML); 
                        except Exception as e: logger.warning(f"{current_chat_log_prefix} Failed proxy note: {e}")

                except TelegramError as e:
                    logger.error(f"{current_chat_log_prefix} TG error sending story: {e}")
                    error_for_owner = (f"TG Send Err ({e.__class__.__name__})", e)
                    error_str = str(e).lower()
                    is_fatal_tg_error = any(sub in error_str for sub in ["blocked", "deactivated", "not found", "kicked", "forbidden", "chat not found", "bot was kicked"])
                    if is_fatal_tg_error: logger.warning(f"{current_chat_log_prefix} Unrecoverable TG error: {e}. Disabling chat."); dm.update_chat_setting(chat_id, 'enabled', False); error_for_owner = (f"Disabled chat: Fatal TG error ({e.__class__.__name__})", e)
                except Exception as e: logger.exception(f"{current_chat_log_prefix} Unexpected error sending story: {e}"); error_for_owner = (f"Send Err ({e.__class__.__name__})", e)

            else: # Ошибка генерации (story is None)
                logger.warning(f"{current_chat_log_prefix} Failed to generate story. Reason: {error_msg_friendly}")
                error_for_owner = (f"Gen Err ({error_msg_friendly or 'Unknown'})", None)
                # Отправляем user-friendly сообщение в чат
                error_text_for_chat = get_text("daily_job_failed_chat_user_friendly", chat_lang, reason=error_msg_friendly or 'неизвестной')
                try: await bot.send_message(chat_id=chat_id, text=error_text_for_chat, parse_mode=ParseMode.HTML)
                except TelegramError as e_err:
                    logger.warning(f"{current_chat_log_prefix} Failed send gen failure notification: {e_err}")
                    error_for_owner = (f"Gen Err + Failed Notify ({e_err.__class__.__name__})", e_err)
                    error_str = str(e_err).lower(); is_fatal_tg_error = any(sub in error_str for sub in ["blocked", "deactivated", "not found", "kicked", "forbidden", "chat not found", "bot was kicked"])
                    if is_fatal_tg_error: logger.warning(f"{current_chat_log_prefix} Disabling chat: TG err sending fail notification."); dm.update_chat_setting(chat_id, 'enabled', False); error_for_owner = (f"Disabled chat: Fatal TG error on notify fail ({e_err.__class__.__name__})", e_err)
                except Exception as e_notify: logger.exception(f"{current_chat_log_prefix} Unexpected error sending fail notification: {e_notify}"); error_for_owner = (f"Gen Err + Unexpected Notify Err ({e_notify.__class__.__name__})", e_notify)

        except Exception as e: logger.exception(f"{current_chat_log_prefix} CRITICAL error processing chat: {e}"); error_for_owner = (f"Critical Err ({e.__class__.__name__})", e)

        # --- Очистка данных и запись ошибки ---
        if story_sent: dm.clear_messages_for_chat(chat_id); processed_in_this_run += 1
        elif error_for_owner: current_errors.append((chat_id, error_for_owner[0], error_for_owner[1])); logger.warning(f"{current_chat_log_prefix} Data NOT cleared due to error: {error_for_owner[0]}")
        else: logger.warning(f"{current_chat_log_prefix} Data NOT cleared, story not sent but no specific error recorded.")

        await asyncio.sleep(1.5) # Пауза между чатами чуть меньше
    # --- Конец цикла по чатам ---

    job_end_time = datetime.datetime.now(pytz.utc)
    duration = job_end_time - job_start_time
    final_error_summary = None
    if current_errors:
        error_lines = [f"Chat {cid}: {msg}" for cid, msg, _ in current_errors]; final_error_summary = "\n".join(error_lines)
        application.bot_data['last_job_error'] = final_error_summary
        await notify_owner(bot=bot, message=f"Errors during scheduled run:\n{final_error_summary}", operation="daily_story_job", important=True)

    logger.info(f"[{bot_username}] Scheduled check finished in {duration.total_seconds():.2f}s. Processed: {processed_in_this_run}/{len(chats_to_process)}. Errors: {len(current_errors)}.")