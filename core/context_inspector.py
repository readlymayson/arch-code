# -*- coding: utf-8 -*-
"""
core.context_inspector — инспекция структуры Python-проекта.

Собирает дерево каталогов и сигнатуры Python-определений
(классы, функции, async-функции) в компактную сводку, пригодную
для передачи LLM-моделям (контекст ~2000 токенов).

Возможности:
- get_project_tree(root_dir, max_depth=4) — дерево каталогов.
  Служебные папки (.git, .venv, __pycache__, node_modules и т.п.)
  игнорируются.
- get_python_signatures(file_path) — сигнатуры из AST: имя, аргументы,
  аннотации типов и первая строка docstring; тела заменяются на '...'.
- generate_summary(root_dir) — сборка дерева и сигнатур в одну строку
  с ограничением ~2000 токенов.

Устойчивость: файлы с синтаксическими ошибками пропускаются
с логированием, не прерывая процесс.

Внимание: это копия модуля из ai-core/core/context_inspector.py
(адаптирован логгер под loguru — форматирование {} вместо %s).
Логика идентична оригиналу.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Optional, Set, Union

from loguru import logger

#: Служебные каталоги, которые всегда игнорируются при обходе дерева
DEFAULT_IGNORED_DIRS: Set[str] = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "dist", "build", ".eggs", "htmlcov", ".tox",
}

#: Служебные файлы, которые всегда игнорируются при обходе дерева
DEFAULT_IGNORED_FILES: Set[str] = {
    ".DS_Store", "Thumbs.db",
}

#: ast.unparse доступен с Python 3.9; для старых версий — фолбэк
_UNPARSE = getattr(ast, "unparse", None)


def estimate_tokens(text: str) -> int:
    """Приблизительная оценка количества токенов (4 символа ≈ 1 токен).

    Используется как дешёвый эвристический подсчёт без внешних
    зависимостей (аналогично fallback в core.history_truncator).
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _unparse(node) -> str:
    """Безопасный ast.unparse с фолбэком на '...' при ошибках."""
    if _UNPARSE is not None:
        try:
            return _UNPARSE(node)
        except Exception:
            pass
    return "..."


def _first_docstring_line(node) -> str:
    """Первая строка docstring определения (или пустая строка)."""
    doc = ast.get_docstring(node, clean=False)
    if not doc:
        return ""
    first = doc.strip().splitlines()[0] if doc.strip() else ""
    return first.strip()


def _format_arg(arg: ast.arg, default=None) -> str:
    """Форматировать один аргумент: имя, аннотация, значение по умолчанию."""
    text = arg.arg
    if arg.annotation is not None:
        text += f": {_unparse(arg.annotation)}"
    if default is not None:
        # ast.unparse для annotation+default даёт "int= 0" — добавляем пробел
        text += f" = {_unparse(default)}"
    return text


def _format_args(args: ast.arguments) -> str:
    """Отформатировать список аргументов функции с аннотациями и дефолтами."""
    parts: List[str] = []
    posonly = list(args.posonlyargs)
    pos = list(args.args)
    defaults = list(args.defaults)
    n_pos = len(posonly) + len(pos)
    offset = n_pos - len(defaults)

    def default_for(index: int):
        """Значение по умолчанию для позиционного аргумента с индексом."""
        di = index - offset
        if 0 <= di < len(defaults):
            return defaults[di]
        return None

    for i, arg in enumerate(posonly):
        parts.append(_format_arg(arg, default_for(i)))
    if posonly and pos:
        parts.append("/")
    for i, arg in enumerate(pos):
        parts.append(_format_arg(arg, default_for(len(posonly) + i)))
    if args.vararg is not None:
        parts.append("*" + _format_arg(args.vararg))
    elif args.kwonlyargs:
        parts.append("*")
    kw_defaults = list(args.kw_defaults or [])
    for i, arg in enumerate(args.kwonlyargs):
        default = kw_defaults[i] if i < len(kw_defaults) else None
        parts.append(_format_arg(arg, default))
    if args.kwarg is not None:
        parts.append("**" + _format_arg(args.kwarg))
    return ", ".join(parts)


def _format_function(node: Union[ast.FunctionDef, ast.AsyncFunctionDef], level: int) -> str:
    """Отформатировать функцию/async-функцию в сигнатуру с docstring."""
    indent = "    " * level
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    header = f"{indent}{kind} {node.name}({_format_args(node.args)})"
    if node.returns is not None:
        header += f" -> {_unparse(node.returns)}"
    header += ": ..."
    doc = _first_docstring_line(node)
    if doc:
        header += f"\n{indent}    \"\"\"{doc}\"\"\""
    return header


def _format_class_keyword(kw: ast.keyword) -> str:
    """Отформатировать keyword-аргумент базового класса (metaclass и т.п.)."""
    if kw.arg is None:
        return f"**{_unparse(kw.value)}"
    return f"{kw.arg}={_unparse(kw.value)}"


def _format_class(node: ast.ClassDef, level: int) -> str:
    """Отформатировать класс в сигнатуру с docstring."""
    indent = "    " * level
    bases = [_unparse(b) for b in node.bases] + [_format_class_keyword(kw) for kw in node.keywords]
    header = f"{indent}class {node.name}"
    if bases:
        header += f"({', '.join(bases)})"
    header += ": ..."
    doc = _first_docstring_line(node)
    if doc:
        header += f"\n{indent}    \"\"\"{doc}\"\"\""
    return header


def _collect_definitions(tree: ast.AST, level: int = 0) -> List[str]:
    """Рекурсивно собрать сигнатуры ClassDef/FunctionDef/AsyncFunctionDef."""
    lines: List[str] = []
    body = getattr(tree, "body", [])
    for child in body:
        if isinstance(child, ast.ClassDef):
            lines.append(_format_class(child, level))
            lines.extend(_collect_definitions(child, level + 1))
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(_format_function(child, level))
            lines.extend(_collect_definitions(child, level + 1))
    return lines


class ProjectContextInspector:
    """Инспектор структуры Python-проекта.

    Собирает дерево каталогов и сигнатуры Python-определений
    для формирования компактной сводки проекта.
    """

    def __init__(
        self,
        ignored_dirs: Optional[Set[str]] = None,
        ignored_files: Optional[Set[str]] = None,
        max_tokens: int = 2000,
    ) -> None:
        """Инициализация инспектора.

        Args:
            ignored_dirs: Дополнительные игнорируемые каталоги
                (объединяются с DEFAULT_IGNORED_DIRS).
            ignored_files: Дополнительные игнорируемые файлы
                (объединяются с DEFAULT_IGNORED_FILES).
            max_tokens: Ограничение сводки generate_summary в токенах.
        """
        self.ignored_dirs = DEFAULT_IGNORED_DIRS | (ignored_dirs or set())
        self.ignored_files = DEFAULT_IGNORED_FILES | (ignored_files or set())
        self.max_tokens = max_tokens

    # ── Дерево проекта ──────────────────────────────────────────

    def get_project_tree(self, root_dir, max_depth: int = 4) -> str:
        """Построить дерево каталогов проекта.

        Служебные папки (.git, .venv, __pycache__, node_modules и т.п.)
        и служебные файлы исключаются из дерева.

        Args:
            root_dir: Корень проекта (str или Path).
            max_depth: Максимальная глубина обхода.

        Returns:
            Многострочная строка с деревом в формате box-drawing.
            Пустая строка, если каталог не существует.
        """
        root = Path(root_dir)
        if not root.exists() or not root.is_dir():
            logger.warning("Каталог {} не существует или не является папкой", root)
            return ""

        lines = [f"{root.name or str(root)}/"]
        lines.extend(self._build_tree_lines(root, max_depth=max_depth))
        return "\n".join(lines)

    def _build_tree_lines(
        self,
        directory: Path,
        max_depth: int,
        prefix: str = "",
        depth: int = 0,
    ) -> List[str]:
        """Рекурсивно собрать строки дерева для каталога."""
        lines: List[str] = []
        if depth >= max_depth:
            return lines
        try:
            entries = sorted(
                (p for p in directory.iterdir() if not self._is_ignored(p)),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError as e:
            logger.warning("Не удалось прочитать каталог {}: {}", directory, e)
            return lines

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{connector}{entry.name}{suffix}")
            if entry.is_dir():
                extension = "    " if is_last else "│   "
                lines.extend(
                    self._build_tree_lines(entry, max_depth, prefix + extension, depth + 1)
                )
        return lines

    def _is_ignored(self, path: Path) -> bool:
        """Проверить, является ли путь служебным (каталог/файл)."""
        if path.name in self.ignored_dirs or path.name in self.ignored_files:
            return True
        if path.is_file() and path.suffix in {".pyc", ".pyo"}:
            return True
        return False

    def _iter_python_files(self, root: Path) -> List[Path]:
        """Обойти каталог и вернуть .py файлы, пропуская служебные папки.

        Возвращает список, отсортированный по относительному пути
        для детерминированного вывода.
        """
        result: List[Path] = []
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError as e:
                logger.warning("Не удалось прочитать каталог {}: {}", current, e)
                continue
            for entry in entries:
                if self._is_ignored(entry):
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.suffix == ".py":
                    result.append(entry)
        result.sort(key=lambda p: p.relative_to(root).as_posix())
        return result

    # ── Сигнатуры из AST ────────────────────────────────────────

    def get_python_signatures(self, file_path) -> str:
        """Извлечь сигнатуры Python-определений из файла.

        Извлекаются ClassDef, FunctionDef и AsyncFunctionDef (включая
        вложенные). Для каждого определения формируется строка с именем,
        аргументами, аннотациями типов и первой строкой docstring.
        Тела определений заменяются на '...'.

        Args:
            file_path: Путь к .py файлу (str или Path).

        Returns:
            Многострочная строка с сигнатурами. Пустая строка при
            синтаксической ошибке — файл пропускается с логированием,
            процесс не прерывается.
        """
        path = Path(file_path)
        try:
            source = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Не удалось прочитать {}: {}", path, e)
            return ""

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            logger.warning(
                "Синтаксическая ошибка в {} (строка {}): {}",
                path, e.lineno, e.msg,
            )
            return ""
        except Exception as e:
            logger.warning("Ошибка парсинга {}: {}", path, e)
            return ""

        return "\n".join(_collect_definitions(tree))

    # ── Сводка проекта ──────────────────────────────────────────

    def generate_summary(self, root_dir, max_tokens: Optional[int] = None) -> str:
        """Собрать сводку проекта: дерево + сигнатуры Python-файлов.

        Args:
            root_dir: Корень проекта (str или Path).
            max_tokens: Максимальное число токенов в сводке.
                По умолчанию используется self.max_tokens (~2000).

        Returns:
            Многострочная строка со сводкой проекта, ограниченная
            по количеству токенов.
        """
        limit = max_tokens or self.max_tokens
        root = Path(root_dir)
        if not root.exists() or not root.is_dir():
            logger.warning("Каталог {} не существует или не является папкой", root)
            return ""

        tree = self.get_project_tree(root, max_depth=4)
        parts: List[str] = [
            f"# Проект: {root}",
            "",
            "## Дерево",
            tree,
            "",
            "## Сигнатуры",
        ]
        used = estimate_tokens("\n".join(parts))

        for py_file in self._iter_python_files(root):
            if used >= limit:
                break
            sigs = self.get_python_signatures(py_file)
            if not sigs.strip():
                continue
            try:
                rel = py_file.relative_to(root)
            except ValueError:
                rel = py_file
            block = f"\n\n### {rel}\n{sigs}"
            block_tokens = estimate_tokens(block)
            if used + block_tokens > limit:
                break
            parts.append(block)
            used += block_tokens

        summary = "\n".join(parts).rstrip()
        if estimate_tokens(summary) > limit:
            summary = self._truncate_to_chars(summary, limit * 4)
        return summary

    @staticmethod
    def _truncate_to_chars(text: str, max_chars: int) -> str:
        """Обрезать текст по границе строки с пометкой об обрезке."""
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        idx = cut.rfind("\n")
        if idx > max_chars // 2:
            cut = cut[:idx]
        return cut.rstrip() + "\n... (обрезано по лимиту токенов)"
