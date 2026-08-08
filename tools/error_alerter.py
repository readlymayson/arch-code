"""Алертинг об ошибках задач arch-code.

Отправляет webhook-уведомление (Telegram / любой HTTP-эндпоинт)
при критическом сбое задачи. Конфигурация через .env:

    ERROR_WEBHOOK_URL=https://api.telegram.org/bot<TOKEN>/sendMessage
    ERROR_WEBHOOK_CHAT_ID=<chat_id>   (для Telegram)

Если ERROR_WEBHOOK_URL не задан — алертинг отключён (graceful).
"""
from __future__ import annotations

import os

import httpx
from loguru import logger

# Тело уведомления (по умолчанию — как отправит Telegram)
DEFAULT_WEBHOOK_URL = os.getenv("ERROR_WEBHOOK_URL", "")
DEFAULT_CHAT_ID = os.getenv("ERROR_WEBHOOK_CHAT_ID", "")


def _build_payload(text: str, chat_id: str) -> dict:
    """Сформировать payload для webhook.

    Если chat_id задан — предполагаем Telegram bot API
    (payload {chat_id, text, parse_mode}).
    Иначе — произвольный JSON {text}.
    """
    if chat_id:
        return {
            "chat_id": chat_id,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    return {"text": text[:4000]}


def send_error_alert(
    task_id: str,
    error: str,
    *,
    webhook_url: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Отправить алерт об ошибке задачи.

    Args:
        task_id: ID задачи.
        error: Текст ошибки (обычно из result["error"]).
        webhook_url: URL webhook (по умолчанию из .env ERROR_WEBHOOK_URL).
        chat_id: Chat ID для Telegram (по умолчанию из .env).

    Returns:
        True если отправлено (или алертинг отключён), False при ошибке.
    """
    url = webhook_url or DEFAULT_WEBHOOK_URL
    cid = chat_id if chat_id is not None else DEFAULT_CHAT_ID

    if not url:
        # Алертинг не настроен — не ошибка, просто пропускаем
        return True

    text = (
        f"❌ <b>arch-code: ошибка задачи</b>\n"
        f"<b>task_id:</b> <code>{task_id}</code>\n"
        f"<b>ошибка:</b>\n<pre>{error[:1500]}</pre>"
    )

    try:
        resp = httpx.post(url, json=_build_payload(text, cid), timeout=15.0)
        if resp.status_code != 200:
            logger.warning(f"Алерт не доставлен: HTTP {resp.status_code}: {resp.text[:300]}")
            return False
        logger.info(f"Алерт об ошибке задачи {task_id} отправлен")
        return True
    except httpx.HTTPError as exc:
        logger.warning(f"Ошибка отправки алерта для {task_id}: {exc}")
        return False
    except Exception as exc:
        logger.warning(f"Неожиданная ошибка алертинга для {task_id}: {exc}")
        return False
