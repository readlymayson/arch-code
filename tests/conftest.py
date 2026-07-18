"""
Общие фикстуры для тестов arch-code.

Калька с ai-core/tests/conftest.py:
- temp_sandbox: временная директория sandbox/{task_id}/
- temp_knowledge: временная директория knowledge/ с sample-файлами
- mock_env: настройка переменных окружения
"""

import os
import shutil
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_sandbox(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Создаёт временную sandbox-директорию с task_id.
    Структура:
        sandbox/
            test_task_001/
                app.js
                package.json
            subdir/
                helper.js
    """
    sandbox_root = tmp_path / "sandbox"
    task_dir = sandbox_root / "test_task_001"
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "app.js").write_text(
        'const express = require("express");\n'
        'const app = express();\n'
        'app.get("/", (req, res) => res.send("Hello"));\n'
        'app.listen(3000);\n'
    )
    (task_dir / "package.json").write_text(
        '{"name": "test-app", "version": "1.0.0", "private": true}\n'
    )

    subdir = task_dir / "subdir"
    subdir.mkdir()
    (subdir / "helper.js").write_text('module.exports = { help: () => "ok" };\n')

    yield sandbox_root


@pytest.fixture
def temp_knowledge(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Создаёт временную knowledge-директорию с документацией.
    Структура:
        knowledge/
            style-guide.md
            api.md
        other/
            secret.txt
    """
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    (knowledge_dir / "style-guide.md").write_text(
        "# Style Guide\n\n"
        "## Naming\n"
        "- Use camelCase\n"
        "- Files: kebab-case\n"
    )
    (knowledge_dir / "api.md").write_text(
        "# API Reference\n\n"
        "## Endpoints\n"
        "- GET /api/health\n"
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "secret.txt").write_text("secret content")

    yield knowledge_dir


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Устанавливает тестовые переменные окружения."""
    monkeypatch.setenv("ROUTERAI_API_KEY", "sk-test-key-12345")
    monkeypatch.setenv("ROUTERAI_BASE_URL", "https://routerai.test.ru/api/v1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def sample_task_description() -> str:
    """Типовое задание для тестов."""
    return (
        "Добавь новый endpoint GET /api/users в Express.js приложение. "
        "Он должен возвращать список пользователей из JSON-файла."
    )
