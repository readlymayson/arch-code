import os

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class KnowledgeInput(BaseModel):
    filename: str = Field(..., description="Имя файла из папки knowledge/ (например, 'style-guide.md')")


class ReadKnowledgeTool(BaseTool):
    name: str = "ReadProjectDocs"
    description: str = (
        "Читает файлы проектной документации из папки knowledge/. "
        "Аргумент: имя файла (например, 'style-guide.md', 'api-contracts.md')."
    )
    args_schema: type[BaseModel] = KnowledgeInput

    def _run(self, filename: str) -> str:
        # Ищем относительно корня проекта
        candidates = [
            os.path.join(os.getcwd(), "knowledge", filename),
            os.path.join(os.path.dirname(__file__), "..", "knowledge", filename),
        ]
        for path in candidates:
            norm = os.path.normpath(path)
            if os.path.exists(norm):
                with open(norm, "r") as f:
                    return f.read()

        return f"Файл '{filename}' не найден в knowledge/. Доступные файлы: {os.listdir(os.path.join(os.path.dirname(__file__), '..', 'knowledge'))}"
