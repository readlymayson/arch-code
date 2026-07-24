"""Атомарный запуск LangGraph-цикла генерации кода (Phase B).

Ядро Phase B: перед запуском ИИ синхронизирует проект в sandbox,
агент читает/пишет файлы через FileManagementTools,
Docker тестирует весь проект, на выходе — список изменённых файлов.

Используется:
  - RQ-воркером для фоновой обработки
  - напрямую из main.py и chat.py
"""

import asyncio
import os
import signal
import subprocess
import sys
import uuid
from typing import Any, Dict, Optional

from graph_worker import coding_graph
from tools.file_tools import RSYNC_EXCLUDE_PATTERNS


# ── Флаг graceful shutdown ──────────────────────────────────────
_shutdown_requested = False


def _handle_sigterm(signum, frame):
    """Обработчик SIGTERM — устанавливает флаг для graceful shutdown.

    RQ посылает SIGTERM при stop-job, даёт ~1-2 секунды до SIGKILL.
    Флаг проверяется в try/finally, finally успевает выполниться.
    """
    global _shutdown_requested
    _shutdown_requested = True
    # Восстанавливаем дефолтный обработчик — повторный SIGTERM/SIGKILL
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


# ── Job meta helper ──────────────────────────────────────────────

def _update_job_meta(**updates):
    """Обновить job.meta для текущей задачи (step tracking).

    Вызывается из execute_coding_task_sync для отправки прогресса
    в Redis, который будет прочитан get_task_status() в ai-core.
    """
    try:
        from rq.job import get_current_job
        job = get_current_job()
        if job is None:
            return
        meta = dict(job.meta or {})
        meta.update(updates)
        meta["_updated_at"] = __import__("time").time()
        job.meta = meta
        job.save_meta()
    except Exception:
        pass  # Job meta — опционально, не должно ломать воркер


# ── Константы ────────────────────────────────────────────────────

# Корень проекта arch-code (там же лежит sandbox/)
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))

# Реальный проект, который будем клонировать в песочницу
# По умолчанию — ai-core (рядом в /home/dev/projects/ai-core)
DEFAULT_SOURCE_PROJECT = os.path.normpath(os.path.join(PROJECT_ROOT, "..", "ai-core"))


# ── Синхронизация проекта в песочницу ────────────────────────────

def sync_project_to_sandbox(task_id: str, source_dir: str | None = None) -> str:
    """Скопировать проект в sandbox/{task_id}/ через rsync.

    Исключает .env, node_modules, venv, __pycache__, .git, logs и т.д.

    Args:
        task_id: Уникальный ID задачи.
        source_dir: Путь к исходному проекту (по умолчанию ai-core).

    Returns:
        sandbox_dir: Абсолютный путь к песочнице.
    """
    source = source_dir or DEFAULT_SOURCE_PROJECT
    sandbox_dir = os.path.join(PROJECT_ROOT, "sandbox", task_id)

    # Создаём целевую папку
    os.makedirs(sandbox_dir, exist_ok=True)

    # Формируем --exclude для rsync
    exclude_args = []
    for pattern in RSYNC_EXCLUDE_PATTERNS:
        exclude_args.extend(["--exclude", pattern])

    cmd = ["rsync", "-a", "--quiet"] + exclude_args + [source + "/", sandbox_dir + "/"]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"rsync превысил таймаут (120 с) при копировании {source}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"rsync не удался: {e.stderr.decode() if e.stderr else e}")

    return sandbox_dir


# ── Git diff в песочнице ─────────────────────────────────────────

def compute_sandbox_diff(sandbox_dir: str) -> list[dict]:
    """Вычислить изменения в песочнице относительно git.

    Предполагает, что git уже инициализирован и есть коммит (initial state).
    Сравнивает HEAD с текущим состоянием рабочей директории.

    ВАЖНО: В результат добавляется поле 'content' — содержимое новых/изменённых
    файлов из sandbox. Это позволяет ai-core создавать патч даже после удаления
    sandbox (cleanup вызывается в finally).

    Returns:
        Список словарей:
        [{"path": "core/billing_manager.py", "diff": "@@...", "status": "added",
          "content": "полное содержимое файла"}, ...]
    """
    timeout = 30

    try:
        # Добавляем и проверяем unfitted-файлы
        subprocess.run(
            ["git", "add", "-A"],
            cwd=sandbox_dir, check=True, capture_output=True,
            timeout=timeout,
        )

        # Статус в staging (индекс) — после git add -A
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=sandbox_dir, check=True, capture_output=True, text=True,
            timeout=timeout,
        )

        changed = []
        for line in status_result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # Формат: "XY filename"
            # X — статус в staging (индекс), Y — статус в working tree
            # После git add -A: X=A/M/D, Y=пусто
            x_status = line[0:1]  # первый символ — статус в staging
            filename = line[3:].strip()

            if x_status == "A":
                status = "added"
            elif x_status == "M":
                status = "modified"
            elif x_status == "D":
                status = "deleted"
            elif line[:2].strip() == "??":
                status = "added"
            else:
                status = "modified"

            # diff относительно HEAD, ИЗ ИНДЕКСА (staged)
            # После git add -A все изменения в staging, а working tree пуст.
            # Без --cached diff будет пустым!
            diff_result = subprocess.run(
                ["git", "diff", "--cached", "HEAD", "--no-color", "--", filename],
                cwd=sandbox_dir, capture_output=True, text=True,
                timeout=timeout,
            )

            # Читаем содержимое файла из sandbox (для create_transactional_patch)
            file_path = os.path.join(sandbox_dir, filename)
            content = None
            if status != "deleted" and os.path.isfile(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    pass

            diff_text = diff_result.stdout[:5000] if diff_result.stdout else ""
            if not diff_text and status == "added" and content is not None:
                diff_text = f"(new file)\n{content[:2000]}"

            entry = {
                "path": filename,
                "status": status,
                "diff": diff_text or "(нет diff)",
            }
            if content is not None:
                entry["content"] = content

            changed.append(entry)

        return changed

    except FileNotFoundError:
        return []
    except Exception as e:
        return [{"path": "_error_", "status": "error", "diff": str(e)}]


# ── Результат ────────────────────────────────────────────────────

def _make_result(
    status: str,
    task_id: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Единый формат результата для очереди."""
    return {
        "status": status,        # "success" | "failed" | "error"
        "task_id": task_id,
        **kwargs,
    }


# ── Синхронная версия (ядро) ─────────────────────────────────────

def execute_coding_task_sync(
    task_description: str,
    task_id: Optional[str] = None,
    test_code: Optional[str] = None,
    project_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Атомарный запуск цикла генерации кода (синхронная версия).

    Args:
        task_description: ТЗ для инженера-программиста.
        task_id: Уникальный ID (если не задан — генерируется).
        test_code: Опциональный тестовый скрипт (node:test).
        project_dir: Путь к проекту для копирования в sandbox.

    Returns:
        Dict с ключами:
            status: "success" | "failed" | "error"
            task_id: str
            iterations: int
            changed_files: list[dict] — изменённые файлы (Phase B)
            generated_files_dir: str — путь к sandbox
            log: str — описание
            error: str — описание ошибки
    """

    # ═══════════════════════════════════════════════════════════
    # 0. Регистрируем обработчик SIGTERM (graceful shutdown)
    # ═══════════════════════════════════════════════════════════
    global _shutdown_requested
    _shutdown_requested = False
    signal.signal(signal.SIGTERM, _handle_sigterm)

    # ═══════════════════════════════════════════════════════════
    # 1. Инициализация
    # ═══════════════════════════════════════════════════════════

    actual_task_id = task_id or uuid.uuid4().hex[:12]
    sandbox_dir = ""

    # ═══════════════════════════════════════════════════════════
    # 1b. Синхронизация проекта в песочницу
    # ═══════════════════════════════════════════════════════════

    try:
        _update_job_meta(current_step="sync", progress=5, iteration=0)
        sandbox_dir = sync_project_to_sandbox(actual_task_id, project_dir)
    except Exception as exc:
        return _make_result(
            "error",
            actual_task_id,
            error=f"Не удалось скопировать проект в sandbox: {exc}",
        )

    # ═══════════════════════════════════════════════════════════
    # 1c. Инициализация git в песочнице (до работы агента)
    #     Нужно для compute_sandbox_diff() — чтобы diff считался
    #     относительно исходного состояния, а не всех 133 файлов.
    # ═══════════════════════════════════════════════════════════
    try:
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=sandbox_dir, check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "config", "user.email", "arch-code@ai.local"],
            cwd=sandbox_dir, check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "config", "user.name", "Arch Code Agent"],
            cwd=sandbox_dir, check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=sandbox_dir, check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial state before agent"],
            cwd=sandbox_dir, check=True, capture_output=True, timeout=30,
        )
    except Exception as exc:
        return _make_result(
            "error",
            actual_task_id,
            error=f"Не удалось инициализировать git в sandbox: {exc}",
        )

    # ── try/finally гарантирует очистку ресурсов ──────────────
    try:

        initial_state = {
            "task_id": actual_task_id,
            "sandbox_dir": sandbox_dir,
            "project_dir": project_dir or DEFAULT_SOURCE_PROJECT,
            "task": task_description,
            "code": "",
            "test_code": test_code or "",
            "test_passed": False,
            "error": "",
            "iterations": 0,
            "success": False,
            "changed_files": [],
            "thought_steps": [],
            "action_steps": [],
            "chain_of_thought": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "model": "deepseek/deepseek-v4-flash",
        }

        # ═══════════════════════════════════════════════════════
        # 2. Запуск графа с step tracking
        # ═══════════════════════════════════════════════════════

        _update_job_meta(current_step="explore", progress=10, iteration=1)

        try:
            final_state = coding_graph.invoke(initial_state)
        except Exception as exc:
            return _make_result(
                "error",
                actual_task_id,
                error=f"Критический сбой графа: {exc}",
            )

        # Обновляем прогресс после графа
        iterations = final_state.get("iterations", 0)
        _update_job_meta(
            current_step="compute_diff", progress=90, iteration=iterations,
            thought_steps=final_state.get("thought_steps", []),
            action_steps=final_state.get("action_steps", []),
            chain_of_thought=final_state.get("chain_of_thought", ""),
            prompt_tokens=final_state.get("prompt_tokens", 0),
            completion_tokens=final_state.get("completion_tokens", 0),
            model=final_state.get("model", "deepseek/deepseek-v4-flash"),
        )

        # ═══════════════════════════════════════════════════════
        # 3. Вычисление git diff после работы агента
        # ═══════════════════════════════════════════════════════

        changed_files = compute_sandbox_diff(sandbox_dir)

        sandbox_path = f"sandbox/{actual_task_id}/"

        _update_job_meta(current_step="done", progress=100)

        if final_state.get("success"):
            return _make_result(
                "success",
                actual_task_id,
                iterations=final_state.get("iterations"),
                code=final_state.get("code", ""),
                changed_files=changed_files,
                generated_files_dir=sandbox_path,
                log=f"Код успешно сгенерирован. Изменено файлов: {len(changed_files)}.",
            )
        else:
            # Сохраняем ошибку в meta, чтобы task_state мог её прочитать
            # даже если RQ result не сохранился (result_ttl истёк)
            err_msg = final_state.get(
                "error",
                "Превышено максимальное число итераций (3) без успеха.",
            )
            err_tb = final_state.get("error_traceback", "")
            _update_job_meta(
                error=err_msg,
                error_traceback=err_tb,
                current_step="done",
                progress=100,
            )
            return _make_result(
                "failed",
                actual_task_id,
                iterations=final_state.get("iterations"),
                changed_files=changed_files,
                error=err_msg,
                error_traceback=err_tb,
            )

    finally:
        # ═══════════════════════════════════════════════════════
        # Публикация в Pub/Sub: уведомляем ai-core о завершении
        # ═══════════════════════════════════════════════════════
        # ВАЖНО: публикуем ДО очистки sandbox, чтобы ai-core
        # успел прочитать файлы для create_transactional_patch()
        try:
            _publish_result_notification(actual_task_id)
        except Exception:
            pass  # не критично, ai-core подхватит fallback polling

        # ═══════════════════════════════════════════════════════
        # Очистка ресурсов (гарантированно выполняется)
        # ═══════════════════════════════════════════════════════
        _cleanup_resources(actual_task_id, sandbox_dir)


def _publish_result_notification(task_id: str) -> None:
    """Опубликовать в Redis Pub/Sub, что задача завершена.

    ai-core подписан на канал "coding_tasks:results" и получит
    уведомление без polling.
    """
    try:
        from redis import Redis
        r = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        r.publish("coding_tasks:results", task_id)
        r.close()
    except Exception:
        pass  # fallback polling в ai-core подхватит


def _cleanup_resources(task_id: str, sandbox_dir: str) -> None:
    """Очистить Docker-контейнеры и sandbox-директорию.

    Вызывается из finally блока execute_coding_task_sync.
    Безопасно даже если некоторые ресурсы уже удалены.
    """
    try:
        from docker_manager import cleanup_containers

        # Останавливаем и удаляем Docker-контейнеры задачи
        cleanup_containers(task_id)
    except Exception as exc:
        print(f"worker: ошибка очистки Docker для {task_id}: {exc}", file=sys.stderr)

    # Удаляем sandbox-директорию
    if sandbox_dir and os.path.exists(sandbox_dir):
        try:
            import shutil
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception as exc:
            print(f"worker: ошибка удаления sandbox {task_id}: {exc}", file=sys.stderr)

    # Отмечаем очистку в job.meta
    try:
        _update_job_meta(cleanup_completed=True)
    except Exception:
        pass


# ── async-обёртка для ai-core (чтобы не блокировать event loop) ──

async def execute_coding_task(
    task_description: str,
    task_id: Optional[str] = None,
    test_code: Optional[str] = None,
) -> Dict[str, Any]:
    """async-версия — запускает синхронную функцию в thread pool."""
    return await asyncio.to_thread(
        execute_coding_task_sync,
        task_description=task_description,
        task_id=task_id,
        test_code=test_code,
    )
