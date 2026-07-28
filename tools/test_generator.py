"""Test Generator — TDD Agent для arch-code.

Генерирует pytest-тесты на основе ТЗ до того, как код написан.
Тесты пишутся так, чтобы падать (RED) на пустом коде
и проходить (GREEN) после правильной реализации.

Graceful degradation: если LLM не смог сгенерировать тесты
(пустой ответ, кривой JSON) — функция возвращает пустую строку,
и граф работает без TDD.
"""

from __future__ import annotations

import json as _json
import logging
import os

logger = logging.getLogger(__name__)

# Максимальное количество попыток генерации тестов
_MAX_TEST_GEN_ATTEMPTS = 2


def _get_llm() -> object:
    """Lazy import LLM из graph_worker (избегаем циклического импорта)."""
    from graph_worker import _get_llm as _llm
    return _llm()


def generate_tests_for_task(
    task: str,
    sandbox_dir: str,
    task_id: str,
) -> str:
    """Сгенерировать pytest-тесты на основе ТЗ.

    Args:
        task: Описание задачи от пользователя.
        sandbox_dir: Путь к sandbox/{task_id}/.
        task_id: ID задачи (для имени файла).

    Returns:
        str — содержимое сгенерированного тестового файла,
        или пустая строка при ошибке/неудаче (graceful degradation).
    """
    # Сначала проверяем, есть ли уже директория tests/ в проекте
    tests_dir = os.path.join(sandbox_dir, "tests")
    if not os.path.isdir(tests_dir):
        try:
            os.makedirs(tests_dir, exist_ok=True)
            logger.info(f"TDD: создана директория {tests_dir}")
        except Exception as e:
            logger.warning(f"TDD: не удалось создать tests/ — {e}")
            return ""

    # Определяем, какие файлы уже есть в tests/ для контекста
    existing_tests = []
    if os.path.isdir(tests_dir):
        try:
            existing_tests = [
                f for f in os.listdir(tests_dir)
                if f.endswith(".py") and f != "__init__.py"
            ]
        except Exception:
            pass

    existing_context = ""
    if existing_tests:
        existing_context = (
            "В проекте уже есть тестовые файлы:\n"
            + "\n".join(f"  - {f}" for f in existing_tests[:5])
            + "\n\nОзнакомься со стилем существующих тестов и пиши в том же духе."
        )

    system_prompt = (
        "Ты — Senior Test Engineer (TDD). Твоя задача — написать pytest-тесты "
        "на основе технического задания.\n\n"
        "ПРАВИЛА:\n"
        "1. Тесты должны быть написаны до реализации кода.\n"
        "2. Тесты должны ПАДАТЬ (RED), если код ещё не реализован.\n"
        "3. Тесты должны ПРОХОДИТЬ (GREEN) после правильной реализации задачи.\n"
        "4. Используй только стандартные библиотеки Python + pytest.\n"
        "5. НЕ используй внешние API, базы данных или сетевые вызовы.\n"
        "6. Все тесты — unit-тесты (изолированные, без side-эффектов).\n"
        "7. Используй assert, pytest.raises для исключений, pytest.approx для float.\n"
        "8. Покрывай: happy path, edge cases, граничные значения, ошибки.\n"
        "9. НЕ пиши тесты на чужой код — только на то, что нужно реализовать "
        "по ТЗ.\n\n"
        "Верни ответ СТРОГО в формате JSON:\n"
        '{"test_code": "полный код тестового файла .py", '
        '"test_description": "краткое описание что тестируется"}'
    )

    user_prompt = (
        f"Техническое задание:\n{task}\n\n"
        f"{existing_context}\n\n"
        "Напиши pytest-тесты для этой задачи. "
        "Помни: тесты должны падать на пустом коде и проходить "
        "после правильной реализации."
    )

    for attempt in range(_MAX_TEST_GEN_ATTEMPTS):
        try:
            response = _get_llm().invoke([
                ("system", system_prompt),
                ("human", user_prompt),
            ])
            content = (response.content or "").strip()

            # Очищаем от markdown-обёрток
            if content.startswith("```"):
                first_nl = content.find("\n")
                last_marker = content.rfind("```")
                if first_nl != -1 and last_marker != -1 and last_marker > first_nl:
                    content = content[first_nl:last_marker].strip()
                else:
                    content = content.replace("```json", "").replace("```", "").strip()

            if not content:
                raise ValueError("Пустой ответ от LLM")

            result = _json.loads(content)
            test_code = result.get("test_code", "")

            if not test_code or len(test_code) < 50:
                raise ValueError("Сгенерированный тест слишком короткий")

            # Проверяем, что это валидный Python-код
            try:
                import ast
                ast.parse(test_code)
            except SyntaxError as e:
                logger.warning(f"TDD: тест содержит синтаксическую ошибку (попытка {attempt + 1}): {e}")
                continue

            # Пишем тестовый файл
            test_filename = f"test_generated_{task_id}.py"
            test_filepath = os.path.join(tests_dir, test_filename)
            with open(test_filepath, "w", encoding="utf-8") as f:
                f.write(test_code)

            logger.info(
                f"TDD: сгенерирован тестовый файл {test_filename} "
                f"({len(test_code)} символов)"
            )
            return test_code

        except Exception as e:
            logger.warning(
                f"TDD: ошибка генерации тестов (попытка {attempt + 1}/{_MAX_TEST_GEN_ATTEMPTS}): {e}"
            )
            if attempt >= _MAX_TEST_GEN_ATTEMPTS - 1:
                logger.warning("TDD: лимит попыток исчерпан — graceful degradation")
                return ""

    return ""
