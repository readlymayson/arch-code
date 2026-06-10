import docker
import os


class NodeSandbox:
    """Изолированная среда для выполнения Node.js кода в Docker-контейнере."""

    def __init__(self):
        self.client = docker.from_env()
        self.sandbox_dir = os.path.abspath("./sandbox")
        os.makedirs(self.sandbox_dir, exist_ok=True)
        self.container_name = "ai_node_sandbox"

    def execute_code(self, filename: str, code: str) -> dict:
        """Записывает код в sandbox/ и выполняет в контейнере node:18-alpine.

        Возвращает {"status": "success"|"error", "output": str}.
        """
        file_path = os.path.join(self.sandbox_dir, filename)
        with open(file_path, "w") as f:
            f.write(code)

        try:
            logs = self.client.containers.run(
                "node:18-alpine",
                command=f"node /app/{filename}",
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
            return {"status": "success", "output": logs.decode("utf-8")}
        except docker.errors.ContainerError as e:
            return {"status": "error", "output": e.stderr.decode("utf-8")}

    def execute_test(self, filename: str, code: str) -> dict:
        """Записывает код в sandbox/ и запускает 'node --test <filename>' (Node 18+).

        Возвращает {"status": "success"|"error", "output": str}.
        """
        file_path = os.path.join(self.sandbox_dir, filename)
        with open(file_path, "w") as f:
            f.write(code)

        try:
            logs = self.client.containers.run(
                "node:18-alpine",
                command=f"node --test /app/{filename}",
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
                stderr=True,
                stdout=True,
            )
            return {"status": "success", "output": logs.decode("utf-8")}
        except docker.errors.ContainerError as e:
            return {"status": "error", "output": e.stderr.decode("utf-8")}
