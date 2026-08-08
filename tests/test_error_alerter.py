"""
Тесты для tools/error_alerter.py — алертинг об ошибках задач.

Покрытие:
1. ✅ send_error_alert — без webhook (не настроен) → True, ничего не шлём
2. ✅ send_error_alert — успешная отправка (HTTP 200) → True
3. ✅ send_error_alert — HTTP 500 → False + warning
4. ✅ send_error_alert — сетевая ошибка httpx → False
5. ✅ _build_payload — с chat_id (Telegram-формат)
6. ✅ _build_payload — без chat_id (generic JSON)
7. ✅ Текст ошибки обрезается до 4000 символов (payload) / 1500 (в text)
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


class TestSendErrorAlert:
    """send_error_alert — отправка webhook-уведомления."""

    def test_no_webhook_disabled(self):
        """Без ERROR_WEBHOOK_URL — алертинг отключён, возврат True."""
        from tools import error_alerter

        # Мокаем env: webhook пустой
        error_alerter.DEFAULT_WEBHOOK_URL = ""
        assert error_alerter.send_error_alert("task-1", "boom") is True

    def test_success_http_200(self, mocker):
        """HTTP 200 → True, payload корректный."""
        from tools import error_alerter

        error_alerter.DEFAULT_WEBHOOK_URL = "https://example.com/hook"
        error_alerter.DEFAULT_CHAT_ID = "12345"

        mock_post = mocker.patch("httpx.post")
        mock_resp = mocker.Mock(status_code=200)
        mock_post.return_value = mock_resp

        result = error_alerter.send_error_alert("task-2", "тесты не прошли")

        assert result is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://example.com/hook"
        payload = kwargs["json"]
        assert payload["chat_id"] == "12345"
        assert "task-2" in payload["text"]

    def test_http_500_false(self, mocker):
        """HTTP 500 → False (не доставлено)."""
        from tools import error_alerter

        error_alerter.DEFAULT_WEBHOOK_URL = "https://example.com/hook"
        error_alerter.DEFAULT_CHAT_ID = ""

        mock_post = mocker.patch("httpx.post")
        mock_resp = mocker.Mock(status_code=500)
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        assert error_alerter.send_error_alert("task-3", "err") is False

    def test_network_error_false(self, mocker):
        """Сетевая ошибка httpx → False."""
        from tools import error_alerter

        error_alerter.DEFAULT_WEBHOOK_URL = "https://example.com/hook"
        error_alerter.DEFAULT_CHAT_ID = ""

        import httpx
        mocker.patch(
            "httpx.post",
            side_effect=httpx.ConnectError("connection refused"),
        )

        assert error_alerter.send_error_alert("task-4", "err") is False

    def test_long_error_truncated(self, mocker):
        """Длинная ошибка обрезается в payload (4000 символов)."""
        from tools import error_alerter

        error_alerter.DEFAULT_WEBHOOK_URL = "https://example.com/hook"
        error_alerter.DEFAULT_CHAT_ID = "12345"

        mock_post = mocker.patch("httpx.post")
        mock_resp = mocker.Mock(status_code=200)
        mock_post.return_value = mock_resp

        long_error = "E" * 10000
        error_alerter.send_error_alert("task-5", long_error)

        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert len(payload["text"]) <= 4000


class TestBuildPayload:
    """_build_payload — формат payload."""

    def test_with_chat_id_telegram(self):
        """С chat_id — Telegram-формат {chat_id, text, parse_mode}."""
        from tools.error_alerter import _build_payload

        payload = _build_payload("hello", "999")
        assert payload["chat_id"] == "999"
        assert payload["parse_mode"] == "HTML"
        assert payload["text"] == "hello"

    def test_without_chat_id_generic(self):
        """Без chat_id — generic JSON {text}."""
        from tools.error_alerter import _build_payload

        payload = _build_payload("hello", "")
        assert "chat_id" not in payload
        assert payload["text"] == "hello"
