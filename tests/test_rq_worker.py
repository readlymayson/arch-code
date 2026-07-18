"""
Тесты для rq_worker.py — RQ-воркер, очистка ресурсов, orphan cleanup.

Покрытие:
1. ✅ get_redis_url — из REDIS_URL
2. ✅ get_redis_url — из компонентов (host/port/db)
3. ✅ get_redis_url — дефолтные значения
4. ✅ _force_cleanup_task — вызывает Docker + sandbox cleanup
5. ✅ _force_cleanup_task — ошибки не прерывают
6. ✅ _make_exception_handler — cleanup если не completed
7. ✅ _make_exception_handler — пропускает если уже completed
8. ✅ _cleanup_orphaned_jobs — находит незакрытые задачи
9. ✅ _cleanup_orphaned_jobs — все закрыты, ничего не делает
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from rq_worker import get_redis_url, _force_cleanup_task, _make_exception_handler, _cleanup_orphaned_jobs


# ── get_redis_url ───────────────────────────────────────────────

class TestGetRedisUrl:
    """Формирование URL для подключения к Redis."""

    def test_from_env_url(self, monkeypatch):
        """Чтение REDIS_URL из окружения."""
        monkeypatch.setenv("REDIS_URL", "redis://myhost:7777/5")
        assert get_redis_url() == "redis://myhost:7777/5"

    def test_from_components(self, monkeypatch):
        """Сборка из REDIS_HOST/PORT/DB."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("REDIS_HOST", "cache.example.com")
        monkeypatch.setenv("REDIS_PORT", "6380")
        monkeypatch.setenv("REDIS_DB", "3")
        assert get_redis_url() == "redis://cache.example.com:6380/3"

    def test_default_values(self, monkeypatch):
        """Дефолтные значения (localhost:6379/0)."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_DB", raising=False)
        assert get_redis_url() == "redis://localhost:6379/0"


# ── _force_cleanup_task ─────────────────────────────────────────

class TestForceCleanupTask:
    """Принудительная очистка ресурсов задачи."""

    def test_calls_docker_and_sandbox(self, mocker):
        """Вызывает cleanup_containers и NodeSandbox.cleanup."""
        mock_cleanup = mocker.patch("docker_manager.cleanup_containers")
        mock_ns = mocker.patch("docker_manager.NodeSandbox")

        _force_cleanup_task("task-123")

        mock_cleanup.assert_called_once_with("task-123")
        mock_ns.assert_called_once_with("task-123")
        mock_ns.return_value.cleanup.assert_called_once()

    def test_errors_do_not_propagate(self, mocker):
        """Ошибки в cleanup не прерывают общий процесс."""
        mocker.patch("docker_manager.cleanup_containers", side_effect=Exception("Docker err"))
        mocker.patch("docker_manager.NodeSandbox", side_effect=Exception("Sandbox err"))

        # Не должно упасть
        _force_cleanup_task("task-faulty")


# ── _make_exception_handler ─────────────────────────────────────

class TestMakeExceptionHandler:
    """Фабрика обработчика ошибок RQ."""

    def test_cleanup_when_not_completed(self, mocker):
        """Если cleanup_completed=False — вызывается _force_cleanup_task."""
        mock_force = mocker.patch("rq_worker._force_cleanup_task")

        mock_job = mocker.MagicMock()
        mock_job.id = "job-1"
        mock_job.meta = {"task_id": "task-1", "cleanup_completed": False}
        mock_job.save_meta = mocker.MagicMock()

        mock_redis = mocker.MagicMock()
        handler = _make_exception_handler(mock_redis)

        result = handler(mock_job, ValueError, ValueError("test"), None)

        assert result is True  # RQ должен переместить в failed
        mock_force.assert_called_once_with("job-1")  # task_id = job.id
        assert mock_job.meta["cleanup_completed"] is True
        mock_job.save_meta.assert_called_once()

    def test_skip_when_already_completed(self, mocker):
        """Если cleanup_completed=True — _force_cleanup_task НЕ вызывается."""
        mock_force = mocker.patch("rq_worker._force_cleanup_task")

        mock_job = mocker.MagicMock()
        mock_job.id = "job-2"
        mock_job.meta = {"cleanup_completed": True}

        mock_redis = mocker.MagicMock()
        handler = _make_exception_handler(mock_redis)

        handler(mock_job, RuntimeError, RuntimeError("err"), None)

        mock_force.assert_not_called()


# ── _cleanup_orphaned_jobs ──────────────────────────────────────

class TestCleanupOrphanedJobs:
    """Сканирование и очистка orphan-задач."""

    def test_cleans_unfinished_jobs(self, mocker):
        """Находит задачи без cleanup_completed — чистит их."""
        mock_redis = mocker.MagicMock()
        # Возвращаем job_id для finished и failed registry
        mock_redis.zrange.side_effect = [
            [b"job-finished-1"],   # finished
            [b"job-failed-1"],     # failed
            [],                     # canceled (пусто)
        ]

        mock_job_finished = mocker.MagicMock()
        mock_job_finished.id = "job-finished-1"
        mock_job_finished.meta = {"task_id": "t-1"}  # нет cleanup_completed

        mock_job_failed = mocker.MagicMock()
        mock_job_failed.id = "job-failed-1"
        mock_job_failed.meta = {"cleanup_completed": False}

        mock_fetch = mocker.patch("rq.job.Job.fetch")
        mock_fetch.side_effect = [mock_job_finished, mock_job_failed]

        mock_force = mocker.patch("rq_worker._force_cleanup_task")

        _cleanup_orphaned_jobs(mock_redis)

        # Должны быть очищены оба
        assert mock_force.call_count == 2
        mock_force.assert_any_call("job-finished-1")
        mock_force.assert_any_call("job-failed-1")

    def test_skips_completed_jobs(self, mocker):
        """Все задачи с cleanup_completed=True — пропускаются."""
        mock_redis = mocker.MagicMock()
        mock_redis.zrange.side_effect = [
            [b"job-ok"],   # finished — всё чисто
            [],              # failed
            [],              # canceled
        ]

        mock_job = mocker.MagicMock()
        mock_job.meta = {"cleanup_completed": True}

        mocker.patch("rq.job.Job.fetch", return_value=mock_job)
        mock_force = mocker.patch("rq_worker._force_cleanup_task")

        _cleanup_orphaned_jobs(mock_redis)

        mock_force.assert_not_called()
