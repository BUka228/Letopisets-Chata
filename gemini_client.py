# gemini_client.py
import logging
import datetime
import asyncio
import httpx # Используем httpx для асинхронных запросов
import base64 # Для кодирования изображений в base64
from typing import List, Dict, Union, Tuple, Optional, Any

# Импортируем URL и токен из конфига
from config import (
    CLOUDFLARE_WORKER_URL,
    CLOUDFLARE_AUTH_TOKEN,
)

logger = logging.getLogger(__name__)

# --- Типы данных ---
ContentPart = Union[str, Dict[str, Any]] # Тип для частей контента (текст или словарь с данными)
PreparedContent = List[Union[str, Dict[str, Union[str, bytes]]]] # Тип для prepare_story_parts

# --- Конфигурация Gemini или ее вызов больше не нужны здесь ---
# def configure_gemini(): ... - УДАЛЯЕМ

# --- Функция prepare_story_parts остается почти без изменений ---
# Она все так же готовит список частей, включая байты изображений
def prepare_story_parts(messages: List[Dict[str, Any]], images_data: Dict[str, bytes]) -> Optional[PreparedContent]:
    # ... (Весь код этой функции из предыдущей версии остается ЗДЕСЬ) ...
    # ... (Важно: она должна возвращать список, где изображения представлены как
    #      {'mime_type': 'image/jpeg', 'data': image_bytes})
    # --- КОПИЯ КОДА prepare_story_parts ИЗ ПРЕДЫДУЩЕЙ ВЕРСИИ ---
    if not messages: return None
    try:
        valid_messages = [m for m in messages if isinstance(m, dict) and 'timestamp' in m]
        valid_messages.sort(key=lambda x: datetime.datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))
    except (ValueError, TypeError, KeyError) as e:
        logger.warning(f"Не удалось отсортировать сообщения по времени ({e}).", exc_info=True)
        valid_messages = messages
    content_parts: PreparedContent = []
    initial_prompt = (
        f"Ты — ИИ-летописец чата с возможностью анализа изображений. Твоя задача — проанализировать лог сообщений и изображения (обозначенные как [IMAGE N]) за прошедший день. "
        f"Напиши краткую, связную и интересную историю событий в чате. "
        # ... (остальной текст промпта) ...
        f"Теперь, пожалуйста, напиши историю этого дня в чате, учитывая как текст, так и изображения:\n"
    )
    content_parts.append(initial_prompt)
    image_counter = 0
    processed_image_ids = set()
    current_text_block = ""
    for msg in valid_messages:
        try: dt_utc = datetime.datetime.fromisoformat(msg.get('timestamp','').replace('Z', '+00:00')); ts_str = dt_utc.strftime('%H:%M')
        except: ts_str = "??:??"
        user = msg.get('username', 'Неизвестный'); content = msg.get('content', ''); msg_type = msg.get('type'); msg_file_unique_id = msg.get('file_unique_id')
        if msg_type == 'photo' and msg_file_unique_id and msg_file_unique_id in images_data:
            if current_text_block: content_parts.append(current_text_block.strip()); current_text_block = ""
            image_counter += 1; image_bytes = images_data[msg_file_unique_id]; processed_image_ids.add(msg_file_unique_id)
            log_entry = f"[{ts_str}] {user}: отправил(а) изображение [IMAGE {image_counter}]{f' с подписью: «{content}»' if content else ''}\n"
            content_parts.append(log_entry.strip())
            # Важно: Добавляем именно байты изображения
            content_parts.append({"mime_type": "image/jpeg", "data": image_bytes})
        else:
            log_entry = f"[{ts_str}] {user}: "
            if msg_type == 'text' and content: log_entry += f"написал(а): \"{content[:150]}{'...' if len(content)>150 else ''}\"\n"
            elif msg_type == 'video': log_entry += f"отправил(а) видео{f' с подписью: «{content}»' if content else ''} (содержание неизвестно)\n"
            # ... (остальные elif для других типов) ...
            elif msg_type == 'photo': log_entry += f"отправил(а) фото (не анализируется){f' с подписью: «{content}»' if content else ''}\n"
            elif content: log_entry += f"отправил(а) медиа с подписью: «{content}» (тип: {msg_type})\n"
            else: log_entry += f"отправил(а) сообщение (тип: {msg_type})\n"
            current_text_block += log_entry
    if current_text_block: content_parts.append(current_text_block.strip())
    content_parts.append(f"---------------------------------\nКОНЕЦ ЛОГА.\n\nТеперь, пожалуйста, напиши историю этого дня в чате, учитывая как текст, так и изображения:\n")
    if len(content_parts) <= 2: logger.warning("Промпт не содержит фактических данных."); return None
    return content_parts
    # --- КОНЕЦ КОПИИ КОДА prepare_story_parts ---


# --- ИЗМЕНЕНО: Функция вызывает Cloudflare Worker ---
async def generate_story_from_proxy(prepared_content: Optional[PreparedContent]) -> Tuple[Optional[str], Optional[str]]:
    """
    Отправляет подготовленный контент (текст + байты изображений) в прокси Cloudflare Worker.
    Возвращает (историю, сообщение_об_ошибке_или_примечание).
    """
    if not CLOUDFLARE_WORKER_URL or not CLOUDFLARE_AUTH_TOKEN:
        logger.error("URL прокси-воркера или токен авторизации не настроены в конфигурации.")
        return None, "Ошибка конфигурации: не задан URL прокси или токен."

    if not prepared_content:
        logger.info("Нет контента для отправки в прокси.")
        # Возвращаем стандартный ответ для случая без сообщений
        return "Сегодня в чате было тихо, сообщений для анализа не нашлось.", None

    # --- ИЗМЕНЕНО: Преобразуем контент для отправки в JSON ---
    # Изображения нужно закодировать в base64
    json_payload_content = []
    try:
        for part in prepared_content:
            if isinstance(part, str):
                json_payload_content.append(part)
            elif isinstance(part, dict) and 'mime_type' in part and 'data' in part and isinstance(part['data'], bytes):
                # Кодируем байты изображения в base64 строку
                base64_encoded_data = base64.b64encode(part['data']).decode('utf-8')
                json_payload_content.append({
                    "mime_type": part['mime_type'],
                    "data_base64": base64_encoded_data # Отправляем base64
                })
            else:
                 logger.warning(f"Пропуск некорректной части контента при подготовке JSON: {type(part)}")

        if not json_payload_content:
             logger.error("После обработки не осталось валидных частей контента для отправки.")
             return None, "Ошибка: не удалось подготовить данные для прокси."

    except Exception as e:
         logger.error(f"Ошибка при кодировании данных в base64: {e}", exc_info=True)
         return None, f"Ошибка подготовки данных: {e}"


    payload = {"content": json_payload_content}
    headers = {
        "Content-Type": "application/json",
        "X-Auth-Token": CLOUDFLARE_AUTH_TOKEN # Используем наш токен
    }

    logger.info(f"Отправка запроса к прокси-воркеру: {CLOUDFLARE_WORKER_URL}/generate")

    try:
        # Используем httpx для асинхронного запроса
        async with httpx.AsyncClient(timeout=60.0) as client: # Увеличим таймаут
            response = await client.post(
                f"{CLOUDFLARE_WORKER_URL}/generate", # Добавляем путь эндпоинта
                json=payload,
                headers=headers
            )

            # Проверяем статус ответа от прокси
            response.raise_for_status() # Вызовет исключение для статусов 4xx/5xx

            # Парсим JSON ответ от прокси
            proxy_response_data = response.json()

            # Ожидаем ответ вида {"response": "текст истории"} или {"error": "описание"}
            if "response" in proxy_response_data:
                logger.info("Успешный ответ получен от прокси-воркера.")
                # Примечание пока не обрабатываем отдельно, оно должно быть в тексте ошибки если что
                return proxy_response_data["response"], None
            elif "error" in proxy_response_data:
                logger.error(f"Прокси-воркер вернул ошибку: {proxy_response_data['error']}")
                return None, f"Ошибка от прокси: {proxy_response_data['error']}"
            else:
                logger.error(f"Неожиданный формат ответа от прокси: {proxy_response_data}")
                return None, "Неверный формат ответа от прокси-сервиса."

    except httpx.HTTPStatusError as e:
        # Ошибка HTTP от прокси (4xx, 5xx)
        error_body = e.response.text # Попытаемся прочитать тело ошибки
        logger.error(f"Ошибка HTTP при вызове прокси-воркера: {e.response.status_code} - {e.request.url}. Ответ: {error_body}", exc_info=False)
        error_message = f"Ошибка прокси ({e.response.status_code})."
        if error_body:
             try: # Попробуем извлечь сообщение об ошибке из JSON
                 error_data = e.response.json()
                 if "error" in error_data: error_message = error_data["error"]
                 elif "message" in error_data: error_message = error_data["message"]
                 else: error_message += f" {error_body[:100]}" # Обрезаем длинный ответ
             except: # Если тело не JSON
                 error_message += f" {error_body[:100]}"
        return None, error_message
    except httpx.RequestError as e:
        # Ошибка сети, таймаут и т.д. при подключении к прокси
        logger.error(f"Ошибка сети при вызове прокси-воркера: {e.__class__.__name__} - {e.request.url}", exc_info=True)
        return None, f"Ошибка сети при подключении к прокси: {e.__class__.__name__}"
    except Exception as e:
        # Любые другие ошибки (например, при парсинге JSON ответа)
        logger.error(f"Неожиданная ошибка при вызове прокси-воркера: {e}", exc_info=True)
        return None, f"Неизвестная ошибка при работе с прокси: {e.__class__.__name__}"