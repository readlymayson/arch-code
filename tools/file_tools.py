"""
FileManagementTools — инструменты для чтения и записи файлов в песочнице.

Каждый инструмент принимает sandbox_dir (абсолютный путь к sandbox/{task_id}/)
и оперирует только внутри него — безопасность на уровне ФС.

Используется субагентом в LangGraph-графе для исследования и модификации проекта.
"""
from __future__ import annotations

import os
import fnmatch


# ── Список исключений при rsync ──────────────────────────────────

RSYNC_EXCLUDE_PATTERNS = [
    ".env",
    ".env.*",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".git",
    "logs",
    "sessions",
    "temp",
    ".DS_Store",
    "*.session",
    "*.log",
    ".gitignore",
    "docker-compose*.yml",
    "Dockerfile*",
]


# ── Безопасная проверка пути ────────────────────────────────────

def _resolve_path(sandbox_dir: str, relative_path: str) -> str | None:
    """Разрешить относительный путь внутри sandbox. Защита от path traversal.

    Returns:
        Абсолютный путь, если он внутри sandbox_dir, иначе None.
    """
    # Если путь уже абсолютный — он должен быть внутри sandbox_dir
    if os.path.isabs(relative_path):
        full = os.path.normpath(relative_path)
    else:
        full = os.path.normpath(os.path.join(sandbox_dir, relative_path))

    # Проверка: путь должен начинаться с sandbox_dir
    if not full.startswith(os.path.normpath(sandbox_dir) + os.sep) and full != os.path.normpath(sandbox_dir):
        return None
    return full


# ── ListFilesTool ─────────────────────────────────────────────────

def list_files(sandbox_dir: str, path: str = "") -> str:
    """Вернуть дерево файлов внутри sandbox (как 'tree').

    Args:
        sandbox_dir: Абсолютный путь к песочнице.
        path: Относительный путь внутри sandbox (или '' для корня).

    Returns:
        Многострочное дерево файлов и папок.
    """
    target = _resolve_path(sandbox_dir, path)
    if target is None:
        return f"❌ Ошибка: путь '{path}' вне песочницы."

    if not os.path.isdir(target):
        return f"❌ Ошибка: '{path}' не является директорией."

    lines = []
    _build_tree(target, "", lines, sandbox_dir)
    return "\n".join(lines) if lines else "(пусто)"


def _build_tree(directory: str, prefix: str, lines: list, root_dir: str) -> None:
    """Рекурсивно строит дерево."""
    try:
        entries = sorted(os.listdir(directory))
    except PermissionError:
        lines.append(f"{prefix}└── (доступ запрещён)")
        return

    # Фильтруем служебные папки
    skip_dirs = {"node_modules", "__pycache__", ".git", "venv", ".venv", ".mypy_cache", ".pytest_cache"}
    skip_extensions = {".pyc", ".pyo", ".session", ".log"}

    entries = [
        e for e in entries
        if e not in skip_dirs
        and not any(e.endswith(ext) for ext in skip_extensions)
        and not e.startswith(".")
    ]

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        full_path = os.path.join(directory, entry)

        rel_path = os.path.relpath(full_path, root_dir)
        if os.path.isdir(full_path):
            lines.append(f"{prefix}{connector}{entry}/")
            extension = "    " if is_last else "│   "
            _build_tree(full_path, prefix + extension, lines, root_dir)
        else:
            size = os.path.getsize(full_path)
            lines.append(f"{prefix}{connector}{entry}  ({_fmt_size(size)})")


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / 1024 ** 2:.1f} MB"


# ── ReadFileTool ─────────────────────────────────────────────────

def read_file(sandbox_dir: str, filepath: str, max_length: int = 50_000) -> str:
    """Прочитать содержимое файла внутри sandbox.

    Args:
        sandbox_dir: Абсолютный путь к песочнице.
        filepath: Путь к файлу (относительно sandbox_dir или абсолютный внутри него).
        max_length: Максимальное количество символов (ограничение контекста).

    Returns:
        Содержимое файла или сообщение об ошибке.
    """
    full_path = _resolve_path(sandbox_dir, filepath)
    if full_path is None:
        return f"❌ Ошибка: путь '{filepath}' вне песочницы."

    if not os.path.isfile(full_path):
        return f"❌ Файл не найден: {filepath}"

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read(max_length)
        if len(content) >= max_length:
            content += f"\n\n... (файл обрезан до {max_length} символов)"
        return content
    except UnicodeDecodeError:
        return f"⚠️ Файл '{filepath}' не является текстовым (бинарный)."
    except Exception as e:
        return f"❌ Ошибка чтения: {e}"


# ── WriteFileTool ────────────────────────────────────────────────

def write_file(sandbox_dir: str, filepath: str, content: str) -> str:
    """Записать содержимое в файл внутри sandbox (создаёт папки при необходимости).

    Args:
        sandbox_dir: Абсолютный путь к песочнице.
        filepath: Путь к файлу (относительно sandbox_dir или абсолютный внутри него).
        content: Текстовое содержимое для записи.

    Returns:
        Сообщение о результате.
    """
    full_path = _resolve_path(sandbox_dir, filepath)
    if full_path is None:
        return f"❌ Ошибка: путь '{filepath}' вне песочницы."

    # Создаём папки
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        rel = os.path.relpath(full_path, sandbox_dir)
        return f"✅ Файл записан: {rel} ({len(content)} символов)"
    except Exception as e:
        return f"❌ Ошибка записи: {e}"


# ── LangChain Tool Definitions (для graph_worker.py) ────────────

from functools import partial
from langchain_core.tools import tool as _lc_tool


def make_coding_tools(sandbox_dir: str) -> list:
    """Создать набор инструментов для агента с привязкой к sandbox_dir.

    Args:
        sandbox_dir: Абсолютный путь к песочнице.

    Returns:
        Список LangChain BaseTool для bind_tools().
    """

    @_lc_tool
    def read_file_tool(relative_path: str) -> str:
        """Прочитать содержимое файла внутри проекта.

        Args:
            relative_path: Путь к файлу относительно корня проекта (например 'core/billing_manager.py').
        """
        return read_file(sandbox_dir, relative_path)

    @_lc_tool
    def write_file_tool(relative_path: str, content: str) -> str:
        """Записать новый файл или перезаписать существующий.

        Args:
            relative_path: Путь к файлу относительно корня проекта.
            content: Полное содержимое файла.
        """
        return write_file(sandbox_dir, relative_path, content)

    @_lc_tool
    def list_files_tool(relative_path: str = "") -> str:
        """Показать дерево файлов и папок внутри проекта.

        Args:
            relative_path: Путь к папке (пустая строка = корень проекта).
        """
        return list_files(sandbox_dir, relative_path)

    @_lc_tool(return_direct=True)
    def done(changed_files: list[str]) -> str:
        """Вызвать, когда все изменения внесены и задача выполнена.

        Args:
            changed_files: Список относительных путей к созданным/изменённым файлам.
        """
        import json
        return f"DONE:{json.dumps(changed_files)}"

    return [read_file_tool, write_file_tool, list_files_tool, done]
