"""Атомарный запуск LangGraph-цикла генерации кода (Phase B).

Ядро Phase B: перед запуском ИИ синхронизирует проект в sandbox,
агент читает/пишет файлы через FileManagementTools,
Docker тестирует весь проект, на выходе — список изменённых файлов.

Используется:
  - RQ-воркером для фоновой обработки
  - напрямую из main.py и chat.py
  - из ai-core (OrderExecutor._run_arch_code) через asyncio.to_thread,
    т.е. в НЕ главном потоке — регистрация signal.signal там невозможна
"""

import asyncio
import os
import signal
import subprocess
import sys
import threading
import uuid
from typing import Any, Dict, Optional

from loguru import logger

from graph_worker import coding_graph
from tools.error_alerter import send_error_alert
from tools.file_tools import RSYNC_EXCLUDE_PATTERNS


# ── Флаг graceful shutdown ──────────────────────────────────────
_shutdown_requested = False


def _register_sigterm_handler() -> None:
    """Зарегистрировать обработчик SIGTERM (graceful shutdown).

    signal.signal() можно вызывать ТОЛЬКО в главном потоке интерпретатора.
    При вызове из asyncio.to_thread (ai-core OrderExecutor) поток не главный,
    и signal.signal() бросит ValueError — это не критично: при недоступном
    обработчике просто полагаемся на дефолтное поведение (SIGTERM → выход).
    """
    try:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _handle_sigterm)
        else:
            logger.debug(
                "SIGTERM handler пропущен: вызов не из главного потока"
            )
    except (ValueError, TypeError):
        logger.debug("SIGTERM handler пропущен: сигналы недоступны в этом потоке")


def _handle_sigterm(signum, frame):
    """Обработчик SIGTERM — устанавливает флаг для graceful shutdown.

    RQ посылает SIGTERM при stop-job, даёт ~1-2 секунды до SIGKILL.
    Флаг проверяется в try/finally, finally успевает выполниться.

    Вызывается только из главного потока (см. _register_sigterm_handler).
    """
    global _shutdown_requested
    _shutdown_requested = True
    # Восстанавливаем дефолтный обработчик — повторный SIGTERM/SIGKILL
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except (ValueError, TypeError):
        pass


# ── Job meta helper ──────────────────────────────────────────────

def _update_job_meta(**updates):
    """Обновить job.meta для текущей задачи (step tracking).

    Вызывается из execute_coding_task_sync для отправки прогресса
    в Redis, который будет прочитан get_task_status() в ai-core.
    Также отправляет heartbeat для Watchdog (ai-core), чтобы тот
    не убивал живую задачу по таймауту heartbeat.
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
        _send_watchdog_heartbeat(job.id)
    except Exception:
        logger.warning("Job meta update failed", exc_info=True)


_HEARTBEAT_PREFIX = "watchdog:heartbeat:"
_last_heartbeat_ts: float = 0.0


def _send_watchdog_heartbeat(task_id: str, *, interval: float = 15.0) -> None:
    """Отправить heartbeat в Redis для Watchdog (ai-core).

    Watchdog убивает задачи, у которых нет свежего heartbeat дольше
    HEARTBEAT_TIMEOUT (120 сек). Без heartbeat живая задача на шаге
    explore/execute (когда LLM отвечает дольше 2 минут) будет убита.

    Heartbeat отправляется не чаще чем раз в `interval` секунд.
    """
    global _last_heartbeat_ts
    now = __import__("time").time()
    if now - _last_heartbeat_ts < interval:
        return  # throttle
    _last_heartbeat_ts = now
    try:
        from redis import Redis
        r = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        key = f"{_HEARTBEAT_PREFIX}{task_id}"
        # TTL = 240с (2 * HEARTBEAT_TIMEOUT) — если воркер умер, ключ истекёт
        r.setex(key, 240, str(now))
        r.close()
    except Exception:
        logger.debug("Watchdog heartbeat send failed", exc_info=True)


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

    # Каталоги, которые НИКОГДА не должны попадать в changed_files:
    # Docker-песочница пишет сюда pip-зависимости (pip --target=/app/.deps).
    # Без фильтра 10+ тысяч файлов пакетов попадают в результат →
    # build_zip держит их содержимое в памяти → OOM-kill воркера.
    _EXCLUDED_DIFF_DIRS = {".deps", "node_modules", "__pycache__", ".venv", "venv"}

    def _diff_is_excluded(rel_path: str) -> bool:
        parts = rel_path.replace("\\", "/").split("/")
        return any(p in _EXCLUDED_DIFF_DIRS for p in parts)

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

            # Пропускаем служебные каталоги (pip-зависимости и т.п.)
            if _diff_is_excluded(filename):
                continue

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
                except Exception as exc:
                    logger.warning(
                        f"compute_sandbox_diff: не удалось прочитать {filename}: {exc}"
                    )

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
    """Единый формат результата для очереди.

    Для статусов "error" и "failed" отправляет алерт об ошибке
    (tools/error_alerter.send_error_alert), если webhook настроен.
    Алертинг graceful: сбой отправки не влияет на результат.
    """
    result = {
        "status": status,        # "success" | "failed" | "error"
        "task_id": task_id,
        **kwargs,
    }
    if status in ("error", "failed"):
        try:
            err = kwargs.get("error") or kwargs.get("error_traceback") or ""
            send_error_alert(task_id=task_id, error=err)
        except Exception as alert_exc:
            logger.warning(f"Алерт об ошибке задачи {task_id} не отправлен: {alert_exc}")
    return result


# ── Синхронная версия (ядро) ─────────────────────────────────────

def execute_coding_task_sync(
    task_description: str,
    task_id: Optional[str] = None,
    test_code: Optional[str] = None,
    project_dir: Optional[str] = None,
    skip_smoke_test: bool = False,
) -> Dict[str, Any]:
    """Атомарный запуск цикла генерации кода (синхронная версия).

    Args:
        task_description: ТЗ для инженера-программиста.
        task_id: Уникальный ID (если не задан — генерируется).
        test_code: Опциональный тестовый скрипт (node:test).
        project_dir: Путь к проекту для копирования в sandbox.
        skip_smoke_test: True → пропустить smoke-проверку приложения
            (для микро-задач без точки входа main.py — экономия 15-20 сек).

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
    #    Только в главном потоке — при вызове из ai-core (asyncio.to_thread)
    #    поток не главный, signal.signal() там запрещён (ValueError).
    # ═══════════════════════════════════════════════════════════
    global _shutdown_requested
    _shutdown_requested = False
    _register_sigterm_handler()

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
            ["git", "commit", "-m", "initial state before agent", "--allow-empty"],
            cwd=sandbox_dir, check=True, capture_output=True, timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        stderr_detail = exc.stderr.decode(errors="replace") if exc.stderr else "(нет stderr)"
        stdout_detail = exc.stdout.decode(errors="replace") if exc.stdout else "(нет stdout)"
        return _make_result(
            "error",
            actual_task_id,
            error=(
                f"git commit failed (exit code {exc.returncode}): "
                f"stdout={stdout_detail}, stderr={stderr_detail}"
            ),
        )
    except Exception as exc:
        return _make_result(
            "error",
            actual_task_id,
            error=f"Не удалось инициализировать git в sandbox: {exc}",
        )

    # ── try/finally гарантирует очистку ресурсов ──────────────
    try:

        # ═══════════════════════════════════════════════════════
        # 1d. TDD: генерация тестов по ТЗ (до реализации)
        #     Активирует модуль tools/test_generator.py (ранее не подключён).
        #     Тесты пишутся в tests/test_generated_{task_id}.py, попадают
        #     в changed_files и запускаются run_tests в Docker.
        #     Graceful: при сбое генерации — пустая строка, граф работает без TDD.
        # ═══════════════════════════════════════════════════════
        _update_job_meta(current_step="tdd", progress=8, iteration=0)
        if not test_code:
            try:
                from tools.test_generator import generate_tests_for_task
                generated_tests = generate_tests_for_task(
                    task=task_description,
                    sandbox_dir=sandbox_dir,
                    task_id=actual_task_id,
                )
                if generated_tests:
                    _update_job_meta(
                        current_step="tdd",
                        progress=9,
                        tdd_generated=True,
                        tdd_tests=len(generated_tests),
                    )
            except Exception as tdd_exc:
                logger.warning(f"TDD: ошибка активации генератора тестов: {tdd_exc}")

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
            # Run Verifier
            "app_type": "",
            "skip_smoke_test": skip_smoke_test,
            "health_endpoint": "/health",
            "health_port": 8000,
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

        # ── Фоновый heartbeat-тред ─────────────────────────────
        # Пока coding_graph.invoke() работает (LLM отвечает долго),
        # шлём heartbeat в Redis каждые 15 сек, чтобы Watchdog в ai-core
        # не убил живую задачу по таймауту heartbeat (120 сек).
        _hb_stop = threading.Event()

        def _heartbeat_loop():
            while not _hb_stop.is_set():
                _send_watchdog_heartbeat(actual_task_id, interval=10.0)
                _hb_stop.wait(10)

        _hb_thread = threading.Thread(
            target=_heartbeat_loop, daemon=True, name=f"hb-{actual_task_id}"
        )
        _hb_thread.start()

        try:
            final_state = coding_graph.invoke(initial_state)
        except Exception as exc:
            return _make_result(
                "error",
                actual_task_id,
                error=f"Критический сбой графа: {exc}",
            )
        finally:
            _hb_stop.set()
            if _hb_thread.is_alive():
                _hb_thread.join(timeout=2)

        # Обновляем прогресс после графа
        iterations = final_state.get("iterations", 0)
        # Рассчитываем CU cost из токенов
        pt = final_state.get("prompt_tokens", 0)
        ct = final_state.get("completion_tokens", 0)
        # deepseek-v4-flash: ~$0.15/M input, ~$0.60/M output
        cu_cost = (pt * 0.15 + ct * 0.60) / 1_000_000

        _update_job_meta(
            current_step="compute_diff", progress=90, iteration=iterations,
            thought_steps=final_state.get("thought_steps", []),
            action_steps=final_state.get("action_steps", []),
            chain_of_thought=final_state.get("chain_of_thought", ""),
            prompt_tokens=pt,
            completion_tokens=ct,
            model=final_state.get("model", "deepseek/deepseek-v4-flash"),
            cu_cost=cu_cost,
        )

        # ═══════════════════════════════════════════════════════
        # 3. Вычисление git diff после работы агента
        # ═══════════════════════════════════════════════════════

        changed_files = compute_sandbox_diff(sandbox_dir)

        # ── Очистка __pycache__ из sandbox перед фиксацией результата ──
        try:
            import shutil as _shutil
            for _root, _dirs, _files in os.walk(sandbox_dir):
                if "__pycache__" in _dirs:
                    _shutil.rmtree(os.path.join(_root, "__pycache__"), ignore_errors=True)
                    _dirs[:] = [d for d in _dirs if d != "__pycache__"]
        except Exception as exc:
            logger.warning(f"Ошибка очистки __pycache__ в sandbox: {exc}")

        # ── Валидация синтаксиса изменённых Python-файлов ────────────
        validation_errors = []
        for entry in changed_files:
            if entry["status"] in ("added", "modified") and entry["path"].endswith(".py"):
                file_path = os.path.join(sandbox_dir, entry["path"])
                if os.path.isfile(file_path):
                    try:
                        import py_compile
                        py_compile.compile(file_path, doraise=True)
                    except py_compile.PyCompileError as e:
                        validation_errors.append({"path": entry["path"], "error": str(e)})

        if validation_errors:
            logger.warning(
                f"⚠️ Синтаксические ошибки в {len(validation_errors)} файлах:"
            )
            for ve in validation_errors:
                logger.warning(f"   • {ve['path']}: {ve['error']}")

        sandbox_path = f"sandbox/{actual_task_id}/"

        _update_job_meta(current_step="done", progress=100)

        # ═══════════════════════════════════════════════════════
        # 4. Сборка deliverable (zip) ДО очистки sandbox
        #    Использует changed_files[].content — работает даже после
        #    удаления песочницы (cleanup в finally).
        # ═══════════════════════════════════════════════════════
        deliverable_path = None
        try:
            from tools.deliverable_builder import build_zip

            deliverable_path = build_zip(
                task_id=actual_task_id,
                changed_files=changed_files,
                output_dir=os.path.join(PROJECT_ROOT, "deliverables"),
                task_description=task_description,
                summary=(
                    final_state.get("chain_of_thought", "")[:500]
                    if final_state.get("success")
                    else "Решение не завершено полностью (см. лог)."
                ),
            )
        except Exception as zip_exc:
            logger.warning(f"Deliverable: не удалось собрать архив: {zip_exc}")

        if final_state.get("success"):
            return _make_result(
                "success",
                actual_task_id,
                iterations=final_state.get("iterations"),
                code=final_state.get("code", ""),
                changed_files=changed_files,
                generated_files_dir=sandbox_path,
                deliverable_path=deliverable_path,
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

            # Алерт об ошибке задачи (если настроен ERROR_WEBHOOK_URL)
            try:
                from tools.error_alerter import send_error_alert
                send_error_alert(actual_task_id, err_msg)
            except Exception as alert_exc:
                logger.warning(f"Алерт не отправлен для {actual_task_id}: {alert_exc}")

            _update_job_meta(
                error=err_msg,
                error_traceback=err_tb,
                current_step="done",
                progress=100,
                prompt_tokens=final_state.get("prompt_tokens", 0),
                completion_tokens=final_state.get("completion_tokens", 0),
                model=final_state.get("model", "deepseek/deepseek-v4-flash"),
                thought_steps=final_state.get("thought_steps", []),
                action_steps=final_state.get("action_steps", []),
                chain_of_thought=final_state.get("chain_of_thought", ""),
            )
            return _make_result(
                "failed",
                actual_task_id,
                iterations=final_state.get("iterations"),
                changed_files=changed_files,
                deliverable_path=deliverable_path,
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
            logger.warning("Pub/sub notification failed", exc_info=True)

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
        logger.warning(f"Redis pub/sub notification failed for {task_id}", exc_info=True)  # fallback polling в ai-core подхватит


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
        logger.warning(f"Ошибка очистки Docker для {task_id}: {exc}")

    # Удаляем sandbox-директорию
    if sandbox_dir and os.path.exists(sandbox_dir):
        try:
            import shutil
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception as exc:
            logger.warning(f"Ошибка удаления sandbox {task_id}: {exc}")

    # Отмечаем очистку в job.meta
    try:
        _update_job_meta(cleanup_completed=True)
    except Exception:
        logger.debug("Cleanup meta update failed (non-critical)")


# ── async-обёртка для ai-core (чтобы не блокировать event loop) ──

async def execute_coding_task(
    task_description: str,
    task_id: Optional[str] = None,
    test_code: Optional[str] = None,
    skip_smoke_test: bool = False,
) -> Dict[str, Any]:
    """async-версия — запускает синхронную функцию в thread pool."""
    return await asyncio.to_thread(
        execute_coding_task_sync,
        task_description=task_description,
        task_id=task_id,
        test_code=test_code,
        skip_smoke_test=skip_smoke_test,
    )
