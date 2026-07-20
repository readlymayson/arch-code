"""
Тесты для tools/file_tools.py — безопасные операции с файлами в песочнице.

Покрытие:
1. ✅ _resolve_path — нормальный путь
2. ✅ _resolve_path — path traversal (..)
3. ✅ _resolve_path — path traversal (глубокий)
4. ✅ _resolve_path — абсолютный путь внутри sandbox
5. ✅ _resolve_path — абсолютный путь вне sandbox
6. ✅ _resolve_path — точка (текущая директория)
7. ✅ _fmt_size — байты
8. ✅ _fmt_size — килобайты
9. ✅ _fmt_size — мегабайты
10. ✅ _fmt_size — граничное значение 1023
11. ✅ _fmt_size — граничное значение 1024
12. ✅ _build_tree — простая структура
13. ✅ _build_tree — с исключениями (node_modules пропущен)
14. ✅ _build_tree — пустая директория
15. ✅ list_files — корень песочницы
16. ✅ list_files — вложенная директория
17. ✅ list_files — несуществующая директория
18. ✅ list_files — path traversal (блокируется)
19. ✅ read_file — успешное чтение
20. ✅ read_file — файл не найден
21. ✅ read_file — path traversal (блокируется)
22. ✅ read_file — бинарный файл
23. ✅ read_file — превышение max_length
24. ✅ write_file — создание нового файла
25. ✅ write_file — перезапись существующего
26. ✅ write_file — создание поддиректорий
27. ✅ write_file — path traversal (блокируется)
"""

import os
import sys

# Добавляем корень проекта в путь для импорта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tools.file_tools import (
    _fmt_size,
    _resolve_path,
    _build_tree,
    list_files,
    read_file,
    write_file,
    make_coding_tools,
)


# ── _resolve_path ────────────────────────────────────────────────

class TestResolvePath:
    """Безопасная проверка пути — защита от path traversal."""

    def test_normal_relative_path(self, temp_sandbox):
        """Обычный относительный путь внутри sandbox."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = _resolve_path(task_dir, "app.js")
        assert result == os.path.normpath(os.path.join(task_dir, "app.js"))

    def test_path_traversal_simple(self, temp_sandbox):
        """Простой path traversal: ../.. — блокируется."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = _resolve_path(task_dir, "../../etc/passwd")
        assert result is None

    def test_path_traversal_deep(self, temp_sandbox):
        """Глубокий path traversal с множественными .. — блокируется."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = _resolve_path(task_dir, "subdir/../../../etc/passwd")
        assert result is None

    def test_absolute_path_inside(self, temp_sandbox):
        """Абсолютный путь внутри sandbox — разрешается."""
        task_dir = str(temp_sandbox / "test_task_001")
        app_js = os.path.join(task_dir, "app.js")
        result = _resolve_path(task_dir, app_js)
        assert result == os.path.normpath(app_js)

    def test_absolute_path_outside(self, temp_sandbox):
        """Абсолютный путь вне sandbox — блокируется."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = _resolve_path(task_dir, "/etc/passwd")
        assert result is None

    def test_current_directory(self, temp_sandbox):
        """Точка — ссылка на корень sandbox."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = _resolve_path(task_dir, ".")
        assert result == os.path.normpath(task_dir)


# ── _fmt_size ────────────────────────────────────────────────────

class TestFmtSize:
    """Форматирование размера файла."""

    def test_bytes(self):
        """Меньше 1024 — в байтах."""
        assert _fmt_size(500) == "500 B"

    def test_kilobytes(self):
        """1024 и больше — в килобайтах."""
        assert _fmt_size(2048) == "2.0 KB"

    def test_megabytes(self):
        """Больше мегабайта — в мегабайтах."""
        assert _fmt_size(2 * 1024 ** 2) == "2.0 MB"

    @pytest.mark.parametrize("size, expected", [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 ** 2, "1.0 MB"),
    ])
    def test_boundary_values(self, size, expected):
        """Граничные значения."""
        assert _fmt_size(size) == expected


# ── _build_tree ──────────────────────────────────────────────────

class TestBuildTree:
    """Построение дерева файлов."""

    def test_simple_tree(self, temp_sandbox):
        """Простая структура с файлами и папками."""
        task_dir = str(temp_sandbox / "test_task_001")
        lines = []
        _build_tree(task_dir, "", lines, task_dir)
        output = "\n".join(lines)

        assert "app.js" in output
        assert "subdir/" in output
        assert "package.json" in output

    def test_skip_excluded_dirs(self, temp_sandbox, tmp_path):
        """Директории node_modules, __pycache__ — исключаются."""
        task_dir = str(temp_sandbox / "test_task_001")
        # Создаём исключаемые папки
        os.makedirs(os.path.join(task_dir, "node_modules"), exist_ok=True)
        os.makedirs(os.path.join(task_dir, "__pycache__"), exist_ok=True)

        lines = []
        _build_tree(task_dir, "", lines, task_dir)
        output = "\n".join(lines)

        assert "node_modules" not in output
        assert "__pycache__" not in output

    def test_empty_directory(self, tmp_path):
        """Пустая директория."""
        empty_dir = str(tmp_path / "empty")
        os.makedirs(empty_dir, exist_ok=True)

        lines = []
        _build_tree(empty_dir, "", lines, empty_dir)
        assert len(lines) == 0


# ── list_files ───────────────────────────────────────────────────

class TestListFiles:
    """Список файлов в песочнице."""

    def test_root_listing(self, temp_sandbox):
        """Корень песочницы — видит все файлы."""
        task_dir = str(temp_sandbox / "test_task_001")
        output = list_files(task_dir, "")
        assert "app.js" in output
        assert "subdir/" in output

    def test_nested_directory(self, temp_sandbox):
        """Вложенная директория."""
        task_dir = str(temp_sandbox / "test_task_001")
        output = list_files(task_dir, "subdir")
        assert "helper.js" in output

    def test_nonexistent_directory(self, temp_sandbox):
        """Несуществующая директория — ошибка."""
        task_dir = str(temp_sandbox / "test_task_001")
        output = list_files(task_dir, "nonexistent")
        assert "❌" in output or "Ошибка" in output

    def test_path_traversal_blocked(self, temp_sandbox):
        """Path traversal блокируется."""
        task_dir = str(temp_sandbox / "test_task_001")
        output = list_files(task_dir, "../../etc")
        assert "❌" in output or "Ошибка" in output or "вне песочницы" in output


# ── read_file ────────────────────────────────────────────────────

class TestReadFile:
    """Чтение файлов."""

    def test_successful_read(self, temp_sandbox):
        """Успешное чтение существующего файла."""
        task_dir = str(temp_sandbox / "test_task_001")
        content = read_file(task_dir, "app.js")
        assert "express" in content
        assert content.startswith("const express")

    def test_file_not_found(self, temp_sandbox):
        """Несуществующий файл — ошибка."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = read_file(task_dir, "nonexistent.js")
        assert "❌" in result or "не найден" in result

    def test_path_traversal_blocked(self, temp_sandbox):
        """Path traversal блокируется."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = read_file(task_dir, "../../etc/passwd")
        assert "❌" in result or "вне песочницы" in result

    def test_binary_file(self, temp_sandbox):
        """Бинарный файл — предупреждение (используем невалидную UTF-8 последовательность)."""
        task_dir = str(temp_sandbox / "test_task_001")
        binary_path = os.path.join(task_dir, "binary.bin")
        with open(binary_path, "wb") as f:
            f.write(b"\xff\xfe\x00\x01")

        result = read_file(task_dir, "binary.bin")
        assert "не является текстовым" in result or "бинарный" in result or "⚠" in result

    def test_max_length_truncation(self, temp_sandbox):
        """Чтение с ограничением длины."""
        task_dir = str(temp_sandbox / "test_task_001")
        long_path = os.path.join(task_dir, "long.txt")
        with open(long_path, "w") as f:
            f.write("a" * 10000)

        result = read_file(task_dir, "long.txt", max_length=100)
        assert len(result) <= 150  # content + truncation message
        assert "обрезан" in result


# ── write_file ───────────────────────────────────────────────────

class TestWriteFile:
    """Запись файлов."""

    def test_create_new_file(self, temp_sandbox):
        """Создание нового файла."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = write_file(task_dir, "newfile.js", 'console.log("test");\n')
        assert "✅" in result
        assert os.path.exists(os.path.join(task_dir, "newfile.js"))

    def test_overwrite_existing(self, temp_sandbox):
        """Перезапись существующего файла."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = write_file(task_dir, "app.js", "modified content")
        assert "✅" in result
        content = read_file(task_dir, "app.js")
        assert content == "modified content"

    def test_create_subdirectories(self, temp_sandbox):
        """Авто-создание поддиректорий."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = write_file(task_dir, "src/utils/helper.js", "// helper")
        assert "✅" in result
        assert os.path.exists(os.path.join(task_dir, "src", "utils", "helper.js"))

    def test_path_traversal_blocked(self, temp_sandbox):
        """Path traversal блокируется."""
        task_dir = str(temp_sandbox / "test_task_001")
        result = write_file(task_dir, "../../malicious.js", "evil code")
        assert "❌" in result or "вне песочницы" in result


# ── make_coding_tools ──────────────────────────────────────────

class TestMakeCodingTools:
    """LangChain Tool definitions для graph_worker."""

    def test_returns_list_of_tools(self, temp_sandbox):
        """Возвращает список инструментов."""
        task_dir = str(temp_sandbox / "test_task_001")
        tools = make_coding_tools(task_dir)
        assert isinstance(tools, list)
        assert len(tools) == 4

    def test_tool_names(self, temp_sandbox):
        """Имена инструментов корректны."""
        task_dir = str(temp_sandbox / "test_task_001")
        tools = make_coding_tools(task_dir)
        names = [t.name for t in tools]
        assert "read_file_tool" in names
        assert "write_file_tool" in names
        assert "list_files_tool" in names
        assert "done" in names

    def test_read_file_tool_works(self, temp_sandbox):
        """read_file_tool читает файл внутри sandbox."""
        task_dir = str(temp_sandbox / "test_task_001")
        tools = make_coding_tools(task_dir)
        read_tool = [t for t in tools if t.name == "read_file_tool"][0]

        result = read_tool.invoke({"relative_path": "app.js"})
        assert "express" in result

    def test_write_file_tool_creates_file(self, temp_sandbox):
        """write_file_tool создаёт файл."""
        task_dir = str(temp_sandbox / "test_task_001")
        tools = make_coding_tools(task_dir)
        write_tool = [t for t in tools if t.name == "write_file_tool"][0]

        result = write_tool.invoke({
            "relative_path": "generated.py",
            "content": "# test\nprint('hello')\n",
        })
        assert "✅" in result or "Файл записан" in result

        created_file = os.path.join(task_dir, "generated.py")
        assert os.path.exists(created_file)
        assert open(created_file).read() == "# test\nprint('hello')\n"

    def test_write_tool_creates_directories(self, temp_sandbox):
        """write_file_tool создаёт поддиректории."""
        task_dir = str(temp_sandbox / "test_task_001")
        tools = make_coding_tools(task_dir)
        write_tool = [t for t in tools if t.name == "write_file_tool"][0]

        write_tool.invoke({
            "relative_path": "core/billing_manager.py",
            "content": "class BillingManager: pass\n",
        })
        assert os.path.exists(os.path.join(task_dir, "core", "billing_manager.py"))

    def test_list_files_tool_shows_tree(self, temp_sandbox):
        """list_files_tool показывает дерево."""
        task_dir = str(temp_sandbox / "test_task_001")
        tools = make_coding_tools(task_dir)
        list_tool = [t for t in tools if t.name == "list_files_tool"][0]

        result = list_tool.invoke({"relative_path": ""})
        assert "app.js" in result
        assert "subdir/" in result

    def test_done_returns_marker(self, temp_sandbox):
        """done возвращает маркер DONE с JSON."""
        task_dir = str(temp_sandbox / "test_task_001")
        tools = make_coding_tools(task_dir)
        done_tool = [t for t in tools if t.name == "done"][0]

        result = done_tool.invoke({"changed_files": ["app.js", "core/new.py"]})
        assert result.startswith("DONE:")
        import json
        files = json.loads(result.split("DONE:")[1])
        assert "app.js" in files
        assert "core/new.py" in files
