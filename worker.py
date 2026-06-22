"""Атомарный запуск LangGraph-цикла генерации кода (Phase B).

Ядро Phase B: перед запуском ИИ синхронизирует проект в sandbox,
агент читает/пишет файлы через FileManagementTools,
Docker тестирует весь проект, на выходе — список изменённых файлов.

Используется:
  - RQ-воркером для фоновой обработки
  - напрямую из main.py и chat.py
"""

import os
import subprocess
import uuid
from typing import Any, Dict, Optional

from graph_worker import coding_graph
from tools.file_tools import RSYNC_EXCLUDE_PATTERNS


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

    Инициализирует временный git-репозиторий, делает add всех файлов,
    и возвращает список изменённых файлов с diff.

    Returns:
        Список словарей: [{"path": "adapters/vk.py", "diff": "...", "status": "modified"}, ...]
    """
    try:
        # Инициализируем git, если ещё нет
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=sandbox_dir, check=True, capture_output=True,
        )
        # Настраиваем user для коммита (чтобы git не ругался)
        subprocess.run(
            ["git", "config", "user.email", "arch-code@ai.local"],
            cwd=sandbox_dir, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Arch Code Agent"],
            cwd=sandbox_dir, check=True, capture_output=True,
        )

        # add всех файлов
        subprocess.run(
            ["git", "add", "-A"],
            cwd=sandbox_dir, check=True, capture_output=True,
        )

        # Статус (нужен для списка изменённых файлов)
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=sandbox_dir, check=True, capture_output=True, text=True,
        )

        changed = []
        for line in status_result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # Формат: "XY filename"
            xy = line[:2].strip()
            filename = line[3:].strip()

            if xy == "??":
                status = "added"
            elif xy in ("M", " M", "M "):
                status = "modified"
            elif xy in ("D", " D", "D "):
                status = "deleted"
            else:
                status = "changed"

            # diff для этого файла
            diff_result = subprocess.run(
                ["git", "diff", "--no-color", "--", filename],
                cwd=sandbox_dir, capture_output=True, text=True,
            )
            # Если файл новый — показываем его содержимое
            if not diff_result.stdout and status == "added":
                try:
                    with open(os.path.join(sandbox_dir, filename)) as f:
                        content = f.read()
                    diff_result.stdout = f"(new file)\n{content[:2000]}"
                except Exception:
                    pass

            changed.append({
                "path": filename,
                "status": status,
                "diff": diff_result.stdout[:5000] if diff_result.stdout else "(нет diff, возможно бинарный)",
            })

        return changed

    except FileNotFoundError:
        # git не установлен — возвращаем пустой список
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
    # 1. Инициализация
    # ═══════════════════════════════════════════════════════════

    actual_task_id = task_id or uuid.uuid4().hex[:12]
    sandbox_dir = ""

    # ═══════════════════════════════════════════════════════════
    # 1b. Синхронизация проекта в песочницу
    # ═══════════════════════════════════════════════════════════
    try:
        sandbox_dir = sync_project_to_sandbox(actual_task_id, project_dir)
    except Exception as exc:
        return _make_result(
            "error",
            actual_task_id,
            error=f"Не удалось скопировать проект в sandbox: {exc}",
        )

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
    }

    # ═══════════════════════════════════════════════════════════
    # 2. Запуск графа (синхронный)
    # ═══════════════════════════════════════════════════════════

    try:
        final_state = coding_graph.invoke(initial_state)
    except Exception as exc:
        return _make_result(
            "error",
            actual_task_id,
            error=f"Критический сбой графа: {exc}",
        )

    # ═══════════════════════════════════════════════════════════
    # 3. Вычисление git diff после работы агента
    # ═══════════════════════════════════════════════════════════

    changed_files = compute_sandbox_diff(sandbox_dir)

    sandbox_path = f"sandbox/{actual_task_id}/"

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
        return _make_result(
            "failed",
            actual_task_id,
            iterations=final_state.get("iterations"),
            changed_files=changed_files,
            error=final_state.get(
                "error",
                "Превышено максимальное число итераций (3) без успеха.",
            ),
        )


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
