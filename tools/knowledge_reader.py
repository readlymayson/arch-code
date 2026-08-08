"""Чтение файлов проектной документации из knowledge/.

Фильтрация по типу проекта:
- Запрос generic-имени ('style-guide.md', 'api-contracts.md') автоматически
  резолвится в python-вариант ('python-style-guide.md', 'python-api-contracts.md')
  если проект Python (есть requirements.txt / pyproject.toml / setup.py).
- Node.js-проекты (package.json) получают старые файлы без префикса.
- Явный параметр project_type ('python' | 'node') переопределяет авто-детекцию.
- Если резолвнутый файл не найден — fallback на запрошенное имя.
"""

import os

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# Generic-имена → python-префикс (если проект Python)
PYTHON_PREFIX_MAP = {
    "style-guide.md": "python-style-guide.md",
    "api-contracts.md": "python-api-contracts.md",
}

# Корень папки knowledge/ (переопределяется в тестах через monkeypatch)
KNOWLEDGE_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "knowledge")
)


def detect_project_type(cwd: str) -> str:
    """Определить тип проекта по маркерным файлам в cwd (или выше).

    Returns:
        "python" | "node" | "unknown"
    """
    markers = {
        "python": ["requirements.txt", "pyproject.toml", "setup.py"],
        "node": ["package.json"],
    }
    # Проверяем cwd и родительские каталоги (несколько уровней)
    current = os.path.abspath(cwd)
    for _ in range(4):
        names = os.listdir(current) if os.path.isdir(current) else []
        for ptype, files in markers.items():
            if any(f in names for f in files):
                return ptype
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return "unknown"


class KnowledgeInput(BaseModel):
    filename: str = Field(..., description="Имя файла из папки knowledge/ (например, 'style-guide.md')")
    project_type: str = Field(
        default="auto",
        description=(
            "Тип проекта: 'python', 'node' или 'auto' (авто-детекция). "
            "Для python generic-имена (style-guide.md, api-contracts.md) "
            "резолвятся в python-варианты."
        ),
    )


class ReadKnowledgeTool(BaseTool):
    name: str = "ReadProjectDocs"
    description: str = (
        "Читает файлы проектной документации из папки knowledge/. "
        "Аргумент: имя файла (например, 'style-guide.md', 'api-contracts.md'). "
        "Опционально project_type='python'|'node'|'auto' — для python "
        "generic-имена автоматически резолвятся в python-специфичные файлы "
        "(python-style-guide.md, python-api-contracts.md)."
    )
    args_schema: type[BaseModel] = KnowledgeInput

    def _resolve_filename(self, filename: str, project_type: str) -> str:
        """Резолв generic-имени в python-вариант при python-проекте."""
        if project_type == "python":
            return PYTHON_PREFIX_MAP.get(filename, filename)
        if project_type == "auto":
            detected = detect_project_type(os.getcwd())
            if detected == "python":
                return PYTHON_PREFIX_MAP.get(filename, filename)
        return filename

    def _run(self, filename: str, project_type: str = "auto") -> str:
        knowledge_root = KNOWLEDGE_ROOT
        # Защита от path traversal: разрешаем только имена без ".." и без слэшей
        if os.path.basename(filename) != filename:
            filename = os.path.basename(filename)

        # Резолв по типу проекта (python → python-* файлы)
        resolved = self._resolve_filename(filename, project_type)

        candidates = [
            os.path.join(os.getcwd(), "knowledge", resolved),
            os.path.join(knowledge_root, resolved),
        ]
        for path in candidates:
            norm = os.path.normpath(path)
            if os.path.exists(norm):
                with open(norm, "r", encoding="utf-8") as f:
                    return f.read()

        # Fallback: если резолвнутый файл не найден — пробуем исходное имя
        if resolved != filename:
            fallback_candidates = [
                os.path.join(os.getcwd(), "knowledge", filename),
                os.path.join(knowledge_root, filename),
            ]
            for path in fallback_candidates:
                norm = os.path.normpath(path)
                if os.path.exists(norm):
                    with open(norm, "r", encoding="utf-8") as f:
                        return f.read()

        # Список доступных файлов
        try:
            available = sorted(os.listdir(knowledge_root))
        except OSError:
            available = []
        return f"Файл '{filename}' не найден в knowledge/. Доступные файлы: {available}"
