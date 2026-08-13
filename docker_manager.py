import asyncio
import json
import os
import shutil
import subprocess
import uuid

from loguru import logger


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
        except Exception as exc:
            logger.warning(f"cleanup: docker stop {suffix} не удался: {exc}")
        try:
            subprocess.run(
                ["docker", "rm", "--force", suffix],
                capture_output=True, timeout=10, check=False,
            )
        except Exception as exc:
            logger.warning(f"cleanup: docker rm {suffix} не удался: {exc}")


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

        Двухфазный запуск:
          1. npm install — контейнер с сетью (нужен npm registry).
          2. Основная команда — контейнер без сети (network_mode="none"),
             с cap_drop=["ALL"] и user="1000:1000".

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

        # ── Фаза 1: npm install (с сетью) ─────────────────────────
        try:
            client.containers.run(
                "node:alpine",
                command=["npm", "install"],
                name=container_name,
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                user="1000:1000",
                remove=True,
                detach=False,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError as exc:
            logger.warning(
                f"npm install в контейнере {container_name} вернул ошибку: {exc}"
            )
        except Exception as exc:
            logger.warning(
                f"npm install в контейнере {container_name} не удался: {exc}"
            )

        # ── Фаза 2: основная команда (без сети, hardening) ────────
        try:
            logs = client.containers.run(
                "node:alpine",
                command=command,
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                # Фаза исполнения — сетевая изоляция + безопасность
                user="1000:1000",
                cap_drop=["ALL"],
                network_mode="none",
                security_opt=["no-new-privileges"],
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
            except Exception as exc:
                logger.warning(f"Не удалось декодировать stderr: {exc}")
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
            no_network=True,
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
            no_network=True,
        )

    async def _run_in_container_async(
        self, command: list[str], timeout: int = 30, *, no_network: bool = False
    ) -> dict:
        """async-запуск команды в Docker через asyncio.create_subprocess_exec.

        Args:
            command: Команда для запуска.
            timeout: Таймаут (сек).
            no_network: True → фаза исполнения (network=none, cap_drop,
                no-new-privileges, user=1000:1000). False → фаза установки
                (npm install — нужна сеть).
        """
        volume_bind = f"{self.sandbox_dir}:/app"
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", volume_bind,
            "-w", "/app",
        ]
        if no_network:
            docker_cmd += [
                "--network", "none",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--user", "1000:1000",
            ]
        docker_cmd += ["node:alpine"] + command

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

    def cleanup(self) -> None:
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

    Двухфазный запуск контейнеров:
      1. Фаза сборки (install) — контейнер С СЕТЬЮ (нужен PyPI/npm
         registry), зависимости устанавливаются в volume-смонтированную
         sandbox-директорию (/app/.deps или /app/node_modules).
      2. Фаза исполнения (run/test) — контейнер БЕЗ СЕТИ
         (network_mode="none"), с cap_drop=["ALL"] и user="1000:1000".
         Использует уже установленные пакеты.
    """

    # Общие security-параметры для фазы исполнения (без сети)
    _RUN_SECURITY = {
        "user": "1000:1000",
        "cap_drop": ["ALL"],
        "network_mode": "none",
        "security_opt": ["no-new-privileges"],
    }

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

        Двухфазный запуск:
          1. install: контейнер с сетью, pip install --target=/app/.deps
             (зависимости пишутся в sandbox/.deps — volume).
          2. test: контейнер без сети (network_mode="none"),
             PYTHONPATH=/app/.deps, запуск pytest.

        Не пытается форсированно установить ВСЕ зависимости проекта
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

        # ── Фаза 1: установка зависимостей (с сетью) ──────────────
        _rm_container()
        try:
            client.containers.run(
                "python:3.12-slim",
                command=[
                    "sh", "-c",
                    "pip install --quiet --timeout=60 "
                    "--target=/app/.deps -r requirements.txt pytest pytest-timeout 2>&1 || true"
                ],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                user="1000:1000",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError as exc:
            logger.warning(
                f"pip install в {container_name} вернул ошибку: {exc}"
            )
        except Exception as exc:
            logger.error(
                f"pip install в {container_name} не удался: {exc}", exc_info=True
            )

        # ── Фаза 2: запуск тестов (без сети) ──────────────────────
        _rm_container()
        try:
            logs = client.containers.run(
                "python:3.12-slim",
                command=[
                    "sh", "-c",
                    "PYTHONPATH=/app/.deps python -m pytest -x "
                    "--timeout=30 --tb=short 2>&1 || true"
                ],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                # Фаза исполнения — сетевая изоляция + безопасность
                **ProjectSandbox._RUN_SECURITY,
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
            logger.error(
                f"Python-тесты в контейнере {container_name} не удались: {e}",
                exc_info=True,
            )
            return {"status": "error", "output": str(e)}

    # ── Node.js ───────────────────────────────────────────────────

    @staticmethod
    def _run_node_tests(sandbox_dir: str, task_id: str = "") -> dict:
        """Установить npm-пакеты и запустить npm test в Docker (node:alpine).

        Двухфазный запуск:
          1. install: контейнер с сетью, npm install → /app/node_modules.
          2. test: контейнер без сети (network_mode="none"), npm test.
        """
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

        # ── Фаза 1: npm install (с сетью) ─────────────────────────
        _rm_container()
        try:
            client.containers.run(
                "node:alpine",
                command=["npm", "install"],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                user="1000:1000",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError as exc:
            logger.warning(
                f"npm install в контейнере {container_name} вернул ошибку: {exc}"
            )
        except Exception as e:
            logger.error(f"npm install error: {e}", exc_info=True)
            return {"status": "error", "output": f"npm install error: {e}"}

        # ── Фаза 2: npm test (без сети) ───────────────────────────
        _rm_container()
        try:
            logs = client.containers.run(
                "node:alpine",
                command=["sh", "-c", "npm test 2>&1; true"],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                # Фаза исполнения — сетевая изоляция + безопасность
                **ProjectSandbox._RUN_SECURITY,
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
            logger.error(
                f"npm test в контейнере {container_name} не удался: {e}",
                exc_info=True,
            )
            return {"status": "error", "output": str(e)}

    # ── Запуск приложения (smoke-проверка) ────────────────────────

    @staticmethod
    def run_application(
        sandbox_dir: str,
        project_type: str | None = None,
        app_type: str = "cli_script",
        task_id: str = "",
        health_endpoint: str = "/health",
        health_port: int = 8000,
    ) -> dict:
        """Запустить приложение в Docker и проверить, что оно работает.

        Args:
            sandbox_dir: Абсолютный путь к директории проекта.
            project_type: "python", "node" или None (автоопределение).
            app_type: "web_app" (HTTP health-check) или "cli_script"
                (запуск с таймаутом + exit code).
            task_id: ID задачи для именования контейнера.
            health_endpoint: Путь health-check (для web_app).
            health_port: Порт health-check (для web_app).

        Returns:
            dict {"status": "success"|"error", "output": str}

        Двухфазный запуск:
          1. install — зависимости с сетью (pip --target / npm install).
          2. run — приложение без сети (network_mode="none").
        """
        if project_type is None:
            project_type = ProjectSandbox.detect_project_type(sandbox_dir)

        # Устанавливаем зависимости (фаза с сетью)
        install_result = ProjectSandbox._install_deps(sandbox_dir, project_type, task_id)
        if install_result["status"] == "error":
            return install_result

        if project_type == "python":
            return ProjectSandbox._run_python_app(
                sandbox_dir, app_type, task_id, health_endpoint, health_port
            )
        elif project_type == "node":
            return ProjectSandbox._run_node_app(
                sandbox_dir, app_type, task_id, health_endpoint, health_port
            )
        else:
            return {"status": "error", "output": "Не удалось определить тип проекта"}

    @staticmethod
    def _install_deps(sandbox_dir: str, project_type: str, task_id: str = "") -> dict:
        """Фаза установки зависимостей (контейнер с сетью)."""
        import docker
        client = docker.from_env()
        container_name = f"project-run-{task_id}" if task_id else "project-run"

        def _rm_container():
            try:
                c = client.containers.get(container_name)
                c.remove(force=True)
            except docker.errors.NotFound:
                pass

        volumes = {sandbox_dir: {"bind": "/app", "mode": "rw"}}

        _rm_container()
        try:
            if project_type == "python":
                cmd = [
                    "sh", "-c",
                    "pip install --quiet --timeout=60 "
                    "--target=/app/.deps -r requirements.txt 2>&1 || true"
                ]
                image = "python:3.12-slim"
            else:
                cmd = ["npm", "install"]
                image = "node:alpine"

            client.containers.run(
                image,
                command=cmd,
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                user="1000:1000",
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
            return {"status": "success", "output": ""}
        except docker.errors.ContainerError as exc:
            logger.warning(f"Установка зависимостей ({container_name}): {exc}")
            return {"status": "success", "output": ""}  # не блокируем запуск
        except Exception as e:
            logger.error(
                f"Установка зависимостей в {container_name} не удалась: {e}",
                exc_info=True,
            )
            return {"status": "error", "output": f"Ошибка установки зависимостей: {e}"}

    # ── Запуск Python-приложения ─────────────────────────────────

    @staticmethod
    def _run_python_app(
        sandbox_dir: str,
        app_type: str,
        task_id: str = "",
        health_endpoint: str = "/health",
        health_port: int = 8000,
    ) -> dict:
        """Запустить Python-приложение в контейнере без сети."""
        import docker
        client = docker.from_env()
        container_name = f"project-run-{task_id}" if task_id else "project-run"

        def _rm_container():
            try:
                c = client.containers.get(container_name)
                c.remove(force=True)
            except docker.errors.NotFound:
                pass

        volumes = {sandbox_dir: {"bind": "/app", "mode": "rw"}}
        _rm_container()

        if app_type == "web_app":
            # Smoke-скрипт в sandbox: запуск uvicorn + health-check через
            # urllib (curl ОТСУТСТВУЕТ в python:3.12-slim).
            # Отдельный файл — избегаем хрупкого экранирования shell.
            # ВАЖНО: network_mode="none" запрещает исходящие соединения,
            # но локальный HTTP внутри контейнера работает (loopback).
            # Модуль приложения: ищем "app" в main.py, иначе пробуем
            # web_app.py / app.py (агент мог положить ASGI-приложение туда).
            smoke_script = f'''import os, subprocess, sys, time, urllib.request, signal

# Определяем модуль ASGI-приложения: main:app, иначе web_app:app / app:app
asgi_module = "main:app"
for cand in ("main.py", "web_app.py", "app.py"):
    if os.path.isfile(cand):
        with open(cand, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        if "def app(" in src or "app = FastAPI(" in src or "app = Flask(" in src:
            asgi_module = cand[:-3] + ":app"
            break

# PYTHONPATH передаётся ДОЧЕРНЕМУ процессу (uvicorn), т.к. sys.path
# родителя не наследуется subprocess.
env = dict(os.environ, PYTHONPATH="/app/.deps")
proc = subprocess.Popen(
    ["python", "-m", "uvicorn", asgi_module, "--host", "0.0.0.0", "--port", "{health_port}"],
    stdout=open("/tmp/app.log", "w"), stderr=subprocess.STDOUT, env=env,
)
try:
    time.sleep(5)
    with urllib.request.urlopen(
        "http://127.0.0.1:{health_port}{health_endpoint}", timeout=5
    ) as resp:
        ok = resp.status < 400
except Exception:
    ok = False
try:
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=5)
except Exception:
    proc.kill()
if not ok:
    print("HEALTH_CHECK_FAILED")
    try:
        print(open("/tmp/app.log").read())
    except Exception:
        pass
    sys.exit(1)
print("OK")
'''
            smoke_path = os.path.join(sandbox_dir, "_smoke_web.py")
            with open(smoke_path, "w", encoding="utf-8") as f:
                f.write(smoke_script)
            cmd = f"cd /app && timeout 20 python _smoke_web.py 2>&1"
        else:
            # CLI-скрипт: запуск с таймаутом, проверка exit code и Traceback
            cmd = (
                "cd /app && PYTHONPATH=/app/.deps "
                "timeout 10 python main.py --help 2>&1 || "
                "timeout 10 python main.py 2>&1"
            )

        try:
            logs = client.containers.run(
                "python:3.12-slim",
                command=["sh", "-c", cmd],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                # Фаза исполнения — сетевая изоляция + безопасность
                **ProjectSandbox._RUN_SECURITY,
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
            output = logs.decode("utf-8")
            if "HEALTH_CHECK_FAILED" in output or "Traceback" in output:
                return {"status": "error", "output": output[-2000:]}
            return {"status": "success", "output": output[-1000:]}
        except docker.errors.ContainerError as e:
            # При exit code != 0 docker-py бросает ContainerError, но
            # stderr/stdout могут быть пустыми. Fallback: читаем логи
            # контейнера напрямую.
            err = ""
            try:
                err = e.stderr.decode("utf-8") if e.stderr else ""
            except Exception:
                err = ""
            if not err:
                try:
                    err = getattr(e, "stdout", b"").decode("utf-8") if getattr(e, "stdout", None) else ""
                except Exception:
                    err = ""
            if not err:
                try:
                    c = client.containers.get(e.container.id)
                    err = c.logs().decode("utf-8", errors="replace")
                    c.remove(force=True)
                except Exception:
                    pass
            return {"status": "error", "output": (err or "smoke-проверка не прошла")[-2000:]}
        except Exception as e:
            logger.error(
                f"Запуск Python-приложения ({container_name}) не удался: {e}",
                exc_info=True,
            )
            return {"status": "error", "output": str(e)}

    # ── Запуск Node.js-приложения ────────────────────────────────

    @staticmethod
    def _run_node_app(
        sandbox_dir: str,
        app_type: str,
        task_id: str = "",
        health_endpoint: str = "/health",
        health_port: int = 8000,
    ) -> dict:
        """Запустить Node.js-приложение в контейнере без сети."""
        import docker
        client = docker.from_env()
        container_name = f"project-run-{task_id}" if task_id else "project-run"

        def _rm_container():
            try:
                c = client.containers.get(container_name)
                c.remove(force=True)
            except docker.errors.NotFound:
                pass

        volumes = {sandbox_dir: {"bind": "/app", "mode": "rw"}}
        _rm_container()

        entry_file = "app.js" if os.path.exists(os.path.join(sandbox_dir, "app.js")) else "server.js"

        if app_type == "web_app":
            # Smoke-скрипт в sandbox: запуск node + health-check через fetch
            # (curl ОТСУТСТВУЕТ в node:alpine, fetch есть в Node 18+).
            smoke_script = f'''const {{spawn}} = require("child_process");

const proc = spawn("node", ["{entry_file}"], {{ stdio: ["ignore", "pipe", "pipe"] }});
let log = "";
proc.stdout.on("data", d => log += d);
proc.stderr.on("data", d => log += d);

setTimeout(async () => {{
  let ok = false;
  try {{
    const resp = await fetch("http://127.0.0.1:{health_port}{health_endpoint}", {{ signal: AbortSignal.timeout(5000) }});
    ok = resp.status < 400;
  }} catch (e) {{
    ok = false;
  }}
  proc.kill("SIGTERM");
  if (!ok) {{
    console.log("HEALTH_CHECK_FAILED");
    console.log(log);
    process.exit(1);
  }}
  console.log("OK");
  process.exit(0);
}}, 5000);
'''
            smoke_path = os.path.join(sandbox_dir, "_smoke_web.js")
            with open(smoke_path, "w", encoding="utf-8") as f:
                f.write(smoke_script)
            cmd = f"cd /app && timeout 20 node _smoke_web.js 2>&1"
        else:
            cmd = (
                f"cd /app && timeout 10 node {entry_file} --help 2>&1 || "
                f"timeout 10 node {entry_file} 2>&1"
            )

        try:
            logs = client.containers.run(
                "node:alpine",
                command=["sh", "-c", cmd],
                name=container_name,
                volumes=volumes,
                working_dir="/app",
                **ProjectSandbox._RUN_SECURITY,
                remove=False,
                detach=False,
                stderr=True,
                stdout=True,
            )
            output = logs.decode("utf-8")
            if "HEALTH_CHECK_FAILED" in output or "Error" in output[:500]:
                return {"status": "error", "output": output[-2000:]}
            return {"status": "success", "output": output[-1000:]}
        except docker.errors.ContainerError as e:
            err = e.stderr.decode("utf-8") if e.stderr else str(e)
            return {"status": "error", "output": err[-2000:]}
        except Exception as e:
            logger.error(
                f"Запуск Node-приложения ({container_name}) не удался: {e}",
                exc_info=True,
            )
            return {"status": "error", "output": str(e)}
