# jobs.py
import logging
import asyncio
import datetime
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError, NetworkError, BadRequest
from typing import Dict, List, Any, Optional, Tuple
import html

# Импорты проекта
import data_manager as dm
import gemini_client as gc
from config import (
    BOT_OWNER_ID, SCHEDULE_HOUR, SCHEDULE_MINUTE, JOB_CHECK_INTERVAL_MINUTES,
    # PURGE_JOB_INTERVAL_HOURS не используется напрямую здесь, интервал берется из main.py
)
from localization import get_text, get_chat_lang, get_output_format_name # Добавили get_output_format_name
from utils import download_images, MAX_PHOTOS_TO_ANALYZE, notify_owner

logger = logging.getLogger(__name__)

# ==================================
# ЗАДАЧА ГЕНЕРАЦИИ СВОДОК (ИСТОРИИ/ДАЙДЖЕСТЫ)
# ==================================
async def daily_story_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Периодически проверяет чаты и генерирует истории/дайджесты по расписанию
    на основе сообщений за последние 24 часа до запланированного времени.
    """
    application: Optional[Application] = context.application
    if not application:
        logger.critical("Missing Application object in job context!")
        return
    bot: Optional[Bot] = context.bot
    if bot is None:
        logger.error("Bot object missing in job context!")
        return

    job_start_time = datetime.datetime.now(pytz.utc)
    job_name = context.job.name if context.job else "daily_story_job"
    application.bot_data[f'last_{job_name}_run_time'] = job_start_time
    application.bot_data[f'last_{job_name}_error'] = None
    current_errors: List[Tuple[int, str, Optional[BaseException]]] = []

    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username or f"Bot{bot_info.id}"
    except Exception as e:
        bot_username = "UnknownBot"
        logger.error(f"Failed get bot name: {e}")

    logger.info(f"[{bot_username}] Running {job_name}...")
    enabled_chat_ids = dm.get_enabled_chats()
    if not enabled_chat_ids:
        logger.info(f"[{bot_username}] No enabled chats found for generation.")
        return

    now_utc = job_start_time
    current_hour_utc = now_utc.hour
    current_minute_utc = now_utc.minute

    # Округляем текущее время до ближайшего интервала проверки
    current_minute_rounded = (current_minute_utc // JOB_CHECK_INTERVAL_MINUTES) * JOB_CHECK_INTERVAL_MINUTES
    logger.debug(f"UTC {now_utc:%H:%M}, checking for tasks ~{current_hour_utc:02d}:{current_minute_rounded:02d}")

    chats_to_process = []
    for chat_id in enabled_chat_ids:
        settings = dm.get_chat_settings(chat_id)
        target_hour_utc, target_minute_utc = SCHEDULE_HOUR, SCHEDULE_MINUTE
        custom_time_utc_str = settings.get('custom_schedule_time')
        if custom_time_utc_str:
            try:
                th, tm = map(int, custom_time_utc_str.split(':'))
                target_hour_utc, target_minute_utc = th, tm
            except (ValueError, TypeError):
                logger.warning(f"[Chat {chat_id}] Invalid time format '{custom_time_utc_str}'. Using default.")
        # Сравниваем текущее округленное время с целевым временем UTC
        if current_hour_utc == target_hour_utc and current_minute_rounded == target_minute_utc:
            chats_to_process.append(chat_id)
            logger.debug(f"[Chat {chat_id}] Scheduled time matched.")

    if not chats_to_process:
        logger.info("No chats due for processing now.")
        return
    logger.info(f"Chats to process now ({len(chats_to_process)}): {chats_to_process}")

    # --- Начало основного цикла обработки чатов ---
    processed_in_this_run = 0
    for chat_id in chats_to_process:
        current_chat_log_prefix = f"[{bot_username}][Chat {chat_id}]"
        logger.info(f"{current_chat_log_prefix} Processing scheduled generation...")
        output_sent = False
        chat_lang = await get_chat_lang(chat_id)
        error_for_owner: Optional[Tuple[str, Optional[BaseException]]] = None
        settings = dm.get_chat_settings(chat_id) # Получаем настройки чата ОДИН РАЗ

        try:
            # --- ОПРЕДЕЛЕНИЕ ПЕРИОДА В 24 ЧАСА ---
            target_hour_utc, target_minute_utc = SCHEDULE_HOUR, SCHEDULE_MINUTE
            custom_time_utc_str = settings.get('custom_schedule_time')
            if custom_time_utc_str:
                try:
                    th, tm = map(int, custom_time_utc_str.split(':'))
                    target_hour_utc, target_minute_utc = th, tm
                except (ValueError, TypeError):
                    pass # Ошибка уже логировалась при проверке времени, используем дефолт

            # Создаем datetime объект для целевого времени UTC СЕГОДНЯ
            target_dt_utc = job_start_time.replace(
                hour=target_hour_utc, minute=target_minute_utc, second=0, microsecond=0
            )

            # Рассчитываем время начала периода (24 часа ДО целевого времени)
            since_dt_utc = target_dt_utc - datetime.timedelta(hours=24)
            logger.debug(f"{current_chat_log_prefix} Fetching messages since {since_dt_utc.isoformat()}")

            # --- ПОЛУЧЕНИЕ СООБЩЕНИЙ ЗА ПЕРИОД ---
            messages = dm.get_messages_for_chat_since(chat_id, since_dt_utc)

            if not messages:
                logger.info(f"{current_chat_log_prefix} No messages found since {since_dt_utc.isoformat()}, skipping.")
                continue # Переходим к следующему чату

            logger.info(f"{current_chat_log_prefix} Found {len(messages)} messages since {since_dt_utc.isoformat()}.")

            # Получаем остальные настройки формата, жанра, личности
            output_format = settings.get('output_format', DEFAULT_OUTPUT_FORMAT)
            chat_genre = settings.get('story_genre', 'default')
            personality_key = settings.get('story_personality', DEFAULT_PERSONALITY)
            output_format_name = get_output_format_name(output_format, chat_lang)
            logger.info(f"{current_chat_log_prefix} Format: {output_format}, Genre: {chat_genre}, Personality: {personality_key}")

            # Скачиваем изображения (только для формата 'story')
            downloaded_images = {}
            if output_format == 'story':
                 downloaded_images = await download_images(context, messages, chat_id)

            # Генерируем результат
            output_text, error_msg_friendly = await gc.safe_generate_output(
                messages, downloaded_images, output_format, chat_genre, personality_key, chat_lang
            )

            # --- Обработка и отправка результата ---
            if output_text:
                try:
                    # Определяем дату для заголовка
                    try:
                        chat_tz_str = settings.get('timezone', 'UTC')
                        chat_tz = pytz.timezone(chat_tz_str)
                        target_dt_local = target_dt_utc.astimezone(chat_tz)
                        date_str = target_dt_local.strftime("%d %B %Y")
                    except Exception:
                         date_str = target_dt_utc.strftime("%d %B %Y") # Fallback to UTC date

                    photo_note_str = get_text("photo_info_text", chat_lang, count=len(downloaded_images)) if downloaded_images else ""
                    chat_title_str = str(chat_id)
                    try:
                        chat_info = await bot.get_chat(chat_id)
                        chat_title_str = f"'{html.escape(chat_info.title)}'" if chat_info.title else str(chat_id)
                    except Exception as e_chat:
                        logger.warning(f"{current_chat_log_prefix} Could not get chat title: {e_chat}")

                    header_loc_key = "daily_story_header"
                    final_message_header = get_text(
                        header_loc_key, chat_lang,
                        output_format_name_capital=get_output_format_name(output_format, chat_lang, capital=True),
                        date_str=date_str, chat_title=chat_title_str, photo_info=photo_note_str
                    )
                    await bot.send_message(chat_id=chat_id, text=final_message_header, parse_mode=ParseMode.HTML)
                    await asyncio.sleep(0.2)

                    # Отправка тела и кнопок
                    sent_message = None
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("👍", callback_data="feedback_good_placeholder"),
                        InlineKeyboardButton("👎", callback_data="feedback_bad_placeholder")
                    ]])
                    MAX_MSG_LEN = 4096
                    parts = [output_text[i:i+MAX_MSG_LEN] for i in range(0, len(output_text), MAX_MSG_LEN)]
                    if len(parts) > 1:
                         logger.warning(f"{current_chat_log_prefix} Output too long ({len(output_text)} chars), splitting into {len(parts)} parts.")

                    for k, part in enumerate(parts):
                        current_reply_markup = keyboard if k == len(parts) - 1 else None
                        sent_message = await bot.send_message(chat_id=chat_id, text=part, reply_markup=current_reply_markup, parse_mode=ParseMode.MARKDOWN)
                        if k < len(parts) - 1: await asyncio.sleep(0.5) # Небольшая пауза между частями

                    if sent_message: # Обновляем ID в кнопках последней части
                        kb_upd = InlineKeyboardMarkup([[
                            InlineKeyboardButton("👍", callback_data=f"feedback_good_{sent_message.message_id}"),
                            InlineKeyboardButton("👎", callback_data=f"feedback_bad_{sent_message.message_id}")
                        ]])
                        try:
                            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=sent_message.message_id, reply_markup=kb_upd)
                        except BadRequest: # Игнорируем ошибку, если сообщение не изменилось
                            pass
                        except TelegramError as e:
                            logger.warning(f"Error updating feedback buttons: {e}")

                    logger.info(f"{current_chat_log_prefix} {output_format_name.capitalize()} sent successfully for the last 24h.")
                    output_sent = True

                    # Отправляем примечание от прокси, если оно есть
                    if error_msg_friendly:
                        try:
                            await bot.send_message(chat_id=chat_id, text=get_text("proxy_note", chat_lang, note=error_msg_friendly), parse_mode=ParseMode.HTML)
                        except Exception as e:
                            logger.warning(f"{current_chat_log_prefix} Failed send proxy note: {e}")

                except TelegramError as e: # Ошибка отправки в Telegram
                    logger.error(f"{current_chat_log_prefix} TG error sending output: {e}")
                    error_for_owner = (f"TG Send Err ({e.__class__.__name__})", e)
                    error_str = str(e).lower()
                    is_fatal = any(sub in error_str for sub in ["blocked", "deactivated", "kicked", "forbidden", "not found"])
                    if is_fatal:
                        logger.warning(f"{current_chat_log_prefix} Disabling chat due to fatal TG error: {e}.")
                        dm.update_chat_setting(chat_id, 'enabled', False)
                        error_for_owner = (f"Disabled: Fatal TG Err ({e.__class__.__name__})", e)
                except Exception as e:
                    logger.exception(f"{current_chat_log_prefix} Unexpected error during sending output: {e}")
                    error_for_owner = (f"Send Err ({e.__class__.__name__})", e)

            else: # Ошибка генерации (output_text is None)
                logger.warning(f"{current_chat_log_prefix} Failed generate {output_format} for the last 24h. Reason: {error_msg_friendly}")
                error_for_owner = (f"Gen Err ({error_msg_friendly or 'Unknown'})", None)
                # Отправляем уведомление пользователю в чат
                error_text_chat = get_text("daily_job_failed_chat_user_friendly", chat_lang, output_format_name=output_format_name, reason=error_msg_friendly or 'неизвестной')
                try:
                    await bot.send_message(chat_id=chat_id, text=error_text_chat, parse_mode=ParseMode.HTML)
                except TelegramError as e_err:
                    logger.warning(f"{current_chat_log_prefix} Failed send failure notification to chat: {e_err}")
                    error_for_owner = (f"Gen Err + Failed Notify ({e_err.__class__.__name__})", e_err) # Обновляем ошибку для владельца
                    error_str = str(e_err).lower()
                    is_fatal = any(sub in error_str for sub in ["blocked", "deactivated", "kicked", "forbidden", "not found"])
                    if is_fatal:
                        logger.warning(f"{current_chat_log_prefix} Disabling chat after fatal TG error on sending failure notification: {e_err}.")
                        dm.update_chat_setting(chat_id, 'enabled', False)
                        error_for_owner = (f"Disabled: Fatal TG Err ({e_err.__class__.__name__})", e_err)
                except Exception as e_notify:
                    logger.exception(f"{current_chat_log_prefix} Unexpected error sending failure notification: {e_notify}")
                    error_for_owner = (f"Gen Err + Notify Err ({e_notify.__class__.__name__})", e_notify)

        except Exception as e: # Глобальная ошибка обработки чата
            logger.exception(f"{current_chat_log_prefix} CRITICAL error processing chat for the last 24h: {e}")
            error_for_owner = (f"Critical Err ({e.__class__.__name__})", e)

        # --- Запись результатов и ошибок ---
        if output_sent:
            processed_in_this_run += 1
        elif error_for_owner:
            current_errors.append((chat_id, error_for_owner[0], error_for_owner[1]))
            logger.warning(f"{current_chat_log_prefix} Error occurred: {error_for_owner[0]}")
        else:
            # Сюда попадаем, если не было сообщений за 24ч или генерация/отправка не удалась без записи явной ошибки
            logger.warning(f"{current_chat_log_prefix} Output not sent (check previous logs for reason - e.g., no messages or non-critical generation/send issue).")

        await asyncio.sleep(1.5) # Небольшая пауза между обработкой чатов
    # --- Конец цикла по чатам ---

    job_end_time = datetime.datetime.now(pytz.utc)
    duration = job_end_time - job_start_time
    if current_errors:
        # Формируем сводку ошибок для владельца (только сообщения об ошибках)
        error_summary = "\n".join([f"Chat {cid}: {msg}" for cid, msg, _ in current_errors])
        application.bot_data[f'last_{job_name}_error'] = error_summary # Записываем последнюю (или первую) ошибку
        await notify_owner(
            bot=bot,
            message=f"Errors during {job_name}:\n{error_summary[:1000]}...", # Ограничиваем длину сводки
            operation=job_name,
            important=True
        )
    logger.info(
        f"[{bot_username}] {job_name} finished in {duration.total_seconds():.2f}s. "
        f"Processed: {processed_in_this_run}/{len(chats_to_process)}. Errors: {len(current_errors)}."
    )


# ============================
# НОВАЯ ЗАДАЧА ОЧИСТКИ СТАРЫХ СООБЩЕНИЙ
# ============================
async def purge_old_messages_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодически удаляет старые сообщения согласно настройкам чатов."""
    application: Optional[Application] = context.application
    if not application:
        logger.critical("Application object not found in purge_job context! Aborting job.")
        return
    bot: Optional[Bot] = context.bot # Для логов и уведомлений

    # Идентификаторы задачи для логов и bot_data
    job_name = context.job.name if context.job else "purge_job"
    job_start_time = datetime.datetime.now(pytz.utc)

    # Записываем время старта и сбрасываем ошибку для ЭТОЙ задачи
    application.bot_data[f'last_{job_name}_run_time'] = job_start_time
    application.bot_data[f'last_{job_name}_error'] = None
    errors_count = 0
    processed_chats_count = 0
    deleted_messages_total = 0

    bot_username = "PurgeJob" # Имя по умолчанию для логов
    if bot:
        try: bot_info = await bot.get_me(); bot_username = bot_info.username or f"Bot{bot_info.id}"
        except Exception: pass

    logger.info(f"[{bot_username}] Running {job_name}...")
    try:
        # Получаем список чатов с установленным сроком хранения
        chats_to_purge = dm.get_chats_with_retention()
        if not chats_to_purge:
            logger.info(f"[{bot_username}] No chats found with retention policy set for purging.")
            return

        logger.info(f"[{bot_username}] Found {len(chats_to_purge)} chats to check for purging.")
        for chat_id, days in chats_to_purge:
            log_prefix = f"[{bot_username}][Chat {chat_id}]"
            if days <= 0: # Пропускаем невалидные значения на всякий случай
                logger.warning(f"{log_prefix} Invalid retention period ({days} days), skipping purge.")
                continue

            logger.info(f"{log_prefix} Purging messages older than {days} days...")
            try:
                # Вызываем функцию удаления из data_manager
                deleted_count = dm.delete_messages_older_than(chat_id, days) # Предполагаем, что она возвращает кол-во
                if deleted_count > 0:
                    deleted_messages_total += deleted_count
                processed_chats_count += 1
            except Exception as e:
                logger.error(f"{log_prefix} Error purging messages (>{days} days): {e}", exc_info=True)
                errors_count += 1
                # Сохраняем первую ошибку для отчета владельцу
                if not application.bot_data.get(f'last_{job_name}_error'):
                    application.bot_data[f'last_{job_name}_error'] = f"Chat {chat_id} (>{days}d): {e.__class__.__name__}"
                # Продолжаем со следующим чатом
            await asyncio.sleep(0.5) # Пауза между чатами для снижения нагрузки

        # Логирование итогов
        job_end_time = datetime.datetime.now(pytz.utc)
        duration = job_end_time - job_start_time
        logger.info(
            f"[{bot_username}] {job_name} finished in {duration.total_seconds():.2f}s. "
            f"Checked: {len(chats_to_purge)}. Processed OK: {processed_chats_count}. "
            f"Total deleted: {deleted_messages_total}. Errors: {errors_count}."
        )
        # Уведомляем владельца, если были ошибки
        if errors_count > 0 and application.bot_data.get(f'last_{job_name}_error'):
             await notify_owner(bot=bot, message=f"Errors during {job_name}: {application.bot_data[f'last_{job_name}_error']} (see logs for details)", operation=job_name, important=True)

    except Exception as e: # Глобальная ошибка в задаче
        logger.exception(f"[{bot_username}] CRITICAL error in {job_name}: {e}")
        application.bot_data[f'last_{job_name}_error'] = f"Critical: {e.__class__.__name__}"
        # Уведомляем владельца
        await notify_owner(bot=bot, message=f"Critical error in {job_name}", exception=e, important=True)