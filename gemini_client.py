# =============================================================================
# ФАЙЛ: gemini_client.py
# (Исправлен импорт DEFAULT_LANGUAGE)
# =============================================================================
# gemini_client.py
import logging
import asyncio
import httpx
import base64
from typing import List, Dict, Union, Tuple, Optional, Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log

import prompt_builder as pb
# --- ИСПРАВЛЕНИЕ: Добавляем DEFAULT_LANGUAGE в импорт ---
from config import CLOUDFLARE_WORKER_URL, CLOUDFLARE_AUTH_TOKEN, DEFAULT_LANGUAGE
# ------------------------------------------------------
# Импортируем функцию перевода ошибок
from localization import get_user_friendly_proxy_error # Убрали DEFAULT_LANGUAGE отсюда, т.к. импортируем из config

logger = logging.getLogger(__name__)
retry_log = logging.getLogger(__name__ + '.retry')

ContentPart = pb.ContentPart
PreparedContent = pb.PreparedContent

def _is_retryable_exception(exception: BaseException) -> bool:
    """Определяет, стоит ли повторять запрос при данной ошибке httpx."""
    if isinstance(exception, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError, httpx.WriteError)): return True
    if isinstance(exception, httpx.HTTPStatusError): return 500 <= exception.response.status_code < 600 or exception.response.status_code == 429
    return False

@retry(
    stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1.5, min=2, max=15),
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    retry_error_callback=lambda retry_state: logger.error(f"Не удалось выполнить запрос к прокси после {retry_state.attempt_number} попыток. Ошибка: {retry_state.outcome.exception()}"),
    before_sleep=before_sleep_log(retry_log, logging.WARNING)
)
async def _call_proxy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Внутренняя функция для вызова прокси с обработкой retry."""
    if not CLOUDFLARE_WORKER_URL or not CLOUDFLARE_AUTH_TOKEN:
        logger.critical("URL/токен прокси не настроены!")
        raise ValueError("Proxy URL or Auth Token is not configured.")

    headers = {"Content-Type": "application/json", "X-Auth-Token": CLOUDFLARE_AUTH_TOKEN}
    proxy_url = f"{CLOUDFLARE_WORKER_URL.rstrip('/')}/generate"

    logger.info(f"Отправка запроса к прокси: {proxy_url} (payload ~{len(str(payload)) // 1024} KB)")
    async with httpx.AsyncClient(timeout=120.0) as client:
        # --- ИСПРАВЛЕНО: Проверка ошибки после вызова ---
        response = await client.post(proxy_url, json=payload, headers=headers)
        try:
            response.raise_for_status() # Проверяем на HTTP ошибки (4xx, 5xx)
        except httpx.HTTPStatusError as http_err:
            # Проверяем, нужно ли повторить именно эту ошибку
            if _is_retryable_exception(http_err):
                logger.warning(f"Получена ошибка {http_err.response.status_code}, попытка повтора...")
                raise http_err # Передаем исключение для механизма retry
            else:
                # Если ошибка не требует retry (напр., 400), логируем и продолжаем обработку ниже
                logger.warning(f"Прокси вернул неretryable статус {http_err.response.status_code}. Тело: {response.text[:500]}")
                # Пытаемся вернуть JSON, даже если это ошибка, чтобы извлечь поле 'error'
                try: return response.json()
                except Exception: return {"error": f"Proxy returned status {http_err.response.status_code}"}
        except httpx.RequestError as req_err:
             # Сетевые ошибки всегда retryable по настройкам tenacity
             logger.warning(f"Сетевая ошибка: {req_err}, попытка повтора...")
             raise req_err

        # Если мы здесь, значит статус 2xx (успех)
        logger.info(f"Успешный ответ ({response.status_code}) от прокси.")
        # Важно: всегда пытаемся вернуть JSON, даже при успехе
        try:
            return response.json()
        except Exception as json_err:
             logger.error(f"Не удалось распарсить JSON из успешного ответа прокси ({response.status_code}): {json_err}")
             # Возвращаем ошибку, чтобы вышестоящий код понял, что что-то не так
             return {"error": f"Proxy returned status {response.status_code} but failed to parse JSON"}


async def generate_via_proxy(prepared_content: Optional[PreparedContent], lang: str = DEFAULT_LANGUAGE) -> Tuple[Optional[str], Optional[str]]:
    """
    Отправляет подготовленный контент в прокси.
    Возвращает (сгенерированный_текст, user_friendly_error_message).
    """
    if not prepared_content:
        logger.info("Нет контента для отправки в прокси.")
        return "Нет данных для обработки.", None # Нейтральный ответ

    json_payload_content = []
    try:
        for part in prepared_content:
            if isinstance(part, str): json_payload_content.append(part)
            elif isinstance(part, dict) and 'data' in part and isinstance(part['data'], bytes):
                base64_encoded_data = base64.b64encode(part['data']).decode('utf-8')
                json_payload_content.append({"mime_type": part.get('mime_type', 'image/jpeg'), "data_base64": base64_encoded_data})
            else: logger.warning(f"Пропуск некорректной части контента при подготовке JSON: {type(part)}")
        if not json_payload_content: raise ValueError("Нет валидных частей контента после форматирования для JSON.")
    except Exception as e:
        logger.error(f"Ошибка подготовки JSON payload: {e}", exc_info=True)
        technical_error = f"Внутренняя ошибка: не удалось подготовить данные ({e.__class__.__name__})."
        user_error = get_user_friendly_proxy_error(technical_error, lang)
        return None, user_error

    payload = {"content": json_payload_content}

    try:
        proxy_response_data = await _call_proxy(payload)
        if isinstance(proxy_response_data, dict):
            if "response" in proxy_response_data:
                # Успешный ответ от Gemini через прокси
                # Доп. проверка на пустой ответ, который может быть нежелателен
                if not proxy_response_data["response"]:
                     logger.warning("Прокси вернул успешный статус, но пустой ответ 'response'.")
                     # Можно вернуть ошибку или специфическое сообщение
                     return None, get_user_friendly_proxy_error("Empty successful response", lang)
                return proxy_response_data["response"], None # Успех
            elif "error" in proxy_response_data:
                # Прокси вернул ошибку (возможно, от Gemini или свою)
                technical_error = proxy_response_data['error']
                logger.error(f"Прокси вернул ошибку: {technical_error}")
                user_error = get_user_friendly_proxy_error(technical_error, lang)
                return None, user_error
            else:
                # Неожиданный формат ответа от прокси (нет ни 'response', ни 'error')
                logger.error(f"Неожиданный формат JSON ответа от прокси: {proxy_response_data}")
                user_error = get_user_friendly_proxy_error("Invalid proxy response format", lang)
                return None, user_error
        else:
             # Если _call_proxy вернул что-то совсем не то (не dict)
             logger.error(f"Получен некорректный тип данных от _call_proxy: {type(proxy_response_data)}")
             user_error = get_user_friendly_proxy_error("Invalid data type from proxy", lang)
             return None, user_error

    except Exception as e: # Ловим ошибки tenacity (RetryError) и другие непредвиденные
        logger.error(f"Не удалось вызвать прокси после нескольких попыток: {e}", exc_info=True)
        technical_error = f"{e.__class__.__name__}: {e}"
        user_error = get_user_friendly_proxy_error(technical_error, lang)
        # Особый случай для критической ошибки конфигурации
        if isinstance(e, ValueError) and "Proxy URL or Auth Token" in str(e):
             user_error = get_text("error_proxy_config_user", lang)
             # Уведомление владельца должно произойти в вызывающем коде
        return None, user_error

# --- Обертки для конкретных задач ---
# --- ИСПРАВЛЕНИЕ: Убрали default для lang, т.к. он берется из generate_via_proxy ---
async def safe_generate_story(
    messages: List[Dict[str, Any]],
    images_data: Dict[str, bytes],
    genre_key: Optional[str] = 'default',
    lang: str = DEFAULT_LANGUAGE # Оставляем default здесь, т.к. вызывающий код может не передать язык
) -> Tuple[Optional[str], Optional[str]]:
    """Безопасно генерирует историю: готовит контент и вызывает прокси."""
    prepared_content = pb.build_story_content(messages, images_data, genre_key)
    if not prepared_content: return "Нет данных для создания истории.", None
    # Передаем lang в generate_via_proxy
    return await generate_via_proxy(prepared_content, lang)

async def safe_generate_summary(
    messages: List[Dict[str, Any]],
    lang: str = DEFAULT_LANGUAGE # Оставляем default здесь
) -> Tuple[Optional[str], Optional[str]]:
    """Безопасно генерирует саммари: готовит контент и вызывает прокси."""
    prepared_content = pb.build_summary_content(messages)
    if not prepared_content: return "Нет текстовых сообщений для выжимки.", None
    # Передаем lang в generate_via_proxy
    return await generate_via_proxy(prepared_content, lang)