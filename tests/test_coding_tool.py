"""
Тесты для tools/coding_tool.py — CrewAI-инструмент LangGraphCodingTool.

Покрытие:
1. ✅ CodeGenerationInput — валидация: task_description обязателен
2. ✅ CodeGenerationInput — test_code опционален
3. ✅ CodeGenerationInput — project_dir опционален
4. ✅ _run — success path: [OK] + changed_files
5. ✅ _run — success с test_code (test passed)
6. ✅ _run — success без изменений
7. ✅ _run — failure path: [FAIL] + error + iterations
8. ✅ _run — failure с changed_files до ошибки
9. ✅ _run — неполный result (missing keys) не падает с KeyError
10. ✅ Инструмент создаётся с правильными name/description/args_schema
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tools.coding_tool import LangGraphCodingTool, CodeGenerationInput


class TestCodeGenerationInput:
    """Pydantic-схема входных данных."""

    def test_task_description_required(self):
        """task_description — обязательное поле."""
        with pytest.raises(Exception):
            CodeGenerationInput()

    def test_valid_minimal(self):
        """Минимальный валидный инпут."""
        inp = CodeGenerationInput(task_description="Добавь endpoint")
        assert inp.task_description == "Добавь endpoint"
        assert inp.test_code is None
        assert inp.project_dir is None

    def test_with_all_fields(self):
        """Все поля заполнены."""
        inp = CodeGenerationInput(
            task_description="Рефакторинг",
            test_code='console.log("test");',
            project_dir="/tmp/my-project",
        )
        assert inp.test_code == 'console.log("test");'
        assert inp.project_dir == "/tmp/my-project"


class TestLangGraphCodingTool:
    """CrewAI-инструмент LangGraphCodingTool."""

    def test_tool_metadata(self):
        """Инструмент имеет правильные метаданные."""
        tool = LangGraphCodingTool()
        assert tool.name == "Autonomous_NodeJS_Coder"
        assert "Инструмент для написания" in tool.description
        assert tool.args_schema == CodeGenerationInput

    def test_run_success_with_changed_files(self, mocker):
        """_run — success: [OK] + список изменённых файлов."""
        mock_result = {
            "status": "success",
            "task_id": "t-1",
            "iterations": 2,
            "code": "// code",
            "changed_files": [
                {"path": "app.js", "status": "modified"},
                {"path": "src/helper.js", "status": "added"},
            ],
            "generated_files_dir": "sandbox/t-1/",
            "log": "Код успешно сгенерирован. Изменено файлов: 2.",
        }
        mocker.patch(
            "tools.coding_tool.execute_coding_task_sync",
            return_value=mock_result,
        )

        tool = LangGraphCodingTool()
        result = tool._run("Добавь endpoint")

        assert "[OK]" in result
        assert "app.js" in result
        assert "helper.js" in result
        assert "modified" in result
        assert "added" in result
        assert "sandbox/t-1/" in result

    def test_run_success_with_test_code(self, mocker):
        """_run — success с test_code: (test passed)."""
        mock_result = {
            "status": "success",
            "task_id": "t-2",
            "changed_files": [{"path": "app.js", "status": "modified"}],
            "generated_files_dir": "sandbox/t-2/",
            "iterations": 1,
        }
        mocker.patch(
            "tools.coding_tool.execute_coding_task_sync",
            return_value=mock_result,
        )

        tool = LangGraphCodingTool()
        result = tool._run(
            "Добавь endpoint",
            test_code='assert.strictEqual(1, 1);',
        )

        assert "[OK]" in result
        assert "test passed" in result
        assert "app.js" in result

    def test_run_success_no_changes(self, mocker):
        """_run — success без изменений: (нет изменений)."""
        mock_result = {
            "status": "success",
            "task_id": "t-3",
            "changed_files": [],
            "generated_files_dir": "sandbox/t-3/",
            "iterations": 1,
        }
        mocker.patch(
            "tools.coding_tool.execute_coding_task_sync",
            return_value=mock_result,
        )

        tool = LangGraphCodingTool()
        result = tool._run("Минимальное изменение")

        assert "[OK]" in result
        assert "нет изменений" in result

    def test_run_failure(self, mocker):
        """_run — failure: [FAIL] + error + iterations."""
        mock_result = {
            "status": "failed",
            "task_id": "t-4",
            "iterations": 3,
            "changed_files": [],
            "error": "Тесты не прошли: AssertionError",
        }
        mocker.patch(
            "tools.coding_tool.execute_coding_task_sync",
            return_value=mock_result,
        )

        tool = LangGraphCodingTool()
        result = tool._run("Сломанный код")

        assert "[FAIL]" in result
        assert "3 iterations" in result or "3" in result
        assert "AssertionError" in result

    def test_run_failure_with_changed_files(self, mocker):
        """_run — failure с changed_files до ошибки."""
        mock_result = {
            "status": "failed",
            "task_id": "t-5",
            "iterations": 2,
            "changed_files": [{"path": "app.js", "status": "modified"}],
            "error": "Docker timeout",
        }
        mocker.patch(
            "tools.coding_tool.execute_coding_task_sync",
            return_value=mock_result,
        )

        tool = LangGraphCodingTool()
        result = tool._run("Нестабильный код")

        assert "[FAIL]" in result
        assert "app.js" in result  # changed files показаны
        assert "Docker timeout" in result

    def test_run_missing_keys(self, mocker):
        """_run — неполный result (только status) не падает с KeyError."""
        mock_result = {"status": "success"}
        mocker.patch(
            "tools.coding_tool.execute_coding_task_sync",
            return_value=mock_result,
        )

        tool = LangGraphCodingTool()
        # Не должно быть KeyError — используем .get()
        result = tool._run("Минимальный вызов")
        assert isinstance(result, str)
