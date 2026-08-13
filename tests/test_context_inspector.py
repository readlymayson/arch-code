# -*- coding: utf-8 -*-
"""
test_context_inspector.py — Тесты ProjectContextInspector.

Покрытие:
1. test_tree_generation_ignores_service_dirs — служебные папки
   (.git, .venv, __pycache__, node_modules) исключаются из дерева.
2. test_ast_signatures_extraction — сигнатуры из AST: классы, функции,
   async-функции, аргументы, аннотации типов и первая строка docstring.
3. test_ast_syntax_error_tolerance — битый код не роняет парсер.

Запуск:
    . venv/bin/activate && python -m pytest tests/test_context_inspector.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.context_inspector import ProjectContextInspector, estimate_tokens


# ═══════════════════════════════════════════════════════════════
#  Фикстуры
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def inspector() -> ProjectContextInspector:
    """Инспектор с настройками по умолчанию."""
    return ProjectContextInspector()


# ═══════════════════════════════════════════════════════════════
#  Тест 1: Дерево проекта игнорирует служебные папки
# ═══════════════════════════════════════════════════════════════


class TestTreeGeneration:
    """Дерево проекта: исключение служебных папок."""

    def test_tree_generation_ignores_service_dirs(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Служебные папки и их содержимое не попадают в дерево."""
        # Обычная структура
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")

        # Служебные папки с содержимым
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "bin").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "app.cpython-311.pyc").write_text("", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "some-pkg").mkdir()

        tree = inspector.get_project_tree(tmp_path)

        # Служебные папки отсутствуют
        assert ".git" not in tree
        assert ".venv" not in tree
        assert "__pycache__" not in tree
        assert "node_modules" not in tree
        # Содержимое служебных папок тоже отсутствует
        assert "config" not in tree
        assert "bin" not in tree
        assert "some-pkg" not in tree
        assert "app.cpython-311.pyc" not in tree

        # Обычные элементы присутствуют
        assert "core/" in tree
        assert "app.py" in tree

    def test_tree_respects_max_depth(self, tmp_path: Path, inspector: ProjectContextInspector):
        """Глубина обхода ограничивается max_depth."""
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "file.txt").write_text("", encoding="utf-8")

        tree = inspector.get_project_tree(tmp_path, max_depth=2)
        assert "a/" in tree
        assert "b/" in tree
        assert "c/" not in tree
        assert "d/" not in tree

    def test_tree_missing_root_returns_empty(self, tmp_path: Path, inspector: ProjectContextInspector):
        """Несуществующий корень → пустая строка."""
        assert inspector.get_project_tree(tmp_path / "no_such_dir") == ""


# ═══════════════════════════════════════════════════════════════
#  Тест 2: Извлечение сигнатур из AST
# ═══════════════════════════════════════════════════════════════


class TestAstSignaturesExtraction:
    """Извлечение сигнатур классов и функций."""

    def test_ast_signatures_extraction(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Классы, функции и async-функции парсятся корректно."""
        source = '''
"""Модуль-пример."""

import os


class Animal:
    """Базовый класс животного."""

    def __init__(self, name: str, age: int = 0) -> None:
        """Конструктор."""
        self.name = name
        self.age = age

    def speak(self, volume: int = 5) -> str:
        """Издать звук с заданной громкостью."""
        return "..."

    async def fetch(self, url: str) -> dict:
        """Асинхронная загрузка данных."""
        return {}


def helper(value: int, *, flag: bool = True) -> int:
    """Вспомогательная функция."""
    return value
'''
        py_file = tmp_path / "sample.py"
        py_file.write_text(source, encoding="utf-8")

        sigs = inspector.get_python_signatures(py_file)

        # Класс с базовым docstring
        assert "class Animal: ..." in sigs
        assert "Базовый класс животного" in sigs

        # Методы с аннотациями и дефолтами
        assert "def __init__(self, name: str, age: int = 0) -> None: ..." in sigs
        assert "Конструктор" in sigs
        assert "def speak(self, volume: int = 5) -> str: ..." in sigs
        assert "async def fetch(self, url: str) -> dict: ..." in sigs

        # Свободная функция с keyword-only аргументом
        assert "def helper(value: int, *, flag: bool = True) -> int: ..." in sigs
        assert "Вспомогательная функция" in sigs

        # Тела функций заменяются на '...'
        assert "return value" not in sigs
        assert "self.name = name" not in sigs

    def test_signatures_include_nested_definitions(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Вложенные определения (класс в классе, функция в функции) извлекаются."""
        source = '''
class Outer:
    """Внешний класс."""

    class Inner:
        """Внутренний класс."""

        def method(self) -> None:
            """Метод внутреннего класса."""
            pass

    def wrap(self):
        def inner_helper(x: int) -> int:
            """Вложенная функция."""
            return x + 1
        return inner_helper
'''
        py_file = tmp_path / "nested.py"
        py_file.write_text(source, encoding="utf-8")

        sigs = inspector.get_python_signatures(py_file)

        assert "class Outer: ..." in sigs
        assert "    class Inner: ..." in sigs
        assert "        def method(self) -> None: ..." in sigs
        assert "    def wrap(self): ..." in sigs
        assert "        def inner_helper(x: int) -> int: ..." in sigs

    def test_empty_file_returns_empty(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Файл без определений → пустая строка."""
        py_file = tmp_path / "empty.py"
        py_file.write_text("# только комментарий\n", encoding="utf-8")
        assert inspector.get_python_signatures(py_file) == ""


# ═══════════════════════════════════════════════════════════════
#  Тест 3: Устойчивость к синтаксическим ошибкам
# ═══════════════════════════════════════════════════════════════


class TestAstSyntaxErrorTolerance:
    """Парсер не падает на битом коде."""

    def test_ast_syntax_error_tolerance(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Битый код → пустая строка, без исключений."""
        broken = '''
def broken(:
    return "never"
'''
        py_file = tmp_path / "broken.py"
        py_file.write_text(broken, encoding="utf-8")

        # Не должно быть исключения
        sigs = inspector.get_python_signatures(py_file)
        assert sigs == ""

    def test_generate_summary_with_broken_file(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """generate_summary не падает при наличии битого файла в проекте."""
        (tmp_path / "ok.py").write_text(
            'def ok() -> int:\n    """Ок."""\n    return 1\n', encoding="utf-8"
        )
        (tmp_path / "bad.py").write_text('def bad(:\n', encoding="utf-8")
        (tmp_path / "broken_class.py").write_text(
            'class Broken(:\n    pass\n', encoding="utf-8"
        )

        summary = inspector.generate_summary(tmp_path)

        assert "# Проект:" in summary
        assert "ok.py" in summary
        assert "def ok() -> int: ..." in summary
        # Битые файлы не роняют сводку
        assert "bad.py" in summary or "broken_class.py" in summary


# ═══════════════════════════════════════════════════════════════
#  Дополнительно: generate_summary и лимит токенов
# ═══════════════════════════════════════════════════════════════


class TestGenerateSummary:
    """Сводка проекта: дерево + сигнатуры + лимит токенов."""

    def test_generate_summary_contains_tree_and_signatures(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Сводка содержит дерево и сигнатуры Python-файлов."""
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pkg" / "mod.py").write_text(
            'class Service:\n    """Сервис."""\n'
            '    def run(self) -> None:\n        """Запуск."""\n        pass\n',
            encoding="utf-8",
        )

        summary = inspector.generate_summary(tmp_path)

        assert "# Проект:" in summary
        assert "## Дерево" in summary
        assert "## Сигнатуры" in summary
        assert "pkg/" in summary
        assert "### pkg/mod.py" in summary
        assert "class Service: ..." in summary
        assert "def run(self) -> None: ..." in summary

    def test_generate_summary_token_limit(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Сводка укладывается в лимит токенов (~2000 по умолчанию)."""
        big = "\n".join(
            f"def func_{i}(a: int) -> int:\n"
            f'    """Функция {i}."""\n'
            f"    return {i}\n"
            for i in range(200)
        )
        (tmp_path / "big.py").write_text(big, encoding="utf-8")

        summary = inspector.generate_summary(tmp_path)

        # Дефолтный лимит ~2000 токенов
        assert estimate_tokens(summary) <= 2100

    def test_generate_summary_custom_token_limit(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Кастомный лимит токенов соблюдается."""
        big = "\n".join(
            f"def func_{i}(a: int) -> int:\n"
            f'    """Функция {i}."""\n'
            f"    return {i}\n"
            for i in range(200)
        )
        (tmp_path / "big.py").write_text(big, encoding="utf-8")

        summary = inspector.generate_summary(tmp_path, max_tokens=200)

        assert estimate_tokens(summary) <= 300

    def test_generate_summary_missing_root(
        self, tmp_path: Path, inspector: ProjectContextInspector
    ):
        """Несуществующий корень → пустая строка."""
        assert inspector.generate_summary(tmp_path / "no_such_dir") == ""

    def test_estimate_tokens(self):
        """Эвристическая оценка токенов корректна."""
        assert estimate_tokens("") == 0
        assert estimate_tokens("A" * 100) == 25  # 100 // 4
        assert estimate_tokens("A") == 1  # минимум 1
