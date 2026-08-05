"""
Тесты для worker.py и chat.py с мокнутыми зависимостями.

Покрытие (worker):
1. ✅ execute_coding_task_sync — успешный прогон
2. ✅ execute_coding_task_sync — ошибка rsync → status="error"
3. ✅ execute_coding_task_sync — ошибка графа → status="error"
4. ✅ execute_coding_task_sync — finally: cleanup вызывается
5. ✅ _cleanup_resources — Docker + sandbox очистка
6. ✅ _cleanup_resources — ошибка Docker не прерывает sandbox

Покрытие (chat):
7. ✅ _truncate_code — короткий код (без обрезки)
8. ✅ _truncate_code — длинный код (обрезка)
9. ✅ _truncate_code — пустой код
10. ✅ _extract_code — ```js блок
11. ✅ _extract_code — ```javascript блок
12. ✅ _extract_code — без маркеров (эвристика)
13. ✅ _recent_history — пустая история
14. ✅ _recent_history — с сообщениями
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ═══════════════════════════════════════════════════════════════════
#  worker.py
# ═══════════════════════════════════════════════════════════════════

class TestExecuteCodingTaskSync:
    """execute_coding_task_sync — оркестратор LangGraph-цикла."""

    def test_success(self, mocker, mock_env, tmp_path):
        """Успешный прогон: sync → git init → graph.invoke → diff → success."""
        from worker import execute_coding_task_sync

        # Реальный sandbox dir (нужен для git init и compute_sandbox_diff)
        sandbox_dir = tmp_path / "sandbox" / "unit-test-001"
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        # Mock sync — возвращаем существующий путь
        mocker.patch("worker.sync_project_to_sandbox", return_value=str(sandbox_dir))
        # Mock git init/config/add/commit (Phase 4: инициализация git в sandbox)
        mocker.patch("worker.subprocess.run", return_value=None)
        # Mock TDD-генератор (Phase 4: tools.test_generator)
        mocker.patch("tools.test_generator.generate_tests_for_task", return_value=[])
        # Mock graph
        mock_graph = mocker.patch("worker.coding_graph")
        mock_graph.invoke.return_value = {
            "success": True,
            "iterations": 2,
            "code": "// code",
            "error": "",
            "changed_files": [],
        }
        # Mock diff
        mocker.patch("worker.compute_sandbox_diff", return_value=[
            {"path": "app.js", "status": "modified", "diff": "+console.log"},
        ])
        # Mock cleanup (чтобы не трогать реальную ФС)
        mocker.patch("worker._cleanup_resources")
        # Mock pub/sub
        mocker.patch("worker._publish_result_notification")
        # Mock update_job_meta
        mocker.patch("worker._update_job_meta")
        # Mock deliverable builder (zip)
        mocker.patch("tools.deliverable_builder.build_zip", return_value="/tmp/unit-test-001.zip")

        result = execute_coding_task_sync(
            task_description="Add hello endpoint",
            task_id="unit-test-001",
            project_dir="/tmp/fake-project",
        )

        assert result["status"] == "success"
        assert result["task_id"] == "unit-test-001"
        assert result["iterations"] == 2
        assert len(result["changed_files"]) == 1

    def test_rsync_error(self, mocker, mock_env):
        """Ошибка rsync → status='error'."""
        from worker import execute_coding_task_sync

        mocker.patch(
            "worker.sync_project_to_sandbox",
            side_effect=RuntimeError("rsync failed"),
        )
        mocker.patch("worker._update_job_meta")
        mocker.patch("worker._publish_result_notification")
        mocker.patch("worker._cleanup_resources")

        result = execute_coding_task_sync(
            task_description="test",
            task_id="rsync-fail",
        )

        assert result["status"] == "error"
        assert "rsync" in result.get("error", "")

    def test_graph_error(self, mocker, mock_env, tmp_path):
        """Ошибка графа → status='error'."""
        from worker import execute_coding_task_sync

        sandbox_dir = tmp_path / "sandbox" / "graph-crash"
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        mocker.patch("worker.sync_project_to_sandbox", return_value=str(sandbox_dir))
        mocker.patch("worker.subprocess.run", return_value=None)
        mocker.patch("tools.test_generator.generate_tests_for_task", return_value=[])
        mock_graph = mocker.patch("worker.coding_graph")
        mock_graph.invoke.side_effect = Exception("Graph crashed")
        mocker.patch("worker.compute_sandbox_diff", return_value=[])
        mocker.patch("worker._cleanup_resources")
        mocker.patch("worker._publish_result_notification")
        mocker.patch("worker._update_job_meta")

        result = execute_coding_task_sync(
            task_description="test",
            task_id="graph-crash",
        )

        assert result["status"] == "error"
        assert "Graph crashed" in result.get("error", "")

    def test_cleanup_called_in_finally(self, mocker, mock_env, tmp_path):
        """cleanup вызывается в finally блоке (даже при ошибке)."""
        from worker import execute_coding_task_sync

        sandbox_dir = tmp_path / "sandbox" / "cleanup-check"
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        mocker.patch("worker.sync_project_to_sandbox", return_value=str(sandbox_dir))
        mocker.patch("worker.subprocess.run", return_value=None)
        mocker.patch("tools.test_generator.generate_tests_for_task", return_value=[])
        mock_graph = mocker.patch("worker.coding_graph")
        mock_graph.invoke.return_value = {"success": True, "iterations": 1,
                                           "code": "", "error": "",
                                           "changed_files": []}
        mocker.patch("worker.compute_sandbox_diff", return_value=[])
        mock_cleanup = mocker.patch("worker._cleanup_resources")
        mocker.patch("worker._publish_result_notification")
        mocker.patch("worker._update_job_meta")

        execute_coding_task_sync("test", task_id="cleanup-check")

        mock_cleanup.assert_called_once()


class TestCleanupResources:
    """Очистка ресурсов после выполнения задачи."""

    def test_cleanup_docker_and_sandbox(self, mocker, tmp_path):
        """Docker cleanup + sandbox удаление."""
        from worker import _cleanup_resources

        mock_cleanup_containers = mocker.patch("docker_manager.cleanup_containers")
        sandbox_dir = str(tmp_path / "sandbox" / "clean-test")
        os.makedirs(sandbox_dir, exist_ok=True)

        _cleanup_resources("clean-test", sandbox_dir)

        mock_cleanup_containers.assert_called_once_with("clean-test")
        assert not os.path.exists(sandbox_dir)

    def test_docker_error_does_not_block_sandbox(self, mocker, tmp_path):
        """Ошибка Docker не прерывает удаление sandbox."""
        from worker import _cleanup_resources

        mocker.patch(
            "docker_manager.cleanup_containers",
            side_effect=Exception("Docker error"),
        )
        sandbox_dir = str(tmp_path / "sandbox" / "partial-clean")
        os.makedirs(sandbox_dir, exist_ok=True)

        _cleanup_resources("partial", sandbox_dir)

        assert not os.path.exists(sandbox_dir)


# ═══════════════════════════════════════════════════════════════════
#  chat.py
# ═══════════════════════════════════════════════════════════════════

class TestTruncateCode:
    """_truncate_code — обрезка длинного кода."""

    def test_short_code(self, mock_env):
        """Короткий код (меньше max_lines) — не обрезается."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        code = "line1\nline2\nline3\n"
        result = arch._truncate_code(code, max_lines=10)
        assert "line1" in result
        assert "line3" in result
        assert "more lines" not in result

    def test_long_code_truncated(self, mock_env):
        """Длинный код — обрезается с суффиксом."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        code = "\n".join([f"line{i}" for i in range(100)])
        result = arch._truncate_code(code, max_lines=10)
        lines = result.split("\n")
        assert len(lines) <= 15  # 10 lines + suffix
        assert "more lines" in result

    def test_empty_code(self, mock_env):
        """Пустой код."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        result = arch._truncate_code("", max_lines=50)
        assert result == ""


class TestExtractCode:
    """_extract_code — извлечение кода из ответа LLM."""

    def test_js_markdown_block(self, mock_env):
        """```js блок — извлекается содержимое."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        text = (
            "Вот код:\n"
            "```js\n"
            "const x = 1;\n"
            "console.log(x);\n"
            "```\n"
            "Готово!"
        )
        result = arch._extract_code(text)
        assert "const x = 1" in result
        assert "console.log" in result

    def test_javascript_markdown_block(self, mock_env):
        """```javascript блок — извлекается."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        text = (
            "```javascript\n"
            "function hello() {\n"
            '  return "world";\n'
            "}\n"
            "```"
        )
        result = arch._extract_code(text)
        assert "function hello" in result
        assert "return" in result

    def test_no_markers_heuristic(self, mock_env):
        """Без маркеров — эвристика (require, import, const)."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        text = (
            'const express = require("express");\n'
            "const app = express();\n"
        )
        result = arch._extract_code(text)
        assert "express" in result

    def test_no_code_at_all(self, mock_env):
        """Нет кода — возвращается как есть."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        text = "Просто текстовый ответ без кода."
        result = arch._extract_code(text)
        assert result == text


class TestRecentHistory:
    """_recent_history — история последних сообщений."""

    def test_empty_history(self, mock_env):
        """Пустая история."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        assert arch._recent_history() == ""

    def test_with_messages(self, mock_env):
        """С сообщениями в истории."""
        from chat import ChatArchitect
        arch = ChatArchitect()
        arch.history = [
            {"role": "user", "text": "Привет"},
            {"role": "assistant", "text": "Здравствуйте"},
        ]
        result = arch._recent_history()
        assert "Привет" in result
        assert "Здравствуйте" in result
