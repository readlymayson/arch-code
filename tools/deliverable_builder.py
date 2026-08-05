"""Deliverable Builder — сборка готового решения клиенту.

Упаковывает результат работы arch-code (changed_files[]) в zip-архив
с README. Использует содержимое из changed_files[].content, поэтому
работает даже после удаления sandbox (cleanup в worker.py).

Структура архива:
    {task_id}/
        README.md          — описание решения, стек, запуск
        ...                — файлы решения (из changed_files[].content)
"""
from __future__ import annotations

import os
import re
import zipfile
from typing import Any, Dict, List, Optional

from loguru import logger

# Папки/файлы, которые исключаем из deliverable
EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}
EXCLUDE_FILES = {".DS_Store", "*.pyc"}


def _safe_relpath(path: str) -> str:
    """Нормализовать относительный путь (защита от path traversal)."""
    norm = os.path.normpath(path)
    # Убираем ведущие слэши и ".."
    parts = [p for p in norm.split(os.sep) if p not in ("", ".", "..")]
    return os.path.join(*parts) if parts else ""


def _is_excluded(rel_path: str) -> bool:
    """Проверить, исключён ли файл/папка из deliverable."""
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if parts and parts[-1] in EXCLUDE_FILES:
        return True
    if parts and parts[-1].endswith(".pyc"):
        return True
    return False


def _build_readme(
    task_id: str,
    task_description: str,
    files: List[Dict[str, str]],
    summary: Optional[str] = None,
) -> str:
    """Сгенерировать README.md для deliverable."""
    lines = [
        f"# Задача {task_id}",
        "",
        "## Описание",
        summary or task_description or "Решение по техническому заданию.",
        "",
        "## Структура решения",
        "",
    ]
    for entry in files:
        path = entry.get("path", "")
        status = entry.get("status", "modified")
        emoji = {"added": "➕", "modified": "✏️", "deleted": "➖"}.get(status, "📄")
        lines.append(f"- {emoji} `{path}`")
    lines += [
        "",
        "## Запуск",
        "",
        "```bash",
        "pip install -r requirements.txt  # если есть зависимости",
        "pytest -q                         # если есть тесты",
        "```",
        "",
    ]
    return "\n".join(lines)


def build_zip(
    task_id: str,
    changed_files: List[Dict[str, str]],
    output_dir: str,
    task_description: str = "",
    summary: Optional[str] = None,
) -> Optional[str]:
    """Собрать zip-архив deliverable из changed_files.

    Args:
        task_id: ID задачи (используется как имя папки в архиве и имя файла).
        changed_files: Список от arch-code:
            [{"path": "...", "status": "added|modified|deleted", "content": "..."}].
            Файлы со статусом "deleted" или без content пропускаются.
        output_dir: Директория для zip-файла (создаётся при необходимости).
        task_description: ТЗ (для README).
        summary: Краткое описание решения (для README).

    Returns:
        Абсолютный путь к zip-файлу или None при ошибке.
    """
    if not changed_files:
        logger.warning(f"Deliverable: нет файлов для задачи {task_id}")
        return None

    # Собираем валидные файлы (не deleted, есть content, не исключены)
    files: List[Dict[str, str]] = []
    for entry in changed_files:
        path = _safe_relpath(entry.get("path", ""))
        if not path:
            continue
        if _is_excluded(path):
            logger.debug(f"Deliverable: исключён файл {path}")
            continue
        status = entry.get("status", "modified")
        content = entry.get("content")
        if status == "deleted" or content is None:
            logger.debug(f"Deliverable: пропущен файл {path} (status={status})")
            continue
        files.append({"path": path, "status": status, "content": content})

    if not files:
        logger.warning(f"Deliverable: после фильтрации нет файлов для задачи {task_id}")
        return None

    os.makedirs(output_dir, exist_ok=True)
    zip_path = os.path.join(output_dir, f"{task_id}.zip")

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            root = _safe_relpath(task_id) or "solution"

            # README
            readme = _build_readme(task_id, task_description, files, summary)
            zf.writestr(f"{root}/README.md", readme.encode("utf-8"))

            # Файлы решения
            for entry in files:
                zf.writestr(
                    f"{root}/{entry['path']}",
                    entry["content"].encode("utf-8"),
                )

        logger.info(
            f"Deliverable: собран архив {zip_path} "
            f"({len(files)} файлов, {os.path.getsize(zip_path) / 1024:.1f} КБ)"
        )
        return zip_path

    except Exception as e:
        logger.error(f"Deliverable: ошибка сборки архива: {e}")
        return None
