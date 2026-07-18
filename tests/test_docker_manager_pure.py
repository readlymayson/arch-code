"""
Тесты для чистых функций docker_manager.py — управление Docker-песочницами.

Покрытие:
1. ✅ ProjectSandbox.detect_project_type — requirements.txt → python
2. ✅ ProjectSandbox.detect_project_type — pyproject.toml → python
3. ✅ ProjectSandbox.detect_project_type — setup.py → python
4. ✅ ProjectSandbox.detect_project_type — package.json → node
5. ✅ ProjectSandbox.detect_project_type — ничего → unknown
6. ✅ ProjectSandbox.detect_project_type — приоритет: python > node
7. ✅ NodeSandbox.__init__ — с заданным task_id
8. ✅ NodeSandbox.__init__ — без task_id (генерируется UUID)
9. ✅ NodeSandbox._ensure_package_json — создаётся при отсутствии
10. ✅ NodeSandbox._ensure_package_json — не перезаписывается при наличии
11. ✅ NodeSandbox.cleanup — удаляет директорию
12. ✅ NodeSandbox.cleanup — ничего не делает, если директории нет
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from docker_manager import NodeSandbox, ProjectSandbox


# ── ProjectSandbox.detect_project_type ─────────────────────────

class TestDetectProjectType:
    """Определение типа проекта по конфигурационным файлам."""

    def test_requirements_txt(self, tmp_path):
        """requirements.txt → python."""
        (tmp_path / "requirements.txt").write_text("pytest\n")
        assert ProjectSandbox.detect_project_type(str(tmp_path)) == "python"

    def test_pyproject_toml(self, tmp_path):
        """pyproject.toml → python."""
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n")
        assert ProjectSandbox.detect_project_type(str(tmp_path)) == "python"

    def test_setup_py(self, tmp_path):
        """setup.py → python."""
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        assert ProjectSandbox.detect_project_type(str(tmp_path)) == "python"

    def test_package_json(self, tmp_path):
        """package.json → node."""
        (tmp_path / "package.json").write_text('{"name": "test"}\n')
        assert ProjectSandbox.detect_project_type(str(tmp_path)) == "node"

    def test_unknown(self, tmp_path):
        """Ничего → unknown."""
        assert ProjectSandbox.detect_project_type(str(tmp_path)) == "unknown"

    def test_python_priority_over_node(self, tmp_path):
        """Приоритет: python > node, если есть оба."""
        (tmp_path / "requirements.txt").write_text("pytest\n")
        (tmp_path / "package.json").write_text('{"name": "test"}\n')
        assert ProjectSandbox.detect_project_type(str(tmp_path)) == "python"


# ── NodeSandbox.__init__ ─────────────────────────────────────────

class TestNodeSandboxInit:
    """Создание экземпляра NodeSandbox."""

    def test_with_task_id(self, tmp_path, monkeypatch):
        """С заданным task_id — директория создаётся с этим ID."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="my-task-42")

        assert ns.task_id == "my-task-42"
        assert ns.sandbox_dir == os.path.join(str(tmp_path), "my-task-42")
        assert os.path.exists(ns.sandbox_dir)

    def test_without_task_id(self, tmp_path, monkeypatch):
        """Без task_id — генерируется UUID."""

        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox()

        assert ns.task_id is not None
        assert len(ns.task_id) == 12  # UUID hex[:12]
        assert os.path.exists(ns.sandbox_dir)

    def test_unique_task_ids(self, tmp_path, monkeypatch):
        """Два инстанса без task_id — разные ID."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns1 = NodeSandbox()
        ns2 = NodeSandbox()
        assert ns1.task_id != ns2.task_id


# ── NodeSandbox._ensure_package_json ────────────────────────────

class TestEnsurePackageJson:
    """Создание package.json."""

    def test_creates_when_missing(self, tmp_path, monkeypatch):
        """Создаётся новый package.json при отсутствии."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="pkg-test")
        ns._ensure_package_json()

        pkg_path = os.path.join(ns.sandbox_dir, "package.json")
        assert os.path.exists(pkg_path)

        with open(pkg_path) as f:
            pkg = json.load(f)
        assert pkg["name"] == "sandbox-pkg-test"
        assert "express" in pkg.get("dependencies", {})

    def test_does_not_overwrite(self, tmp_path, monkeypatch):
        """Не перезаписывает существующий package.json."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="pkg-test2")

        # Создаём кастомный package.json
        custom_pkg = {"name": "custom", "version": "2.0.0"}
        pkg_path = os.path.join(ns.sandbox_dir, "package.json")
        with open(pkg_path, "w") as f:
            json.dump(custom_pkg, f)

        # Вызываем _ensure_package_json — не должен перезаписать
        ns._ensure_package_json()

        with open(pkg_path) as f:
            pkg = json.load(f)
        assert pkg["name"] == "custom"
        assert pkg["version"] == "2.0.0"


# ── NodeSandbox.cleanup ─────────────────────────────────────────

class TestNodeSandboxCleanup:
    """Очистка sandbox-директории."""

    def test_removes_directory(self, tmp_path, monkeypatch):
        """Удаляет sandbox-директорию."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="cleanup-test")
        assert os.path.exists(ns.sandbox_dir)

        ns.cleanup()
        assert not os.path.exists(ns.sandbox_dir)

    def test_no_error_if_missing(self, tmp_path, monkeypatch):
        """Не падает, если директории нет."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="missing-dir")

        # Удаляем директорию вручную
        os.rmdir(ns.sandbox_dir)

        # cleanup не должен падать
        ns.cleanup()


# ── ProjectSandbox.run_project_tests ────────────────────────────

class TestRunProjectTests:
    """Оркестрация запуска тестов по типу проекта."""

    def test_auto_detect_python(self, tmp_path, mocker):
        """Автоопределение: python → вызов _run_python_tests."""
        (tmp_path / "requirements.txt").write_text("pytest\n")
        mock_python = mocker.patch(
            "docker_manager.ProjectSandbox._run_python_tests",
            return_value={"status": "success", "output": "tests passed"},
        )

        result = ProjectSandbox.run_project_tests(str(tmp_path))

        mock_python.assert_called_once()
        assert result["status"] == "success"

    def test_auto_detect_node(self, tmp_path, mocker):
        """Автоопределение: node → вызов _run_node_tests."""
        (tmp_path / "package.json").write_text('{"name": "test"}\n')
        mock_node = mocker.patch(
            "docker_manager.ProjectSandbox._run_node_tests",
            return_value={"status": "success", "output": "tests passed"},
        )

        result = ProjectSandbox.run_project_tests(str(tmp_path))

        mock_node.assert_called_once()
        assert result["status"] == "success"

    def test_unknown_type(self, tmp_path):
        """Неизвестный тип проекта — ошибка."""
        result = ProjectSandbox.run_project_tests(str(tmp_path))
        assert result["status"] == "error"
        assert "определить" in result["output"]
