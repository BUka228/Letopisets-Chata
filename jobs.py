# jobs.py
import logging
import asyncio
from telegram.ext import ContextTypes
from telegram.error import TelegramError, NetworkError
from typing import Dict, List, Any

# Импортируем модули, работающие с данными и API
import data_manager as dm # Теперь работает с SQLite
import gemini_client as gc # Теперь поддерживает мультимодальность

logger = logging.getLogger(__name__)

# Максимальное количество фото для анализа за один раз
MAX_PHOTOS_TO_ANALYZE = 5 # Настраиваемый параметр

async def download_images(
    context: ContextTypes.DEFAULT_TYPE,
    messages: List[Dict[str, Any]], # Список словарей сообщений из БД
    chat_id: int,
    max_photos: int
) -> Dict[str, bytes]:
    """
    Скачивает изображения из сообщений типа 'photo' по file_id.
    Возвращает словарь {file_unique_id: image_bytes}.
    Ограничивает количество скачиваемых фото.
    """
    images_data: Dict[str, bytes] = {}
    # Отбираем сообщения типа 'photo' с необходимыми ID
    photo_messages = [
        m for m in messages
        if m.get('type') == 'photo' and m.get('file_id') and m.get('file_unique_id')
    ]

    if not photo_messages:
        logger.debug(f"[Chat {chat_id}] Фото для скачивания не найдены.")
        return images_data

    # Сортируем по времени, чтобы взять первые фото дня
    # Используем безопасное получение timestamp
    photo_messages.sort(key=lambda x: x.get('timestamp', ''))

    logger.info(f"[Chat {chat_id}] Найдено {len(photo_messages)} фото. Попытка скачать до {max_photos} шт.")
    download_count = 0

    for msg in photo_messages:
        if download_count >= max_photos:
            logger.info(f"[Chat {chat_id}] Достигнут лимит ({max_photos}) скачиваемых фото для анализа.")
            break

        file_id = msg['file_id']
        file_unique_id = msg['file_unique_id']

        # Пропускаем, если уже скачали (на случай дубликатов в исходных данных)
        if file_unique_id in images_data:
            continue

        try:
            logger.debug(f"[Chat {chat_id}] Скачивание фото file_id={file_id} (unique: {file_unique_id})...")
            file = await context.bot.get_file(file_id)
            # Используем асинхронное скачивание в память
            image_bytearray = await file.download_as_bytearray()
            # Ограничим размер скачиваемого файла, если нужно (например, 20MB - лимит Gemini API)
            # MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
            # if len(image_bytearray) > MAX_FILE_SIZE_BYTES:
            #     logger.warning(f"[Chat {chat_id}] Фото {file_unique_id} слишком большое ({len(image_bytearray)} байт), пропускаем.")
            #     continue

            images_data[file_unique_id] = bytes(image_bytearray) # Преобразуем в неизменяемые байты
            download_count += 1
            logger.debug(f"[Chat {chat_id}] Фото {file_unique_id} ({len(image_bytearray)} байт) успешно скачано.")
            # Небольшая пауза между скачиваниями, чтобы не перегружать Telegram API
            await asyncio.sleep(0.3) # Немного увеличим паузу
        except (TelegramError, NetworkError, TimeoutError) as e:
            # Логируем ожидаемые ошибки сети/Telegram
            logger.error(f"[Chat {chat_id}] Ошибка скачивания фото file_id={file_id}: {e.__class__.__name__}: {e}")
        except Exception as e:
             # Логируем любые другие неожиданные ошибки
             logger.error(f"[Chat {chat_id}] Неожиданная ошибка при скачивании фото file_id={file_id}: {e}", exc_info=True)

    logger.info(f"[Chat {chat_id}] Успешно скачано {download_count} фото для анализа.")
    return images_data


async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Выполняет ежедневную задачу: генерация и отправка историй с анализом изображений,
    используя данные из SQLite.
    """
    if context.bot is None:
        logger.error("Объект бота не найден в контексте jobs.daily_story_job. Завершение задачи.")
        return

    bot_username = "UnknownBot"
    try:
        # Получаем имя бота асинхронно
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username or "UnknownBot"
    except Exception as e:
        logger.error(f"Не удалось получить информацию о боте: {e}")

    logger.info(f"[{bot_username}] Запуск ЕЖЕДНЕВНОЙ ЗАДАЧИ генерации историй (с Vision, SQLite)...")

    # Получаем список ID чатов из БД
    all_chat_ids = dm.get_all_chat_ids()
    if not all_chat_ids:
        logger.info(f"[{bot_username}] Нет чатов с сообщениями в БД для обработки. Задача завершена.")
        return

    total_chats = len(all_chat_ids)
    processed_chats_count = 0
    logger.info(f"[{bot_username}] Обнаружено {total_chats} чатов для обработки.")

    # Обрабатываем чаты последовательно
    for i, chat_id in enumerate(all_chat_ids):
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id} ({i+1}/{total_chats})]"
        logger.info(f"{current_chat_log_prefix} Начало обработки...")

        # 1. Получение сообщений из БД
        messages_to_process = dm.get_messages_for_chat(chat_id)

        if not messages_to_process:
            # Этого не должно происходить, если get_all_chat_ids работает верно,
            # но на всякий случай проверяем и очищаем, если чат пуст
            logger.warning(f"{current_chat_log_prefix} Нет сообщений в БД, хотя ID был в списке. Очищаем (если есть что).")
            dm.clear_messages_for_chat(chat_id)
            await asyncio.sleep(0.5) # Небольшая пауза перед следующим чатом
            continue

        logger.info(f"{current_chat_log_prefix} Собрано {len(messages_to_process)} сообщений из БД.")

        # 2. Скачивание изображений
        downloaded_images = await download_images(context, messages_to_process, chat_id, MAX_PHOTOS_TO_ANALYZE)

        # 3. Подготовка мультимодального контента для Gemini
        gemini_input_content = gc.prepare_story_parts(messages_to_process, downloaded_images)
        if not gemini_input_content:
             logger.warning(f"{current_chat_log_prefix} Не удалось подготовить контент для Gemini (возможно, нет валидных сообщений). Пропускаем чат.")
             # Очищать ли данные в этом случае? Скорее нет, т.к. это ошибка обработки, а не генерации.
             await asyncio.sleep(1)
             continue

        # 4. Генерация истории через Gemini API
        story, note_or_error = await gc.generate_story_from_gemini(gemini_input_content)

        # 5. Отправка результата в чат
        story_sent_successfully = False
        if story:
            try:
                MAX_MESSAGE_LENGTH = 4000
                photo_note = f" (с анализом до {MAX_PHOTOS_TO_ANALYZE} фото)" if downloaded_images else ""
                final_text_header = f"📝 История дня{photo_note}:\n\n"
                full_message_text = final_text_header + story

                if len(full_message_text) > MAX_MESSAGE_LENGTH:
                    logger.warning(f"{current_chat_log_prefix} История слишком длинная ({len(full_message_text)}), разбиваем.")
                    # Отправляем заголовок отдельно
                    await context.bot.send_message(chat_id=chat_id, text=f"📝 История дня{photo_note} получилась объемной, вот она:")
                    await asyncio.sleep(0.5)
                    # Разбиваем *оригинальную* историю без заголовка
                    parts = [story[j:j+MAX_MESSAGE_LENGTH] for j in range(0, len(story), MAX_MESSAGE_LENGTH)]
                    for k, part in enumerate(parts):
                        await context.bot.send_message(chat_id=chat_id, text=part)
                        await asyncio.sleep(0.5) # Пауза между частями
                else:
                     # Отправляем одним сообщением
                     await context.bot.send_message(chat_id=chat_id, text=full_message_text)

                logger.info(f"{current_chat_log_prefix} История успешно отправлена.")
                story_sent_successfully = True # Ставим флаг успеха

                # Если было примечание (note) от Gemini, отправляем его тоже
                if note_or_error:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=f"ℹ️ Примечание от нейросети: {note_or_error}")
                        logger.info(f"{current_chat_log_prefix} Примечание к истории отправлено.")
                    except TelegramError as e_warn:
                         # Логируем, но не считаем основной отправкой неуспешной
                         logger.warning(f"{current_chat_log_prefix} Не удалось отправить примечание: {e_warn}")

            except TelegramError as e:
                # Обработка ошибок Telegram при отправке ОСНОВНОЙ истории
                logger.error(f"{current_chat_log_prefix} Ошибка Telegram при отправке истории: {e.__class__.__name__}: {e}")
                # Проверяем на невосстановимые ошибки, при которых нужно очистить данные
                error_str = str(e).lower()
                if "bot was blocked" in error_str or \
                   "user is deactivated" in error_str or \
                   "chat not found" in error_str or \
                   "bot was kicked" in error_str or \
                   "chat_write_forbidden" in error_str: # Добавлено: нет прав на запись
                     logger.warning(f"{current_chat_log_prefix} Неустранимая ошибка отправки ({e.__class__.__name__}). Очищаем данные этого чата.")
                     # Очищаем данные, т.к. дальнейшая отправка в этот чат невозможна
                     dm.clear_messages_for_chat(chat_id)
                # В остальных случаях (временные сетевые проблемы и т.д.)
                # story_sent_successfully остается False, данные не очищаются

            except Exception as e:
                 # Обработка других неожиданных ошибок при отправке
                 logger.error(f"{current_chat_log_prefix} Неожиданная ошибка при отправке истории: {e}", exc_info=True)
                 # story_sent_successfully остается False

        else:
            # Если история НЕ была сгенерирована (story is None)
            logger.warning(f"{current_chat_log_prefix} Не удалось сгенерировать историю. Причина от API: {note_or_error}")
            # Отправляем сообщение об ошибке генерации в чат, если это не системная проблема API
            # и если есть куда отправлять
            if note_or_error and "ошибка" not in note_or_error.lower(): # Не отправляем системные ошибки API
                 try:
                     # Убедимся, что можем писать в чат перед отправкой ошибки
                     await context.bot.send_chat_action(chat_id=chat_id, action='typing')
                     await asyncio.sleep(0.1) # Короткая пауза
                     await context.bot.send_message(chat_id=chat_id, text=f"😕 Сегодня не удалось создать историю дня.\nПричина: {note_or_error}")
                 except TelegramError as e_err:
                     # Если даже сообщение об ошибке отправить не удалось
                     logger.warning(f"{current_chat_log_prefix} Не удалось отправить сообщение об ошибке генерации в чат: {e_err}")
                     # Проверяем на невосстановимые ошибки и очищаем, если нужно
                     error_str = str(e_err).lower()
                     if "bot was blocked" in error_str or \
                        "user is deactivated" in error_str or \
                        "chat not found" in error_str or \
                        "bot was kicked" in error_str or \
                        "chat_write_forbidden" in error_str:
                           logger.warning(f"{current_chat_log_prefix} Очищаем данные из-за неустранимой ошибки Telegram при отправке уведомления.")
                           dm.clear_messages_for_chat(chat_id)

        # 6. Очистка данных из БД ТОЛЬКО если история была УСПЕШНО ОТПРАВЛЕНА
        if story_sent_successfully:
            dm.clear_messages_for_chat(chat_id) # Функция сама логирует успешную очистку
            processed_chats_count += 1
        else:
            # Если не отправлено успешно И данные еще не были очищены из-за ошибки Telegram
            current_messages_after_attempt = dm.get_messages_for_chat(chat_id)
            if current_messages_after_attempt: # Проверяем, остались ли еще сообщения
                 logger.warning(f"{current_chat_log_prefix} Данные НЕ будут очищены из-за ошибки генерации или отправки.")

        # Пауза между обработкой чатов
        logger.debug(f"{current_chat_log_prefix} Завершение обработки. Пауза перед следующим чатом...")
        await asyncio.sleep(5) # Пауза 5 секунд

    logger.info(f"[{bot_username}] Ежедневная задача завершена. Успешно обработано и очищено чатов: {processed_chats_count}/{total_chats}")

    # Вызов сохранения данных больше не нужен, т.к. SQLite записывает изменения сразу