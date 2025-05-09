
import logging
import asyncio
import httpx
import base64
from typing import List, Dict, Union, Tuple, Optional, Any
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type,
    before_sleep_log, RetryError
)

# Импорты проекта
import prompt_builder as pb
from config import (
    CLOUDFLARE_WORKER_URL, CLOUDFLARE_AUTH_TOKEN, DEFAULT_LANGUAGE,
    INTERVENTION_MAX_RETRY, INTERVENTION_TIMEOUT_SEC # Настройки для вмешательств
)
from localization import get_user_friendly_proxy_error, get_text # Добавили get_text для user-friendly ошибки конфига

logger = logging.getLogger(__name__)
retry_log = logging.getLogger(__name__ + '.retry') # Отдельный логгер для ретраев

# Типы из prompt_builder
ContentPart = pb.ContentPart
PreparedContent = pb.PreparedContent

# --- Логика Retry ---

def _is_retryable_exception(exception: BaseException) -> bool:
    """Определяет, стоит ли повторять запрос при данной ошибке httpx."""
    if isinstance(exception, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError, httpx.WriteError)):
        return True
    # Проверяем, что это HTTP ошибка и код статуса 5xx или 429
    if isinstance(exception, httpx.HTTPStatusError):
        return 500 <= exception.response.status_code < 600 or exception.response.status_code == 429
    return False

# Декоратор retry для стандартных запросов (истории, дайджесты, саммари)
_default_retry_decorator = retry(
    stop=stop_after_attempt(4), # 1 + 3 retries
    wait=wait_exponential(multiplier=1.5, min=2, max=15), # ~2s, 5s, 9.5s wait
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)), # Основные типы ошибок httpx
    retry_error_callback=lambda retry_state: logger.error(
        f"Запрос к прокси НЕ удался после {retry_state.attempt_number} попыток. Последняя ошибка: {retry_state.outcome.exception()}"
    ),
    before_sleep=before_sleep_log(retry_log, logging.WARNING) # Логи перед повторной попыткой
)

# Декоратор retry для запросов вмешательств (менее критично, меньше попыток)
_intervention_retry_decorator = retry(
    stop=stop_after_attempt(INTERVENTION_MAX_RETRY + 1), # Используем значение из config + 1
    wait=wait_exponential(multiplier=1.2, min=1, max=5), # Более быстрые ретраи
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    retry_error_callback=lambda retry_state: logger.warning(
        f"Запрос вмешательства НЕ удался после {retry_state.attempt_number} попыток. Ошибка: {retry_state.outcome.exception()}"
    ), # Логируем как WARNING
    before_sleep=before_sleep_log(retry_log, logging.INFO) # Логируем попытки на уровне INFO
)

# --- Основная функция вызова прокси ---

async def _call_proxy(
    payload: Dict[str, Any],
    use_intervention_retry: bool = False, # Флаг для выбора настроек retry/timeout
    timeout: float = 120.0 # Таймаут по умолчанию для долгих запросов
) -> Dict[str, Any]:
    """
    Внутренняя функция для вызова прокси Cloudflare Worker.
    Использует разные настройки retry и timeout в зависимости от флага.
    Возвращает словарь с результатом или ошибкой.
    """
    if not CLOUDFLARE_WORKER_URL or not CLOUDFLARE_AUTH_TOKEN:
        logger.critical("URL/токен прокси не настроены в конфигурации!")
        # Выбрасываем ValueError, который будет пойман в generate_via_proxy
        raise ValueError("Proxy URL or Auth Token is not configured.")

    headers = {"Content-Type": "application/json", "X-Auth-Token": CLOUDFLARE_AUTH_TOKEN}
    proxy_url = f"{CLOUDFLARE_WORKER_URL.rstrip('/')}/generate" # Путь к ендпоинту на воркере

    # Выбираем декоратор и лог префикс
    retry_decorator = _intervention_retry_decorator if use_intervention_retry else _default_retry_decorator
    log_prefix = "[Intervention]" if use_intervention_retry else "[Generation]"
    effective_timeout = INTERVENTION_TIMEOUT_SEC if use_intervention_retry else timeout

    # Определяем внутреннюю асинхронную функцию, к которой применим декоратор
    @retry_decorator
    async def _make_request():
        logger.info(f"{log_prefix} Отправка запроса к прокси: {proxy_url} (payload ~{len(str(payload)) // 1024} KB, timeout={effective_timeout}s)")
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            response = await client.post(proxy_url, json=payload, headers=headers)

            try:
                response.raise_for_status() # Генерирует исключение для 4xx/5xx
                # Если мы здесь, статус успешный (2xx)
                logger.info(f"{log_prefix} Успешный ответ ({response.status_code}) от прокси.")
                try:
                    # Пытаемся распарсить JSON из успешного ответа
                    return response.json()
                except Exception as json_err:
                     logger.error(f"{log_prefix} Не удалось распарсить JSON из УСПЕШНОГО ответа прокси ({response.status_code}): {json_err}")
                     # Возвращаем словарь ошибки, т.к. не смогли получить результат
                     return {"error": f"Proxy OK but failed to parse JSON ({response.status_code})"}

            except httpx.HTTPStatusError as http_err:
                # Ошибка HTTP (4xx/5xx)
                # Проверяем, требует ли ошибка повторной попытки согласно нашей логике
                if _is_retryable_exception(http_err):
                    logger.warning(f"{log_prefix} Получена ошибка {http_err.response.status_code}, попытка повтора...")
                    raise http_err # Передаем исключение дальше для механизма retry
                else:
                    # Ошибка не требует повтора (напр., 400 Bad Request, 401 Unauthorized)
                    logger.warning(f"{log_prefix} Прокси вернул неretryable статус {http_err.response.status_code}. Тело: {response.text[:200]}")
                    # Пытаемся получить JSON с описанием ошибки от прокси
                    try:
                        return response.json()
                    except Exception:
                         # Если тело ответа - не JSON, возвращаем свою ошибку
                         return {"error": f"Proxy returned status {http_err.response.status_code}"}

            except httpx.RequestError as req_err:
                # Ошибка сети или подключения (всегда требует повтора по нашим правилам)
                logger.warning(f"{log_prefix} Сетевая ошибка: {req_err}, попытка повтора...")
                raise req_err # Передаем исключение дальше для механизма retry

    # Выполняем внутреннюю функцию с применением retry декоратора
    try:
        result = await _make_request()
        return result
    except RetryError as e: # Ловим ошибку ПОСЛЕ всех неудачных ретраев
        logger.error(f"{log_prefix} Запрос к прокси НЕ УДАЛСЯ после всех попыток: {e}")
        # Поднимаем исключение, чтобы внешний код мог его поймать
        raise e
    except ValueError as ve: # Ловим ошибку конфигурации
        logger.critical(f"{log_prefix} {ve}")
        raise ve # Пробрасываем выше
    except Exception as general_e: # Другие непредвиденные ошибки
         logger.exception(f"{log_prefix} Неожиданная ошибка при вызове прокси: {general_e}")
         raise general_e

# --- Обработка ответа и подготовка данных ---

async def generate_via_proxy(
    prepared_content: Optional[PreparedContent],
    lang: str = DEFAULT_LANGUAGE,
    use_intervention_retry: bool = False # Флаг для _call_proxy
) -> Tuple[Optional[str], Optional[str]]:
    """
    Отправляет подготовленный контент в прокси, обрабатывает ответ.
    Возвращает (сгенерированный_текст, user_friendly_error_message_или_примечание).
    """
    if not prepared_content:
        logger.info("Нет контента для отправки в прокси.")
        # Это не ошибка, просто нет данных
        return "Нет данных для обработки.", None

    json_payload_content = []
    try:
        # Кодируем изображения в Base64
        for part in prepared_content:
            if isinstance(part, str):
                json_payload_content.append(part)
            elif isinstance(part, dict) and 'data' in part and isinstance(part['data'], bytes):
                base64_encoded_data = base64.b64encode(part['data']).decode('utf-8')
                json_payload_content.append({
                    "mime_type": part.get('mime_type', 'image/jpeg'), # Gemini предпочитает JPEG/PNG/WEBP/HEIC/HEIF
                    "data_base64": base64_encoded_data
                })
            else:
                logger.warning(f"Пропуск некорректной части контента при подготовке JSON: {type(part)}")

        if not json_payload_content:
            raise ValueError("Нет валидных частей контента после форматирования для JSON.")

    except Exception as e:
        logger.error(f"Ошибка подготовки JSON payload: {e}", exc_info=True)
        technical_error = f"Payload prep error: {e.__class__.__name__}"
        user_error = get_user_friendly_proxy_error(technical_error, lang)
        return None, user_error

    payload = {"content": json_payload_content}

    try:
        # Вызываем прокси, передавая флаг для настроек retry/timeout
        proxy_response_data = await _call_proxy(
            payload,
            use_intervention_retry=use_intervention_retry
        )

        # Обрабатываем результат (должен быть словарем)
        if isinstance(proxy_response_data, dict):
            if "response" in proxy_response_data:
                generated_text = proxy_response_data["response"]
                # Проверка на пустой ответ, что может быть ошибкой Gemini
                if not generated_text:
                    logger.warning("Прокси вернул успешный статус, но пустой ответ 'response'.")
                    user_error = get_user_friendly_proxy_error("Empty successful response", lang)
                    return None, user_error # Возвращаем ошибку вместо пустого текста
                return generated_text.strip(), None # Успех, убираем лишние пробелы

            elif "error" in proxy_response_data:
                # Прокси явно вернул ошибку
                technical_error = proxy_response_data['error']
                logger.error(f"Прокси вернул ошибку: {technical_error}")
                user_error = get_user_friendly_proxy_error(technical_error, lang)
                return None, user_error
            else:
                # Неожиданный формат JSON от прокси (нет ни 'response', ни 'error')
                logger.error(f"Неожиданный формат JSON ответа от прокси: {proxy_response_data}")
                user_error = get_user_friendly_proxy_error("Invalid proxy response format", lang)
                return None, user_error
        else:
             # Если _call_proxy вернул что-то совсем не то (не dict, маловероятно)
             logger.error(f"Получен некорректный тип данных от _call_proxy: {type(proxy_response_data)}")
             user_error = get_user_friendly_proxy_error("Invalid data type from proxy", lang)
             return None, user_error

    except (RetryError, ValueError, Exception) as e: # Ловим RetryError, ошибку конфигурации и другие
        logger.error(f"Не удалось вызвать прокси после попыток или др. ошибка: {e}", exc_info=(not isinstance(e, RetryError))) # Не пишем traceback для RetryError
        technical_error = f"{e.__class__.__name__}: {e}"
        user_error = get_user_friendly_proxy_error(technical_error, lang)
        # Особый случай для критической ошибки конфигурации
        if isinstance(e, ValueError) and "Proxy URL or Auth Token" in str(e):
             # Получаем текст ошибки конфигурации через get_text
             user_error = get_text("error_proxy_config_user", lang)
             # Уведомление владельца произойдет в вызывающем коде (generate_now и т.д.)
        return None, user_error

# --- Обертки для конкретных задач ---

async def safe_generate_output(
    messages: List[Dict[str, Any]],
    images_data: Dict[str, bytes],
    output_format: str,
    genre_key: Optional[str],
    personality_key: str,
    lang: str = DEFAULT_LANGUAGE
) -> Tuple[Optional[str], Optional[str]]:
    """
    Безопасно генерирует историю ИЛИ дайджест.
    Использует стандартные настройки retry/timeout.
    """
    logger.debug(f"Generating output: format={output_format}, genre={genre_key}, personality={personality_key}, lang={lang}")
    prepared_content = pb.build_content(
        messages, images_data, output_format, genre_key, personality_key
    )
    if not prepared_content:
        # Используем локализованное имя формата в сообщении
        format_name = get_text(f"output_format_name_{output_format}", lang)
        return f"Нет данных для генерации '{format_name}'.", None
    # Вызываем прокси со стандартными настройками
    return await generate_via_proxy(prepared_content, lang, use_intervention_retry=False)

async def safe_generate_summary(
    messages: List[Dict[str, Any]],
    lang: str = DEFAULT_LANGUAGE
) -> Tuple[Optional[str], Optional[str]]:
    """
    Безопасно генерирует саммари (для команды /summarize).
    Использует стандартные настройки retry/timeout.
    """
    logger.debug(f"Generating simple summary, lang={lang}")
    prepared_content = pb.build_summary_content(messages)
    if not prepared_content:
        return "Нет текстовых сообщений для выжимки.", None
    # Вызываем прокси со стандартными настройками
    return await generate_via_proxy(prepared_content, lang, use_intervention_retry=False)


async def safe_generate_intervention(
    # ПАРАМЕТР УЖЕ ПРАВИЛЬНЫЙ: Принимаем готовую строку промпта
    intervention_prompt_string: Optional[str],
    lang: str = DEFAULT_LANGUAGE # Оставляем lang на всякий случай
) -> Optional[str]:
    """
    Генерирует короткий комментарий для вмешательства ИЗ ГОТОВОГО ПРОМПТА.
    Использует урезанные настройки retry/timeout.
    В случае ошибки возвращает None и логирует ее (НЕ user-friendly ошибку).
    """
    # КОД ЭТОЙ ФУНКЦИИ ОСТАЕТСЯ БЕЗ ИЗМЕНЕНИЙ (как в вашем последнем бандле),
    # так как он уже принимает intervention_prompt_string.

    if not intervention_prompt_string:
        logger.debug("safe_generate_intervention received empty prompt string.")
        return None

    payload = {"content": [intervention_prompt_string]}

    try:
        response_data = await _call_proxy(
            payload,
            use_intervention_retry=True,
            timeout=INTERVENTION_TIMEOUT_SEC
        )

        if isinstance(response_data, dict) and "response" in response_data:
             result_text = response_data["response"].strip()
             if result_text:
                 return result_text
             else:
                 logger.warning("Intervention AI returned empty response text.")
                 return None
        else:
            if isinstance(response_data, dict) and "error" in response_data:
                 logger.warning(f"Intervention generation failed: {response_data['error']}")
            else:
                 logger.warning(f"Intervention generation got unexpected response: {response_data}")
            return None
    except (RetryError, ValueError, Exception) as e:
        logger.warning(f"Exception during intervention generation after retries: {e.__class__.__name__}: {e}", exc_info=(not isinstance(e, RetryError)))
        return None