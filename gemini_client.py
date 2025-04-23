# gemini_client.py
import logging
import datetime
import asyncio
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
# Добавляем типы для type hinting мультимодального контента
from typing import List, Dict, Union, Tuple, Optional

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL_NAME, # Убедитесь, что модель поддерживает Vision (gemini-1.5-flash/pro должны)
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_TEMPERATURE,
)

logger = logging.getLogger(__name__)

# --- Константы для кодов завершения ---
FINISH_REASON_STOP = 1
FINISH_REASON_MAX_TOKENS = 2
FINISH_REASON_SAFETY = 3
FINISH_REASON_RECITATION = 4
FINISH_REASON_OTHER = 5
FINISH_REASON_UNSPECIFIED = 0

# --- Типы для мультимодального контента ---
# Элемент контента может быть строкой (текст) или словарем (изображение)
ContentPart = Union[str, Dict[str, Union[str, bytes]]]
# Весь контент для Gemini - это список таких частей
GeminiContent = List[ContentPart]

# --- Конфигурация Gemini ---
_gemini_generation_config = None
_gemini_safety_settings = None
_gemini_model = None

def configure_gemini():
    """Настраивает клиент Gemini API."""
    global _gemini_generation_config, _gemini_safety_settings, _gemini_model
    try:
        if not GEMINI_API_KEY:
            raise ValueError("Ключ Gemini API не установлен.")

        genai.configure(api_key=GEMINI_API_KEY)

        _gemini_generation_config = genai.types.GenerationConfig(
            temperature=GEMINI_TEMPERATURE,
            max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        )
        _gemini_safety_settings = [] # Настройки безопасности по умолчанию

        # Указываем модель, которая точно поддерживает Vision
        # gemini-1.5-flash-latest или gemini-pro-vision
        # Используем модель из конфига, но убеждаемся, что она подходит
        if "vision" not in GEMINI_MODEL_NAME and "1.5" not in GEMINI_MODEL_NAME:
             logger.warning(f"Модель {GEMINI_MODEL_NAME} может не поддерживать Vision API. Рекомендуется gemini-1.5-flash/pro или gemini-pro-vision.")

        _gemini_model = genai.GenerativeModel(
            GEMINI_MODEL_NAME,
            generation_config=_gemini_generation_config,
            safety_settings=_gemini_safety_settings
        )
        logger.info(f"Клиент Gemini API успешно сконфигурирован для модели '{GEMINI_MODEL_NAME}'.")
        return True
    except Exception as e:
        logger.error(f"Критическая ошибка конфигурации Gemini API: {e}", exc_info=True)
        _gemini_model = None
        return False

# --- ИЗМЕНЕНО: Функция теперь готовит мультимодальный контент ---
def prepare_story_parts(messages: list, images_data: Dict[str, bytes]) -> Optional[GeminiContent]:
    """
    Форматирует собранные сообщения и данные изображений в список частей для Gemini API.
    Возвращает None, если нет сообщений.
    """
    if not messages:
        return None

    try:
        valid_messages = [m for m in messages if isinstance(m, dict) and 'timestamp' in m]
        valid_messages.sort(key=lambda x: datetime.datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))
    except (ValueError, TypeError, KeyError) as e:
        logger.warning(f"Не удалось отсортировать сообщения по времени ({e}). Обработка без сортировки.", exc_info=True)
        valid_messages = messages # Используем оригинальный порядок

    content_parts: GeminiContent = [] # Список для хранения частей контента

    # --- ИЗМЕНЕНО: Улучшенный промпт с упоминанием анализа изображений ---
    initial_prompt = (
        f"Ты — ИИ-летописец чата с возможностью анализа изображений. Твоя задача — проанализировать лог сообщений и изображения (обозначенные как [IMAGE N]) за прошедший день. "
        f"Напиши краткую, связную и интересную историю событий в чате. "
        f"Упоминай пользователей по их именам (username). Не цитируй текст дословно, а пересказывай суть. "
        f"Кратко опиши содержание ключевых изображений, если они важны для понимания событий дня. "
        f"Опиши общую атмосферу дня в чате.\n\n"
        f"ЛОГ СООБЩЕНИЙ И ИЗОБРАЖЕНИЙ ЗА ДЕНЬ:\n"
        f"---------------------------------\n"
    )
    content_parts.append(initial_prompt)

    image_counter = 0
    processed_image_ids = set()

    current_text_block = "" # Накапливаем текстовые сообщения

    for msg in valid_messages:
        try:
            dt_utc = datetime.datetime.fromisoformat(msg.get('timestamp','').replace('Z', '+00:00'))
            ts_str = dt_utc.strftime('%H:%M')
        except (ValueError, TypeError):
             ts_str = "??:??"

        user = msg.get('username', 'Неизвестный')
        content = msg.get('content', '')
        msg_type = msg.get('type', 'unknown')
        msg_file_unique_id = msg.get('file_unique_id') # Получаем уникальный ID файла

        log_entry = f"[{ts_str}] {user}: "

        # --- ИЗМЕНЕНО: Обработка изображений ---
        if msg_type == 'photo' and msg_file_unique_id and msg_file_unique_id in images_data:
            if current_text_block: # Если есть накопленный текст, добавляем его
                 content_parts.append(current_text_block.strip())
                 current_text_block = "" # Сбрасываем текстовый блок

            image_counter += 1
            image_bytes = images_data[msg_file_unique_id]
            processed_image_ids.add(msg_file_unique_id)

            # Добавляем текстовый маркер изображения
            log_entry += f"отправил(а) изображение [IMAGE {image_counter}]{f' с подписью: «{content}»' if content else ''}\n"
            content_parts.append(log_entry.strip())

            # Добавляем само изображение как часть контента
            # mime_type лучше определять, но для начала можно использовать jpeg
            # Telegram обычно использует jpeg для фото
            content_parts.append({
                "mime_type": "image/jpeg",
                "data": image_bytes
            })

        else: # Если это не фото или фото не было скачано/передано
            # Добавляем запись в текущий текстовый блок
            if msg_type == 'text' and content:
                log_entry += f"написал(а): \"{content[:150]}{'...' if len(content)>150 else ''}\"\n"
            # --- ИЗМЕНЕНО: Улучшенное описание других типов ---
            elif msg_type == 'video':
                log_entry += f"отправил(а) видео{f' с подписью: «{content}»' if content else ''} (содержание неизвестно)\n"
            elif msg_type == 'sticker':
                log_entry += f"отправил(а) стикер{f' ({content})' if content else ''}\n"
            elif msg_type == 'voice':
                log_entry += f"записал(а) голосовое сообщение\n"
            elif msg_type == 'video_note':
                log_entry += f"записал(а) видео-сообщение (кружок)\n"
            elif msg_type == 'document':
                 log_entry += f"отправил(а) документ '{msg.get('file_name', 'без имени')}'{f' с подписью: «{content}»' if content else ''}\n"
            elif msg_type == 'audio':
                 log_entry += f"отправил(а) аудио файл '{msg.get('file_name', 'без имени')}'{f' с подписью: «{content}»' if content else ''}\n"
            elif msg_type == 'photo': # Фото без скачанных данных
                 log_entry += f"отправил(а) фото (не анализируется){f' с подписью: «{content}»' if content else ''}\n"
            elif content: # Другие медиа с подписью
                 log_entry += f"отправил(а) медиа с подписью: «{content}» (тип: {msg_type})\n"
            else:
                log_entry += f"отправил(а) сообщение (тип: {msg_type})\n"
            current_text_block += log_entry

    # Добавляем оставшийся текстовый блок, если он есть
    if current_text_block:
        content_parts.append(current_text_block.strip())

    # Добавляем финальную часть промпта
    content_parts.append(
        f"---------------------------------\n"
        f"КОНЕЦ ЛОГА.\n\n"
        f"Теперь, пожалуйста, напиши историю этого дня в чате, учитывая как текст, так и изображения:\n"
    )

    # Проверка, что что-то было добавлено кроме инструкций
    if len(content_parts) <= 2: # Только initial_prompt и final_prompt
        logger.warning("Промпт для Gemini не содержит фактических данных сообщений.")
        # Можно вернуть пустой контент или специальную строку, чтобы не отправлять запрос
        return None

    return content_parts


# --- ИЗМЕНЕНО: Функция принимает GeminiContent ---
async def generate_story_from_gemini(content: GeminiContent) -> Tuple[Optional[str], Optional[str]]:
    """
    Отправляет МУЛЬТИМОДАЛЬНЫЙ контент (текст + изображения) в Gemini API.
    Возвращает (историю, примечание_или_ошибку).
    """
    if not _gemini_model:
        logger.error("Попытка генерации истории без инициализированной модели Gemini.")
        return None, "Клиент нейросети (Gemini) не настроен."
    if not content:
        # Если prepare_story_parts вернул None (нет сообщений)
        return "Сегодня в чате было тихо, сообщений для анализа не нашлось.", None

    # Считаем количество изображений для лога
    image_count = sum(1 for part in content if isinstance(part, dict) and 'data' in part)
    logger.info(f"Отправка МУЛЬТИМОДАЛЬНОГО запроса к Gemini API (Модель: {GEMINI_MODEL_NAME}, Изображений: {image_count}).")
    # Логировать весь контент опасно из-за байтов изображений

    try:
        # --- ИЗМЕНЕНО: Передаем список частей контента ---
        response = await _gemini_model.generate_content_async(content)

        story_text = None
        note_or_error_msg = None

        # Анализ блокировки промпта (остается как было)
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            reason_code = response.prompt_feedback.block_reason
            reason_name = getattr(response.prompt_feedback, 'block_reason_message', f'Код {reason_code}')
            logger.warning(f"Запрос к Gemini был заблокирован ДО генерации по причине: {reason_name} ({reason_code})")
            note_or_error_msg = f"История не сгенерирована: запрос заблокирован из-за настроек безопасности (причина: {reason_name})."
            return None, note_or_error_msg

        # Анализ результата генерации (остается как было, используем числовые коды)
        if response.parts:
            story_text = response.text.strip()
            logger.info(f"Gemini API успешно вернуло историю (Длина: {len(story_text)} симв.).")

            if response.candidates:
                candidate = response.candidates[0]
                finish_reason_value = candidate.finish_reason
                logger.debug(f"Причина завершения от Gemini (числовой код): {finish_reason_value}")

                if finish_reason_value != FINISH_REASON_STOP:
                    logger.warning(f"Генерация завершена НЕ штатно. Причина (код): {finish_reason_value}")
                    if finish_reason_value == FINISH_REASON_MAX_TOKENS:
                        note_or_error_msg = "(История может быть неполной из-за достижения лимита токенов)"
                    elif finish_reason_value == FINISH_REASON_SAFETY:
                        note_or_error_msg = "(Генерация была остановлена из-за настроек безопасности контента)"
                    elif finish_reason_value == FINISH_REASON_RECITATION:
                        note_or_error_msg = "(Генерация была остановлена из-за обнаружения цитирования защищенного контента)"
                    elif finish_reason_value == FINISH_REASON_OTHER:
                        note_or_error_msg = "(Генерация была остановлена по неизвестной причине (OTHER))"
                    else:
                        note_or_error_msg = f"(Генерация была остановлена по причине с кодом: {finish_reason_value})"
            else:
                logger.warning("Gemini API вернуло текст, но не предоставило информацию о причине завершения (нет candidates).")
        else:
            logger.warning(f"Gemini API вернуло пустой ответ без текста и без блокировки промпта. Response: {response}")
            note_or_error_msg = "Нейросеть (Gemini) не смогла сгенерировать историю по неизвестной причине (пустой ответ)."
            return None, note_or_error_msg

        return story_text, note_or_error_msg

    # Обработка исключений API (остается без изменений)
    except google_exceptions.ResourceExhausted as e:
        logger.error(f"Ошибка квот Gemini API: {e}", exc_info=True)
        return None, "Ошибка: Превышен лимит запросов к нейросети (Gemini). Попробуем позже."
    except google_exceptions.PermissionDenied as e:
         logger.error(f"Ошибка доступа к Gemini API: {e}", exc_info=True)
         return None, "Ошибка: Проблема с ключом доступа к нейросети (Gemini). Проверьте API ключ."
    except google_exceptions.InternalServerError as e:
         logger.error(f"Внутренняя ошибка сервера Gemini API: {e}", exc_info=True)
         return None, "Ошибка: Внутренний сбой на сервере нейросети (Gemini). Попробуем позже."
    except google_exceptions.GoogleAPIError as e:
        logger.error(f"Ошибка Google API при вызове Gemini: {e}", exc_info=True)
        return None, f"Ошибка API нейросети (Gemini): {e.__class__.__name__}."
    except AttributeError as e:
        logger.error(f"Ошибка атрибута при работе с ответом Gemini API: {e}", exc_info=True)
        return None, f"Ошибка обработки ответа нейросети (Gemini): {e.__class__.__name__}. Возможно, изменилась структура ответа API."
    except Exception as e:
        logger.error(f"Неожиданная ошибка при вызове Gemini API: {e}", exc_info=True)
        return None, f"Неизвестная ошибка при обращении к нейросети (Gemini): {e.__class__.__name__}."