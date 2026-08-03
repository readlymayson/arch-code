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
import threading
import time
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


def _make_exception_handler(redis_conn):
    """Создать обработчик ошибок RQ для очистки ресурсов упавшей задачи.

    Возвращает функцию-обработчик, совместимую с RQ API.
    """
    def handler(job, exc_type, exc_value, exc_tb):
        logger.error(
            f"❌ Ошибка при выполнении задачи {job.id}: {exc_value}\n"
            + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        try:
            task_id = job.id
            meta = job.meta or {}
            if not meta.get("cleanup_completed"):
                logger.warning(f"Задача {task_id} упала без cleanup — чистим ресурсы")
                _force_cleanup_task(task_id)
                meta["cleanup_completed"] = True
                job.meta = meta
                job.save_meta()
        except Exception as clean_exc:
            logger.error(f"Fallback cleanup for {job.id} failed: {clean_exc}")
        return True
    return handler


def _force_cleanup_task(task_id: str) -> None:
    """Принудительная очистка ресурсов задачи (Docker + sandbox)."""
    from docker_manager import cleanup_containers, NodeSandbox

    try:
        cleanup_containers(task_id)
    except Exception:
        pass
    try:
        NodeSandbox(task_id).cleanup()
    except Exception:
        pass


def _cleanup_orphaned_jobs(redis_conn) -> None:
    """Сканировать завершённые задачи и дочистить orphan-ресурсы.

    Вызывается при старте воркера. Покрывает кейс kill -9,
    когда finally блок не выполнился.
    """
    from rq.job import Job

    for registry_key in (
        "rq:finished:coding_tasks",
        "rq:failed:coding_tasks",
        "rq:canceled:coding_tasks",
    ):
        try:
            raw_ids = redis_conn.zrange(registry_key, 0, -1)
            for raw in raw_ids:
                job_id = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                try:
                    job = Job.fetch(job_id, connection=redis_conn)
                    meta = job.meta or {}
                    if not meta.get("cleanup_completed"):
                        logger.info(f"Orphan cleanup: задача {job_id} без cleanup — чистим")
                        _force_cleanup_task(job_id)
                        meta["cleanup_completed"] = True
                        job.meta = meta
                        job.save_meta()
                except Exception:
                    pass
        except Exception as exc:
            logger.warning(f"Orphan scan for {registry_key}: {exc}")


def start_worker() -> int:
    """Запустить RQ-воркер для очереди coding_tasks."""
    _setup_logger()

    # ── Глобальный перехватчик необработанных исключений ────
    def _global_exception_hook(exc_type, exc_value, exc_tb):
        import traceback
        logger.critical(
            "Необработанное исключение в воркере:\n"
            + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        # Снимаем lock, если воркер падает с необработанным исключением.
        # Имена могут быть ещё не определены (исключение до инициализации lock) —
        # закрываем try/except.
        try:
            _lock_refresh_stop.set()
        except Exception:
            pass
        try:
            _release_worker_lock()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _global_exception_hook

    redis_url = get_redis_url()
    logger.info(f"=== Запуск фонового воркера кода (arch-code) ===")
    logger.info(f"Redis: {redis_url}")
    logger.info(f"Очередь: coding_tasks")

    try:
        redis_conn = Redis.from_url(redis_url, decode_responses=False)
        redis_conn.ping()
        logger.info("Подключение к Redis установлено")
    except Exception as exc:
        logger.error(f"Не удалось подключиться к Redis ({redis_url}): {exc}")
        return 1

    import socket
    hostname = socket.gethostname()
    worker_name = f"arch-code-worker-{hostname}-{os.getpid()}"

    # ── Redis SETNX lock: защита от дублирующихся воркеров ──────────
    # Атомарный setnx предотвращает запуск второго воркера при рестартах
    # systemd (когда старый процесс ещё не успел очистить rq:worker:* ключи).
    # TTL 30 секунд — если воркер жив дольше, lock обновляется в фоне.
    LOCK_KEY = "worker:arch-code:lock"
    LOCK_TTL = 30  # секунд
    acquired = redis_conn.set(LOCK_KEY, str(os.getpid()), nx=True, ex=LOCK_TTL)
    if not acquired:
        owner_pid = redis_conn.get(LOCK_KEY)
        owner_pid = owner_pid.decode("utf-8") if isinstance(owner_pid, bytes) else owner_pid
        logger.error(
            f"❌ Другой воркер arch-code уже активен (lock владеет PID={owner_pid}). "
            f"Выход. Если это stale-lock — удалите ключ {LOCK_KEY} вручную."
        )
        return 1

    def _release_worker_lock():
        """Снять lock, только если мы его владелец (сравнение по PID)."""
        try:
            current = redis_conn.get(LOCK_KEY)
            current = current.decode("utf-8") if isinstance(current, bytes) else current
            if current == str(os.getpid()):
                redis_conn.delete(LOCK_KEY)
                logger.info(f"Lock {LOCK_KEY} снят (PID {os.getpid()})")
        except Exception as exc:
            logger.warning(f"Не удалось снять lock {LOCK_KEY}: {exc}")

    # Фоновый тред продлевает TTL, пока воркер жив
    _lock_refresh_stop = threading.Event()

    def _lock_refresh_loop():
        while not _lock_refresh_stop.is_set():
            try:
                redis_conn.expire(LOCK_KEY, LOCK_TTL)
            except Exception:
                pass
            _lock_refresh_stop.wait(LOCK_TTL // 2)

    _lock_refresh_thread = threading.Thread(
        target=_lock_refresh_loop, daemon=True, name="worker-lock-refresh"
    )
    _lock_refresh_thread.start()
    logger.info(f"Воркер-лок {LOCK_KEY} захвачен (PID {os.getpid()}, TTL {LOCK_TTL}s)")

    # ── Очищаем stale-воркеров ─────────────────────────────────
    try:
        # Используем SCAN вместо KEYS для production-безопасности,
        # ищем все stale ключи воркеров arch-code
        stale_keys = []
        cursor = 0
        while True:
            cursor, batch = redis_conn.scan(
                cursor=cursor,
                match="rq:worker:arch-code-worker*",
                count=100,
            )
            stale_keys.extend(batch)
            if cursor == 0:
                break
        if stale_keys:
            redis_conn.delete(*stale_keys)
            logger.warning(f"Очищено {len(stale_keys)} stale-ключей воркера из Redis")
    except Exception as exc:
        logger.warning(f"Не удалось очистить stale-ключи: {exc}")

    queues = [Queue("coding_tasks", connection=redis_conn)]

    # ── Orphan cleanup: дочищаем ресурсы упавших задач ──────────
    try:
        _cleanup_orphaned_jobs(redis_conn)
    except Exception as exc:
        logger.warning(f"Orphan cleanup error: {exc}")

    # ── PID-файл для systemd / ручного контроля ──────────────────
    PID_FILE = Path(PROJECT_ROOT / "rq_worker.pid")
    if PID_FILE.exists():
        try:
            old_pid = PID_FILE.read_text().strip()
            logger.warning(f"Найден старый PID-файл: PID={old_pid}, удаляю")
            PID_FILE.unlink()
        except Exception:
            pass
    try:
        PID_FILE.write_text(str(os.getpid()))
        logger.info(f"PID {os.getpid()} записан в {PID_FILE}")
    except Exception as exc:
        logger.warning(f"Не удалось записать PID-файл: {exc}")

    import signal as _signal

    def _shutdown_handler(signum, frame):
        """Graceful shutdown: чистим Redis-ключи, lock и PID-файл."""
        logger.info(f"Получен сигнал {signum}, завершение воркера...")
        try:
            worker_key = f"rq:worker:{worker_name}"
            redis_conn.delete(worker_key)
            logger.info(f"Ключ воркера {worker_key} очищен")
        except Exception:
            pass
        _lock_refresh_stop.set()
        _release_worker_lock()
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass
        sys.exit(0)

    _signal.signal(_signal.SIGTERM, _shutdown_handler)
    _signal.signal(_signal.SIGINT, _shutdown_handler)

    # ── Пытаемся запустить воркер, при дублировании имени — retry ──
    max_attempts = 3
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                worker = Worker(queues, connection=redis_conn, name=worker_name)
                worker.push_exc_handler(_make_exception_handler(redis_conn))
                logger.info(f"Воркер '{worker_name}' запущен. Ожидание задач...")
                worker.work(with_scheduler=True)
                break  # успешно — выходим из цикла
            except ValueError as e:
                if "active worker" in str(e).lower():
                    logger.warning(
                        f"Попытка {attempt}/{max_attempts}: воркер '{worker_name}' уже активен. "
                        f"Пробую с новым именем..."
                    )
                    # Выбираем новое имя с увеличивающимся суффиксом
                    worker_name = f"arch-code-worker-{hostname}-{os.getpid()}-retry{attempt}"
                else:
                    raise
    finally:
        # Воркер завершился (worker.work вернулся или упал) — снимаем lock
        _lock_refresh_stop.set()
        _release_worker_lock()

    return 0


if __name__ == "__main__":
    raise SystemExit(start_worker())
