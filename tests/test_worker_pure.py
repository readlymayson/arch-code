"""
Тесты для чистых функций worker.py — атомарный запуск LangGraph-цикла.

Покрытие:
1. ✅ _make_result — status="success" с доп. kwargs
2. ✅ _make_result — status="error" без доп. полей
3. ✅ _make_result — все базовые поля (status, task_id)
4. ✅ compute_sandbox_diff — новый файл (added)
5. ✅ compute_sandbox_diff — изменённый файл (modified)
6. ✅ compute_sandbox_diff — нет изменений
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker import _make_result


# ── _make_result ─────────────────────────────────────────────────

class TestMakeResult:
    """Фабрика результата."""

    def test_basic_success(self):
        """status='success' с task_id."""
        result = _make_result("success", "task-123")
        assert result["status"] == "success"
        assert result["task_id"] == "task-123"

    def test_error_with_message(self):
        """status='error' с сообщением об ошибке."""
        result = _make_result("error", "task-456", error="Something went wrong")
        assert result["status"] == "error"
        assert result["error"] == "Something went wrong"

    def test_failure_triggers_alert(self, mocker):
        """status='failed' → отправляется алерт об ошибке."""
        mock_alert = mocker.patch("worker.send_error_alert")
        result = _make_result(
            "failed", "task-fail", error="Тесты не прошли"
        )
        mock_alert.assert_called_once()
        kwargs = mock_alert.call_args.kwargs
        assert kwargs["task_id"] == "task-fail"
        assert "Тесты не прошли" in kwargs["error"]

    def test_success_no_alert(self, mocker):
        """status='success' → алерт НЕ отправляется."""
        mock_alert = mocker.patch("worker.send_error_alert")
        _make_result("success", "task-ok")
        mock_alert.assert_not_called()

    def test_alert_failure_graceful(self, mocker):
        """Сбой отправки алерта не ломает результат."""
        mocker.patch(
            "worker.send_error_alert",
            side_effect=RuntimeError("webhook down"),
        )
        result = _make_result("error", "task-err", error="boom")
        assert result["status"] == "error"
        assert result["error"] == "boom"

    def test_with_extra_fields(self):
        """Дополнительные kwargs."""
        result = _make_result(
            "success", "task-789",
            iterations=3,
            code="console.log('hi')",
            changed_files=[{"path": "app.js", "status": "modified"}],
        )
        assert result["iterations"] == 3
        assert result["code"] == "console.log('hi')"
        assert len(result["changed_files"]) == 1


# ── compute_sandbox_diff ─────────────────────────────────────────

class TestComputeSandboxDiff:
    """Git diff в песочнице (с реальным git)."""

    @pytest.fixture
    def sandbox_with_git(self, tmp_path):
        """Создаёт sandbox-директорию с git-репозиторием."""
        sandbox_dir = str(tmp_path / "sandbox" / "test_diff")
        os.makedirs(sandbox_dir, exist_ok=True)

        # Инициализируем git
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=sandbox_dir,
                       check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=sandbox_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"],
                       cwd=sandbox_dir, check=True, capture_output=True)

        # Создаём начальный коммит с одним файлом
        (tmp_path / "sandbox" / "test_diff" / "existing.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=sandbox_dir,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=sandbox_dir,
                       check=True, capture_output=True)

        return sandbox_dir

    def test_new_file_added(self, sandbox_with_git):
        """Новый файл — появляется в diff."""
        from worker import compute_sandbox_diff

        # Добавляем новый файл
        new_file_path = os.path.join(sandbox_with_git, "newfile.py")
        with open(new_file_path, "w") as f:
            f.write("y = 2\n")

        result = compute_sandbox_diff(sandbox_with_git)
        # Новые файлы могут быть 'added' или '??' в разных версиях git
        assert len(result) > 0
        assert any("newfile.py" in f["path"] for f in result)

    def test_modified_file(self, sandbox_with_git):
        """Изменённый файл — статус 'modified'."""
        from worker import compute_sandbox_diff

        # Изменяем существующий файл
        existing = os.path.join(sandbox_with_git, "existing.py")
        with open(existing, "a") as f:
            f.write("y = 2\n")

        result = compute_sandbox_diff(sandbox_with_git)
        modified = [f for f in result if f["status"] == "modified"]
        assert len(modified) >= 1
        assert any("existing.py" in f["path"] for f in modified)

    def test_no_changes(self, sandbox_with_git):
        """Без изменений — пустой список."""
        from worker import compute_sandbox_diff

        result = compute_sandbox_diff(sandbox_with_git)
        assert len(result) == 0

    def test_nonexistent_directory_handled(self):
        """Несуществующая директория — пустой список (git не найден или ошибка)."""
        from worker import compute_sandbox_diff

        result = compute_sandbox_diff("/tmp/nonexistent_sandbox_xyz")
        # Должен вернуть пустой список или ошибку, но не упасть
        assert isinstance(result, list)

    def test_deps_excluded(self, sandbox_with_git):
        """Каталог .deps (pip-зависимости) не попадает в diff.

        Регрессия OOM-бага: Docker-песочница ставит зависимости в
        sandbox/.deps (pip install --target=/app/.deps). Если их включить
        в changed_files, build_zip держит содержимое тысяч файлов в памяти
        → OOM-kill воркера. Файлы .deps должны фильтроваться.
        """
        from worker import compute_sandbox_diff

        # Реальный файл агента
        new_file_path = os.path.join(sandbox_with_git, "real_change.py")
        with open(new_file_path, "w") as f:
            f.write("print('real change')\n")

        # Мусор .deps (имитация pip install --target=/app/.deps)
        deps_dir = os.path.join(sandbox_with_git, ".deps")
        os.makedirs(os.path.join(deps_dir, "aiohttp"), exist_ok=True)
        with open(os.path.join(deps_dir, "aiohttp", "__init__.py"), "w") as f:
            f.write("pkg\n")
        with open(os.path.join(deps_dir, "setup.cfg"), "w") as f:
            f.write("[metadata]\n")
        # Вложенный .deps в подкаталоге тоже должен исключаться
        nested = os.path.join(sandbox_with_git, "sub", ".deps")
        os.makedirs(nested, exist_ok=True)
        with open(os.path.join(nested, "x.py"), "w") as f:
            f.write("nested\n")

        result = compute_sandbox_diff(sandbox_with_git)
        paths = [f["path"] for f in result]

        # Реальное изменение на месте
        assert any("real_change.py" in p for p in paths)
        # Ни одного .deps файла
        assert not any(".deps" in p for p in paths), f".deps попал в diff: {paths}"
        assert not any(p.startswith(".deps/") for p in paths)
        assert not any(p.startswith("sub/.deps/") for p in paths)

    def test_node_modules_and_pycache_excluded(self, sandbox_with_git):
        """node_modules / __pycache__ тоже не попадают в diff."""
        from worker import compute_sandbox_diff

        # Мусор
        for rel in ("node_modules/pkg/index.js", "pkg/__pycache__/mod.cpython-312.pyc"):
            full = os.path.join(sandbox_with_git, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write("x\n")

        result = compute_sandbox_diff(sandbox_with_git)
        paths = [f["path"] for f in result]

        assert not any("node_modules" in p for p in paths)
        assert not any("__pycache__" in p for p in paths)
