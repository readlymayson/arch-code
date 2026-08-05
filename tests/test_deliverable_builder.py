"""Тесты Deliverable Builder — сборка zip из changed_files.

Покрытие:
1. ✅ build_zip создаёт архив с README + файлами
2. ✅ Файлы со статусом deleted пропускаются
3. ✅ Исключение .git/__pycache__/node_modules
4. ✅ Пустой changed_files → None
5. ✅ Path traversal защита (../ в пути)
"""
from __future__ import annotations

import os
import zipfile

import pytest

from tools.deliverable_builder import build_zip


@pytest.fixture
def sample_files():
    """Типичный результат arch-code."""
    return [
        {
            "path": "main.py",
            "status": "added",
            "content": "def main():\n    print('hello')\n",
            "diff": "(new file)",
        },
        {
            "path": "bot/handlers.py",
            "status": "modified",
            "content": "async def start():\n    pass\n",
            "diff": "@@ ...",
        },
        {
            "path": "old.py",
            "status": "deleted",
            "content": None,
            "diff": "...",
        },
        {
            "path": ".git/config",
            "status": "added",
            "content": "[core]",
            "diff": "...",
        },
        {
            "path": "app/__pycache__/cache.pyc",
            "status": "added",
            "content": "binary",
            "diff": "...",
        },
    ]


class TestBuildZip:
    """Сборка zip-архива."""

    def test_build_zip_creates_archive(self, sample_files, tmp_path):
        """Архив создаётся с README и файлами решения."""
        zip_path = build_zip(
            task_id="task_123",
            changed_files=sample_files,
            output_dir=str(tmp_path),
            task_description="Сделать бота",
        )

        assert zip_path is not None
        assert os.path.exists(zip_path)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            # README присутствует
            assert any(n.endswith("README.md") for n in names)
            # main.py и bot/handlers.py присутствуют
            assert any(n.endswith("main.py") for n in names)
            assert any(n.endswith("bot/handlers.py") for n in names)

    def test_deleted_and_excluded_skipped(self, sample_files, tmp_path):
        """deleted-файлы и .git/__pycache__ не попадают в архив."""
        zip_path = build_zip(
            task_id="task_123",
            changed_files=sample_files,
            output_dir=str(tmp_path),
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert not any(n.endswith("old.py") for n in names)
            assert not any(".git" in n for n in names)
            assert not any("__pycache__" in n for n in names)

    def test_empty_files_returns_none(self, tmp_path):
        """Пустой changed_files → None."""
        assert (
            build_zip(task_id="t", changed_files=[], output_dir=str(tmp_path))
            is None
        )

    def test_all_deleted_returns_none(self, tmp_path):
        """Все файлы deleted → None."""
        files = [{"path": "a.py", "status": "deleted", "content": None}]
        assert (
            build_zip(task_id="t", changed_files=files, output_dir=str(tmp_path))
            is None
        )

    def test_path_traversal_sanitized(self, tmp_path):
        """../ в пути не создаёт файлы вне корня архива."""
        files = [
            {"path": "../../etc/passwd", "status": "added", "content": "root"},
        ]
        zip_path = build_zip(
            task_id="task_x", changed_files=files, output_dir=str(tmp_path)
        )
        assert zip_path is not None

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert all(not n.startswith("/") for n in names)
            assert all(".." not in n for n in names)

    def test_readme_contains_task(self, sample_files, tmp_path):
        """README содержит описание задачи."""
        zip_path = build_zip(
            task_id="task_123",
            changed_files=sample_files,
            output_dir=str(tmp_path),
            task_description="Сделать Telegram-бота",
        )

        with zipfile.ZipFile(zip_path) as zf:
            readme_name = next(n for n in zf.namelist() if n.endswith("README.md"))
            readme = zf.read(readme_name).decode("utf-8")
            assert "Telegram-бота" in readme
            assert "main.py" in readme
