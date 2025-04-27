# =============================================================================
# ФАЙЛ: prompt_builder.py
# (Версия с детализированными промптами для форматов и личностей)
# =============================================================================
import logging
import datetime
from typing import List, Dict, Union, Optional, Any

# Импортируем необходимые константы и словари из конфига
from config import (
    SUPPORTED_GENRES, SUPPORTED_PERSONALITIES, DEFAULT_PERSONALITY,
    DEFAULT_OUTPUT_FORMAT, SUPPORTED_OUTPUT_FORMATS
)

logger = logging.getLogger(__name__)

# Типы для ясности
ContentPart = Union[str, Dict[str, Any]] # Текст или словарь с mime_type/data (bytes или base64)
PreparedContent = List[ContentPart]

# ===========================================
# Промпты для Ежедневной Истории / Дайджеста
# ===========================================

def get_output_initial_prompt(
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    genre_key: Optional[str] = 'default',
    personality_key: str = DEFAULT_PERSONALITY
) -> str:
    """
    Генерирует комплексный начальный промпт для истории или дайджеста,
    учитывая формат, жанр и личность.
    """

    # 1. Определение Инструкции по Личности
    personality_instruction = ""
    personality_closing = "" # Текст, который нужно добавить в САМЫЙ КОНЕЦ ответа ИИ

    if personality_key == 'wise':
        personality_instruction = (
            "Ты Мудрый Старец-летописец. Используй спокойный, вдумчивый, немного философский тон. "
            "Избегай жаргона и суеты. Твоя цель - найти смысл или урок даже в обыденном."
        )
        personality_closing = "\n\n_(Мысли Летописца: )_" # ИИ должен дополнить после двоеточия

    elif personality_key == 'sarcastic':
        personality_instruction = (
            "Ты Саркастичный Наблюдатель-летописец. Твой тон - сухая ирония, направленная на **общие человеческие слабости и паттерны поведения**, видимые в чате (например, обсуждение еды, повторяющиеся споры, прокрастинация), **а не на личности или конкретные мнения**. "
            "**НИКОГДА НЕ ОСКОРБЛЯЙ И НЕ УНИЖАЙ ПОЛЬЗОВАТЕЛЕЙ.** Твоя цель - вызвать легкую усмешку узнавания, а не обидеть. "
            "Избегай прямых оценок. Твоя ирония должна быть тонкой."
        )
        # Просим добавить в конце ОДНУ фразу общего характера
        personality_closing = "\n\n_(Заметка на полях: )_" # ИИ должен дополнить после двоеточия

    elif personality_key == 'poet':
        personality_instruction = (
            "Ты Поэт-Романтик, ведущий летопись чата. Используй возвышенный, метафоричный язык. "
            "Обращай внимание на эмоции, красоту момента (даже в онлайне), эфемерность общения. "
            "Твоя задача - передать не только факты, но и поэтическую атмосферу дня."
        )
        personality_closing = "\n\n~~~\n\n~~~\n" # ИИ должен вставить пару строк между тильдами

    elif personality_key == 'neutral':
        personality_instruction = "Ты Нейтральный Летописец чата. Придерживайся объективного, информативного тона."
    else: # Fallback
        personality_instruction = "Ты Летописец чата."


    # 2. Определение Инструкции по Формату и Жанру
    format_instruction = ""
    use_photos = True # По умолчанию используем фото

    if output_format == 'story':
        genre_name = SUPPORTED_GENRES.get(genre_key or 'default', 'Стандартный')
        genre_specific_instruction = ""
        # Детализация для жанров
        if genre_key == 'default':
            genre_specific_instruction = "Напиши связную и интересную **историю событий** в чате (1-3 абзаца)."
        elif genre_key == 'humor':
            genre_specific_instruction = "Напиши **юмористическую историю** дня (1-3 абзаца). Найди и подчеркни забавные моменты, диалоги, недоразумения. Цель - вызвать улыбку."
        elif genre_key == 'detective':
            genre_specific_instruction = "Представь события дня как **детективную загадку** или расследование (1-3 абзаца). Намекни на 'улики', 'подозреваемых' (в шутку!), 'главную тайну дня'."
        elif genre_key == 'fantasy':
            genre_specific_instruction = "Напиши **фэнтезийную историю** дня (1-3 абзаца). Придай событиям и обсуждениям волшебный или эпический оттенок, используй метафоры из фэнтези (квесты, артефакты, гильдии и т.д.)."
        elif genre_key == 'news_report':
            genre_specific_instruction = "Составь **краткий новостной репортаж** о событиях дня (2-4 абзаца). Будь объективен, используй формальный стиль, цитаты (перефразированные!), упомяни ключевых 'спикеров'."
        else: # Fallback genre
             genre_specific_instruction = f"Напиши связную **историю событий** дня в чате в {genre_name.lower()} стиле (1-3 абзаца)."

        format_instruction = (
            f"{genre_specific_instruction}\n"
            f"Используй **Markdown**: придумай **жирный заголовок** истории, используй абзацы. Упоминай *имена* пользователей курсивом. Не цитируй дословно, а **пересказывай суть**. "
            f"Кратко опиши 1-2 ключевых изображения ([IMAGE N]), если они важны, и кто их отправил. Опиши общую атмосферу (активность, настроение). **Основывайся строго на логе!**"
        )
    elif output_format == 'digest':
        format_instruction = (
            "Создай **информативный дайджест (список)** основных событий, тем обсуждений и принятых решений за день.\n"
            "Используй **Markdown**: Начни с краткого вступления (1 предложение). Далее используй **список с маркерами (`- `)** для каждого пункта дайджеста. "
            "В пунктах выделяй `**ключевые слова**` жирным и *имена* курсивом. Кратко упоминай [IMAGE N], если они были значимы для пункта."
            "Будь лаконичен, фокусируйся на сути. В конце сделай общий вывод об активности/настроении дня (1 предложение)."
        )
        # use_photos = False # Для дайджеста можно отключить анализ фото для экономии
    else: # Fallback format
        format_instruction = "Создай краткую сводку событий дня в Markdown."

    # 3. Сборка Финального Промпта
    initial_prompt = (
        f"{personality_instruction} "
        f"Твоя задача — проанализировать лог сообщений (и изображения [IMAGE N], если требуется) за период и подготовить сводку.\n"
        f"{format_instruction}\n\n"
        f"ЛОГ СООБЩЕНИЙ{ ' И ИЗОБРАЖЕНИЙ' if use_photos else '' } ЗА ПЕРИОД:\n"
        f"---------------------------------\n"
    )

    # Возвращаем сам промпт и ожидаемое завершение для него
    return initial_prompt, personality_closing

# ===========================================
# Форматирование Записи Лога (Без Изменений)
# ===========================================
def format_log_entry(msg: Dict[str, Any], image_counter: Optional[int] = None, image_placeholder: str = "[IMAGE {count}]") -> str:
    """Форматирует одну запись лога для включения в промпт."""
    try:
        dt_utc = datetime.datetime.fromisoformat(msg.get('timestamp','').replace('Z', '+00:00'))
        # Конвертация в Московское время для наглядности в логе для ИИ (можно убрать или сделать опцией)
        try:
            ts_str = dt_utc.astimezone(pytz.timezone('Europe/Moscow')).strftime('%H:%M MSK')
        except Exception:
             ts_str = dt_utc.strftime('%H:%M UTC')
    except Exception:
        ts_str = "??:??"

    user = msg.get('username', 'Неизвестный')
    content = msg.get('content', '')
    msg_type = msg.get('type')
    file_name = msg.get('file_name')

    log_entry = f"[{ts_str}] *{user}*: "

    if msg_type == 'photo' and image_counter is not None:
        log_entry += f"отправил(а) изображение {image_placeholder.format(count=image_counter)}{f' с подписью: «{content}»' if content else ''}\n"
    elif msg_type == 'text' and content:
        log_entry += f"написал(а): \"{content[:150]}{'...' if len(content)>150 else ''}\"\n"
    elif msg_type == 'video':
        log_entry += f"отправил(а) видео{f' «{content}»' if content else ''} (содержание не анализируется)\n"
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
    elif msg_type == 'photo': # Фото без данных (например, превышен лимит анализа)
        log_entry += f"отправил(а) фото (не анализируется){f' «{content}»' if content else ''}\n"
    elif content: # Другие типы с подписью
        log_entry += f"отправил(а) медиа с подписью: «{content}» (тип: {msg_type})\n"
    else: # Другие типы без подписи/содержания
        log_entry += f"отправил(а) медиа/сообщение (тип: {msg_type})\n"

    return log_entry

# ================================================
# Сборка Контента для Ежедневной Сводки
# ================================================
def build_content(
    messages: List[Dict[str, Any]],
    images_data: Dict[str, bytes],
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    genre_key: Optional[str] = 'default',
    personality_key: str = DEFAULT_PERSONALITY
) -> Optional[PreparedContent]:
    """
    Собирает полный контент (промпт + логи + изображения) для генерации
    истории или дайджеста с учетом всех настроек.
    """
    if not messages:
        logger.info("Нет сообщений для сборки контента.")
        return None

    # Сортировка сообщений (важно для последовательности лога)
    try:
        valid_messages = [m for m in messages if isinstance(m, dict) and 'timestamp' in m]
        valid_messages.sort(key=lambda x: datetime.datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))
    except Exception as e:
        logger.warning(f"Не удалось отсортировать сообщения ({e}). Используется исходный порядок.", exc_info=True)
        valid_messages = messages

    # Получаем начальный промпт и ожидаемое завершение
    initial_prompt, personality_closing = get_output_initial_prompt(output_format, genre_key, personality_key)
    content_parts: PreparedContent = [initial_prompt]
    image_counter = 0
    current_text_block = ""

    # Формируем тело лога с изображениями
    for msg in valid_messages:
        log_entry = None
        msg_type = msg.get('type')
        msg_file_unique_id = msg.get('file_unique_id')
        # Вставляем изображение, если оно есть и требуется для формата
        # TODO: Добавить проверку use_photos (возвращать из get_output_initial_prompt?)
        if msg_type == 'photo' and msg_file_unique_id and msg_file_unique_id in images_data:
            if current_text_block:
                content_parts.append(current_text_block.strip())
                current_text_block = ""
            image_counter += 1
            image_bytes = images_data[msg_file_unique_id]
            log_entry = format_log_entry(msg, image_counter=image_counter) # Форматируем с placeholder [IMAGE N]
            content_parts.append(log_entry.strip()) # Добавляем текст лога
            content_parts.append({"mime_type": "image/jpeg", "data": image_bytes}) # Добавляем сами байты картинки
        else:
            # Форматируем запись для других типов или фото без данных
            log_entry = format_log_entry(msg)
            current_text_block += log_entry

    # Добавляем последний текстовый блок лога, если он есть
    if current_text_block:
        content_parts.append(current_text_block.strip())

    # Завершающая часть промпта
    final_instruction = "Теперь, выполни свою задачу как Летописец."
    # Добавляем ожидаемое закрытие для личности ИИ (например, '(Мысли: ...)')
    if personality_closing:
        final_instruction += f" Не забудь добавить финальную заметку в твоем стиле ({personality_closing})."

    content_parts.append(f"\n---------------------------------\nКОНЕЦ ЛОГА.\n\n{final_instruction}\n")

    if len(content_parts) <= 2: # Проверяем, что кроме стартового и финального промпта что-то есть
        logger.warning("Промпт для сводки не содержит данных сообщений.")
        return None

    return content_parts

# ================================================
# Сборка Контента для Команды /summarize
# ================================================
def build_summary_content(messages: List[Dict[str, Any]]) -> Optional[PreparedContent]:
    """
    Собирает контент для генерации простого саммари (для команды /summarize).
    Использует только текст, без личностей/жанров, формат Markdown.
    """
    if not messages:
        logger.info("Нет сообщений для сборки /summarize контента.")
        return None
    try:
        # Фильтруем и сортируем только текстовые сообщения
        text_messages = [ m for m in messages if isinstance(m, dict) and m.get('timestamp') and m.get('type') == 'text' and m.get('content')]
        if not text_messages:
             logger.info("Не найдено текстовых сообщений для /summarize.")
             return None
        text_messages.sort(key=lambda x: datetime.datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))
    except Exception as e:
        logger.warning(f"Не удалось отсортировать сообщения для /summarize ({e}).", exc_info=True)
        return None # Возвращаем None при ошибке сортировки

    content_parts: PreparedContent = []
    # Простой промпт для /summarize
    initial_prompt = (
        "Проанализируй лог текстовых сообщений из чата. "
        "Создай **краткую выжимку** основных тем, вопросов или событий. "
        "Используй **СТРОГО MarkdownV2** для форматирования:\n"
        "- Для *курсива* используй ТОЛЬКО `_вот так_` (например, для имен `_Анна_`). Не используй `*` для курсива!\n"
        "- Для **жирного** используй ТОЛЬКО `*вот так*` (например, `*важно*`).\n"
        # "- Для `моноширинного` используй ``текст``." # Пока не просим
        "- Для списков используй `-` в начале строки.\n"
        "- Используй ДВА ПЕРЕНОСА строки (`\\n\\n`) для разделения абзацев.\n"
        "- **Критически важно**: НЕ используй символы `.`, `!`, `-`, `+`, `(`, `)`, `#` внутри *курсива* или **жирного** текста. Эти символы нужно выносить за пределы форматирования или экранировать обратным слешем (напр., `\\!`).\n" # Строгие правила V2
        "- Пересказывай суть, не цитируй дословно.\n"
        "- Игнорируй флуд и приветствия.\n\n"
        "ЛОГ СООБЩЕНИЙ:\n"
        "---------------------------------\n"
    )
    content_parts.append(initial_prompt)

    # Добавляем только текст сообщений в лог
    log_block = "".join(format_log_entry(msg) for msg in text_messages)
    content_parts.append(log_block.strip())

    # Финальная инструкция
    content_parts.append(f"\n---------------------------------\nКОНЕЦ ЛОГА.\n\nНапиши краткую выжимку обсуждений в Markdown:\n")

    return content_parts

# ================================================
# Сборка Контента для Вмешательств
# ================================================
def build_intervention_prompt(
    recent_messages_texts: List[str],
    personality_key: str = DEFAULT_PERSONALITY
) -> Optional[str]:
    """Генерирует промпт для короткого комментария-вмешательства."""
    if not recent_messages_texts: return None

    personality_instruction = ""
    if personality_key == 'wise': personality_instruction = "Ты Мудрый Старец."
    elif personality_key == 'sarcastic': personality_instruction = "Ты Саркастичный Наблюдатель." # Правила безопасности для сарказма в другом месте не нужны, т.к. коммент короткий и не отвечает
    elif personality_key == 'poet': personality_instruction = "Ты Поэт-Романтик."
    else: personality_instruction = "Ты Нейтральный Наблюдатель." # Важно: "Наблюдатель"

    log_preview = "\n".join([f"- «{text[:80].strip()}{'…' if len(text)>80 else ''}»" for text in recent_messages_texts])

    prompt = (
        f"{personality_instruction} "
        f"Ниже последние сообщения чата:\n{log_preview}\n\n"
        f"Твоя задача: Напиши **ОДНУ ОЧЕНЬ КОРОТКУЮ (макс. 10-15 слов)** фразу-наблюдение в твоем стиле, **вдохновленную** этими сообщениями. "
        f"**ЗАПРЕЩЕНО**: Отвечать кому-либо, задавать вопросы, напрямую комментировать последнее сообщение, пытаться продолжить диалог, использовать Markdown."
        f"Твоя фраза должна быть **отстраненной заметкой на полях**, мыслью вслух."
        f"Пример (Мудрый): 'Время утекает, а слова остаются...'."
        f"Пример (Саркастичный): 'Кажется, обсуждение достигло пика продуктивности.'."
        f"Пример (Поэт): 'Словно листья на ветру, мысли кружат в этом чате...'."
        f"Пример (Нейтральный): 'Обсуждение продолжается.'."
        f"\nТвоя фраза-наблюдение:" # Оставляем для AI чтобы он написал только саму фразу
    )
    return prompt