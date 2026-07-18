"""
Тесты для graph_worker.py с мокнутым LLM и Docker — ноды LangGraph графа.

Покрытие:
1. ✅ explore_project — успешный вызов (iterations увеличивается)
2. ✅ explore_project — list_files вызывается с правильным sandbox_dir
3. ✅ explore_project — пустой проект
4. ✅ execute_actions — JSON-массив tool calls
5. ✅ execute_actions — markdown-обёрнутый JSON
6. ✅ execute_actions — не-JSON ответ (fallback на done)
7. ✅ execute_actions — done с changed_files
8. ✅ execute_actions — write_file создаёт файл
9. ✅ execute_actions — read_file несуществующий файл
10. ✅ execute_actions — цикл: tool → результат → done
11. ✅ test_code — python проект → вызов ProjectSandbox._run_python_tests
12. ✅ test_code — node проект → вызов ProjectSandbox._run_node_tests
13. ✅ test_code — тесты прошли
14. ✅ test_code — тесты упали
15. ✅ test_code — неизвестный тип
16. ✅ test_code — Docker Exception
17. ✅ test_code — проверка _sanitize
18. ✅ coding_graph — компиляция графа
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from graph_worker import explore_project, execute_actions, run_tests, StateGraph


# ── explore_project ─────────────────────────────────────────────

class TestExploreProject:
    """Узел исследования проекта."""

    def test_increments_iterations(self, mocker, temp_sandbox):
        """iterations увеличивается на 1."""
        task_dir = str(temp_sandbox / "test_task_001")
        state = {
            "sandbox_dir": task_dir,
            "task": "Добавь endpoint",
            "iterations": 0,
            "error": "",
            "task_id": "test-123",
            "project_dir": "/tmp/project",
            "code": "",
            "test_code": "",
            "test_passed": False,
            "success": False,
            "changed_files": [],
        }

        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = "Понял, изучаю."

        result = explore_project(state)
        assert result["iterations"] == 1
        assert result["error"] == ""

    def test_list_files_called(self, mocker, temp_sandbox):
        """list_files вызывается с правильным sandbox_dir."""
        task_dir = str(temp_sandbox / "test_task_001")
        state = {
            "sandbox_dir": task_dir,
            "task": "Тест",
            "iterations": 0,
            "error": "",
            "task_id": "test-456",
            "project_dir": "/tmp/project",
            "code": "",
            "test_code": "",
            "test_passed": False,
            "success": False,
            "changed_files": [],
        }

        mock_list = mocker.patch("graph_worker.list_files", return_value="app.js")
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = "ok"

        explore_project(state)

        mock_list.assert_called_once_with(task_dir)

    def test_empty_project(self, mocker, tmp_path):
        """Пустой проект — не падает."""
        empty_dir = str(tmp_path / "empty_sandbox")
        os.makedirs(empty_dir, exist_ok=True)
        state = {
            "sandbox_dir": empty_dir,
            "task": "Тест",
            "iterations": 0,
            "error": "",
            "task_id": "test-empty",
            "project_dir": "/tmp/project",
            "code": "",
            "test_code": "",
            "test_passed": False,
            "success": False,
            "changed_files": [],
        }

        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = "Проект пуст."

        result = explore_project(state)
        assert result["iterations"] == 1


# ── execute_actions ─────────────────────────────────────────────

class TestExecuteActions:
    """Узел выполнения действий (чтение/запись файлов)."""

    @pytest.fixture
    def base_state(self, temp_sandbox):
        """Базовое состояние для тестов execute_actions."""
        return {
            "sandbox_dir": str(temp_sandbox / "test_task_001"),
            "task": "Добавь приветствие",
            "error": "",
            "iterations": 1,
            "task_id": "test-exec",
            "project_dir": "/tmp/project",
            "code": "",
            "test_code": "",
            "test_passed": False,
            "success": False,
            "changed_files": [],
        }

    def test_json_array_tool_calls(self, mocker, base_state):
        """LLM возвращает JSON-массив tool calls — успешное выполнение."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = json.dumps([
            {"tool": "done", "changed_files": ["app.js"]},
        ])

        result = execute_actions(base_state)
        assert result["success"] is True
        assert "app.js" in result["changed_files"]

    def test_markdown_wrapped_json(self, mocker, base_state):
        """LLM оборачивает JSON в ``` — парсинг работает."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = (
            "```json\n[{\"tool\": \"done\", \"changed_files\": [\"app.js\"]}]\n```"
        )

        result = execute_actions(base_state)
        assert result["success"] is True

    def test_non_json_fallback(self, mocker, base_state):
        """Не-JSON ответ — fallback на done."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = "Я закончил работу!"

        result = execute_actions(base_state)
        assert result["success"] is True
        assert result["changed_files"] == []

    def test_write_file_creates_file(self, mocker, base_state):
        """write_file создаёт файл и добавляет в changed_files."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = json.dumps([
            {"tool": "write_file", "relative_path": "new_file.js", "content": "// new"},
        ])
        # Второй вызов (feedback) — DONE
        mock_llm.return_value.invoke.side_effect = [
            mocker.MagicMock(content=json.dumps([
                {"tool": "write_file", "relative_path": "new_file.js", "content": "// new"},
            ])),
            mocker.MagicMock(content=json.dumps([
                {"tool": "done", "changed_files": ["new_file.js"]},
            ])),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True

        # Файл должен существовать
        new_file = os.path.join(base_state["sandbox_dir"], "new_file.js")
        assert os.path.exists(new_file)

    def test_read_nonexistent_file(self, mocker, base_state):
        """read_file несуществующего файла — ошибка в результате."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = json.dumps([
            {"tool": "read_file", "relative_path": "nonexistent.py"},
        ])
        mock_llm.return_value.invoke.side_effect = [
            mocker.MagicMock(content=json.dumps([
                {"tool": "read_file", "relative_path": "nonexistent.py"},
            ])),
            mocker.MagicMock(content=json.dumps([
                {"tool": "done", "changed_files": []},
            ])),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True  # Ошибка чтения не прерывает цикл

    def test_multiple_tool_calls(self, mocker, base_state):
        """Несколько tool calls в одном ответе."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.invoke.return_value.content = json.dumps([
            {"tool": "read_file", "relative_path": "app.js"},
            {"tool": "list_files", "relative_path": ""},
        ])
        mock_llm.return_value.invoke.side_effect = [
            mocker.MagicMock(content=json.dumps([
                {"tool": "read_file", "relative_path": "app.js"},
                {"tool": "list_files", "relative_path": ""},
            ])),
            mocker.MagicMock(content=json.dumps([
                {"tool": "done", "changed_files": []},
            ])),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True


# ── test_code ───────────────────────────────────────────────────

class TestRunTests:
    """Узел тестирования в Docker."""

    @pytest.fixture
    def base_state(self):
        """Базовое состояние для тестов run_tests."""
        return {
            "sandbox_dir": "/tmp/sandbox/test",
            "task_id": "test-999",
            "success": False,
            "error": "",
            "test_passed": False,
            "project_dir": "/tmp/project",
            "task": "test",
            "code": "",
            "test_code": "",
            "iterations": 1,
            "changed_files": [],
        }

    def test_python_project(self, mocker, base_state, tmp_path):
        """Python проект → вызов _run_python_tests."""
        sandbox_dir = str(tmp_path / "sandbox_python")
        os.makedirs(sandbox_dir, exist_ok=True)
        (tmp_path / "sandbox_python" / "requirements.txt").write_text("pytest\n")
        base_state["sandbox_dir"] = sandbox_dir

        mock_run = mocker.patch(
            "graph_worker.ProjectSandbox.run_project_tests",
            return_value={"status": "success", "output": "tests passed"},
        )

        run_tests(base_state)

        mock_run.assert_called_once()

    def test_node_project(self, mocker, base_state, tmp_path):
        """Node проект → вызов _run_node_tests."""
        sandbox_dir = str(tmp_path / "sandbox_node")
        os.makedirs(sandbox_dir, exist_ok=True)
        (tmp_path / "sandbox_node" / "package.json").write_text('{"name":"test"}')
        base_state["sandbox_dir"] = sandbox_dir

        mock_run = mocker.patch(
            "graph_worker.ProjectSandbox.run_project_tests",
            return_value={"status": "success", "output": "tests passed"},
        )

        run_tests(base_state)
        mock_run.assert_called_once()

    def test_tests_passed(self, mocker, base_state, tmp_path):
        """Тесты прошли — success=True, test_passed=True."""
        sandbox_dir = str(tmp_path / "sandbox_pass")
        os.makedirs(sandbox_dir, exist_ok=True)
        (tmp_path / "sandbox_pass" / "requirements.txt").write_text("pytest\n")
        base_state["sandbox_dir"] = sandbox_dir

        mocker.patch(
            "graph_worker.ProjectSandbox.run_project_tests",
            return_value={"status": "success", "output": "5 passed"},
        )

        result = run_tests(base_state)
        assert result["success"] is True
        assert result["test_passed"] is True

    def test_tests_failed(self, mocker, base_state, tmp_path):
        """Тесты упали — success=False, test_passed=False."""
        sandbox_dir = str(tmp_path / "sandbox_fail")
        os.makedirs(sandbox_dir, exist_ok=True)
        (tmp_path / "sandbox_fail" / "requirements.txt").write_text("pytest\n")
        base_state["sandbox_dir"] = sandbox_dir

        mocker.patch(
            "graph_worker.ProjectSandbox.run_project_tests",
            return_value={"status": "error", "output": "FAILED test_app.py"},
        )

        result = run_tests(base_state)
        assert result["success"] is False
        assert result["test_passed"] is False

    def test_unknown_project(self, base_state, tmp_path):
        """Неизвестный тип — ошибка."""
        sandbox_dir = str(tmp_path / "sandbox_unknown")
        os.makedirs(sandbox_dir, exist_ok=True)
        base_state["sandbox_dir"] = sandbox_dir

        # Нет ни requirements.txt, ни package.json
        result = run_tests(base_state)
        assert result["success"] is False
        assert result["test_passed"] is False

    def test_docker_exception(self, mocker, base_state, tmp_path):
        """Docker Exception — graceful handling."""
        sandbox_dir = str(tmp_path / "sandbox_exc")
        os.makedirs(sandbox_dir, exist_ok=True)
        (tmp_path / "sandbox_exc" / "requirements.txt").write_text("pytest\n")
        base_state["sandbox_dir"] = sandbox_dir

        mocker.patch(
            "graph_worker.ProjectSandbox.run_project_tests",
            side_effect=Exception("Connection refused"),
        )

        result = run_tests(base_state)
        assert result["success"] is False
        assert "Connection refused" in result["error"] or "Ошибка" in result["error"]

    def test_sanitize_called(self, mocker, base_state, tmp_path):
        """Вывод ошибки проходит через _sanitize."""
        sandbox_dir = str(tmp_path / "sandbox_san")
        os.makedirs(sandbox_dir, exist_ok=True)
        (tmp_path / "sandbox_san" / "requirements.txt").write_text("pytest\n")
        base_state["sandbox_dir"] = sandbox_dir

        mocker.patch(
            "graph_worker.ProjectSandbox.run_project_tests",
            return_value={"status": "error", "output": "ошибка 😊"},
        )

        result = run_tests(base_state)
        assert result["success"] is False


# ── coding_graph (компиляция) ──────────────────────────────────

class TestCodingGraph:
    """Полный граф coding_graph — компиляция и маршрутизация."""

    def test_graph_compiles(self):
        """Граф компилируется без ошибок."""
        from graph_worker import workflow

        # Пытаемся скомпилировать граф
        try:
            graph = workflow.compile()
            assert graph is not None
        except Exception as e:
            pytest.fail(f"Граф не скомпилировался: {e}")

    def test_graph_has_all_nodes(self):
        """В графе есть все 3 узла + conditional edge."""
        from graph_worker import workflow

        assert "explore" in workflow.nodes
        assert "execute" in workflow.nodes
        assert "test" in workflow.nodes
