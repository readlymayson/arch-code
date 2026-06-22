from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from worker import execute_coding_task_sync


class CodeGenerationInput(BaseModel):
    task_description: str = Field(
        ..., description="Детальное ТЗ для написания/изменения кода."
    )
    test_code: str | None = Field(
        None,
        description=(
            "Опциональный тестовый скрипт на Node.js (используйте assert или node:test). "
            "Если передан, Docker будет прогонять тест для валидации логики, а не просто запуск кода."
        ),
    )
    project_dir: str | None = Field(
        None,
        description=(
            "Абсолютный путь к проекту, который нужно скопировать в песочницу. "
            "По умолчанию — ai-core."
        ),
    )


class LangGraphCodingTool(BaseTool):
    name: str = "Autonomous_NodeJS_Coder"
    description: str = (
        "Инструмент для написания и рефакторинга кода. Принимает ТЗ и опциональный тест. "
        "Копирует проект в песочницу, позволяет ИИ читать/писать файлы, "
        "запускает тесты в Docker, возвращает diff изменений."
    )
    args_schema: type[BaseModel] = CodeGenerationInput

    def _run(
        self,
        task_description: str,
        test_code: str | None = None,
        project_dir: str | None = None,
    ) -> str:
        result = execute_coding_task_sync(
            task_description=task_description,
            test_code=test_code,
            project_dir=project_dir,
        )

        if result["status"] == "success":
            changed = result.get("changed_files", [])
            changed_list = "\n".join(f"  • {c['path']} ({c['status']})" for c in changed) if changed else "  (нет изменений)"
            msg = "[OK] Code generated and tested!"
            if test_code:
                msg += " (test passed)"
            return (
                f"{msg}\n\n"
                f"Изменённые файлы:\n{changed_list}\n\n"
                f"Sandbox: {result.get('generated_files_dir', '?')}"
            )
        else:
            changed = result.get("changed_files", [])
            changed_info = ""
            if changed:
                changed_info = "\nИзменённые файлы (до ошибки):\n" + "\n".join(
                    f"  • {c['path']}" for c in changed
                )
            return (
                f"[FAIL] Task failed after {result.get('iterations', '?')} iterations.\n"
                f"Error: {result.get('error', 'unknown')}{changed_info}"
            )
