"""LangGraph-цикл: генерация → тестирование → (исправление) → конец."""

import os
from typing import TypedDict

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from docker_manager import NodeSandbox

load_dotenv()

flash_llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
)
sandbox = NodeSandbox()


# ── Фильтр cp1251-несовместимых символов ─────────────────────────

def _sanitize(text: str) -> str:
    """Заменяет символы, не поддерживаемые cp1251, на '?'."""
    return text.encode("cp1251", errors="replace").decode("cp1251")


# ── Состояние графа ──────────────────────────────────────────────
class AgentState(TypedDict):
    task: str          # описание задачи от Архитектора
    code: str          # сгенерированный код приложения
    test_code: str     # тестовый скрипт для валидации логики (опционально)
    test_passed: bool  # тест пройден?
    error: str         # последняя ошибка выполнения
    iterations: int    # счётчик попыток
    success: bool      # финальный успех


# ── Узел: генерация / исправление кода ──────────────────────────
def generate_code(state: AgentState):
    prompt = (
        f"Задача: {state['task']}\n\n"
        "Напиши ОДИН самодостаточный JavaScript-файл, который можно запустить "
        "через 'node app.js'. Файл должен содержать все необходимые импорты и "
        "запускать сервер (app.listen).\n"
        "Не используй многофайловую структуру — всё в одном файле.\n"
        "Требования к ответу:\n"
        "- Верни ТОЛЬКО код, без пояснений, без описаний, без markdown-разметки.\n"
        "- Не оборачивай код в ```javascript или ```.\n"
        "- Никакого текста до или после кода.\n"
        "- Код должен быть готов к выполнению 'node app.js'.\n"
        "- Все npm-пакеты (express и т.д.) будут установлены автоматически.\n"
    )
    if state["error"]:
        prompt += (
            f"\nПредыдущий код упал с ошибкой:\n{state['error']}\n"
            "Исправь ошибку. Верни ТОЛЬКО исправленный код, без пояснений."
        )

    response = flash_llm.invoke(prompt)
    raw = response.content
    # Извлекаем содержимое первой пары ``` … ``` если разметка есть
    if "```" in raw:
        parts = raw.split("```")
        # Берём первый блок между ``` и ```
        for i, part in enumerate(parts):
            if i % 2 == 1:  # нечётные индексы — содержимое блоков
                # Убираем language hint (javascript, js, etc.)
                code = part.strip()
                if "\n" in code:
                    code = code[code.index("\n") + 1 :]
                clean = code.strip()
                break
        else:
            clean = raw
    else:
        clean = raw

    return {"code": _sanitize(clean), "iterations": state["iterations"] + 1}


# ── Узел: тестирование в Docker ─────────────────────────────────
def test_code(state: AgentState):
    if state["test_code"]:
        # Есть тест — выполняем тестовый скрипт
        # Вшиваем тестируемый код в тестовый файл
        test_runner = f"{state['code']}\n\n\n{state['test_code']}"
        result = sandbox.execute_test("app.test.js", test_runner)
    else:
        # Нет теста — просто выполняем код (проверка на crash)
        result = sandbox.execute_code("app.js", state["code"])

    if result["status"] == "success":
        return {"success": True, "error": "", "test_passed": True}
    else:
        return {"success": False, "error": _sanitize(result["output"]), "test_passed": False}


# ── Маршрутизация ────────────────────────────────────────────────
def route_next_step(state: AgentState):
    if state["success"]:
        return END
    if state["iterations"] >= 3:
        return END
    return "generate"


# ── Сборка графа ─────────────────────────────────────────────────
workflow = StateGraph(AgentState)

workflow.add_node("generate", generate_code)
workflow.add_node("test", test_code)

workflow.set_entry_point("generate")
workflow.add_edge("generate", "test")
workflow.add_conditional_edges("test", route_next_step)

coding_graph = workflow.compile()
