from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from graph_worker import coding_graph


class CodeGenerationInput(BaseModel):
    task_description: str = Field(
        ..., description="Детальное ТЗ для написания кода на Node.js."
    )
    test_code: str | None = Field(
        None,
        description=(
            "Опциональный тестовый скрипт на Node.js (используйте assert или node:test). "
            "Если передан, Docker будет прогонять тест для валидации логики, а не просто запуск кода."
        ),
    )


class LangGraphCodingTool(BaseTool):
    name: str = "Autonomous_NodeJS_Coder"
    description: str = (
        "Инструмент для написания и тестирования Node.js кода. "
        "Принимает ТЗ и опциональный тест. Самостоятельно исправляет ошибки "
        "в цикле (до 3 итераций) с помощью LangGraph и DeepSeek-V4-Flash."
    )
    args_schema: type[BaseModel] = CodeGenerationInput

    def _run(self, task_description: str, test_code: str | None = None) -> str:
        initial_state = {
            "task": task_description,
            "code": "",
            "test_code": test_code or "",
            "test_passed": False,
            "error": "",
            "iterations": 0,
            "success": False,
        }

        final_state = coding_graph.invoke(initial_state)

        if final_state["success"]:
            msg = "✅ Код успешно написан и протестирован!"
            if test_code:
                msg += " (тест пройден)"
            return f"{msg}\n\nКод:\n{final_state['code']}"
        else:
            return (
                f"❌ Не удалось выполнить задачу за {final_state['iterations']} итераций.\n"
                f"Последняя ошибка: {final_state['error']}\n\n"
                f"Последняя версия кода:\n{final_state['code']}"
            )
