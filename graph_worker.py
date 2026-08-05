"""LangGraph-цикл: explore → generate → test → (исправление) → конец.

Phase B: агент работает внутри sandbox с полным проектом.
Использует нативный Tool Calling (bind_tools) вместо JSON-эмуляции.
"""

import json as _json
import os
import time as _time
from typing import TypedDict

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, StateGraph

from docker_manager import ProjectSandbox
from tools.file_tools import list_files, make_coding_tools

load_dotenv()


# ── Lazy-инициализация LLM ──────────────────────────────────────

_flash_llm = None

def _get_llm() -> ChatOpenAI:
    """Возвращает экземпляр LLM (lazy singleton — не требует API key при импорте)."""
    global _flash_llm
    if _flash_llm is None:
        _flash_llm = ChatOpenAI(
            model="deepseek/deepseek-v4-flash",
            api_key=os.getenv("ROUTERAI_API_KEY"),
            base_url=os.getenv("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1"),
            timeout=float(os.getenv("ARCH_CODE_LLM_TIMEOUT", "120")),
            max_retries=1,
        )
    return _flash_llm


def _invoke_llm(llm, messages, *, timeout: float = 120.0):
    """Вызвать LLM с жёстким таймаутом.

    RouterAI иногда отвечает медленно (несколько минут). Без таймаута
    задача висит на шаге explore, пока RQ не убьёт её по job_timeout
    (600 сек), — и пользователь видит '❌ Ошибка за 0 сек на 10%'.

    Args:
        llm: Экземпляр ChatOpenAI (или bind_tools()).
        messages: Сообщения для invoke.
        timeout: Максимальное время ожидания ответа (сек).

    Returns:
        Ответ LLM (AIMessage / ChatResult).

    Raises:
        TimeoutError: Если LLM не ответил за timeout секунд.
    """
    import concurrent.futures as _cf

    with _cf.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(llm.invoke, messages)
        try:
            return future.result(timeout=timeout)
        except _cf.TimeoutError:
            future.cancel()
            raise TimeoutError(
                f"LLM не ответил за {timeout}с "
                f"(RouterAI/DeepSeek V4 Flash) — таймаут"
            )


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
    # ── Подробности для панели мониторинга ───────────────────
    thought_steps: list   # структурированные мысли агента
    action_steps: list    # структурированные действия
    chain_of_thought: str # свободная цепочка рассуждений
    # ── Billing / метрики ────────────────────────────────────
    prompt_tokens: int    # всего токенов промпта
    completion_tokens: int  # всего токенов генерации
    model: str            # название модели


# ── Утилита для таймстампов ──────────────────────────────────────


def _now_iso() -> str:
    """Текущее время в ISO-формате."""
    return _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime())


def _sum_tokens(response: object) -> dict:
    """Извлечь количество токенов из LLM-ответа.

    Returns:
        dict с prompt_tokens, completion_tokens.
    """
    try:
        md = getattr(response, "response_metadata", {}) or {}
        usage = md.get("token_usage", md.get("usage", {}))
        if isinstance(usage, dict):
            return {
                "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)),
            }
    except Exception:
        pass
    return {"prompt_tokens": 0, "completion_tokens": 0}


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
        "4. Когда закончишь, вызови done() с путями изменённых файлов.\n"
        "5. НЕ выдумывай информацию — читай реальные файлы проекта.\n"
        "6. НЕ трогай .env, конфиги с секретами.\n\n"
        "Какие файлы тебе нужно прочитать для начала работы?"
    )

    response = _invoke_llm(_get_llm(), prompt, timeout=LLM_TIMEOUT)
    thought = getattr(response, "content", str(response))[:500]
    tokens = _sum_tokens(response)

    ts = _now_iso()
    thought_steps = state.get("thought_steps", []) + [
        {"node": "explore", "thought": thought, "timestamp": ts},
    ]
    action_steps = state.get("action_steps", []) + [
        {"node": "explore", "action": f"Изучение структуры проекта ({len(tree)} строк)", "status": "completed", "timestamp": ts},
    ]

    return {
        "error": "",
        "iterations": state["iterations"] + 1,
        "thought_steps": thought_steps,
        "action_steps": action_steps,
        "chain_of_thought": state.get("chain_of_thought", "") + f"\n## Explore\n{thought}\n",
        "prompt_tokens": state.get("prompt_tokens", 0) + tokens["prompt_tokens"],
        "completion_tokens": state.get("completion_tokens", 0) + tokens["completion_tokens"],
        "model": "deepseek/deepseek-v4-flash",
    }


# ── Узел: выполнение действий (чтение/запись файлов) ────────────

# Максимальное количество вызовов инструментов в одном узле
_MAX_TOOL_CALLS = 25

# Таймаут на один LLM-вызов (сек). RouterAI/DeepSeek V4 Flash иногда
# отвечает медленно; жёсткий таймаут не даёт задаче зависнуть навсегда.
LLM_TIMEOUT = float(os.getenv("ARCH_CODE_LLM_TIMEOUT", "120"))


def execute_actions(state: AgentState):
    """Агент читает и пишет файлы через нативный Tool Calling.

    Использует llm.bind_tools() — модель получает инструменты через API,
    а не через текстовый JSON-промпт. LangChain сам парсит tool_calls,
    Pydantic валидирует аргументы.
    """
    sandbox_dir = state["sandbox_dir"]
    task = state["task"]
    last_error = state["error"]

    # Инструменты с привязкой к sandbox_dir (partial)
    tools = make_coding_tools(sandbox_dir)
    llm = _get_llm().bind_tools(tools)

    error_context = (
        f"\nПредыдущая попытка упала с ошибкой:\n{last_error}\n"
        f"Исправь код и вернись в done().\n"
        if last_error else ""
    )

    # ── Формируем system prompt ────────────────────────────────
    system_prompt = (
        "Ты — AI-разработчик. Ты работаешь в fullstack-копии Python-проекта.\n\n"
        "Твои инструменты:\n"
        "- read_file(relative_path) — прочитать файл\n"
        "- write_file(relative_path, content) — записать новый или перезаписать существующий файл\n"
        "- list_files(relative_path) — показать содержимое папки\n"
        "- done(changed_files) — завершить задачу (передай список созданных/изменённых файлов)\n\n"
        "Правила:\n"
        "1. Сначала изучи структуру и прочитай существующие файлы.\n"
        "2. Каждый новый файл пиши ПОЛНОСТЬЮ — не используй '...' или 'остальное без изменений'.\n"
        "3. После внесения всех изменений ВСЕГДА вызови done() с путями изменённых файлов.\n"
        "4. Не трогай .env, базы данных, .git/, venv/, __pycache__.\n"
        "5. Если нужно создать новые папки — write_file создаст их автоматически."
    )

    messages = [
        ("system", system_prompt),
        ("human", (
            f"Задача: {task}\n{error_context}"
            f"Изучи проект, прочитай нужные файлы и реализуй требуемые изменения. "
            f"По завершении вызови done()."
        )),
    ]

    changed_files = []
    action_steps = state.get("action_steps", [])
    thought_steps = state.get("thought_steps", [])
    chain_of_thought = state.get("chain_of_thought", "")
    prompt_tokens = state.get("prompt_tokens", 0)
    completion_tokens = state.get("completion_tokens", 0)

    for _ in range(_MAX_TOOL_CALLS):
        response = _invoke_llm(llm, messages, timeout=LLM_TIMEOUT)

        # Собираем токены с каждого LLM-вызова
        tokens = _sum_tokens(response)
        prompt_tokens += tokens["prompt_tokens"]
        completion_tokens += tokens["completion_tokens"]

        # ── Проверяем наличие tool_calls ──────────────────────
        if hasattr(response, "tool_calls") and response.tool_calls:
            # Добавляем AIMessage в историю (с tool_calls)
            messages.append(response)
            ts = _now_iso()

            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})

                # Инструмент done — завершение
                if tool_name == "done":
                    changed_files = tool_args.get("changed_files", [])
                    action_steps.append({
                        "node": "execute",
                        "action": f"Вызов done() → изменено файлов: {len(changed_files)}",
                        "status": "completed",
                        "timestamp": ts,
                    })
                    return {
                        "success": True,
                        "changed_files": changed_files,
                        "iterations": state["iterations"] + 1,
                        "thought_steps": thought_steps,
                        "action_steps": action_steps,
                        "chain_of_thought": chain_of_thought,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "model": "deepseek/deepseek-v4-flash",
                    }

                # Выполняем инструмент
                result_text = ""
                for t in tools:
                    if t.name == tool_name:
                        try:
                            result_text = t.invoke(tool_args)
                        except Exception as e:
                            result_text = f"❌ Ошибка вызова {tool_name}: {e}"
                        break

                if not result_text:
                    result_text = f"❌ Инструмент '{tool_name}' не найден"

                # Записываем действие
                action_summary = result_text[:200].replace("\n", " ")
                action_steps.append({
                    "node": "execute",
                    "action": f"{tool_name}({_fmt_args(tool_args)}) → {action_summary}",
                    "status": "completed" if not result_text.startswith("❌") else "failed",
                    "timestamp": ts,
                })

                messages.append(
                    ToolMessage(content=result_text, tool_call_id=tc["id"])
                )

            # После выполнения tool_calls — идём на следующий виток
            continue

        # ── Нет tool_calls — возможно DONE в тексте ──────────
        content = response.content or ""
        messages.append(AIMessage(content=content))
        if content.strip():
            ts = _now_iso()
            thought_steps.append({
                "node": "execute",
                "thought": content[:500],
                "timestamp": ts,
            })
            chain_of_thought += f"\n## Execute\n{content}\n"

        if "DONE" in content.upper() or "done" in content.lower():
            break

        messages.append(
            HumanMessage(
                content="Ты не вызвал done(). Если задача выполнена — вызови done() "
                        "с путями изменённых файлов. Если нет — продолжай работу."
            )
        )

    return {
        "success": True,
        "changed_files": changed_files,
        "iterations": state["iterations"] + 1,
        "thought_steps": thought_steps,
        "action_steps": action_steps,
        "chain_of_thought": chain_of_thought,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model": "deepseek/deepseek-v4-flash",
    }


def _fmt_args(args: dict) -> str:
    """Форматировать аргументы для лога (обрезает длинные значения)."""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 60:
            v_str = v_str[:57] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)


# ── Узел: тестирование в Docker ─────────────────────────────────

def run_tests(state: AgentState):
    """Запустить тесты проекта в Docker-контейнере."""
    sandbox_dir = state["sandbox_dir"]
    ts = _now_iso()
    action_steps = state.get("action_steps", [])

    # Определяем тип проекта
    has_requirements = os.path.exists(os.path.join(sandbox_dir, "requirements.txt"))
    has_package_json = os.path.exists(os.path.join(sandbox_dir, "package.json"))
    has_pyproject = os.path.exists(os.path.join(sandbox_dir, "pyproject.toml"))
    has_setup_py = os.path.exists(os.path.join(sandbox_dir, "setup.py"))

    if has_requirements or has_pyproject or has_setup_py:
        sandbox_type = "python"
    elif has_package_json:
        sandbox_type = "node"
    else:
        sandbox_type = "unknown"

    action_steps.append({
        "node": "test",
        "action": f"Тип проекта: {sandbox_type}. Запуск Docker-тестов...",
        "status": "running",
        "timestamp": ts,
    })

    # Устанавливаем зависимости и запускаем тесты
    try:
        result = ProjectSandbox.run_project_tests(sandbox_dir, sandbox_type, task_id=state["task_id"])
    except Exception as e:
        action_steps.append({
            "node": "test",
            "action": f"Ошибка Docker: {e}",
            "status": "failed",
            "timestamp": _now_iso(),
        })
        return {
            "success": False,
            "error": f"Ошибка Docker: {e}",
            "test_passed": False,
            "action_steps": action_steps,
        }

    if result["status"] == "success":
        action_steps.append({
            "node": "test",
            "action": "✅ Тесты пройдены",
            "status": "completed",
            "timestamp": _now_iso(),
        })
        return {"success": True, "error": "", "test_passed": True, "action_steps": action_steps}
    else:
        action_steps.append({
            "node": "test",
            "action": f"❌ Тесты не пройдены: {result['output'][:300]}",
            "status": "failed",
            "timestamp": _now_iso(),
        })
        return {
            "success": False,
            "error": result["output"],
            "test_passed": False,
            "action_steps": action_steps,
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
workflow.add_node("test", run_tests)

workflow.set_entry_point("explore")
workflow.add_edge("explore", "execute")
workflow.add_edge("execute", "test")
workflow.add_conditional_edges("test", route_next_step)

coding_graph = workflow.compile()
