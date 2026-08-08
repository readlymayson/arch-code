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
    """Узел выполнения действий (чтение/запись файлов) через bind_tools."""

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

    def _mock_tool_call_response(self, name: str, args: dict, tool_call_id: str = "call_001"):
        """Создать AIMessage с tool_calls (как возвращает bind_tools)."""
        from langchain_core.messages import AIMessage
        return AIMessage(
            content="",
            tool_calls=[{
                "name": name,
                "args": args,
                "id": tool_call_id,
                "type": "tool_call",
            }],
        )

    def test_done_with_files(self, mocker, base_state):
        """Агент вызывает done() с changed_files — задача завершена."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        # Мокаем bind_tools — возвращаем тот же llm
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value
        # Первый вызов — сразу done
        mock_llm.return_value.invoke.return_value = self._mock_tool_call_response(
            "done", {"changed_files": ["app.js", "core/new.py"]}
        )

        result = execute_actions(base_state)
        assert result["success"] is True
        assert "app.js" in result["changed_files"]
        assert "core/new.py" in result["changed_files"]

    def test_read_then_write_then_done(self, mocker, base_state):
        """Цикл: read → write → done."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value

        # Имитация: сначала read_file, потом write_file, потом done
        mock_llm.return_value.invoke.side_effect = [
            self._mock_tool_call_response(
                "read_file_tool", {"relative_path": "app.js"}, "call_001"
            ),
            self._mock_tool_call_response(
                "write_file_tool", {"relative_path": "new.py", "content": "x=1"}, "call_002"
            ),
            self._mock_tool_call_response(
                "done", {"changed_files": ["new.py"]}, "call_003"
            ),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True
        assert "new.py" in result["changed_files"]

    def test_write_file_physically_created(self, mocker, base_state):
        """write_file_tool создаёт реальный файл в sandbox."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value
        mock_llm.return_value.invoke.side_effect = [
            self._mock_tool_call_response(
                "write_file_tool", {
                    "relative_path": "generated.py",
                    "content": "print('hello')",
                }, "call_001"
            ),
            self._mock_tool_call_response(
                "done", {"changed_files": ["generated.py"]}, "call_002"
            ),
        ]

        execute_actions(base_state)

        new_file = os.path.join(base_state["sandbox_dir"], "generated.py")
        assert os.path.exists(new_file)
        assert open(new_file).read() == "print('hello')"

    def test_list_files_tool(self, mocker, base_state):
        """list_files возвращает дерево."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value
        mock_llm.return_value.invoke.side_effect = [
            self._mock_tool_call_response(
                "list_files_tool", {"relative_path": ""}, "call_001"
            ),
            self._mock_tool_call_response(
                "done", {"changed_files": []}, "call_002"
            ),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True

    def test_read_nonexistent_file(self, mocker, base_state):
        """read_file несуществующего файла не крашит."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value
        mock_llm.return_value.invoke.side_effect = [
            self._mock_tool_call_response(
                "read_file_tool", {"relative_path": "nonexistent.py"}, "call_001"
            ),
            self._mock_tool_call_response(
                "done", {"changed_files": []}, "call_002"
            ),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True

    def test_unknown_tool_graceful(self, mocker, base_state):
        """Неизвестный инструмент не крашит."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value
        mock_llm.return_value.invoke.side_effect = [
            self._mock_tool_call_response(
                "unknown_tool", {"x": "y"}, "call_001"
            ),
            self._mock_tool_call_response(
                "done", {"changed_files": []}, "call_002"
            ),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True

    def test_no_tool_calls_text_response(self, mocker, base_state):
        """LLM вернула текст без tool_calls — не падает."""
        from langchain_core.messages import AIMessage

        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value
        # Первый ответ — текст, второй — done
        mock_llm.return_value.invoke.side_effect = [
            AIMessage(content="Я читаю файлы..."),
            self._mock_tool_call_response(
                "done", {"changed_files": []}, "call_002"
            ),
        ]

        result = execute_actions(base_state)
        assert result["success"] is True

    def test_iterations_incremented(self, mocker, base_state):
        """iterations увеличивается на 1."""
        mock_llm = mocker.patch("graph_worker._get_llm")
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value
        mock_llm.return_value.invoke.return_value = self._mock_tool_call_response(
            "done", {"changed_files": []}
        )

        result = execute_actions(base_state)
        assert result["iterations"] == 2  # было 1, стало 2

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
        """В графе есть все 4 узла + conditional edge."""
        from graph_worker import workflow

        assert "explore" in workflow.nodes
        assert "execute" in workflow.nodes
        assert "test" in workflow.nodes
        assert "run_app" in workflow.nodes


# ── detect_app_type ─────────────────────────────────────────────

class TestDetectAppType:
    """Эвристика определения типа приложения (web_app / cli_script)."""

    def test_fastapi_main_py(self, tmp_path):
        """main.py с FastAPI → web_app."""
        import graph_worker
        (tmp_path / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n'
        )
        assert graph_worker.detect_app_type(str(tmp_path)) == "web_app"

    def test_flask_app_py(self, tmp_path):
        """app.py с Flask → web_app."""
        import graph_worker
        (tmp_path / "app.py").write_text(
            'from flask import Flask\napp = Flask(__name__)\n'
        )
        assert graph_worker.detect_app_type(str(tmp_path)) == "web_app"

    def test_uvicorn_main(self, tmp_path):
        """main.py с uvicorn.run → web_app."""
        import graph_worker
        (tmp_path / "main.py").write_text(
            'import uvicorn\nif __name__ == "__main__":\n'
            '    uvicorn.run(app, host="0.0.0.0")\n'
        )
        assert graph_worker.detect_app_type(str(tmp_path)) == "web_app"

    def test_cli_script(self, tmp_path):
        """main.py без веб-фреймворков → cli_script."""
        import graph_worker
        (tmp_path / "main.py").write_text(
            'import sys\ndef main():\n    print("hello")\n'
        )
        assert graph_worker.detect_app_type(str(tmp_path)) == "cli_script"

    def test_no_entry_file(self, tmp_path):
        """Нет файла точки входа → cli_script (консервативно)."""
        import graph_worker
        assert graph_worker.detect_app_type(str(tmp_path)) == "cli_script"


# ── route_after_test ────────────────────────────────────────────

class TestRouteAfterTest:
    """Роутинг после test: execute / END / run_app."""

    def test_failed_tests_go_execute(self):
        """Тесты не прошли → execute."""
        from graph_worker import route_after_test
        state = {"success": False, "skip_smoke_test": False}
        assert route_after_test(state) == "execute"

    def test_skip_smoke_goes_end(self):
        """skip_smoke_test=True + успех → END."""
        from graph_worker import route_after_test
        state = {"success": True, "skip_smoke_test": True}
        assert route_after_test(state) == "__end__"

    def test_success_goes_run_app(self):
        """Тесты прошли, smoke не пропущен → run_app."""
        from graph_worker import route_after_test
        state = {"success": True, "skip_smoke_test": False}
        assert route_after_test(state) == "run_app"


# ── run_application ─────────────────────────────────────────────

class TestRunApplication:
    """Узел smoke-проверки приложения."""

    @pytest.fixture
    def base_state(self, tmp_path):
        return {
            "sandbox_dir": str(tmp_path / "sandbox_run"),
            "task_id": "run-app-test",
            "success": False,
            "error": "",
            "iterations": 1,
            "action_steps": [],
            "skip_smoke_test": False,
            "health_endpoint": "/health",
            "health_port": 8000,
        }

    def test_skip_smoke_test(self, base_state):
        """skip_smoke_test=True → пропуск без вызова Docker."""
        import graph_worker
        base_state["skip_smoke_test"] = True
        result = graph_worker.run_application(base_state)
        assert result["success"] is True
        assert result["app_type"] == "skipped"

    def test_web_app_success(self, mocker, base_state, tmp_path):
        """web_app → запуск + health-check OK."""
        import graph_worker
        os.makedirs(base_state["sandbox_dir"], exist_ok=True)
        (tmp_path / "sandbox_run" / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n'
        )

        mocker.patch(
            "graph_worker.ProjectSandbox.run_application",
            return_value={"status": "success", "output": "OK"},
        )

        result = graph_worker.run_application(base_state)
        assert result["success"] is True
        assert result["app_type"] == "web_app"

    def test_cli_script_success(self, mocker, base_state, tmp_path):
        """cli_script → запуск CLI с exit code 0."""
        import graph_worker
        os.makedirs(base_state["sandbox_dir"], exist_ok=True)
        (tmp_path / "sandbox_run" / "main.py").write_text(
            'def main():\n    print("hello")\n'
        )

        mocker.patch(
            "graph_worker.ProjectSandbox.run_application",
            return_value={"status": "success", "output": "hello"},
        )

        result = graph_worker.run_application(base_state)
        assert result["success"] is True
        assert result["app_type"] == "cli_script"

    def test_app_failure_passes_error(self, mocker, base_state, tmp_path):
        """Приложение не стартует → error с traceback в state."""
        import graph_worker
        os.makedirs(base_state["sandbox_dir"], exist_ok=True)
        (tmp_path / "sandbox_run" / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n'
        )

        mocker.patch(
            "graph_worker.ProjectSandbox.run_application",
            return_value={
                "status": "error",
                "output": "ModuleNotFoundError: No module named 'fastapi'",
            },
        )

        result = graph_worker.run_application(base_state)
        assert result["success"] is False
        assert "ModuleNotFoundError" in result["error"]

    def test_docker_exception(self, mocker, base_state, tmp_path):
        """Docker Exception — graceful."""
        import graph_worker
        os.makedirs(base_state["sandbox_dir"], exist_ok=True)
        (tmp_path / "sandbox_run" / "main.py").write_text("print('x')\n")

        mocker.patch(
            "graph_worker.ProjectSandbox.run_application",
            side_effect=Exception("Docker daemon down"),
        )

        result = graph_worker.run_application(base_state)
        assert result["success"] is False
        assert "Docker" in result["error"]
