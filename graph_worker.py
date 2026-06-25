"""LangGraph-цикл: explore → generate → test → (исправление) → конец.

Phase B: агент работает внутри sandbox с полным проектом.
Умеет читать/писать файлы через FileManagementTools.
"""

import os
from typing import TypedDict

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from docker_manager import ProjectSandbox
from tools.file_tools import list_files, read_file, write_file

load_dotenv()


# ── Lazy-инициализация LLM ──────────────────────────────────────

_flash_llm = None

def _get_llm() -> ChatOpenAI:
    """Возвращает экземпляр LLM (lazy singleton — не требует API key при импорте)."""
    global _flash_llm
    if _flash_llm is None:
        _flash_llm = ChatOpenAI(
            model="routerai/deepseek-v4-flash",
            api_key=os.getenv("ROUTERAI_API_KEY"),
            base_url="https://api.routerai.com/v1",
        )
    return _flash_llm


# ── Фильтр cp1251-несовместимых символов ─────────────────────────

def _sanitize(text: str) -> str:
    """Заменяет символы, не поддерживаемые cp1251, на '?'."""
    return text.encode("cp1251", errors="replace").decode("cp1251")


# ── Состояние графа ──────────────────────────────────────────────
class AgentState(TypedDict):
    task_id: str          # уникальный ID задачи (для изоляции sandbox)
    sandbox_dir: str      # абсолютный путь к sandbox/{task_id}/
    project_dir: str      # исходный проект (для справки)
    task: str             # описание задачи от Архитектора
    code: str             # итоговый код (для обратной совместимости)
    test_code: str        # тестовый скрипт (опционально)
    test_passed: bool     # тест пройден?
    error: str            # последняя ошибка выполнения
    iterations: int       # счётчик попыток
    success: bool         # финальный успех
    changed_files: list   # список изменённых файлов


# ── Узел: исследование проекта ──────────────────────────────────
def explore_project(state: AgentState):
    """Первый запуск: показать агенту структуру проекта."""
    sandbox_dir = state["sandbox_dir"]
    task = state["task"]

    # Получаем дерево проекта
    tree = list_files(sandbox_dir)

    prompt = (
        f"Ты — AI-разработчик. Твоя задача:\n{task}\n\n"
        "Перед тобой — полная копия проекта. Изучи его структуру и файлы, "
        "потом внеси необходимые изменения.\n\n"
        "Текущая структура проекта:\n"
        "─────────────────────────────────\n"
        f"{tree}\n"
        "─────────────────────────────────\n\n"
        "ИНСТРУКЦИЯ ПО РАБОТЕ:\n"
        "1. Сначала изучи структуру — она показана выше.\n"
        "2. Прочитай нужные файлы через read_file().\n"
        "3. Внеси изменения через write_file().\n"
        "4. Когда закончишь, напиши 'DONE' и перечисли изменённые файлы.\n"
        "5. НЕ выдумывай информацию — читай реальные файлы проекта.\n"
        "6. НЕ трогай .env, конфиги с секретами.\n\n"
        "Какие файлы тебе нужно прочитать для начала работы?"
    )

    response = _get_llm().invoke(prompt)
    return {"error": "", "iterations": state["iterations"] + 1}


# ── Узел: выполнение действий (чтение/запись файлов) ────────────
def execute_actions(state: AgentState):
    """Агент читает и пишет файлы. Вызывается повторно, пока не скажет DONE."""
    sandbox_dir = state["sandbox_dir"]
    task = state["task"]
    last_error = state["error"]

    # Собираем контекст: читаем ключевые файлы для понимания задачи
    # Сначала покажем агенту результат предыдущей итерации (если была ошибка)
    error_context = ""
    if last_error:
        error_context = f"\nПредыдущая попытка упала с ошибкой:\n{last_error}\nИсправь код.\n"

    # Даём агенту инструменты через prompt
    # В LangGraph мы НЕ вызываем инструменты автоматически, а даём LLM
    # описание инструментов и просим вернуть JSON с вызовами.
    # Парсим ответ и вызываем соответствующие функции.

    prompt = (
        f"Ты — AI-разработчик. Задача: {task}\n"
        f"{error_context}\n"
        "Ты работаешь в копии проекта. У тебя есть инструменты:\n\n"
        "1. read_file(relative_path) — прочитать файл (вернёт содержимое)\n"
        "2. write_file(relative_path, content) — записать/перезаписать файл\n"
        "3. list_files(relative_path='') — показать содержимое папки\n\n"
        "ВАЖНО:\n"
        "- Верни ТОЛЬКО JSON-массив вызовов инструментов.\n"
        "- Формат: [{\"tool\": \"read_file\", \"relative_path\": \"...\"}, ...]\n"
        "- Если задача решена, верни: [{\"tool\": \"done\", \"changed_files\": [\"file1.py\", \"file2.py\"]}]\n"
        "- Если нужно исправить ошибку — сделай write_file с исправленным кодом.\n"
        "- Не используй markdown, только JSON.\n"
        "- Относительные пути считаются от корня проекта.\n"
    )

    response = _get_llm().invoke(prompt)
    raw = _sanitize(response.content)

    # Парсим JSON-ответ
    import json as _json
    try:
        # Извлекаем JSON из ответа
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        calls = _json.loads(raw)
        if not isinstance(calls, list):
            calls = [calls]
    except Exception:
        # Если не JSON — считаем, что это текстовый ответ (DONE)
        calls = [{"tool": "done", "changed_files": []}]

    # Выполняем вызовы
    results = []
    changed = []
    is_done = False

    for call in calls:
        tool = call.get("tool", "")
        if tool == "done":
            is_done = True
            changed = call.get("changed_files", [])
            break
        elif tool == "read_file":
            path = call.get("relative_path", "")
            result = read_file(sandbox_dir, path)
            results.append(f"--- read_file({path}) ---\n{result}")
        elif tool == "write_file":
            path = call.get("relative_path", "")
            content = call.get("content", "")
            result = write_file(sandbox_dir, path, content)
            results.append(result)
            if "✅" in result:
                changed.append(path)
        elif tool == "list_files":
            path = call.get("relative_path", "")
            result = list_files(sandbox_dir, path)
            results.append(f"--- list_files({path}) ---\n{result}")

    # Если агент не сказал DONE, даём ему результаты и просим продолжить
    if not is_done:
        log = "\n\n".join(results)
        feedback_prompt = (
            f"Результаты выполнения:\n{log}\n\n"
            "Продолжай. Если всё готово — верни [{\"tool\": \"done\", \"changed_files\": [...]}]"
        )
        feedback = _get_llm().invoke(feedback_prompt)
        try:
            raw2 = feedback.content
            if "```json" in raw2:
                raw2 = raw2.split("```json")[1].split("```")[0].strip()
            elif "```" in raw2:
                raw2 = raw2.split("```")[1].split("```")[0].strip()
            done_call = _json.loads(raw2)
            if isinstance(done_call, list):
                for c in done_call:
                    if c.get("tool") == "done":
                        is_done = True
                        changed = c.get("changed_files", [])
                        break
            elif done_call.get("tool") == "done":
                is_done = True
                changed = done_call.get("changed_files", [])
        except Exception:
            pass

    return {
        "success": True,
        "changed_files": changed,
        "iterations": state["iterations"] + 1,
    }


# ── Узел: тестирование в Docker ─────────────────────────────────
def test_code(state: AgentState):
    """Запустить тесты проекта в Docker-контейнере."""
    sandbox_dir = state["sandbox_dir"]

    # Определяем тип проекта через штатный метод ProjectSandbox
    sandbox_type = ProjectSandbox.detect_project_type(sandbox_dir)

    # Устанавливаем зависимости и запускаем тесты
    try:
        result = ProjectSandbox.run_project_tests(sandbox_dir, sandbox_type)
    except Exception as e:
        return {
            "success": False,
            "error": _sanitize(f"Ошибка Docker: {e}"),
            "test_passed": False,
        }

    if result["status"] == "success":
        return {"success": True, "error": "", "test_passed": True}
    else:
        return {
            "success": False,
            "error": _sanitize(result["output"]),
            "test_passed": False,
        }


# ── Маршрутизация ────────────────────────────────────────────────
def route_next_step(state: AgentState):
    if state["success"]:
        return END
    if state["iterations"] >= 3:
        return END
    return "execute"


# ── Сборка графа ─────────────────────────────────────────────────
workflow = StateGraph(AgentState)

workflow.add_node("explore", explore_project)
workflow.add_node("execute", execute_actions)
workflow.add_node("test", test_code)

workflow.set_entry_point("explore")
workflow.add_edge("explore", "execute")
workflow.add_edge("execute", "test")
workflow.add_conditional_edges("test", route_next_step)

coding_graph = workflow.compile()
