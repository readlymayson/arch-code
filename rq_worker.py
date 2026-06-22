#!/usr/bin/env python3
"""RQ-воркер для arch-code.

Слушает очередь 'coding_tasks' в Redis, забирает задачи
и выполняет их через worker.execute_coding_task_sync().

Запуск:
    python rq_worker.py

Или через RQ CLI (после настройки PYTHONPATH):
    rq worker coding_tasks --url redis://localhost:6379/0
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Добавляем корень arch-code в sys.path, чтобы RQ мог импортировать worker
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Загружаем .env, если есть
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from loguru import logger
from redis import Redis
from rq import Worker, Queue


def _setup_logger() -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message}",
    )


def get_redis_url() -> str:
    """Получить URL Redis из окружения или использовать дефолт."""
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", 6379))
    db = int(os.getenv("REDIS_DB", 0))
    return os.getenv("REDIS_URL", f"redis://{host}:{port}/{db}")


def start_worker() -> int:
    """Запустить RQ-воркер для очереди coding_tasks."""
    _setup_logger()

    redis_url = get_redis_url()
    logger.info(f"=== Запуск фонового воркера кода (arch-code) ===")
    logger.info(f"Redis: {redis_url}")
    logger.info(f"Очередь: coding_tasks")
    logger.info(f"Модуль worker: {PROJECT_ROOT / 'worker.py'}")

    try:
        redis_conn = Redis.from_url(redis_url, decode_responses=False)
        redis_conn.ping()
        logger.info("Подключение к Redis установлено")
    except Exception as exc:
        logger.error(f"Не удалось подключиться к Redis ({redis_url}): {exc}")
        return 1

    queues = [Queue("coding_tasks", connection=redis_conn)]

    worker = Worker(queues, connection=redis_conn, name="arch-code-worker")
    logger.info(f"Воркер запущен. Ожидание задач в очереди 'coding_tasks'...")
    worker.work(with_scheduler=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(start_worker())
