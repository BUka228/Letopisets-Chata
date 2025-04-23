# jobs.py
import logging
import asyncio
from telegram.ext import ContextTypes
from telegram.error import TelegramError, NetworkError
from typing import Dict, List, Any, Optional # Добавлен Optional

import data_manager as dm
import gemini_client as gc

logger = logging.getLogger(__name__)

MAX_PHOTOS_TO_ANALYZE = 5

async def download_images(
    context: ContextTypes.DEFAULT_TYPE,
    messages: List[Dict[str, Any]],
    chat_id: int,
    max_photos: int
) -> Dict[str, bytes]:
    images_data: Dict[str, bytes] = {}
    photo_messages = [
        m for m in messages
        if m.get('type') == 'photo' and m.get('file_id') and m.get('file_unique_id')
    ]
    if not photo_messages:
        logger.debug(f"[Chat {chat_id}] Фото для скачивания не найдены.")
        return images_data
    photo_messages.sort(key=lambda x: x.get('timestamp', ''))
    logger.info(f"[Chat {chat_id}] Найдено {len(photo_messages)} фото. Попытка скачать до {max_photos} шт.")
    download_count = 0
    for msg in photo_messages:
        if download_count >= max_photos:
            logger.info(f"[Chat {chat_id}] Достигнут лимит ({max_photos}) скачиваемых фото.")
            break
        file_id = msg['file_id']
        file_unique_id = msg['file_unique_id']
        if file_unique_id in images_data: continue
        try:
            logger.debug(f"[Chat {chat_id}] Скачивание фото file_id={file_id} (unique: {file_unique_id})...")
            if context.bot is None: # Добавлена проверка
                logger.error(f"[Chat {chat_id}] Объект бота недоступен для скачивания файла {file_id}.")
                continue
            file = await context.bot.get_file(file_id)
            image_bytearray = await file.download_as_bytearray()
            images_data[file_unique_id] = bytes(image_bytearray)
            download_count += 1
            logger.debug(f"[Chat {chat_id}] Фото {file_unique_id} ({len(image_bytearray)} байт) успешно скачано.")
            await asyncio.sleep(0.3)
        except (TelegramError, NetworkError, TimeoutError) as e:
            logger.error(f"[Chat {chat_id}] Ошибка скачивания фото file_id={file_id}: {e.__class__.__name__}: {e}")
        except Exception as e:
             logger.error(f"[Chat {chat_id}] Неожиданная ошибка при скачивании фото file_id={file_id}: {e}", exc_info=True)
    logger.info(f"[Chat {chat_id}] Успешно скачано {download_count} фото для анализа.")
    return images_data

async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.bot is None:
        logger.error("Объект бота не найден в контексте jobs.daily_story_job.")
        return
    bot_username = "UnknownBot"
    try:
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username or "UnknownBot"
    except Exception as e:
        logger.error(f"Не удалось получить информацию о боте: {e}")
    logger.info(f"[{bot_username}] Запуск ЕЖЕДНЕВНОЙ ЗАДАЧИ генерации историй (с Vision, SQLite)...")
    all_chat_ids = dm.get_all_chat_ids()
    if not all_chat_ids:
        logger.info(f"[{bot_username}] Нет чатов с сообщениями в БД для обработки.")
        return
    total_chats = len(all_chat_ids)
    processed_chats_count = 0
    logger.info(f"[{bot_username}] Обнаружено {total_chats} чатов для обработки.")
    for i, chat_id in enumerate(all_chat_ids):
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id} ({i+1}/{total_chats})]"
        logger.info(f"{current_chat_log_prefix} Начало обработки...")
        story_sent_successfully = False # Флаг для очистки
        try:
            messages_to_process = dm.get_messages_for_chat(chat_id)
            if not messages_to_process:
                logger.warning(f"{current_chat_log_prefix} Нет сообщений в БД. Очищаем (если есть что).")
                dm.clear_messages_for_chat(chat_id)
                await asyncio.sleep(0.5)
                continue
            logger.info(f"{current_chat_log_prefix} Собрано {len(messages_to_process)} сообщений из БД.")
            downloaded_images = await download_images(context, messages_to_process, chat_id, MAX_PHOTOS_TO_ANALYZE)
            gemini_input_content = gc.prepare_story_parts(messages_to_process, downloaded_images)
            if not gemini_input_content:
                 logger.warning(f"{current_chat_log_prefix} Не удалось подготовить контент для Gemini. Пропускаем чат.")
                 await asyncio.sleep(1)
                 continue
            story, note_or_error = await gc.generate_story_from_proxy(gemini_input_content)
            if story:
                try:
                    MAX_MESSAGE_LENGTH = 4000
                    photo_note = f" (с анализом до {MAX_PHOTOS_TO_ANALYZE} фото)" if downloaded_images else ""
                    final_text_header = f"📝 История дня{photo_note}:\n\n"
                    full_message_text = final_text_header + story
                    if len(full_message_text) > MAX_MESSAGE_LENGTH:
                        logger.warning(f"{current_chat_log_prefix} История слишком длинная, разбиваем.")
                        await context.bot.send_message(chat_id=chat_id, text=f"📝 История дня{photo_note} получилась объемной, вот она:")
                        await asyncio.sleep(0.5)
                        parts = [story[j:j+MAX_MESSAGE_LENGTH] for j in range(0, len(story), MAX_MESSAGE_LENGTH)]
                        for k, part in enumerate(parts):
                            await context.bot.send_message(chat_id=chat_id, text=part)
                            await asyncio.sleep(0.5)
                    else:
                         await context.bot.send_message(chat_id=chat_id, text=full_message_text)
                    logger.info(f"{current_chat_log_prefix} История успешно отправлена.")
                    story_sent_successfully = True
                    if note_or_error:
                        try: await context.bot.send_message(chat_id=chat_id, text=f"ℹ️ Примечание: {note_or_error}")
                        except TelegramError as e_warn: logger.warning(f"{current_chat_log_prefix} Не удалось отправить примечание: {e_warn}")
                except TelegramError as e:
                    logger.error(f"{current_chat_log_prefix} Ошибка Telegram при отправке истории: {e.__class__.__name__}: {e}")
                    error_str = str(e).lower()
                    if "bot was blocked" in error_str or "user is deactivated" in error_str or \
                       "chat not found" in error_str or "bot was kicked" in error_str or \
                       "chat_write_forbidden" in error_str:
                         logger.warning(f"{current_chat_log_prefix} Неустранимая ошибка отправки. Очищаем данные.")
                         dm.clear_messages_for_chat(chat_id)
                except Exception as e:
                     logger.error(f"{current_chat_log_prefix} Неожиданная ошибка при отправке истории: {e}", exc_info=True)
            else:
                logger.warning(f"{current_chat_log_prefix} Не удалось сгенерировать историю. Причина: {note_or_error}")
                if note_or_error and "ошибка" not in note_or_error.lower():
                     try:
                         await context.bot.send_chat_action(chat_id=chat_id, action='typing')
                         await asyncio.sleep(0.1)
                         await context.bot.send_message(chat_id=chat_id, text=f"😕 Не удалось создать историю дня.\nПричина: {note_or_error}")
                     except TelegramError as e_err:
                         logger.warning(f"{current_chat_log_prefix} Не удалось отправить сообщение об ошибке генерации: {e_err}")
                         error_str = str(e_err).lower()
                         if "bot was blocked" in error_str or "user is deactivated" in error_str or \
                            "chat not found" in error_str or "bot was kicked" in error_str or \
                            "chat_write_forbidden" in error_str:
                               logger.warning(f"{current_chat_log_prefix} Очищаем данные из-за ошибки Telegram при отправке уведомления.")
                               dm.clear_messages_for_chat(chat_id)
        except Exception as e:
             # Ловим ошибки на уровне обработки всего чата (например, при получении сообщений)
             logger.error(f"{current_chat_log_prefix} КРИТИЧЕСКАЯ ОШИБКА при обработке чата: {e}", exc_info=True)
             # В этом случае не очищаем данные, если они не были очищены ранее

        # Очистка данных
        if story_sent_successfully:
            dm.clear_messages_for_chat(chat_id)
            processed_chats_count += 1
        else:
            current_messages_after_attempt = dm.get_messages_for_chat(chat_id)
            if current_messages_after_attempt:
                 logger.warning(f"{current_chat_log_prefix} Данные НЕ будут очищены из-за ошибки.")

        logger.debug(f"{current_chat_log_prefix} Завершение обработки. Пауза...")
        await asyncio.sleep(5)
    logger.info(f"[{bot_username}] Ежедневная задача завершена. Успешно обработано и очищено чатов: {processed_chats_count}/{total_chats}")