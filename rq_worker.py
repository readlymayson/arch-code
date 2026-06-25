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
import traceback
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

    # ── Глобальный перехватчик необработанных исключений ────
    # Чтобы systemd видел не «exit code 1», а осмысленный трейсбек в stderr.
    def _global_exception_hook(exc_type, exc_value, exc_tb):
        import traceback
        logger.critical(
            "Необработанное исключение в воркере:\n"
            + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _global_exception_hook

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

    # ── Уникальное имя воркера ───────────────────────────────────
    # Включаем hostname + PID, чтобы исключить коллизии между
    # запусками (systemd restart, ручной kill, duplicate instance).
    import socket
    hostname = socket.gethostname()
    worker_name = f"arch-code-worker-{hostname}-{os.getpid()}"

    # ── Очищаем stale-воркеров с тем же шаблоном ─────────────────
    # Если предыдущий воркер упал жёстко (kill -9) и не успел
    # отписаться, его ключи висят в Redis. Новый воркер не сможет
    # зарегистрироваться под тем же именем — вычищаем все.
    try:
        stale_keys = redis_conn.keys("rq:worker:arch-code-worker*")
        if stale_keys:
            redis_conn.delete(*stale_keys)
            logger.warning(f"Очищено {len(stale_keys)} stale-ключей воркера из Redis")
    except Exception as exc:
        logger.warning(f"Не удалось очистить stale-ключи: {exc}")

    queues = [Queue("coding_tasks", connection=redis_conn)]

    worker = Worker(queues, connection=redis_conn, name=worker_name)

    # ── Перехватчик ошибок RQ (падение при выполнении задачи) ──
    def _exception_handler(job, exc_type, exc_value, exc_tb):
        logger.error(
            f"❌ Ошибка при выполнении задачи {job.id}: {exc_value}\n"
            + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        # Вызываем дефолтный обработчик RQ (чтобы задача ушла в failed)
        return True

    worker.push_exc_handler(_exception_handler)

    logger.info(f"Воркер '{worker_name}' запущен. Ожидание задач...")
    worker.work(with_scheduler=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(start_worker())
