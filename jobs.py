# jobs.py
import logging
import asyncio
import datetime
import pytz # Для работы с UTC временем
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
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
    """
    Запускается периодически. Проверяет чаты, для которых подошло время генерации,
    и генерирует историю, если есть необработанные сообщения.
    """
    global last_job_run_time, last_job_error # Используем глобальные переменные
    job_start_time = datetime.datetime.now(pytz.utc) # Работаем с UTC
    last_job_run_time = job_start_time
    last_job_error = None # Сбрасываем ошибку при каждом запуске проверки
    current_errors = [] # Собираем ошибки этого запуска

    bot = context.bot; 
    if bot is None: logger.error("Объект бота недоступен!"); return
    bot_username = "UnknownBot"; 
    try: bot_username = (await bot.get_me()).username or "UnknownBot"
    except Exception as e: logger.error(f"Не удалось получить имя бота: {e}")

    logger.info(f"[{bot_username}] Запуск ПРОВЕРКИ чатов для генерации историй...")

    enabled_chat_ids = dm.get_enabled_chats()
    if not enabled_chat_ids: logger.info(f"[{bot_username}] Нет активных чатов."); return

    # Получаем текущее время UTC для сравнения
    now_utc = datetime.datetime.now(pytz.utc)
    current_hour_utc = now_utc.hour
    current_minute_utc = now_utc.minute
    # Округляем текущую минуту до ближайшего интервала проверки для сравнения
    # Например, если интервал 5 минут, то 10:03 станет 10:00, 10:06 станет 10:05
    current_minute_rounded = (current_minute_utc // JOB_CHECK_INTERVAL_MINUTES) * JOB_CHECK_INTERVAL_MINUTES

    logger.debug(f"Текущее время UTC: {now_utc.strftime('%H:%M')}, Округленное время для проверки: {current_hour_utc:02d}:{current_minute_rounded:02d}")

    processed_in_this_run = 0

    for chat_id in enabled_chat_ids:
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id}]"
        should_process = False
        target_hour = SCHEDULE_HOUR
        target_minute = SCHEDULE_MINUTE

        try:
            settings = dm.get_chat_settings(chat_id)
            custom_time_str = settings.get('custom_schedule_time')

            if custom_time_str: # Если установлено пользовательское время
                try:
                    parts = custom_time_str.split(':')
                    target_hour = int(parts[0])
                    target_minute = int(parts[1])
                except (ValueError, IndexError):
                    logger.warning(f"{current_chat_log_prefix} Неверный формат custom_schedule_time '{custom_time_str}', используем время по умолчанию.")
                    # Оставляем target_hour/minute по умолчанию
            # else: Используем время по умолчанию, уже присвоенное выше

            # Сравниваем ЧАС и ОКРУГЛЕННУЮ МИНУТУ
            if current_hour_utc == target_hour and current_minute_rounded == target_minute:
                logger.debug(f"{current_chat_log_prefix} Время {target_hour:02d}:{target_minute:02d} совпало с текущим округленным {current_hour_utc:02d}:{current_minute_rounded:02d}. Проверяем сообщения...")
                # Проверяем, есть ли сообщения для обработки (это наш флаг "не обработано сегодня")
                messages_exist = dm.get_messages_for_chat(chat_id) # Только проверяем наличие
                if messages_exist:
                    logger.info(f"{current_chat_log_prefix} Время совпало и есть сообщения ({len(messages_exist)} шт.). Начинаем генерацию!")
                    should_process = True
                    messages_to_process = messages_exist # Используем уже полученные сообщения
                else:
                     logger.debug(f"{current_chat_log_prefix} Время совпало, но нет новых сообщений для обработки.")
            # else: Время не совпало, пропускаем этот чат в этом запуске

            if should_process:
                processed_in_this_run += 1
                story_sent = False # Флаг для очистки
                chat_lang = settings.get('lang', DEFAULT_LANGUAGE)
                # --- Логика генерации и отправки (почти без изменений) ---
                logger.info(f"{current_chat_log_prefix} Собрано {len(messages_to_process)} сообщ.")
                downloaded_images = await download_images(context, messages_to_process, chat_id, MAX_PHOTOS_TO_ANALYZE)
                prepared_content = gc.prepare_story_parts(messages_to_process, downloaded_images)
                if not prepared_content: logger.warning(f"{current_chat_log_prefix} Не удалось подготовить контент."); current_errors.append(f"Chat {chat_id}: Ошибка подготовки"); continue # К следующему чату

                story, error_msg = await gc.safe_generate_story(prepared_content)
                if story:
                    # ... (Код отправки истории с кнопками - БЕЗ ИЗМЕНЕНИЙ) ...
                    story_sent = True # Устанавливаем флаг только ПОСЛЕ успешной отправки
                else: # Ошибка генерации
                    logger.warning(f"{current_chat_log_prefix} Не удалось сгенерировать историю. Причина: {error_msg}"); current_errors.append(f"Chat {chat_id}: Generation Error ({error_msg or 'Unknown'})")
                    try: await bot.send_message(chat_id=chat_id, text=get_text("daily_job_failed_chat", chat_lang, error=error_msg or 'Неизвестно'))
                    except Exception as e_err: # Обработка ошибки отправки уведомления
                        logger.warning(f"{current_chat_log_prefix} Не удалось отправить сообщение об ошибке генерации: {e_err}")
                        # ... (Проверка на фатальные ошибки Telegram и очистка при необходимости) ...

                # --- Очистка данных ТОЛЬКО при УСПЕШНОЙ отправке ---
                if story_sent:
                    dm.clear_messages_for_chat(chat_id)
                else:
                    logger.warning(f"{current_chat_log_prefix} Данные НЕ очищены из-за ошибки генерации или отправки.")

                # Небольшая пауза после обработки чата, чтобы не перегружать API
                await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"{current_chat_log_prefix} КРИТИЧЕСКАЯ ОШИБКА при проверке/обработке чата: {e}", exc_info=True)
            current_errors.append(f"Chat {chat_id}: Critical Error ({e.__class__.__name__})")
            await asyncio.sleep(1) # Пауза при критической ошибке

    # --- Завершение периодической задачи ---
    last_job_error = "\n".join(current_errors) if current_errors else None # Обновляем глобальную ошибку
    logger.info(f"[{bot_username}] Проверка чатов завершена. Обработано в этом запуске: {processed_in_this_run}. Обнаружено ошибок: {len(current_errors)}.")
    if last_job_error:
        await notify_owner(context, f"Обнаружены ошибки при проверке/генерации историй:\n{last_job_error}")