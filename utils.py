# utils.py
import logging
import asyncio
from typing import Dict, List, Any, Optional
from telegram.ext import ContextTypes
from telegram.error import TelegramError, NetworkError
# Импорт для Retries из tenacity (если вы его используете)
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log
from config import BOT_OWNER_ID

logger = logging.getLogger(__name__)
retry_log = logging.getLogger(__name__ + '.retry') # Отдельный логгер для retries

# --- Константа ---
MAX_PHOTOS_TO_ANALYZE = 5

async def notify_owner(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Отправляет сообщение владельцу бота."""
    if not context.bot:
        logger.error("Невозможно отправить уведомление владельцу: объект бота отсутствует.")
        return
    # Проверяем, что BOT_OWNER_ID установлен и не равен 0
    if BOT_OWNER_ID and BOT_OWNER_ID != 0:
        try:
            max_len = 4000
            truncated_message = message[:max_len] + '...' if len(message) > max_len else message
            await context.bot.send_message(
                chat_id=BOT_OWNER_ID,
                text=f"🚨 Уведомление от Летописца:\n\n{truncated_message}"
            )
            logger.info(f"Уведомление отправлено владельцу (ID: {BOT_OWNER_ID})")
        except TelegramError as e:
            # Логируем специфичные ошибки Telegram
            logger.error(f"Не удалось отправить уведомление владельцу (ID: {BOT_OWNER_ID}): {e}")
        except Exception as e:
             # Логируем любые другие неожиданные ошибки
             logger.error(f"Неожиданная ошибка при отправке уведомления владельцу: {e}", exc_info=True)
    else:
        # Логируем предупреждение, только если сообщение действительно важное (содержит ошибки)
        if "ошибка" in message.lower() or "failed" in message.lower() or "error" in message.lower():
            logger.warning("BOT_OWNER_ID не настроен в .env, важное уведомление не отправлено.")

# --- Скачивание изображений с ретраями ---
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(3), # Ждать 3 секунды между попытками
    retry=retry_if_exception_type((TelegramError, NetworkError, TimeoutError)),
    before_sleep=before_sleep_log(retry_log, logging.WARNING)
)
async def download_single_image(
    context: ContextTypes.DEFAULT_TYPE, file_id: str, chat_id_for_log: int
) -> Optional[bytes]:
    """Скачивает одно изображение с повторными попытками."""
    log_prefix = f"[Chat {chat_id_for_log}]"
    if context.bot is None:
        logger.error(f"{log_prefix} Bot object unavailable for download {file_id}")
        return None
    try:
        logger.debug(f"{log_prefix} Attempting download file_id={file_id}...")
        file = await context.bot.get_file(file_id)
        # Ограничим время на скачивание одного файла
        image_bytearray = await asyncio.wait_for(file.download_as_bytearray(), timeout=30.0)

        MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024 # 20 MB
        if len(image_bytearray) > MAX_FILE_SIZE_BYTES:
            logger.warning(f"{log_prefix} Photo {file_id} too large ({len(image_bytearray)} bytes), skipping.")
            return None

        logger.debug(f"{log_prefix} Photo {file_id} ({len(image_bytearray)} bytes) downloaded.")
        return bytes(image_bytearray)
    except asyncio.TimeoutError:
        logger.error(f"{log_prefix} Timeout downloading file {file_id}.")
        return None
    except (TelegramError, NetworkError) as e:
        logger.error(f"{log_prefix} Telegram/Network error downloading file {file_id}: {e.__class__.__name__} - {e}")
        raise # Передаем исключение для механизма retry
    except Exception as e:
        logger.exception(f"{log_prefix} Unexpected error downloading file {file_id}: {e}")
        return None

async def download_images(
    context: ContextTypes.DEFAULT_TYPE,
    messages: List[Dict[str, Any]],
    chat_id: int,
    max_photos: int # Принимаем лимит как аргумент
) -> Dict[str, bytes]:
    """Скачивает изображения, используя download_single_image с retries."""
    images_data: Dict[str, bytes] = {}
    photo_messages = [
        m for m in messages
        if m.get('type') == 'photo' and m.get('file_id') and m.get('file_unique_id')
    ]
    if not photo_messages:
        return images_data

    photo_messages.sort(key=lambda x: x.get('timestamp', ''))
    logger.info(
        f"[Chat {chat_id}] Found {len(photo_messages)} photos. "
        f"Downloading up to {max_photos}..."
    )

    tasks = []
    unique_ids_to_download = []
    processed_unique_ids = set()

    for msg in photo_messages:
        if len(unique_ids_to_download) >= max_photos:
            break
        file_unique_id = msg.get('file_unique_id')
        file_id = msg.get('file_id')
        if file_unique_id and file_id and file_unique_id not in processed_unique_ids:
            # Передаем chat_id в функцию скачивания для логов
            tasks.append(asyncio.create_task(download_single_image(context, file_id, chat_id)))
            unique_ids_to_download.append(file_unique_id)
            processed_unique_ids.add(file_unique_id)

    if not tasks:
        return images_data

    logger.debug(f"[Chat {chat_id}] Waiting for {len(tasks)} download tasks...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful_downloads = 0
    for i, unique_id in enumerate(unique_ids_to_download):
        result = results[i]
        if isinstance(result, bytes):
            images_data[unique_id] = result
            successful_downloads += 1
            logger.debug(f"[Chat {chat_id}] Photo {unique_id} ({len(result)} bytes) processed.")
        elif isinstance(result, Exception):
            logger.error(
                f"[Chat {chat_id}] Final download error for photo unique_id={unique_id}: "
                f"{result.__class__.__name__}"
            )
        else:
            logger.warning(f"[Chat {chat_id}] Download for {unique_id} returned None (timeout or error).")

    logger.info(
        f"[Chat {chat_id}] Successfully downloaded {successful_downloads}/"
        f"{len(unique_ids_to_download)} requested photos."
    )
    return images_data