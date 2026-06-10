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
    base_url="https://api.deepseek.com",
)
sandbox = NodeSandbox()


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
    prompt = f"Задача: {state['task']}\n\n"
    if state["error"]:
        prompt += (
            f"Предыдущий код упал с ошибкой:\n{state['error']}\n"
            "Исправь код. Верни ТОЛЬКО код на JavaScript без markdown-разметки."
        )

    response = flash_llm.invoke(prompt)
    clean = response.content.replace("```javascript", "").replace("```", "").strip()
    return {"code": clean, "iterations": state["iterations"] + 1}


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
        return {"success": False, "error": result["output"], "test_passed": False}


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
