#!/usr/bin/env python3
"""Playwright-скрейпер фриланс-бирж для Windows (ПК).

Запускает ExchangePlaywrightReceiver (ai-core) отдельным процессом на ПК
и публикует найденные заказы в Redis-канал (список) — ai-core на VPS
читает их (remote_mode) и передаёт в FreelanceOrderFilter.

Роль в новой архитектуре (2026-09-01):
- SPA-биржи (Kwork и др.) требуют реального браузера (Playwright) —
  на VPS это дорого/нестабильно, поэтому скрейпер переезжает на ПК.
- Заказы публикуются в Redis LIST `freelance:remote:orders` (LPUSH)
  в формате JSON (FreelanceOrder.to_json + raw_text + source meta).
- ai-core (VPS) в remote_mode читает список через LRANGE+LTRIM
  (ExchangePlaywrightReceiver, remote_list_key) и передаёт
  заказы в тот же конвейер (дедуп → ключевые слова → LLM).

Запуск:
    python playwright_worker.py            # все playwright-источники из конфига
    python playwright_worker.py --source kwork   # только Kwork
    python playwright_worker.py --once      # один проход (для отладки)

Зависимости:
    pip install playwright aiohttp beautifulsoup4 redis loguru python-dotenv
    playwright install chromium
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# ── Пути ────────────────────────────────────────────────────────
# Скрипт лежит в arch-code/ (ПК). ai-core — рядом (../ai-core).
PROJECT_ROOT = Path(__file__).resolve().parent
AI_CORE_ROOT = PROJECT_ROOT.parent / "ai-core"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(AI_CORE_ROOT))

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(AI_CORE_ROOT / ".env", override=False)

# ── Redis-ключи (общие с ai-core remote_mode) ───────────────────
# ВАЖНО: ключ LIST должен совпадать с remote_list_key в ai-core
# ExchangePlaywrightReceiver (core/freelance_exchange_scraper.py:930).
# См. также REMOTE_WINDOWS.md — там задокументирован этот же ключ.
REDIS_ORDERS_LIST = "freelance:remote:orders"   # LPUSH (VPS) / LRANGE+LTRIM (ai-core)
REDIS_ORDERS_CHANNEL = "freelance_exchange:orders:channel"  # Pub/Sub (оповещения)


def _setup_logger() -> None:
    # Windows: консоль по умолчанию cp1251 — переключаем на UTF-8
    # (иначе эмодзи/кириллица в логах → UnicodeEncodeError).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message}",
    )


def get_redis_url() -> str:
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", 6379))
    db = int(os.getenv("REDIS_DB", 0))
    return os.getenv("REDIS_URL", f"redis://{host}:{port}/{db}")


def _load_exchange_sources() -> list[dict]:
    """Загрузить exchange_sources из config.json (ai-core).

    Возвращает ТОЛЬКО источники с type == "playwright" (enabled=true).
    """
    cfg_path = AI_CORE_ROOT / "config" / "config.json"
    if not cfg_path.exists():
        logger.error(f"config.json не найден: {cfg_path}")
        return []

    try:
        import json as _json
        with open(cfg_path, encoding="utf-8") as f:
            cfg = _json.load(f)
        sources = (cfg.get("freelance_monitor") or {}).get("exchange_sources") or []
        pw_sources = [
            s for s in sources
            if s.get("type") == "playwright" and s.get("enabled", True)
        ]
        return pw_sources
    except Exception as exc:
        logger.error(f"Не удалось загрузить exchange_sources: {exc}")
        return []


async def _redis_publish_order(
    redis_conn, order_json: dict, raw_text: str, source: dict
) -> None:
    """Опубликовать заказ в Redis (list + channel)."""
    payload = {
        "title": order_json.get("title", ""),
        "description": order_json.get("description", ""),
        "budget": order_json.get("budget"),
        "currency": order_json.get("currency"),
        "deadline": order_json.get("deadline"),
        "url": order_json.get("url", ""),
        "source": order_json.get("source", ""),
        "tags": order_json.get("tags", []),
        "author_username": order_json.get("author_username"),
        "published_at": order_json.get("published_at"),
        "source_name": source.get("name") or order_json.get("source", ""),
        "raw_text": raw_text,
        "meta": {
            "worker": "windows-playwright",
            "host": os.uname().nodename if hasattr(os, "uname") else os.getenv("COMPUTERNAME", "pc"),
        },
    }
    try:
        await redis_conn.lpush(REDIS_ORDERS_LIST, json.dumps(payload, ensure_ascii=False))
        await redis_conn.publish(REDIS_ORDERS_CHANNEL, json.dumps(payload, ensure_ascii=False))
        logger.info(f"Заказ опубликован в Redis: {order_json.get('title', '')[:60]}")
    except Exception as exc:
        logger.warning(f"Не удалось опубликовать заказ в Redis: {exc}")


async def main() -> None:
    _setup_logger()
    parser = argparse.ArgumentParser(description="Playwright-скрейпер бирж (Windows)")
    parser.add_argument("--source", default="", help="Фильтр по имени источника (kwork, fl_ru)")
    parser.add_argument("--once", action="store_true", help="Один проход и выход (отладка)")
    parser.add_argument("--interval", type=int, default=600, help="Интервал опроса (сек)")
    args = parser.parse_args()

    sources = _load_exchange_sources()
    if args.source:
        sources = [s for s in sources if args.source.lower() in (s.get("name") or "").lower()]
    if not sources:
        logger.error(
            "Нет playwright-источников в config.json. "
            "Проверьте ai-core/config/config.json → freelance_monitor.exchange_sources"
        )
        return

    # Redis
    from redis import asyncio as redis_async
    redis_conn = None
    try:
        redis_conn = redis_async.from_url(get_redis_url(), decode_responses=True)
        await redis_conn.ping()
        logger.info(f"Redis подключён: {get_redis_url()}")
    except Exception as exc:
        logger.error(f"Redis недоступен ({get_redis_url()}): {exc}")
        return

    # Импортируем ресивер из ai-core
    try:
        from core.freelance_exchange_scraper import ExchangePlaywrightReceiver
    except ImportError as exc:
        logger.error(f"Не удалось импортировать ExchangePlaywrightReceiver: {exc}")
        logger.error("Убедитесь, что ai-core лежит рядом: ../ai-core")
        return

    receiver = ExchangePlaywrightReceiver(
        sources=sources,
        interval_sec=args.interval,
        max_orders_per_run=60,
        timeout_sec=30.0,
        headless=True,
        wait_timeout_ms=20000,
        max_retries=2,
    )

    # on_message → Redis
    async def _on_order(raw_text, source_id, message_link, chat_title, sender_username, **kwargs):
        """Заказ с биржи → Redis (для ai-core remote_mode)."""
        try:
            # source_id = URL заказа, chat_title = "kwork: Kwork (...)"
            src_name = (chat_title or "").split(":", 1)[0] or source_id or ""
            # Ищем полный source-конфиг для контекста
            src_cfg = next((s for s in sources if (s.get("name") or "").lower() in src_name.lower()), {})
            payload = {
                "title": raw_text.split("\n")[0].replace("📌 ", "") if raw_text else "",
                "description": raw_text,
                "url": source_id or "",
                "source": src_name.strip(),
                "raw_text": raw_text,
                "source_name": src_cfg.get("name", src_name),
                "meta": {"worker": "windows-playwright"},
            }
            await redis_conn.lpush(
                REDIS_ORDERS_LIST,
                json.dumps(payload, ensure_ascii=False),
            )
            await redis_conn.publish(
                REDIS_ORDERS_CHANNEL,
                json.dumps(payload, ensure_ascii=False),
            )
            logger.info(f"📦 Заказ → Redis: {raw_text.splitlines()[0][:60] if raw_text else '?'}")
        except Exception as exc:
            logger.warning(f"Ошибка публикации заказа: {exc}")

    receiver.on_message = _on_order

    # ── Валидация доступности источников ──
    status = await receiver.validate_chats()
    for name, ok in status.items():
        if ok:
            logger.info(f"✅ Источник доступен: {name}")
        else:
            logger.warning(f"⚠️ Источник НЕДОСТУПЕН: {name}")
    receiver.sources = [
        s for s in receiver.sources
        if status.get(s.get("name") or s.get("url"), True)
    ]
    if not receiver.sources:
        logger.error("Все playwright-источники недоступны — выход")
        return

    logger.info(
        f"=== Запуск Playwright-скрейпера (Windows) ===\n"
        f"Источники: {[s.get('name') for s in receiver.sources]}\n"
        f"Интервал: {args.interval}с | Redis: {get_redis_url()}"
    )

    if args.once:
        # Один проход
        await receiver._poll_once()
        await receiver.stop()
        logger.info("Одиночный проход завершён")
        return

    # Цикл
    try:
        await receiver.start()
        # Держим процесс живым
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Остановка скрейпера...")
    finally:
        await receiver.stop()
        if redis_conn:
            await redis_conn.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Скрейпер остановлен (Ctrl+C)")
