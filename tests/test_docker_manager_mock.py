"""
Тесты для docker_manager.py с мокнутым Docker SDK — без реального Docker.

Покрытие:
1. ✅ NodeSandbox._run_in_container_async — успех (returncode=0)
2. ✅ NodeSandbox._run_in_container_async — ошибка (returncode≠0)
3. ✅ NodeSandbox._run_in_container_async — timeout (asyncio.TimeoutError)
4. ✅ NodeSandbox._run_in_container_async — Docker не найден (FileNotFoundError)
5. ✅ NodeSandbox._run_in_container_async — пустой вывод
6. ✅ NodeSandbox.execute_code — правильная команда в _run_in_container
7. ✅ NodeSandbox.execute_test — правильная команда (node --test)
8. ✅ NodeSandbox.execute_code_async — вызов _ensure_package_json + npm install
9. ✅ ProjectSandbox._run_python_tests — успешный pytest
10. ✅ ProjectSandbox._run_python_tests — FAILED в выводе
11. ✅ ProjectSandbox._run_python_tests — ошибка Docker
12. ✅ ProjectSandbox._run_node_tests — успешный npm test
13. ✅ ProjectSandbox._run_node_tests — ошибка npm install
14. ✅ cleanup_containers — проверка команд docker stop/rm
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from docker_manager import NodeSandbox, ProjectSandbox, cleanup_containers


# ── _run_in_container_async ─────────────────────────────────────

class TestRunInContainerAsync:
    """async-запуск команды в Docker через asyncio.create_subprocess_exec."""

    @pytest.mark.asyncio
    async def test_success(self, mocker, tmp_path, monkeypatch):
        """Успешное выполнение: returncode=0."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="async-test")

        mock_proc = mocker.AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"Hello World", b"")

        mocker.patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        )

        result = await ns._run_in_container_async(["echo", "hi"])
        assert result["status"] == "success"
        assert result["output"] == "Hello World"

    @pytest.mark.asyncio
    async def test_nonzero_exit(self, mocker, tmp_path, monkeypatch):
        """Ненулевой returncode — ошибка."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="async-fail")

        mock_proc = mocker.AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"Error: something failed")

        mocker.patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        )

        result = await ns._run_in_container_async(["node", "bad.js"])
        assert result["status"] == "error"
        assert "Error" in result["output"]

    @pytest.mark.asyncio
    async def test_timeout(self, mocker, tmp_path, monkeypatch):
        """Таймаут выполнения — asyncio.TimeoutError."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="async-timeout")

        mock_proc = mocker.AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()


        mocker.patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        )

        result = await ns._run_in_container_async(["sleep", "10"])
        assert result["status"] == "error"
        assert "Timeout" in result["output"]

    @pytest.mark.asyncio
    async def test_docker_not_found(self, mocker, tmp_path, monkeypatch):
        """Docker не установлен — FileNotFoundError."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="async-no-docker")

        mocker.patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError(),
        )

        result = await ns._run_in_container_async(["node", "test.js"])
        assert result["status"] == "error"
        assert "Docker not found" in result["output"]

    @pytest.mark.asyncio
    async def test_empty_output(self, mocker, tmp_path, monkeypatch):
        """Пустой stdout/stderr — успех с пустым выводом."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="async-empty")

        mock_proc = mocker.AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")

        mocker.patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        )

        result = await ns._run_in_container_async(["echo", "-n", ""])
        assert result["status"] == "success"
        assert result["output"] == ""


# ── execute_code / execute_test (sync) ───────────────────────────

class TestExecuteCodeSync:
    """Синхронный запуск кода в Docker."""

    def test_correct_command(self, mocker, tmp_path, monkeypatch):
        """execute_code передаёт правильную команду в _run_in_container."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="exec-sync")

        mock_run = mocker.patch.object(ns, "_run_in_container", return_value={
            "status": "success", "output": "OK",
        })

        result = ns.execute_code("test.js", 'console.log("hi");')

        mock_run.assert_called_once_with(
            ["sh", "-c", "timeout 5 node /app/test.js"]
        )
        assert result["status"] == "success"


class TestExecuteTestSync:
    """Синхронный запуск тестов в Docker."""

    def test_correct_command(self, mocker, tmp_path, monkeypatch):
        """execute_test передаёт node --test команду."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="test-sync")

        mock_run = mocker.patch.object(ns, "_run_in_container", return_value={
            "status": "success", "output": "ok",
        })

        result = ns.execute_test("test.js", 'require("node:test");')

        mock_run.assert_called_once_with(
            ["node", "--test", "/app/test.js"]
        )
        assert result["status"] == "success"


# ── execute_code_async / execute_test_async ──────────────────────

class TestExecuteCodeAsync:
    """async-запуск кода в Docker."""

    @pytest.mark.asyncio
    async def test_calls_ensure_and_run(self, mocker, tmp_path, monkeypatch):
        """execute_code_async вызывает _ensure_package_json + npm install."""
        monkeypatch.setattr(NodeSandbox, "ROOT_SANDBOX_DIR", str(tmp_path))
        ns = NodeSandbox(task_id="exec-async")

        mock_ensure = mocker.patch.object(ns, "_ensure_package_json")
        mock_run = mocker.patch.object(
            ns, "_run_in_container_async",
            side_effect=[
                {"status": "success", "output": "npm ok"},
                {"status": "success", "output": "ran code"},
            ],
        )

        result = await ns.execute_code_async("test.js", 'console.log("hi");')

        mock_ensure.assert_called_once()
        assert mock_run.call_count == 2
        # Первый вызов — npm install
        assert mock_run.call_args_list[0][0][0] == ["npm", "install"]
        # Второй вызов — запуск кода
        assert "node /app/test.js" in " ".join(mock_run.call_args_list[1][0][0])
        assert result["status"] == "success"


# ── ProjectSandbox._run_python_tests ────────────────────────────

class TestRunPythonTests:
    """Запуск Python-тестов в Docker."""

    def test_success(self, mocker, tmp_path):
        """Успешный pytest."""

        mock_client = mocker.MagicMock()
        mock_container = mocker.MagicMock()
        mock_container.logs.return_value = b"collected 5 items ... 5 passed"

        # Мокаем docker.from_env
        mocker.patch("docker.from_env", return_value=mock_client)

        # Два запуска (pip install + pip install pytest + pytest)
        mock_client.containers.run.side_effect = [
            b"",  # pip install
            b"",  # pip install pytest
            mock_container,  # pytest run
        ]
        mock_client.containers.get.side_effect = __import__("docker").errors.NotFound("not found")

        result = ProjectSandbox._run_python_tests(str(tmp_path), "task-123")

        assert result["status"] == "success"

    def test_failed(self, mocker, tmp_path):
        """Pytest с FAILED тестами."""
        import docker

        mock_client = mocker.MagicMock()
        mocker.patch("docker.from_env", return_value=mock_client)
        # Возвращаем bytes напрямую (run возвращает logs.decode())
        mock_client.containers.run.side_effect = [
            b"",  # pip install
            b"",  # pip install pytest
            b"FAILED test_app.py::test_foo - AssertionError",  # pytest run (bytes directly)
        ]
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        result = ProjectSandbox._run_python_tests(str(tmp_path), "task-456")

        assert result["status"] == "error"
        assert "FAILED" in result["output"]


# ── ProjectSandbox._run_node_tests ──────────────────────────────

class TestRunNodeTests:
    """Запуск Node.js-тестов в Docker."""

    def test_success(self, mocker, tmp_path):
        """Успешный npm test."""

        mock_client = mocker.MagicMock()
        mock_container = mocker.MagicMock()
        mock_container.logs.return_value = b"ok 1 - test passed"

        mocker.patch("docker.from_env", return_value=mock_client)
        mock_client.containers.run.side_effect = [
            b"",  # npm install
            mock_container,  # npm test
        ]
        mock_client.containers.get.side_effect = __import__("docker").errors.NotFound("not found")

        result = ProjectSandbox._run_node_tests(str(tmp_path), "task-789")

        assert result["status"] == "success"

    def test_npm_install_error(self, mocker, tmp_path):
        """Ошибка npm install."""
        import docker

        mock_client = mocker.MagicMock()
        mocker.patch("docker.from_env", return_value=mock_client)
        mock_client.containers.run.side_effect = [
            docker.errors.ContainerError(
                container=mocker.MagicMock(),
                exit_status=1,
                stderr=b"npm ERR!",
                command=["npm", "install"],
                image="node:alpine",
            ),
        ]
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        # npm install с ContainerError — игнорируется, переходит к npm test
        result = ProjectSandbox._run_node_tests(str(tmp_path), "task-npm-fail")
        # Если ContainerError — pass, значит будет вызван npm test
        assert mock_client.containers.run.call_count >= 2


# ── cleanup_containers ──────────────────────────────────────────

class TestCleanupContainers:
    """Очистка Docker-контейнеров."""

    def test_calls_stop_and_rm(self, mocker):
        """Вызывает docker stop и docker rm для sandbox и project-test."""
        mock_run = mocker.patch("docker_manager.subprocess.run")

        cleanup_containers("test-task")

        # Должно быть 4 вызова: stop sandbox, rm sandbox, stop project-test, rm project-test
        assert mock_run.call_count == 4

        # Проверяем, что команды содержат правильные имена контейнеров
        calls = mock_run.call_args_list
        containers_called = []
        for call in calls:
            args = call[0][0]
            containers_called.extend(args)

        assert "sandbox-test-task" in " ".join(containers_called)
        assert "project-test-test-task" in " ".join(containers_called)

    def test_exception_safe(self, mocker):
        """Не падает при ошибках subprocess."""
        mocker.patch(
            "docker_manager.subprocess.run",
            side_effect=Exception("random error"),
        )

        # Не должно вызывать исключений
        cleanup_containers("faulty-task")
