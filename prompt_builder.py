# prompt_builder.py
import logging
import datetime
from typing import List, Dict, Union, Optional, Any

# Импортируем сюда список жанров, когда он будет определен в bot_handlers
# from bot_handlers import SUPPORTED_GENRES

logger = logging.getLogger(__name__)

# Типы для ясности
ContentPart = Union[str, Dict[str, Any]] # Текст или словарь с mime_type/data (bytes или base64)
PreparedContent = List[ContentPart]

# --- Промпты для Истории ---

def get_story_initial_prompt(genre_key: Optional[str] = 'default') -> str:
    """Возвращает начальную часть промпта для генерации истории, учитывая жанр."""

    # TODO: Заменить на импорт, когда SUPPORTED_GENRES будет в bot_handlers
    TEMP_SUPPORTED_GENRES = {
        'default': 'Стандартный', 'humor': 'Юмористический', 'detective': 'Детективный',
        'fantasy': 'Фэнтезийный', 'news_report': 'Новостной репортаж'
    }
    genre_name = TEMP_SUPPORTED_GENRES.get(genre_key or 'default', 'стандартном')

    if genre_key == 'default' or not genre_key:
        genre_instruction = "Напиши **связную и интересную историю событий** в чате в 1-3 абзацах."
    else:
        # Указываем ИИ на жанр. Можно добавить больше специфики для каждого жанра.
        genre_instruction = f"Напиши **связную и интересную историю событий** в чате в 1-3 абзацах в **{genre_name.lower()}** стиле."
        if genre_key == 'humor':
            genre_instruction += " Постарайся сделать её забавной, найди смешные моменты в общении."
        elif genre_key == 'detective':
            genre_instruction += " Попробуй представить события как загадочное дело или расследование."
        elif genre_key == 'news_report':
            genre_instruction += " Оформи историю как краткий новостной репортаж: объективно, по фактам, с упоминанием ключевых участников."
        # Добавьте другие инструкции по необходимости

    initial_prompt = (
        f"Ты — ИИ-летописец чата с возможностью анализа изображений. Твоя задача — проанализировать лог сообщений и изображения (обозначенные как [IMAGE N]) за указанный период. "
        f"{genre_instruction} Используй **Markdown** для форматирования:\n"
        f"- Придумай и используй **жирный заголовок** для истории (например, `**Заголовок Истории**`).\n"
        f"- Используй абзацы для читаемости.\n"
        f"- Упоминай пользователей по их *именам* (username) курсивом (например, `*Анна*`).\n"
        f"- Не цитируй текст дословно, а пересказывай суть.\n"
        f"- Кратко опиши содержание 1-2 ключевых изображений, если они важны, и упомяни, кто их отправил.\n"
        f"- Опиши общую атмосферу (активный, спокойный, веселый и т.д.). Не выдумывай события, основывайся строго на логе.\n\n"
        f"ЛОГ СООБЩЕНИЙ И ИЗОБРАЖЕНИЙ ЗА ПЕРИОД:\n"
        f"---------------------------------\n"
    )
    return initial_prompt

def format_log_entry(msg: Dict[str, Any], image_counter: Optional[int] = None, image_placeholder: str = "[IMAGE {count}]") -> str:
    """Форматирует одну запись лога для промпта."""
    try:
        dt_utc = datetime.datetime.fromisoformat(msg.get('timestamp','').replace('Z', '+00:00'))
        ts_str = dt_utc.strftime('%H:%M')
    except:
        ts_str = "??:??"
    user = msg.get('username', 'Неизвестный')
    content = msg.get('content', '')
    msg_type = msg.get('type')
    file_name = msg.get('file_name')

    log_entry = f"[{ts_str}] *{user}*: " # Выделяем имя курсивом

    if msg_type == 'photo' and image_counter is not None:
        placeholder = image_placeholder.format(count=image_counter)
        log_entry += f"отправил(а) изображение {placeholder}{f' с подписью: «{content}»' if content else ''}\n"
    elif msg_type == 'text' and content:
        log_entry += f"написал(а): \"{content[:150]}{'...' if len(content)>150 else ''}\"\n"
    elif msg_type == 'video':
        log_entry += f"отправил(а) видео{f' «{content}»' if content else ''} (содержание анализу не подлежит)\n"
    elif msg_type == 'sticker':
        log_entry += f"отправил(а) стикер{f' ({content})' if content else ''}\n"
    elif msg_type == 'voice':
        log_entry += f"записал(а) голосовое сообщение\n"
    elif msg_type == 'video_note':
        log_entry += f"записал(а) видео-сообщение (кружок)\n"
    elif msg_type == 'document':
        log_entry += f"отправил(а) документ '{file_name or 'без имени'}'{f' «{content}»' if content else ''}\n"
    elif msg_type == 'audio':
        log_entry += f"отправил(а) аудио '{file_name or 'без имени'}'{f' «{content}»' if content else ''}\n"
    elif msg_type == 'photo': # Фото без данных (не скачалось или лимит)
        log_entry += f"отправил(а) фото (не анализируется){f' «{content}»' if content else ''}\n"
    elif content: # Другие типы с подписью
        log_entry += f"отправил(а) медиа с подписью: «{content}» (тип: {msg_type})\n"
    else: # Другие типы без подписи
        log_entry += f"отправил(а) сообщение (тип: {msg_type})\n"

    return log_entry


def build_story_content(
    messages: List[Dict[str, Any]],
    images_data: Dict[str, bytes],
    genre_key: Optional[str] = 'default'
) -> Optional[PreparedContent]:
    """
    Собирает полный контент (промпт + логи + изображения) для генерации истории.
    """
    if not messages:
        logger.info("Нет сообщений для построения контента истории.")
        return None

    try:
        # Сортировка сообщений
        valid_messages = [m for m in messages if isinstance(m, dict) and 'timestamp' in m]
        valid_messages.sort(key=lambda x: datetime.datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))
    except Exception as e:
        logger.warning(f"Не удалось отсортировать сообщения по времени ({e}). Используется исходный порядок.", exc_info=True)
        valid_messages = messages # Используем как есть, если сортировка не удалась

    content_parts: PreparedContent = []
    content_parts.append(get_story_initial_prompt(genre_key))

    image_counter = 0
    current_text_block = ""

    for msg in valid_messages:
        log_entry = None
        msg_type = msg.get('type')
        msg_file_unique_id = msg.get('file_unique_id')

        if msg_type == 'photo' and msg_file_unique_id and msg_file_unique_id in images_data:
            # Если есть текстовый блок, добавляем его перед изображением
            if current_text_block:
                content_parts.append(current_text_block.strip())
                current_text_block = ""

            image_counter += 1
            image_bytes = images_data[msg_file_unique_id]

            # Добавляем лог-запись для изображения
            log_entry = format_log_entry(msg, image_counter=image_counter)
            content_parts.append(log_entry.strip())

            # Добавляем само изображение (в виде словаря с байтами)
            content_parts.append({"mime_type": "image/jpeg", "data": image_bytes})

        else:
            # Форматируем запись для других типов сообщений и добавляем в текстовый блок
            log_entry = format_log_entry(msg)
            current_text_block += log_entry

    # Добавляем последний текстовый блок, если он не пустой
    if current_text_block:
        content_parts.append(current_text_block.strip())

    # Завершающая часть промпта
    content_parts.append(f"\n---------------------------------\nКОНЕЦ ЛОГА.\n\nТеперь, напиши историю дня в выбранном стиле:\n")

    # Проверка, что контент не пустой (кроме начального и конечного промпта)
    if len(content_parts) <= 2:
        logger.warning("Промпт для истории не содержит данных сообщений.")
        return None

    return content_parts


# --- Промпты для Саммари ---

def build_summary_content(messages: List[Dict[str, Any]]) -> Optional[PreparedContent]:
    """
    Собирает контент (промпт + текстовые логи) для генерации саммари.
    Изображения здесь не используются.
    """
    if not messages:
        logger.info("Нет сообщений для построения контента саммари.")
        return None

    try:
        # Сортировка сообщений
        text_messages = [
            m for m in messages
            if isinstance(m, dict) and m.get('timestamp') and m.get('type') == 'text' and m.get('content')
        ]
        text_messages.sort(key=lambda x: datetime.datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))
    except Exception as e:
        logger.warning(f"Не удалось отсортировать сообщения для саммари ({e}). Используется исходный порядок.", exc_info=True)
        # Попробуем взять все сообщения, если сортировка текстовых не удалась
        text_messages = [m for m in messages if isinstance(m, dict) and m.get('timestamp')]


    if not text_messages:
        logger.info("Не найдено текстовых сообщений для саммари.")
        return None

    content_parts: PreparedContent = []
    initial_prompt = (
        "Проанализируй следующий лог текстовых сообщений из Telegram-чата. "
        "Создай **краткую выжимку (summary)** основных тем, вопросов, решений или событий. "
        "Используй **Markdown** для форматирования:\n"
        # "- Придумай и используй **жирный заголовок** для выжимки (например, `**Главное за период**`).\n" # Заголовок мы формируем сами
        "- Используй абзацы (двойной перенос строки) для читаемости.\n"
        "- Упоминай пользователей по их *именам* курсивом (например, `*Анна*`).\n"
        "- НЕ цитируй текст дословно, а пересказывай суть.\n"
        "- Игнорируй приветствия, флуд и малозначительные сообщения.\n"
        "- Выжимка должна быть лаконичной и информативной.\n\n"
        "ЛОГ ТЕКСТОВЫХ СООБЩЕНИЙ:\n"
        "---------------------------------\n"
    )
    content_parts.append(initial_prompt)

    log_block = ""
    for msg in text_messages:
        # Используем ту же функцию форматирования, но только для текста
        log_block += format_log_entry(msg)

    content_parts.append(log_block.strip())

    # Завершающая часть промпта
    content_parts.append(f"\n---------------------------------\nКОНЕЦ ЛОГА.\n\nТеперь, напиши краткую выжимку (summary) обсуждений:\n")

    # Промпт сам по себе уже не пустой, если есть initial_prompt и final_prompt
    return content_parts