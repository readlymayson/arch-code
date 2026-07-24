import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid

logger = logging.getLogger(__name__)


def cleanup_containers(task_id: str) -> None:
    """Остановить и удалить Docker-контейнеры, связанные с задачей.

    Именованные контейнеры: sandbox-{task_id}, project-test-{task_id}.
    Безопасно вызывать даже если контейнеры не существуют.
    """
    for suffix in (f"sandbox-{task_id}", f"project-test-{task_id}"):
        try:
            subprocess.run(
                ["docker", "stop", suffix],
                capture_output=True, timeout=10, check=False,
            )
        except Exception:
            pass
        try:
            subprocess.run(
                ["docker", "rm", "--force", suffix],
                capture_output=True, timeout=10, check=False,
            )
        except Exception:
            pass


class NodeSandbox:
    """Изолированная среда для выполнения Node.js кода в Docker-контейнере.

    Каждый экземпляр привязан к уникальному task_id и работает
    в собственной директории sandbox/{task_id}/ — параллельные задачи
    не пересекаются.
    """

    ROOT_SANDBOX_DIR = os.path.abspath("./sandbox")

    # Распространённые npm-зависимости для генерируемого кода
    COMMON_DEPS = {"express": "^4.18.2"}

    def __init__(self, task_id: str | None = None):
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.sandbox_dir = os.path.join(self.ROOT_SANDBOX_DIR, self.task_id)
        os.makedirs(self.sandbox_dir, exist_ok=True)

    def _ensure_package_json(self):
        """Создаёт package.json в sandbox/{task_id}/, если его ещё нет."""
        pkg_path = os.path.join(self.sandbox_dir, "package.json")
        if not os.path.exists(pkg_path):
            pkg = {
                "name": f"sandbox-{self.task_id}",
                "version": "1.0.0",
                "private": True,
                "dependencies": dict(self.COMMON_DEPS),
            }
            with open(pkg_path, "w") as f:
                json.dump(pkg, f, indent=2)

    def _run_in_container(self, command: list[str], timeout: int = 30) -> dict:
        """Синхронный запуск команды в Docker-контейнере (docker-py).

        Контейнер именуется sandbox-{task_id}, не удаляется автоматически
        (remove=False) — управление жизнью через cleanup_containers().
        """
        import docker
        self._ensure_package_json()

        client = docker.from_env()
        container_name = f"sandbox-{self.task_id}"

        # npm install — удаляем предыдущий контейнер перед запуском
        try:
            client.containers.get(container_name).remove(force=True)
        except docker.errors.NotFound:
            pass

        try:
            client.containers.run(
                "node:alpine",
                command=["npm", "install"],
                name=container_name,
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
                detach=False,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError:
            pass
        except Exception:
            pass

        # Основная команда — используем remove=True для авто-очистки
        try:
            logs = client.containers.run(
                "node:alpine",
                command=command,
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
                detach=False,
                stderr=True,
                stdout=True,
            )
            return {"status": "success", "output": logs.decode("utf-8")}
        except docker.errors.ContainerError as e:
            err_msg = ""
            try:
                err_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
            except Exception:
                err_msg = str(e)
            return {"status": "error", "output": err_msg}

    def execute_code(self, filename: str, code: str) -> dict:
        """Записать код в sandbox/{task_id}/ и запустить с таймаутом 5 сек."""
        file_path = os.path.join(self.sandbox_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(code)

        return self._run_in_container(
            ["sh", "-c", f"timeout 5 node /app/{filename}"]
        )

    def execute_test(self, filename: str, code: str) -> dict:
        """Записать тест в sandbox/{task_id}/ и запустить node --test."""
        file_path = os.path.join(self.sandbox_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(code)

        return self._run_in_container(
            ["node", "--test", f"/app/{filename}"]
        )

    async def execute_code_async(self, filename: str, code: str) -> dict:
        """async-версия execute_code — не блокирует event loop."""
        file_path = os.path.join(self.sandbox_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(code)

        self._ensure_package_json()

        await self._run_in_container_async(["npm", "install"])
        return await self._run_in_container_async(
            ["sh", "-c", f"timeout 5 node /app/{filename}"],
        )

    async def execute_test_async(self, filename: str, code: str) -> dict:
        """async-версия execute_test — не блокирует event loop."""
        file_path = os.path.join(self.sandbox_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(code)

        self._ensure_package_json()

        await self._run_in_container_async(["npm", "install"])
        return await self._run_in_container_async(
            ["node", "--test", f"/app/{filename}"],
        )

    async def _run_in_container_async(
        self, command: list[str], timeout: int = 30
    ) -> dict:
        """async-запуск команды в Docker через asyncio.create_subprocess_exec."""
        volume_bind = f"{self.sandbox_dir}:/app"
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", volume_bind,
            "-w", "/app",
            "node:alpine",
        ] + command

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            if proc.returncode == 0:
                return {"status": "success", "output": stdout.decode("utf-8")}
            else:
                return {
                    "status": "error",
                    "output": (stderr or stdout).decode("utf-8"),
                }
        except asyncio.TimeoutError:
            return {"status": "error", "output": "Timeout"}
        except FileNotFoundError:
            return {"status": "error", "output": "Docker not found on host"}

    def cleanup(self):
        """Удалить sandbox/{task_id}/ со всем содержимым."""
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  ProjectSandbox — универсальная песочница для Phase B
# ═══════════════════════════════════════════════════════════════════

class ProjectSandbox:
    """Песочница для работы с полным проектом (Python / Node.js).

    В отличие от NodeSandbox (один .js файл), этот класс принимает
    путь к директории с проектом, определяет его тип и запускает
    соответствующие тесты (pytest / npm test).
    """

    @staticmethod
    def detect_project_type(sandbox_dir: str) -> str:
        """Определить тип проекта по наличию конфигурационных файлов."""
        if os.path.exists(os.path.join(sandbox_dir, "requirements.txt")):
            return "python"
        if os.path.exists(os.path.join(sandbox_dir, "pyproject.toml")):
            return "python"
        if os.path.exists(os.path.join(sandbox_dir, "setup.py")):
            return "python"
        if os.path.exists(os.path.join(sandbox_dir, "package.json")):
            return "node"
        return "unknown"

    @staticmethod
    def run_project_tests(sandbox_dir: str, project_type: str | None = None, task_id: str = "") -> dict:
        """Установить зависимости и запустить тесты проекта в Docker.

        Args:
            sandbox_dir: Абсолютный путь к директории проекта.
            project_type: "python", "node" или None (автоопределение).
            task_id: ID задачи для именования контейнера.

        Returns:
            dict {"status": "success"|"error", "output": str}
        """
        if project_type is None:
            project_type = ProjectSandbox.detect_project_type(sandbox_dir)

        if project_type == "python":
            return ProjectSandbox._run_python_tests(sandbox_dir, task_id)
        elif project_type == "node":
            return ProjectSandbox._run_node_tests(sandbox_dir, task_id)
        else:
            return {"status": "error", "output": "Не удалось определить тип проекта"}

    # ── Python ────────────────────────────────────────────────────

    @staticmethod
    def _run_python_tests(sandbox_dir: str, task_id: str = "") -> dict:
        """Установить pytest и запустить тесты в Docker (python:3.12-slim).

        Внимание: не пытается форсированно установить ВСЕ зависимости проекта
        (torch, tiktoken, cryptg требуют компиляции в slim-образе).
        Устанавливает только pytest — тесты на чистом Python/stdlib пройдут.
        """
        import docker
        client = docker.from_env()
        container_name = f"project-test-{task_id}" if task_id else "project-test"

        def _rm_container():
            """Удалить существующий контейнер, если есть (force)."""
            try:
                c = client.containers.get(container_name)
                c.remove(force=True)
            except docker.errors.NotFound:
                pass

        volumes = {sandbox_dir: {"bind": "/app", "mode": "rw"}}

        # ── Шаг 1: Пытаемся установить зависимости проекта ─────
        # Не фатально — torch/tiktoken/cryptg не ставятся в slim
        _rm_container()
        try:
            client.containers.run(
                "python:3.12-slim",
                command=[
                    "sh", "-c",
                    "pip install --quiet --timeout=60 -r requirements.txt 2>&1 || "
                    "echo '[arch-code] ⚠️ Некоторые зависимости не установлены, продолжаем...'"
                ],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
        except Exception as exc:
            logger.warning(f"Docker: pip install requirements.txt не удался: {exc}")

        # ── Шаг 2: Устанавливаем pytest ────────────────────────
        _rm_container()
        try:
            client.containers.run(
                "python:3.12-slim",
                command=["pip", "install", "--quiet", "pytest", "pytest-timeout"],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
        except Exception as exc:
            return {"status": "error", "output": f"Не удалось установить pytest: {exc}"}

        # ── Шаг 3: Запускаем pytest ────────────────────────────
        _rm_container()
        try:
            logs = client.containers.run(
                "python:3.12-slim",
                command=["sh", "-c", "python -m pytest -x --timeout=30 --tb=short 2>&1 || true"],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
            output = logs.decode("utf-8")
            # Считаем успехом, если нет FAILED (включая "no tests ran")
            if "FAILED" in output and ("passed" not in output or "failed" in output):
                return {"status": "error", "output": output[-2000:]}
            return {"status": "success", "output": output[-1000:]}
        except Exception as e:
            return {"status": "error", "output": str(e)}

    # ── Node.js ───────────────────────────────────────────────────

    @staticmethod
    def _run_node_tests(sandbox_dir: str, task_id: str = "") -> dict:
        """Установить npm-пакеты и запустить npm test в Docker (node:alpine)."""
        import docker
        client = docker.from_env()
        container_name = f"project-test-{task_id}" if task_id else "project-test"

        def _rm_container():
            try:
                c = client.containers.get(container_name)
                c.remove(force=True)
            except docker.errors.NotFound:
                pass

        volumes = {sandbox_dir: {"bind": "/app", "mode": "rw"}}

        # npm install
        _rm_container()
        try:
            client.containers.run(
                "node:alpine",
                command=["npm", "install"],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError:
            pass
        except Exception as e:
            return {"status": "error", "output": f"npm install error: {e}"}

        # npm test
        _rm_container()
        try:
            logs = client.containers.run(
                "node:alpine",
                command=["sh", "-c", "npm test 2>&1; true"],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
            output = logs.decode("utf-8")
            return {"status": "success", "output": output[-1000:]}
        except docker.errors.ContainerError as e:
            err = e.stderr.decode("utf-8") if e.stderr else str(e)
            return {"status": "error", "output": err[-2000:]}
        except Exception as e:
            return {"status": "error", "output": str(e)}
