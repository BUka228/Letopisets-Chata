# jobs.py
import logging
import asyncio
import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError, NetworkError, BadRequest # Добавляем BadRequest для обновления кнопок
from typing import Dict, List, Any, Optional
# Импорт для Retries
from tenacity import retry, stop_after_attempt, wait_fixed, wait_exponential, retry_if_exception_type, before_sleep_log

# Импорты из проекта
import data_manager as dm
import gemini_client as gc # Используем обновленный gemini_client с safe_generate_story
from config import BOT_OWNER_ID # Импортируем только BOT_OWNER_ID
from localization import get_text, get_chat_lang # Импортируем функции локализации

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
    """Ежедневная задача: генерация и отправка историй с учетом настроек чата."""
    global last_job_run_time, last_job_error # Используем глобальные переменные
    job_start_time = datetime.datetime.now(datetime.timezone.utc)
    last_job_run_time = job_start_time # Фиксируем время старта

    bot = context.bot
    if bot is None: logger.error("Объект бота не найден в daily_story_job!"); return
    bot_username = "UnknownBot"
    try: bot_username = (await bot.get_me()).username or "UnknownBot"
    except Exception as e: logger.error(f"Не удалось получить имя бота: {e}")

    logger.info(f"[{bot_username}] Запуск ЕЖЕДНЕВНОЙ ЗАДАЧИ генерации историй...")
    enabled_chat_ids = dm.get_enabled_chats()
    if not enabled_chat_ids: logger.info(f"[{bot_username}] Нет активных чатов."); return

    total_chats = len(enabled_chat_ids)
    processed_chats_count = 0
    failed_chats_count = 0
    global_error_summary = []

    logger.info(f"[{bot_username}] Обнаружено {total_chats} активных чатов для обработки.")

    for i, chat_id in enumerate(enabled_chat_ids):
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id} ({i+1}/{total_chats})]"
        logger.info(f"{current_chat_log_prefix} Начало обработки...")
        story_sent_successfully = False
        chat_lang = await get_chat_lang(chat_id)

        try:
            messages = dm.get_messages_for_chat(chat_id)
            if not messages:
                logger.info(f"{current_chat_log_prefix} Нет сообщений в БД."); dm.clear_messages_for_chat(chat_id); await asyncio.sleep(0.1); continue

            logger.info(f"{current_chat_log_prefix} Собрано {len(messages)} сообщ.")
            # --- Вызов download_images с константой ---
            downloaded_images = await download_images(context, messages, chat_id, MAX_PHOTOS_TO_ANALYZE)
            prepared_content = gc.prepare_story_parts(messages, downloaded_images)

            if not prepared_content:
                logger.warning(f"{current_chat_log_prefix} Не удалось подготовить контент."); global_error_summary.append(f"Chat {chat_id}: Ошибка подготовки"); failed_chats_count += 1; await asyncio.sleep(0.5); continue

            # --- Используем safe_generate_story для вызова прокси с retries ---
            story, error_msg = await gc.safe_generate_story(prepared_content)

            if story:
                # --- Отправка истории с кнопками ---
                try:
                    photo_note_str = get_text("photo_info_text", chat_lang, count=MAX_PHOTOS_TO_ANALYZE) if downloaded_images else ""
                    header_key = "daily_story_header"; full_message_text = get_text(header_key, chat_lang, photo_info=photo_note_str) + story
                    MAX_MSG_LEN = 4096; sent_message = None
                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"), InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")]]) # Заглушка

                    if len(full_message_text) > MAX_MSG_LEN:
                         logger.warning(f"{current_chat_log_prefix} История длинная, разбиваем.")
                         header_long_key = "daily_story_header_long"; await bot.send_message(chat_id=chat_id, text=get_text(header_long_key, chat_lang, photo_info=photo_note_str)); await asyncio.sleep(0.5)
                         parts = [story[j:j+MAX_MSG_LEN] for j in range(0, len(story), MAX_MSG_LEN)]
                         for k, part in enumerate(parts):
                             # Кнопки добавляем только к последней части
                             current_reply_markup = keyboard if k == len(parts) - 1 else None
                             sent_message = await bot.send_message(chat_id=chat_id, text=part, reply_markup=current_reply_markup); await asyncio.sleep(0.5)
                    else: sent_message = await bot.send_message(chat_id=chat_id, text=full_message_text, reply_markup=keyboard)

                    # Обновляем callback_data в кнопках ID реального сообщения
                    if sent_message:
                         keyboard_updated = InlineKeyboardMarkup([[ InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_message.message_id}"), InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_message.message_id}") ]])
                         try: await bot.edit_message_reply_markup(chat_id=chat_id, message_id=sent_message.message_id, reply_markup=keyboard_updated)
                         # Игнорируем ошибку BadRequest (если пользователь успел удалить сообщение или нажать кнопку)
                         except BadRequest as e_br: logger.debug(f"Не удалось обновить кнопки для msg {sent_message.message_id}: {e_br}")
                         except TelegramError as e_edit: logger.warning(f"Ошибка обновления кнопок для msg {sent_message.message_id}: {e_edit}")

                    logger.info(f"{current_chat_log_prefix} История успешно отправлена."); story_sent_successfully = True
                    if error_msg: # Обработка примечания от прокси
                         try: await bot.send_message(chat_id=chat_id, text=get_text("proxy_note", chat_lang, note=error_msg))
                         except Exception as e: logger.warning(f"{current_chat_log_prefix} Не удалось отправить примечание: {e}")
                except TelegramError as e: # Ошибки отправки Telegram
                    logger.error(f"{current_chat_log_prefix} Ошибка Telegram при отправке: {e}"); error_str = str(e).lower(); is_fatal_tg_error = False
                    if "bot was blocked" in error_str or "user is deactivated" in error_str or "chat not found" in error_str or "bot was kicked" in error_str or "chat_write_forbidden" in error_str:
                         logger.warning(f"{current_chat_log_prefix} Неустранимая ошибка Telegram. Очищаем данные."); dm.clear_messages_for_chat(chat_id); is_fatal_tg_error = True
                    global_error_summary.append(f"Chat {chat_id}: TG Error ({e.__class__.__name__})"); failed_chats_count += 1
                    if not is_fatal_tg_error: 
                        try: await bot.send_message(chat_id=chat_id, text=get_text("error_telegram", chat_lang, error=e)) 
                        except Exception: pass
                except Exception as e: # Другие ошибки отправки
                    logger.error(f"{current_chat_log_prefix} Неожиданная ошибка при отправке: {e}", exc_info=True); global_error_summary.append(f"Chat {chat_id}: Send Error ({e.__class__.__name__})"); failed_chats_count += 1; 
                    try: await bot.send_message(chat_id=chat_id, text=get_text("error_unexpected_send", chat_lang)) 
                    except Exception: pass
            else: # Ошибка генерации (story is None)
                logger.warning(f"{current_chat_log_prefix} Не удалось сгенерировать историю. Причина: {error_msg}"); global_error_summary.append(f"Chat {chat_id}: Generation Error ({error_msg or 'Unknown'})"); failed_chats_count += 1
                try: await bot.send_message(chat_id=chat_id, text=get_text("daily_job_failed_chat", chat_lang, error=error_msg or 'Неизвестно'))
                except TelegramError as e_err: # Ошибка отправки уведомления об ошибке
                    error_str = str(e_err).lower()
                    if "bot was blocked" in error_str or "user is deactivated" in error_str or "chat not found" in error_str or "bot was kicked" in error_str or "chat_write_forbidden" in error_str: logger.warning(f"{current_chat_log_prefix} Очищаем данные из-за ошибки TG при отправке уведомления об ошибке."); dm.clear_messages_for_chat(chat_id)
                    else: logger.warning(f"{current_chat_log_prefix} Не удалось отправить сообщение об ошибке генерации: {e_err}")
                except Exception as e: logger.error(f"{current_chat_log_prefix} Неожиданная ошибка при отправке уведомления об ошибке генерации: {e}", exc_info=True)
        except Exception as e: # Глобальный обработчик ошибок для чата
             logger.error(f"{current_chat_log_prefix} КРИТИЧЕСКАЯ ОШИБКА обработки чата: {e}", exc_info=True); global_error_summary.append(f"Chat {chat_id}: Critical Error ({e.__class__.__name__})"); failed_chats_count += 1

        # --- Очистка данных ---
        if story_sent_successfully:
            dm.clear_messages_for_chat(chat_id); processed_chats_count += 1
        else:
            if dm.get_messages_for_chat(chat_id): logger.warning(f"{current_chat_log_prefix} Данные НЕ очищены из-за ошибки.")

        # --- Пауза ---
        logger.debug(f"{current_chat_log_prefix} Завершение обработки. Пауза...")
        await asyncio.sleep(5) # Пауза между чатами

    # --- Завершение задачи ---
    job_end_time = datetime.datetime.now(datetime.timezone.utc); duration = job_end_time - job_start_time
    last_job_error = "\n".join(global_error_summary) if global_error_summary else None # Сохраняем ошибки
    logger.info(f"[{bot_username}] Ежедневная задача завершена за {duration}. Успешно: {processed_chats_count}/{total_chats}. Ошибок: {failed_chats_count}.")
    if failed_chats_count > 0:
        await notify_owner(context, f"Ежедневная задача завершена с {failed_chats_count} ошибками:\n{last_job_error}")