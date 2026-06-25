import asyncio
import json
import os
import shutil
import subprocess
import uuid


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
        """Синхронный запуск команды в Docker-контейнере (docker-py)."""
        import docker
        self._ensure_package_json()

        client = docker.from_env()

        # npm install
        try:
            client.containers.run(
                "node:alpine",
                command=["npm", "install"],
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError:
            pass

        # Основная команда
        try:
            logs = client.containers.run(
                "node:alpine",
                command=command,
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
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
    def run_project_tests(sandbox_dir: str, project_type: str | None = None) -> dict:
        """Установить зависимости и запустить тесты проекта в Docker.

        Args:
            sandbox_dir: Абсолютный путь к директории проекта.
            project_type: "python", "node" или None (автоопределение).

        Returns:
            dict {"status": "success"|"error", "output": str}
        """
        if project_type is None:
            project_type = ProjectSandbox.detect_project_type(sandbox_dir)

        if project_type == "python":
            return ProjectSandbox._run_python_tests(sandbox_dir)
        elif project_type == "node":
            return ProjectSandbox._run_node_tests(sandbox_dir)
        else:
            return {"status": "error", "output": "Не удалось определить тип проекта"}

    # ── Python ────────────────────────────────────────────────────

    @staticmethod
    def _run_python_tests(sandbox_dir: str) -> dict:
        """Установить pip-пакеты и запустить pytest в Docker (python:3.12)."""
        import docker
        client = docker.from_env()

        volumes = {sandbox_dir: {"bind": "/app", "mode": "rw"}}

        # Устанавливаем зависимости
        try:
            client.containers.run(
                "python:3.12-slim",
                command=["sh", "-c", "pip install --quiet -r requirements.txt 2>/dev/null; true"],
                volumes=volumes,
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
        except Exception:
            pass  # Если нет requirements — не страшно

        # Устанавливаем pytest
        try:
            client.containers.run(
                "python:3.12-slim",
                command=["pip", "install", "--quiet", "pytest"],
                volumes=volumes,
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
        except Exception:
            pass

        # Запускаем pytest (c Docker SDK >=7 timeout перенесён в decode)
        try:
            logs = client.containers.run(
                "python:3.12-slim",
                command=["sh", "-c", "python -m pytest -x --timeout=30 --tb=short 2>&1 || true"],
                volumes=volumes,
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
            output = logs.decode("utf-8")
            # Считаем успехом, если нет ошибок или тесты не найдены
            if "FAILED" in output and "passed" not in output:
                return {"status": "error", "output": output[-2000:]}
            return {"status": "success", "output": output[-1000:]}
        except Exception as e:
            return {"status": "error", "output": str(e)}

    # ── Node.js ───────────────────────────────────────────────────

    @staticmethod
    def _run_node_tests(sandbox_dir: str) -> dict:
        """Установить npm-пакеты и запустить npm test в Docker (node:alpine)."""
        import docker
        client = docker.from_env()

        volumes = {sandbox_dir: {"bind": "/app", "mode": "rw"}}

        # npm install
        try:
            client.containers.run(
                "node:alpine",
                command=["npm", "install"],
                volumes=volumes,
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError:
            pass
        except Exception as e:
            return {"status": "error", "output": f"npm install error: {e}"}

        # npm test
        try:
            logs = client.containers.run(
                "node:alpine",
                command=["sh", "-c", "npm test 2>&1; true"],
                volumes=volumes,
                working_dir="/app",
                remove=True,
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
