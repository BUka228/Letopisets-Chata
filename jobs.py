# jobs.py
import logging
import asyncio
import datetime
import pytz # Для работы с UTC временем
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application 
from telegram.error import TelegramError, NetworkError, BadRequest
from typing import Dict, List, Any, Optional
# Импорт для Retries
from tenacity import retry, stop_after_attempt, wait_fixed, wait_exponential, retry_if_exception_type, before_sleep_log

# Импорты из проекта
import data_manager as dm
import gemini_client as gc
# --- ВОТ ПРАВИЛЬНЫЙ ИМПОРТ ИЗ CONFIG ---
from config import (
    BOT_OWNER_ID,
    SCHEDULE_HOUR,
    SCHEDULE_MINUTE,
    JOB_CHECK_INTERVAL_MINUTES # <-- ВОТ ОН!
)
# ------------------------------------
from localization import get_text, get_chat_lang

logger = logging.getLogger(__name__)
retry_log = logging.getLogger(__name__ + '.retry') # Отдельный логгер для retries

# --- КОНСТАНТА ---
# Максимальное количество фото для анализа за один раз
MAX_PHOTOS_TO_ANALYZE = 5

# --- Глобальные переменные для статуса (обновляются в конце job) ---
last_job_run_time: Optional[datetime.datetime] = None
last_job_error: Optional[str] = None

# --- Уведомление владельцу ---
async def notify_owner(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Отправляет сообщение владельцу бота."""
    if not context.bot:
        logger.error("Невозможно отправить уведомление владельцу: объект бота отсутствует.")
        return
    if BOT_OWNER_ID and BOT_OWNER_ID != 0:
        try:
            # Ограничим длину сообщения на всякий случай
            max_len = 4000
            truncated_message = message[:max_len] + '...' if len(message) > max_len else message
            await context.bot.send_message(chat_id=BOT_OWNER_ID, text=f"🚨 Уведомление от Летописца:\n\n{truncated_message}")
            logger.info(f"Уведомление отправлено владельцу (ID: {BOT_OWNER_ID})")
        except TelegramError as e:
            logger.error(f"Не удалось отправить уведомление владельцу (ID: {BOT_OWNER_ID}): {e}")
        except Exception as e:
             logger.error(f"Неожиданная ошибка при отправке уведомления владельцу: {e}", exc_info=True)
    else:
        # Логируем ошибку только если сообщение было важным (содержит "ошибка" или "failed")
        if "ошибка" in message.lower() or "failed" in message.lower() or "error" in message.lower():
            logger.warning("BOT_OWNER_ID не настроен, важное уведомление не отправлено.")

# --- Скачивание изображений с ретраями ---
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(3), # Ждать 3 секунды между попытками скачивания
    retry=retry_if_exception_type((TelegramError, NetworkError, TimeoutError)), # Типы ошибок для повтора
    before_sleep=before_sleep_log(retry_log, logging.WARNING) # Логируем перед повтором
)
async def download_single_image(context: ContextTypes.DEFAULT_TYPE, file_id: str, chat_id_for_log: int) -> Optional[bytes]:
    """Скачивает одно изображение с повторными попытками."""
    log_prefix = f"[Chat {chat_id_for_log}]"
    if context.bot is None:
        logger.error(f"{log_prefix} Объект бота недоступен для скачивания файла {file_id}.")
        return None
    try:
        logger.debug(f"{log_prefix} Попытка скачивания file_id={file_id}...")
        file = await context.bot.get_file(file_id)
        # Ограничим время на скачивание одного файла
        image_bytearray = await asyncio.wait_for(file.download_as_bytearray(), timeout=30.0)

        # Опционально: Проверка максимального размера файла
        MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024 # 20 MB (Примерный лимит Gemini)
        if len(image_bytearray) > MAX_FILE_SIZE_BYTES:
            logger.warning(f"{log_prefix} Фото {file_id} слишком большое ({len(image_bytearray)} байт), пропускаем.")
            return None

        logger.debug(f"{log_prefix} Фото {file_id} ({len(image_bytearray)} байт) успешно скачано.")
        return bytes(image_bytearray)
    except asyncio.TimeoutError:
        logger.error(f"{log_prefix} Таймаут при скачивании файла {file_id}.")
        return None # Не повторяем при таймауте asyncio
    except (TelegramError, NetworkError) as e:
         logger.error(f"{log_prefix} Ошибка Telegram/Сети при скачивании файла {file_id}: {e.__class__.__name__} - {e}")
         raise # Передаем исключение для механизма retry
    except Exception as e:
         logger.error(f"{log_prefix} Неожиданная ошибка при скачивании файла {file_id}: {e}", exc_info=True)
         return None # Не повторяем при других ошибках


async def download_images(
    context: ContextTypes.DEFAULT_TYPE,
    messages: List[Dict[str, Any]],
    chat_id: int,
    max_photos: int
) -> Dict[str, bytes]:
    """Скачивает изображения, используя download_single_image с retries."""
    images_data: Dict[str, bytes] = {}
    photo_messages = [m for m in messages if m.get('type') == 'photo' and m.get('file_id') and m.get('file_unique_id')]
    if not photo_messages: return images_data

    photo_messages.sort(key=lambda x: x.get('timestamp', ''))
    logger.info(f"[Chat {chat_id}] Найдено {len(photo_messages)} фото. Скачивание до {max_photos}...")

    tasks = []
    unique_ids_to_download = [] # Храним уникальные ID файлов для скачивания

    # Отбираем уникальные ID в пределах лимита
    processed_unique_ids = set()
    for msg in photo_messages:
         if len(unique_ids_to_download) >= max_photos: break
         file_unique_id = msg.get('file_unique_id')
         file_id = msg.get('file_id')
         if file_unique_id and file_id and file_unique_id not in processed_unique_ids:
             tasks.append(asyncio.create_task(download_single_image(context, file_id, chat_id)))
             unique_ids_to_download.append(file_unique_id)
             processed_unique_ids.add(file_unique_id)

    if not tasks: return images_data

    logger.debug(f"[Chat {chat_id}] Ожидание завершения {len(tasks)} задач скачивания...")
    results = await asyncio.gather(*tasks, return_exceptions=True) # Собираем результаты и исключения

    # Обрабатываем результаты
    successful_downloads = 0
    for i, unique_id in enumerate(unique_ids_to_download):
        result = results[i]
        if isinstance(result, bytes): # Успешное скачивание
            images_data[unique_id] = result
            successful_downloads += 1
            logger.debug(f"[Chat {chat_id}] Фото {unique_id} ({len(result)} байт) успешно обработано.")
        elif isinstance(result, Exception): # Ошибка после retries
            logger.error(f"[Chat {chat_id}] Финальная ошибка скачивания фото unique_id={unique_id}: {result.__class__.__name__}")
        else: # Вернулся None или что-то иное
            logger.warning(f"[Chat {chat_id}] Скачивание для {unique_id} не вернуло байты (возможно, таймаут или ошибка).")

    logger.info(f"[Chat {chat_id}] Успешно скачано {successful_downloads} из {len(unique_ids_to_download)} запрошенных фото.")
    return images_data

# --- Основная ежедневная задача ---
async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код получения application, bot_username, enabled_chat_ids, now_utc, chats_to_process) ...

    for chat_id in chats_to_process: # Обрабатываем отобранные чаты
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id}]"
        logger.info(f"{current_chat_log_prefix} Processing...")
        story_sent = False
        chat_lang = await get_chat_lang(chat_id)

        try:
            # ... (код получения сообщений, скачивания фото, подготовки контента) ...
            messages = dm.get_messages_for_chat(chat_id)
            if not messages: logger.info(f"{current_chat_log_prefix} No messages found, skipping."); continue
            logger.info(f"{current_chat_log_prefix} Found {len(messages)} messages.")
            downloaded_images = await download_images(context, messages, chat_id, MAX_PHOTOS_TO_ANALYZE)
            prepared_content = gc.prepare_story_parts(messages, downloaded_images)
            if not prepared_content: logger.warning(f"{current_chat_log_prefix} Failed to prepare content."); current_errors.append(f"Chat {chat_id}: Prepare Error"); continue

            story, error_msg = await gc.safe_generate_story(prepared_content)

            if story:
                # --- Отправка истории ---
                try:
                    date_str = job_start_time.strftime("%d %B %Y")
                    photo_note_str = get_text("photo_info_text", chat_lang, count=MAX_PHOTOS_TO_ANALYZE) if downloaded_images else ""
                    header_key = "daily_story_header"
                    chat_title_str = str(chat_id) # Fallback
                    try: chat_info = await bot.get_chat(chat_id); chat_title_str = f"'{chat_info.title}'" if chat_info.title else str(chat_id); 
                    except Exception: pass
                    final_message_header = get_text(header_key, chat_lang, date_str=date_str, chat_title=chat_title_str, photo_info=photo_note_str)

                    # --- ИЗМЕНЕНО: Отправляем заголовок отдельно ---
                    await bot.send_message(
                        chat_id=chat_id, text=final_message_header,
                        parse_mode=ParseMode.HTML
                    )
                    await asyncio.sleep(0.2) # Маленькая пауза
                    # -------------------------------------------

                    # --- Отправляем историю с кнопками ---
                    sent_message = None # Для ID последнего сообщения
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"),
                        InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")
                    ]])
                    MAX_MSG_LEN = 4096
                    if len(story) > MAX_MSG_LEN:
                        logger.warning(f"{current_chat_log_prefix} Story too long, splitting.")
                        parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                        for k, part in enumerate(parts):
                            current_reply_markup = keyboard if k == len(parts) - 1 else None
                            sent_message = await bot.send_message(
                                chat_id=chat_id, text=part,
                                reply_markup=current_reply_markup,
                                parse_mode=ParseMode.MARKDOWN # Markdown v1
                            )
                            await asyncio.sleep(0.5)
                    else:
                        sent_message = await bot.send_message(
                            chat_id=chat_id, text=story,
                            reply_markup=keyboard,
                            parse_mode=ParseMode.MARKDOWN # Markdown v1
                        )

                    # Обновляем кнопки
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
                        except BadRequest: pass
                        except TelegramError as e: logger.warning(f"Error updating buttons: {e}")
                    # --------------------------------------

                    logger.info(f"{current_chat_log_prefix} Story sent successfully.")
                    story_sent = True # Флаг успеха

                    if error_msg: # Примечание от прокси
                        try: await bot.send_message(chat_id=chat_id, text=get_text("proxy_note", chat_lang, note=error_msg))
                        except Exception as e: logger.warning(f"{current_chat_log_prefix} Failed proxy note: {e}")

                # ... (остальная обработка ошибок TelegramError и Exception без изменений) ...
                except TelegramError as e: 
                    logger.error(f"{current_chat_log_prefix} TG error sending story: {e}"); current_errors.append(f"Chat {chat_id}: TG Send Err ({e.__class__.__name__})"); error_str = str(e).lower(); 
                    if "blocked" in error_str or "deactivated" in error_str or "not found" in error_str or "kicked" in error_str or "forbidden" in error_str: logger.warning(f"{current_chat_log_prefix} Unrecoverable TG err. Clearing data."); dm.clear_messages_for_chat(chat_id); dm.update_chat_setting(chat_id, 'enabled', False);
                except Exception as e: logger.exception(f"{current_chat_log_prefix} Unexpected err sending story: {e}"); current_errors.append(f"Chat {chat_id}: Send Err ({e.__class__.__name__})")

            else: # Ошибка генерации
                # ... (логика обработки ошибки генерации без изменений) ...
                logger.warning(f"{current_chat_log_prefix} Failed gen story. Reason: {error_msg}"); current_errors.append(f"Chat {chat_id}: Gen Err ({error_msg or 'Unknown'})")
                try: await bot.send_message(chat_id=chat_id, text=get_text("daily_job_failed_chat", chat_lang, error=error_msg or 'Unknown'))
                except Exception as e_err: logger.warning(f"{current_chat_log_prefix} Failed send gen failure notification: {e_err}"); error_str = str(e_err).lower(); 
                if "blocked" in error_str or "deactivated" in error_str or "not found" in error_str or "kicked" in error_str or "forbidden" in error_str: logger.warning(f"{current_chat_log_prefix} Clearing data: TG err sending fail notification."); dm.clear_messages_for_chat(chat_id); dm.update_chat_setting(chat_id, 'enabled', False);

        # ... (остальная часть try...except и цикла без изменений) ...
        except Exception as e: logger.exception(f"{current_chat_log_prefix} CRITICAL error processing chat: {e}"); current_errors.append(f"Chat {chat_id}: Critical Err ({e.__class__.__name__})")
        if story_sent: dm.clear_messages_for_chat(chat_id); processed_in_this_run += 1
        else: 
            if dm.get_messages_for_chat(chat_id): logger.warning(f"{current_chat_log_prefix} Data NOT cleared due to error.")
        await asyncio.sleep(2) # Пауза после обработки чата

    # ... (Завершение задачи и обновление статуса в bot_data без изменений) ...
    job_end_time = datetime.datetime.now(pytz.utc); duration = job_end_time - job_start_time; final_error_summary = "\n".join(current_errors) if current_errors else None; application.bot_data['last_job_error'] = final_error_summary
    logger.info(f"[{bot_username}] Scheduled check finished in {duration}. Processed: {processed_in_this_run}/{len(chats_to_process)}. Errors: {len(current_errors)}.")
    if final_error_summary: await notify_owner(context, f"Errors during scheduled generation:\n{final_error_summary}")