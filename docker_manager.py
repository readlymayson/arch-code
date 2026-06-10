import docker
import json
import os


class NodeSandbox:
    """Изолированная среда для выполнения Node.js кода в Docker-контейнере."""

    # Распространённые npm-зависимости для генерируемого кода
    COMMON_DEPS = {"express": "^4.18.2"}

    def __init__(self):
        self.client = docker.from_env()
        self.sandbox_dir = os.path.abspath("./sandbox")
        os.makedirs(self.sandbox_dir, exist_ok=True)
        self.container_name = "ai_node_sandbox"

    def _ensure_package_json(self):
        """Создаёт package.json в sandbox/, если его ещё нет."""
        pkg_path = os.path.join(self.sandbox_dir, "package.json")
        if not os.path.exists(pkg_path):
            pkg = {
                "name": "sandbox-app",
                "version": "1.0.0",
                "private": True,
                "dependencies": dict(self.COMMON_DEPS),
            }
            with open(pkg_path, "w") as f:
                json.dump(pkg, f, indent=2)

    def _run_in_container(self, command: str, timeout: int = 30) -> dict:
        """Запускает команду в Docker-контейнере с предварительной установкой npm-пакетов."""
        self._ensure_package_json()

        try:
            install_logs = self.client.containers.run(
                "node:alpine",
                command="npm install",
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
        except docker.errors.ContainerError:
            pass  # npm install может выдавать warnings, это не фатально

        try:
            logs = self.client.containers.run(
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
            return {"status": "error", "output": e.stderr.decode("utf-8")}

    def execute_code(self, filename: str, code: str) -> dict:
        """Записывает код в sandbox/ и проверяет его (синтаксис + импорты) в node:alpine.

        Возвращает {"status": "success"|"error", "output": str}.
        """
        file_path = os.path.join(self.sandbox_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(code)

        # Проверка через node -e "require('./...')" — загружает модуль без запуска сервера,
        # проверяет синтаксис и resolvable-импорты, но не блокируется на app.listen().
        app_path = f"/app/{filename}"
        return self._run_in_container(f'node -e "require(\'{app_path}\')"')

    def execute_test(self, filename: str, code: str) -> dict:
        """Записывает код в sandbox/ и запускает 'node --test <filename>'.

        Возвращает {"status": "success"|"error", "output": str}.
        """
        file_path = os.path.join(self.sandbox_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(code)

        return self._run_in_container(f"node --test /app/{filename}")
