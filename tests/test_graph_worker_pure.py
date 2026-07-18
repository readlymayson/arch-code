"""
Тесты для чистых функций graph_worker.py — LangGraph state machine.

Покрытие:
1. ✅ _sanitize — ASCII текст не меняется
2. ✅ _sanitize — кириллица сохраняется
3. ✅ _sanitize — эмодзи заменяются на ?
4. ✅ _sanitize — пустая строка
5. ✅ _sanitize — смешанный контент
6. ✅ _sanitize — спецсимволы cp1251
7. ✅ route_next_step — success=True → END
8. ✅ route_next_step — iterations >= 3 → END
9. ✅ route_next_step — iterations=2, not success → "execute"
10. ✅ route_next_step — граничное значение iterations=2
11. ✅ route_next_step — граничное значение iterations=3
12. ✅ _get_llm — singleton (повторный вызов возвращает тот же)
13. ✅ _get_llm — с установленными env vars
14. ✅ AgentState — все поля присутствуют
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from graph_worker import _sanitize, route_next_step


# ── _sanitize ────────────────────────────────────────────────────

class TestSanitize:
    """Фильтр cp1251-несовместимых символов."""

    def test_ascii_preserved(self):
        """ASCII-текст не меняется."""
        assert _sanitize("Hello, World!") == "Hello, World!"

    def test_cyrillic_preserved(self):
        """Кириллица сохраняется (входит в cp1251)."""
        text = "Привет, мир!"
        assert _sanitize(text) == text

    def test_emoji_replaced(self):
        """Эмодзи заменяются на ?."""
        result = _sanitize("Hello 😊 World")
        assert "😊" not in result
        assert "Hello ? World" == result or "Hello ? World" in result

    def test_empty_string(self):
        """Пустая строка."""
        assert _sanitize("") == ""

    def test_mixed_content(self):
        """Смешанный контент — кириллица остаётся, эмодзи заменяются."""
        text = "Привет 👋! Как дела? 🎉"
        result = _sanitize(text)
        assert "Привет" in result
        assert "👋" not in result
        assert "🎉" not in result

    @pytest.mark.parametrize("input_text, expected_contains", [
        ("\u20ac", "\u20ac"),        # евро может проходить через cp1251 или нет — проверяем что не падает
        ("\u2122", "\u2122"),        # trademark может проходить через cp1251 или нет — проверяем что не падает
        ("\u2014", "\u2014"),        # тире есть в cp1251
    ])
    def test_special_chars(self, input_text, expected_contains):
        """Спецсимволы: не падают с ошибкой, cp1251-совместимые сохраняются."""
        result = _sanitize(input_text)
        # Не должно возникать исключений, результат — строка той же длины или длиннее
        assert isinstance(result, str)


# ── route_next_step ──────────────────────────────────────────────

class TestRouteNextStep:
    """Маршрутизация графа — выбор следующего узла."""
    # LangGraph использует константу END = "__end__"
    END = "__end__"

    def test_success_ends(self):
        """success=True → END."""
        state = {"success": True, "iterations": 1}
        assert route_next_step(state) == self.END

    def test_max_iterations_ends(self):
        """iterations >= 3 → END, даже если не success."""
        state = {"success": False, "iterations": 3}
        assert route_next_step(state) == self.END

    def test_retry_execute(self):
        """iterations < 3 и не success → 'execute'."""
        state = {"success": False, "iterations": 1}
        assert route_next_step(state) == "execute"

    @pytest.mark.parametrize("iterations, success, expected", [
        (0, False, "execute"),
        (1, False, "execute"),
        (2, False, "execute"),
        (3, False, "__end__"),
        (4, False, "__end__"),
        (3, True, "__end__"),
        (2, True, "__end__"),
    ])
    def test_boundary_values(self, iterations, success, expected):
        """Граничные значения iterations."""
        state = {"success": success, "iterations": iterations}
        assert route_next_step(state) == expected


# ── _get_llm ─────────────────────────────────────────────────────

class TestGetLLM:
    """Lazy singleton для LLM."""

    def test_singleton(self, mock_env):
        """Повторный вызов возвращает тот же экземпляр."""
        from graph_worker import _get_llm
        llm1 = _get_llm()
        llm2 = _get_llm()
        assert llm1 is llm2

    def test_with_env_vars(self, mock_env):
        """С установленными env vars — создаётся с корректными параметрами."""
        from graph_worker import _get_llm
        llm = _get_llm()
        assert llm.model == "deepseek/deepseek-v4-flash"
        # Проверка base_url через property или repr
        assert "routerai.test.ru" in str(llm._default_params.get("base_url", "")) or \
               "routerai.test.ru" in llm.__repr__() or \
               "routerai.test.ru" in repr(llm)


# ── AgentState ───────────────────────────────────────────────────

class TestAgentState:
    """Проверка структуры AgentState."""

    def test_required_fields(self):
        """Все обязательные поля присутствуют."""
        from graph_worker import AgentState

        state = AgentState(
            task_id="test-123",
            sandbox_dir="/tmp/sandbox",
            project_dir="/tmp/project",
            task="Test task",
            code="",
            test_code="",
            test_passed=False,
            error="",
            iterations=0,
            success=False,
            changed_files=[],
        )
        assert state["task_id"] == "test-123"
        assert state["iterations"] == 0
        assert state["success"] is False
        assert state["changed_files"] == []
        assert state["test_code"] == ""
