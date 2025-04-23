# gemini_client.py
import logging
import datetime
import asyncio
import httpx
import base64
from typing import List, Dict, Union, Tuple, Optional, Any
# --- НОВОЕ: Импорт для Retries ---
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import CLOUDFLARE_WORKER_URL, CLOUDFLARE_AUTH_TOKEN

logger = logging.getLogger(__name__)

ContentPart = Union[str, Dict[str, Any]]
PreparedContent = List[Union[str, Dict[str, Union[str, bytes]]]]

# --- ИЗМЕНЕНО: Улучшенная функция подготовки контента ---
def prepare_story_parts(messages: List[Dict[str, Any]], images_data: Dict[str, bytes]) -> Optional[PreparedContent]:
    """
    Форматирует сообщения и изображения в контент для прокси.
    Улучшено описание разных типов сообщений.
    """
    if not messages: return None
    try:
        valid_messages = [m for m in messages if isinstance(m, dict) and 'timestamp' in m]
        valid_messages.sort(key=lambda x: datetime.datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))
    except Exception as e:
        logger.warning(f"Не удалось отсортировать сообщения по времени ({e}).", exc_info=True)
        valid_messages = messages

    content_parts: PreparedContent = []
    initial_prompt = (
        f"Ты — ИИ-летописец чата с возможностью анализа изображений. Твоя задача — проанализировать лог сообщений и изображения (обозначенные как [IMAGE N]) за прошедший день. "
        f"Напиши связную и интересную историю событий в чате в 1-3 абзацах. " # Конкретизируем длину
        f"Обязательно используй имена пользователей (username). Не цитируй текст дословно, а пересказывай суть. "
        f"Кратко опиши содержание 1-2 ключевых изображений, если они важны для понимания событий дня, и упомяни, кто их отправил. " # Уточняем про изображения
        f"Опиши общую атмосферу дня (например, активный, спокойный, веселый, напряженный). Не выдумывай события.\n\n"
        f"ЛОГ СООБЩЕНИЙ И ИЗОБРАЖЕНИЙ ЗА ДЕНЬ:\n"
        f"---------------------------------\n"
    )
    content_parts.append(initial_prompt)

    image_counter = 0
    current_text_block = ""

    for msg in valid_messages:
        try: dt_utc = datetime.datetime.fromisoformat(msg.get('timestamp','').replace('Z', '+00:00')); ts_str = dt_utc.strftime('%H:%M')
        except: ts_str = "??:??"
        user = msg.get('username', 'Неизвестный'); content = msg.get('content', ''); msg_type = msg.get('type'); msg_file_unique_id = msg.get('file_unique_id'); file_name = msg.get('file_name')

        log_entry = f"[{ts_str}] {user}: "

        if msg_type == 'photo' and msg_file_unique_id and msg_file_unique_id in images_data:
            if current_text_block: content_parts.append(current_text_block.strip()); current_text_block = ""
            image_counter += 1; image_bytes = images_data[msg_file_unique_id]
            log_entry += f"отправил(а) изображение [IMAGE {image_counter}]{f' с подписью: «{content}»' if content else ''}\n"
            content_parts.append(log_entry.strip())
            content_parts.append({"mime_type": "image/jpeg", "data": image_bytes})
        else:
            # Улучшаем описание других типов
            if msg_type == 'text' and content: log_entry += f"написал(а): \"{content[:150]}{'...' if len(content)>150 else ''}\"\n"
            elif msg_type == 'video': log_entry += f"отправил(а) видео{f' «{content}»' if content else ''} (содержание неизвестно)\n"
            elif msg_type == 'sticker': log_entry += f"отправил(а) стикер{f' ({content})' if content else ''}\n"
            elif msg_type == 'voice': log_entry += f"записал(а) голосовое сообщение\n"
            elif msg_type == 'video_note': log_entry += f"записал(а) видео-сообщение (кружок)\n"
            elif msg_type == 'document': log_entry += f"отправил(а) документ '{file_name or 'без имени'}'{f' «{content}»' if content else ''}\n"
            elif msg_type == 'audio': log_entry += f"отправил(а) аудио '{file_name or 'без имени'}'{f' «{content}»' if content else ''}\n"
            elif msg_type == 'photo': log_entry += f"отправил(а) фото (не анализируется){f' «{content}»' if content else ''}\n"
            elif content: log_entry += f"отправил(а) медиа с подписью: «{content}» (тип: {msg_type})\n"
            else: log_entry += f"отправил(а) сообщение (тип: {msg_type})\n" # Добавили тип для неопознанных
            current_text_block += log_entry

    if current_text_block: content_parts.append(current_text_block.strip())
    content_parts.append(f"---------------------------------\nКОНЕЦ ЛОГА.\n\nТеперь, напиши историю дня:\n")
    if len(content_parts) <= 2: logger.warning("Промпт не содержит данных."); return None
    return content_parts

# --- НОВОЕ: Функция для проверки, нужно ли повторять запрос ---
def _is_retryable_exception(exception: BaseException) -> bool:
    """Определяет, стоит ли повторять запрос при данной ошибке httpx."""
    # Повторяем при сетевых ошибках и ошибках сервера (5xx)
    return isinstance(exception, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.ReadTimeout)) or \
           (isinstance(exception, httpx.HTTPStatusError) and 500 <= exception.response.status_code < 600)

# --- ИЗМЕНЕНО: Добавлен декоратор @retry ---
@retry(
    stop=stop_after_attempt(3),  # Максимум 3 попытки (1 оригинальная + 2 повторные)
    wait=wait_exponential(multiplier=1, min=2, max=10), # Ждать 2с, 4с перед повторами
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)), # Условие повтора
    before_sleep=lambda retry_state: logger.warning(f"Ошибка вызова прокси, повторная попытка #{retry_state.attempt_number} через {retry_state.idle_for:.1f}с...")
)
async def generate_story_from_proxy(prepared_content: Optional[PreparedContent]) -> Tuple[Optional[str], Optional[str]]:
    """Отправляет подготовленный контент в прокси Cloudflare Worker с повторными попытками."""
    if not CLOUDFLARE_WORKER_URL or not CLOUDFLARE_AUTH_TOKEN:
        logger.error("URL/токен прокси не настроены.")
        return None, "Ошибка конфигурации: не задан URL прокси или токен."
    if not prepared_content:
        logger.info("Нет контента для отправки в прокси."); return "Сегодня было тихо.", None # Изменен ответ

    json_payload_content = []
    try:
        for part in prepared_content:
            if isinstance(part, str): json_payload_content.append(part)
            elif isinstance(part, dict) and 'data' in part and isinstance(part['data'], bytes):
                base64_encoded_data = base64.b64encode(part['data']).decode('utf-8')
                json_payload_content.append({"mime_type": part.get('mime_type', 'image/jpeg'), "data_base64": base64_encoded_data})
            else: logger.warning(f"Пропуск некорректной части контента: {type(part)}")
        if not json_payload_content: raise ValueError("Нет валидных частей контента")
    except Exception as e: logger.error(f"Ошибка кодирования base64: {e}", exc_info=True); return None, f"Ошибка подготовки данных: {e}"

    payload = {"content": json_payload_content}
    headers = {"Content-Type": "application/json", "X-Auth-Token": CLOUDFLARE_AUTH_TOKEN}
    proxy_url = f"{CLOUDFLARE_WORKER_URL.rstrip('/')}/generate" # Убедимся, что URL корректный

    logger.info(f"Отправка запроса к прокси: {proxy_url}")
    # httpx сам выбросит исключение при ошибке, которое поймает @retry или внешний try/except
    async with httpx.AsyncClient(timeout=90.0) as client: # Увеличим таймаут еще больше
        response = await client.post(proxy_url, json=payload, headers=headers)
        # Проверка статуса ПОСЛЕ попыток retry
        response.raise_for_status()
        proxy_response_data = response.json()

        if "response" in proxy_response_data:
            logger.info("Успешный ответ получен от прокси.")
            return proxy_response_data["response"], None # Нет примечаний от прокси пока
        elif "error" in proxy_response_data:
            logger.error(f"Прокси вернул ошибку: {proxy_response_data['error']}")
            return None, f"Ошибка от прокси: {proxy_response_data['error']}"
        else:
            logger.error(f"Неожиданный формат ответа от прокси: {proxy_response_data}")
            return None, "Неверный формат ответа от прокси."

# --- Обертка для обработки ошибок Retry ---
async def safe_generate_story(prepared_content: Optional[PreparedContent]) -> Tuple[Optional[str], Optional[str]]:
     """Безопасно вызывает generate_story_from_proxy и обрабатывает ошибки tenacity."""
     try:
         return await generate_story_from_proxy(prepared_content)
     except Exception as e: # Ловим ошибки tenacity и другие непредвиденные
          logger.error(f"Не удалось сгенерировать историю после нескольких попыток: {e}", exc_info=True)
          error_msg = f"Ошибка связи с сервисом генерации ({e.__class__.__name__})."
          # Можно добавить более специфичные сообщения для разных исключений
          if isinstance(e, httpx.HTTPStatusError):
               error_msg = f"Ошибка сервиса генерации ({e.response.status_code})."
          elif isinstance(e, httpx.RequestError):
               error_msg = f"Ошибка сети при связи с сервисом генерации."
          return None, error_msg