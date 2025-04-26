# utils.py
import logging
import asyncio
import traceback
from typing import Dict, List, Any, Optional

# --- ИСПРАВЛЕНИЕ: Добавляем импорт Bot ---
from telegram import Bot
# -----------------------------------------

from telegram.ext import ContextTypes
from telegram.error import TelegramError, NetworkError
from telegram.constants import ParseMode
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log
from config import BOT_OWNER_ID

logger = logging.getLogger(__name__)
retry_log = logging.getLogger(__name__ + '.retry') # Отдельный логгер для retries

# --- Константа ---
MAX_PHOTOS_TO_ANALYZE = 5

# --- Улучшенная функция уведомления владельца ---
async def notify_owner(
    context: Optional[ContextTypes.DEFAULT_TYPE] = None,
    bot: Optional[Bot] = None, # Теперь Bot импортирован и известен
    message: str = "Произошло событие",
    chat_id: Optional[int] = None,
    user_id: Optional[int] = None,
    operation: Optional[str] = None,
    exception: Optional[BaseException] = None,
    important: bool = False
):
    """Отправляет форматированное сообщение владельцу бота."""
    if not BOT_OWNER_ID or BOT_OWNER_ID == 0:
        if important:
            logger.warning("BOT_OWNER_ID не настроен. Важное уведомление не отправлено: %s", message)
        return

    target_bot = bot
    if not target_bot and context and context.bot:
        target_bot = context.bot
    if not target_bot:
         logger.error("Невозможно отправить уведомление владельцу: объект Bot не найден.")
         return

    try:
        full_message = f"🔔 <b>Уведомление Летописца</b> 🔔\n\n"
        if operation: full_message += f"<b>Операция:</b> {operation}\n"
        if chat_id: full_message += f"<b>Чат:</b> <code>{chat_id}</code>\n"
        if user_id: full_message += f"<b>Пользователь:</b> <code>{user_id}</code>\n"
        full_message += f"\n{message}\n"

        if exception:
            full_message += f"\n<b>Тип ошибки:</b> {exception.__class__.__name__}\n"
            try:
                tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__, limit=5)
                tb_str = "".join(tb_lines).replace('\n\n', '\n').strip()
                # Экранируем HTML символы в traceback
                escaped_tb_str = tb_str.replace('&', '&').replace('<', '<').replace('>', '>')
                full_message += f"\n<pre><code class=\"language-python\">{escaped_tb_str[:1000]}{'...' if len(escaped_tb_str)>1000 else ''}</code></pre>"
            except Exception as format_e:
                logger.error(f"Ошибка форматирования traceback для notify_owner: {format_e}")
                full_message += f"\n<i>(Не удалось отформатировать traceback)</i>"

        max_len = 4096
        if len(full_message) > max_len:
            cutoff_msg = "\n\n[...] (сообщение было обрезано)"
            full_message = full_message[:max_len - len(cutoff_msg)] + cutoff_msg

        await target_bot.send_message(
            chat_id=BOT_OWNER_ID,
            text=full_message,
            parse_mode=ParseMode.HTML
        )
        logger.info(f"Уведомление отправлено владельцу (ID: {BOT_OWNER_ID}). Операция: {operation or 'N/A'}")

    except TelegramError as e:
        logger.error(f"Не удалось отправить уведомление владельцу (ID: {BOT_OWNER_ID}): {e}")
    except Exception as e:
        logger.exception(f"Неожиданная ошибка при отправке уведомления владельцу:")


# --- НОВАЯ: Вспомогательная функция для проверки прав администратора ---
async def is_user_admin(
    chat_id: int, user_id: int, context: Optional[ContextTypes.DEFAULT_TYPE] = None, bot: Optional[Bot] = None
) -> bool:
    """Проверяет, является ли пользователь администратором или создателем чата."""
    if chat_id > 0: # В личных чатах пользователь всегда "админ"
        return True

    # Получаем объект бота
    target_bot = bot
    if not target_bot and context and context.bot:
        target_bot = context.bot
    if not target_bot:
        logger.error(f"Bot object unavailable for admin check user {user_id} in chat {chat_id}")
        # В случае отсутствия бота, безопаснее считать, что пользователь не админ
        return False

    try:
        chat_member = await target_bot.get_chat_member(chat_id, user_id)
        is_admin_status = chat_member.status in [
            chat_member.status.ADMINISTRATOR,
            chat_member.status.OWNER
        ]
        logger.debug(f"Admin check for user {user_id} in chat {chat_id}: status={chat_member.status}, is_admin={is_admin_status}")
        return is_admin_status
    except TelegramError as e:
        # Логируем частые ошибки доступа чуть тише
        error_msg = str(e).lower()
        if "chat not found" in error_msg or "user not found" in error_msg or "peer_id_invalid" in error_msg:
            logger.warning(f"Could not get member status user {user_id} in chat {chat_id}: {e}")
        elif "not enough rights" in error_msg:
             logger.warning(f"Bot doesn't have enough rights to get member status in chat {chat_id}: {e}")
        else:
            logger.error(f"Telegram error checking admin user {user_id} in chat {chat_id}: {e}")
        # Если не можем проверить, считаем, что не админ
        return False
    except Exception as e:
        logger.exception(f"Unexpected error checking admin user {user_id} in chat {chat_id}:")
        return False

# --- Функции скачивания изображений (download_single_image, download_images) ---
#     Остаются без изменений в логике, но используют импортированный Bot
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(3),
    retry=retry_if_exception_type((TelegramError, NetworkError, TimeoutError)),
    before_sleep=before_sleep_log(retry_log, logging.WARNING)
)
async def download_single_image(
    context: ContextTypes.DEFAULT_TYPE, file_id: str, chat_id_for_log: int
) -> Optional[bytes]:
    """Скачивает одно изображение с повторными попытками."""
    log_prefix = f"[Chat {chat_id_for_log}]"
    # --- ИЗМЕНЕНО: Используем context.bot ---
    if not context.bot:
        logger.error(f"{log_prefix} Bot object unavailable for download {file_id}")
        return None
    # --------------------------------------
    try:
        logger.debug(f"{log_prefix} Attempting download file_id={file_id}...")
        file = await context.bot.get_file(file_id)
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
    max_photos: int = MAX_PHOTOS_TO_ANALYZE # Используем значение по умолчанию
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
            # Передаем context в функцию скачивания
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