# gemini_client.py
import logging
import asyncio
import httpx
import base64
from typing import List, Dict, Union, Tuple, Optional, Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log # Добавлен before_sleep_log

# Импортируем функции из нового модуля
import prompt_builder as pb

from config import CLOUDFLARE_WORKER_URL, CLOUDFLARE_AUTH_TOKEN

logger = logging.getLogger(__name__)
retry_log = logging.getLogger(__name__ + '.retry') # Отдельный логгер для tenacity

# Типы из prompt_builder
ContentPart = pb.ContentPart
PreparedContent = pb.PreparedContent

# --- Функция prepare_story_parts УДАЛЕНА отсюда ---
#     Логика теперь в prompt_builder.build_story_content

# --- Функция для проверки, нужно ли повторять запрос ---
def _is_retryable_exception(exception: BaseException) -> bool:
    """Определяет, стоит ли повторять запрос при данной ошибке httpx."""
    # Повторяем при сетевых ошибках и ошибках сервера (5xx)
    if isinstance(exception, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError, httpx.WriteError)):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        # Повторяем при 5xx и 429 (слишком много запросов)
        return 500 <= exception.response.status_code < 600 or exception.response.status_code == 429
    return False

# --- Функция вызова прокси с ретраями ---
@retry(
    stop=stop_after_attempt(4),  # Максимум 4 попытки (1 оригинальная + 3 повторные)
    wait=wait_exponential(multiplier=1.5, min=2, max=15), # Ждать 2с, 5с, 9.5с ~
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)), # Ловим базовые типы httpx
    retry_error_callback=lambda retry_state: logger.error(
        f"Не удалось выполнить запрос к прокси после {retry_state.attempt_number} попыток. Последняя ошибка: {retry_state.outcome.exception()}"
    ),
    before_sleep=before_sleep_log(retry_log, logging.WARNING) # Используем стандартный логгер tenacity
)
async def _call_proxy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Внутренняя функция для вызова прокси с обработкой retry."""
    if not CLOUDFLARE_WORKER_URL or not CLOUDFLARE_AUTH_TOKEN:
        logger.critical("URL/токен прокси не настроены в конфигурации!")
        raise ValueError("Proxy URL or Auth Token is not configured.") # Выбрасываем ошибку, чтобы retry не продолжался

    headers = {"Content-Type": "application/json", "X-Auth-Token": CLOUDFLARE_AUTH_TOKEN}
    proxy_url = f"{CLOUDFLARE_WORKER_URL.rstrip('/')}/generate"

    logger.info(f"Отправка запроса к прокси: {proxy_url} (размер payload ~{len(str(payload)) // 1024} KB)")
    async with httpx.AsyncClient(timeout=120.0) as client: # Увеличенный таймаут
        response = await client.post(proxy_url, json=payload, headers=headers)
        # Проверяем на ошибки, которые могут требовать retry
        if not response.is_success and _is_retryable_exception(httpx.HTTPStatusError(request=response.request, response=response)):
             response.raise_for_status() # Выбросит исключение для retry

        # Если статус не ОК, но не требует retry (например, 400 Bad Request от прокси)
        if not response.is_success:
             logger.warning(f"Прокси вернул статус {response.status_code}. Тело ответа: {response.text[:500]}")
             # Пытаемся парсить JSON даже при ошибке, вдруг там есть поле 'error'
             try:
                 return response.json()
             except Exception:
                 # Если не JSON, возвращаем общую ошибку
                 return {"error": f"Proxy returned status {response.status_code} with non-JSON body"}

        # Если статус ОК (2xx)
        logger.info(f"Успешный ответ ({response.status_code}) получен от прокси.")
        return response.json()


async def generate_via_proxy(prepared_content: Optional[PreparedContent]) -> Tuple[Optional[str], Optional[str]]:
    """
    Отправляет подготовленный контент (текст + байты изображений) в прокси.
    Возвращает (сгенерированный_текст, сообщение_об_ошибке_или_примечание).
    """
    if not prepared_content:
        logger.info("Нет контента для отправки в прокси.")
        # Возвращаем нейтральный ответ, если контента не было изначально
        return "Нет данных для обработки.", None

    json_payload_content = []
    try:
        for part in prepared_content:
            if isinstance(part, str):
                json_payload_content.append(part)
            elif isinstance(part, dict) and 'data' in part and isinstance(part['data'], bytes):
                # Кодируем байты изображения в base64 для JSON
                base64_encoded_data = base64.b64encode(part['data']).decode('utf-8')
                json_payload_content.append({
                    "mime_type": part.get('mime_type', 'image/jpeg'),
                    "data_base64": base64_encoded_data
                })
            else:
                logger.warning(f"Пропуск некорректной части контента при подготовке JSON: {type(part)}")

        if not json_payload_content:
            raise ValueError("Нет валидных частей контента после форматирования для JSON.")

    except Exception as e:
        logger.error(f"Ошибка подготовки JSON payload: {e}", exc_info=True)
        return None, f"Внутренняя ошибка: не удалось подготовить данные ({e.__class__.__name__})."

    payload = {"content": json_payload_content}

    try:
        proxy_response_data = await _call_proxy(payload)

        if isinstance(proxy_response_data, dict):
            if "response" in proxy_response_data:
                # Успешный ответ от Gemini через прокси
                return proxy_response_data["response"], None
            elif "error" in proxy_response_data:
                # Прокси вернул ошибку (возможно, от Gemini или свою)
                error_message = proxy_response_data['error']
                logger.error(f"Прокси вернул ошибку: {error_message}")
                # Возвращаем ошибку как "примечание", чтобы она отобразилась пользователю
                return None, f"Ошибка генерации: {error_message}"
            else:
                # Неожиданный формат ответа от прокси
                logger.error(f"Неожиданный формат JSON ответа от прокси: {proxy_response_data}")
                return None, "Ошибка связи с сервисом: неверный формат ответа."
        else:
             # Если _call_proxy вернул что-то совсем не то (маловероятно)
             logger.error(f"Получен некорректный тип данных от _call_proxy: {type(proxy_response_data)}")
             return None, "Внутренняя ошибка: некорректный ответ от прокси."

    except Exception as e: # Ловим ошибки tenacity (RetryError) и другие непредвиденные
        logger.error(f"Не удалось вызвать прокси после нескольких попыток: {e}", exc_info=True)
        error_msg = f"Ошибка сети или сервиса ({e.__class__.__name__}). Попробуйте позже."
        # Можно добавить более специфичные сообщения для разных исключений
        if isinstance(e, httpx.TimeoutException):
             error_msg = "Сервис генерации не ответил вовремя. Попробуйте позже."
        elif isinstance(e, httpx.HTTPStatusError):
             error_msg = f"Сервис генерации вернул ошибку ({e.response.status_code})."
        elif isinstance(e, httpx.RequestError):
             error_msg = "Ошибка сети при подключении к сервису генерации."
        elif isinstance(e, ValueError) and "Proxy URL or Auth Token" in str(e):
             error_msg = "Критическая ошибка конфигурации бота." # Сообщение для пользователя
             # Здесь также нужно уведомить владельца! (Будет сделано в вызывающем коде)
        return None, error_msg

# --- Обертки для конкретных задач ---

async def safe_generate_story(
    messages: List[Dict[str, Any]],
    images_data: Dict[str, bytes],
    genre_key: Optional[str] = 'default'
) -> Tuple[Optional[str], Optional[str]]:
    """
    Безопасно генерирует историю: готовит контент и вызывает прокси.
    """
    prepared_content = pb.build_story_content(messages, images_data, genre_key)
    if not prepared_content:
        # Если сам prompt_builder не смог ничего создать (например, пустые сообщения)
        return "Нет данных для создания истории за этот период.", None
    return await generate_via_proxy(prepared_content)


async def safe_generate_summary(
    messages: List[Dict[str, Any]]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Безопасно генерирует саммари: готовит контент и вызывает прокси.
    """
    prepared_content = pb.build_summary_content(messages)
    if not prepared_content:
        return "Нет текстовых сообщений для создания выжимки.", None
    return await generate_via_proxy(prepared_content)