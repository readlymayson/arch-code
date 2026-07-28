"""
arch-code — Multi-turn chat с AI-архитектором.

Запуск:
    python chat.py                        # интерактивный чат
    python chat.py "напиши TODO API"      # однострочная задача

Особенности:
    • Streaming-вывод — мысли агента в реальном времени
    • Multi-turn — контекст сохраняется между сообщениями
    • Команды: /exit, /help, /docs, /clear, /code, /sandbox
"""

import os
import sys
from datetime import datetime

from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv

from tools.coding_tool import LangGraphCodingTool
from tools.knowledge_reader import ReadKnowledgeTool

load_dotenv()

# ── Константы буфера контекста ───────────────────────────────────
MAX_HISTORY_TURNS = 3          # макс. пар реплик в истории
MAX_CODE_PREVIEW_LINES = 50    # макс. строк кода в контексте


# ═══════════════════════════════════════════════════════════════════
#  ChatArchitect — класс с ручным буфером контекста и streaming
# ═══════════════════════════════════════════════════════════════════

class ChatArchitect:
    """Управляет диалогом: хранит историю, собирает контекст, рендерит stream."""

    def __init__(self):
        self.llm = LLM(
            model="deepseek/deepseek-v4-flash",
            api_key=os.getenv("ROUTERAI_API_KEY"),
            base_url="https://api.routerai.com/v1",
        )
        self.knowledge_tool = ReadKnowledgeTool()
        self.coding_tool = LangGraphCodingTool()

        # Ручной буфер (CrewAI Memory = False)
        self.history: list[dict] = []      # [{"role": "user"|"assistant", "text": str}]
        self.last_code: str = ""
        self.last_code_truncated: bool = False

    # ── Вспомогательные методы ────────────────────────────────────

    def _get_sandbox_files(self) -> list[str]:
        """Возвращает список .js файлов в sandbox/ (рекурсивно по task_id)."""
        sandbox_dir = os.path.join(os.path.dirname(__file__), "sandbox")
        if not os.path.isdir(sandbox_dir):
            return []
        files = []
        for root, _dirs, fnames in os.walk(sandbox_dir):
            for f in fnames:
                if f.endswith(".js"):
                    rel = os.path.relpath(os.path.join(root, f), sandbox_dir)
                    files.append(rel)
        return sorted(files)

    def _truncate_code(self, code: str, max_lines: int = MAX_CODE_PREVIEW_LINES) -> str:
        """Обрезает код до max_lines строк, если он слишком длинный."""
        lines = code.split("\n")
        if len(lines) <= max_lines:
            self.last_code_truncated = False
            return code
        self.last_code_truncated = True
        return "\n".join(lines[:max_lines]) + f"\n# ... ({len(lines) - max_lines} more lines)"

    def _recent_history(self) -> str:
        """Последние N пар реплик в текстовом виде (макс 300 символов каждая)."""
        lines = []
        for entry in self.history[-MAX_HISTORY_TURNS * 2:]:
            prefix = "User" if entry["role"] == "user" else "Architect"
            text = entry["text"][:300]
            lines.append(f"{prefix}: {text}")
        return "\n".join(lines)

    # ── Сборка контекста для Task.description ─────────────────────

    def _build_context(self, user_input: str) -> str:
        """Собирает контекст: sandbox-файлы + последний код + история + новый запрос."""
        parts = []

        # 1. Список файлов в sandbox
        sandbox_files = self._get_sandbox_files()
        if sandbox_files:
            parts.append("[Sandbox files]")
            parts.append(", ".join(sandbox_files))
            parts.append("")

        # 2. Последний код (обрезанный)
        if self.last_code:
            preview = self._truncate_code(self.last_code)
            parts.append("[Last generated code]")
            parts.append(preview)
            if self.last_code_truncated:
                parts.append(f"(truncated — показаны первые {MAX_CODE_PREVIEW_LINES} строк)")
            parts.append("")

        # 3. История диалога
        recent = self._recent_history()
        if recent:
            parts.append("[Recent conversation]")
            parts.append(recent)
            parts.append("")

        # 4. Текущий запрос (всегда последним)
        parts.append("[Current request]")
        parts.append(f"User: {user_input}")
        parts.append("")

        # 5. Инструкция агенту
        parts.append(
            "---\n"
            "Instructions:\n"
            "- Read the relevant docs from knowledge/ if needed (use ReadProjectDocs).\n"
            "- If the user requests a MODIFICATION to existing code, describe what "
            "changed and call Autonomous_NodeJS_Coder with the FULL updated TASK.\n"
            "- If the user requests something NEW, delegate to Autonomous_NodeJS_Coder.\n"
            "- Return the final code and a brief summary."
        )

        return "\n".join(parts)

    # ── Создание агента ───────────────────────────────────────────

    def _create_agent(self) -> Agent:
        return Agent(
            role="System Architect & Tech Lead",
            goal=(
                "Проектировать архитектуру приложения, сверяться с документацией "
                "проекта и управлять написанием кода через Autonomous_NodeJS_Coder."
            ),
            backstory=(
                "Опытный Tech Lead. Перед началом работы ты должен обязательно прочитать "
                "правила из knowledge/style-guide.md и спецификации из knowledge/api-contracts.md. "
                "Используй инструмент ReadProjectDocs для чтения файлов. "
                "После изучения документации декомпозируй задачу и передай детальное ТЗ "
                "в Autonomous_NodeJS_Coder. Если задача требует валидации бизнес-логики, "
                "обязательно приложи тестовый скрипт (параметр test_code)."
            ),
            verbose=False,
            allow_delegation=False,
            llm=self.llm,
            tools=[self.knowledge_tool, self.coding_tool],
        )

    # ── Streaming-рендеринг ───────────────────────────────────────

    def _render_stream(self, stream: object) -> object:
        """
        Выводит токены в реальном времени.
        Принимает CrewStreamingOutput, возвращает финальный результат.
        """
        final_output = None
        try:
            for chunk in stream:
                chunk_type = getattr(chunk, "chunk_type", None)

                if chunk_type == "TEXT":
                    content = getattr(chunk, "content", "") or ""
                    print(content, end="", flush=True)

                elif chunk_type == "TOOL_CALL":
                    tool_call = getattr(chunk, "tool_call", None)
                    if tool_call:
                        tool_name = getattr(tool_call, "tool_name", "?")
                        print(f"\n🔧 [{tool_name}] Выполняется...\n", flush=True)

                elif hasattr(chunk, "content"):
                    content = chunk.content or ""
                    if content:
                        print(content, end="", flush=True)

            final_output = getattr(stream, "result", None)

        except Exception as e:
            print(f"\n\n❌ Ошибка во время выполнения: {e}", flush=True)

        return final_output

    # ── Извлечение кода из результата ─────────────────────────────

    def _extract_code(self, result: object) -> str:
        """Извлекает JS-код из ответа архитектора (ищет markdown-блоки)."""
        raw = str(result)

        # Ищем ```js / ```javascript / ```node / ```
        for marker in ["```javascript", "```js", "```node", "```"]:
            start = raw.find(marker)
            if start != -1:
                start += len(marker)
                end = raw.find("```", start)
                if end != -1:
                    return raw[start:end].strip()

        # Если markdown-блоков нет — ищем сигнатуры кода построчно
        lines = raw.split("\n")
        code_lines = []
        in_code = False
        for line in lines:
            stripped = line.strip()
            if any(kw in stripped for kw in ("require(", "import ", "const ", "let ", "var ", "function ")):
                in_code = True
            if in_code:
                code_lines.append(line)

        return "\n".join(code_lines) if code_lines else raw

    # ── Основной метод: запуск задачи ─────────────────────────────

    def run(self, user_input: str) -> str:
        """Принимает запрос, запускает CrewAI со streaming, возвращает результат."""

        context = self._build_context(user_input)
        architect = self._create_agent()

        task = Task(
            description=context,
            expected_output=(
                "Рабочий .js файл в папке sandbox/, прошедший проверку в Docker-песочнице."
            ),
            agent=architect,
        )

        crew = Crew(
            agents=[architect],
            tasks=[task],
            process=Process.sequential,
            stream=True,
            memory=False,
            verbose=False,
        )

        # ── Запуск со streaming ──
        streaming = crew.kickoff()
        final_output = self._render_stream(streaming)

        # ── Fallback: если stream не вернул результат ──
        if final_output is None:
            crew_fallback = Crew(
                agents=[architect],
                tasks=[task],
                process=Process.sequential,
                stream=False,
                memory=False,
                verbose=True,
            )
            final_output = crew_fallback.kickoff()

        result_text = str(final_output)

        # ── Сохраняем в буфер ──
        self.history.append({"role": "user", "text": user_input})
        self.history.append({"role": "assistant", "text": result_text[:500]})

        extracted = self._extract_code(result_text)
        if extracted.strip():
            self.last_code = extracted

        return result_text


# ═══════════════════════════════════════════════════════════════════
#  Чат-интерфейс
# ═══════════════════════════════════════════════════════════════════

BANNER = r"""

╔══════════════════════════════════════════════════╗
║           arch-code — AI-разработчик             ║
║  Опиши, что нужно сделать, и я напишу код!      ║
╚══════════════════════════════════════════════════╝

Команды:
  /exit     — выход
  /help     — эта подсказка
  /docs     — прочитать документацию из knowledge/
  /code     — показать последний сгенерированный код
  /sandbox  — показать файлы в sandbox/
  /clear    — очистить экран

Пример:
  > напиши Express сервер с GET /health
  > добавь middleware для логирования    ← multi-turn!
"""

HELP = """Доступные команды:
  /exit       — завершить сессию
  /help       — показать эту подсказку
  /docs       — вывести содержимое knowledge/
  /code       — показать последний сгенерированный код
  /sandbox    — показать файлы в папке sandbox/
  /clear      — очистить экран

  Любой другой текст отправляется архитектору.
  Контекст предыдущих сообщений сохраняется.

Примеры:
  • Напиши Express сервер с GET /health
  • Сделай REST API для TODO-списка
  • Создай скрипт для сортировки JSON-файла
  • Добавь к предыдущему CORS и body-parser
"""


def show_docs():
    """Показывает содержимое knowledge/ (первые 30 строк каждого файла)."""
    docs_dir = os.path.join(os.path.dirname(__file__), "knowledge")
    if not os.path.exists(docs_dir):
        print("  [docs] Папка knowledge/ не найдена.")
        return

    files = sorted(f for f in os.listdir(docs_dir) if f.endswith(".md"))
    if not files:
        print("  [docs] В knowledge/ нет .md файлов.")
        return

    for fname in files:
        fpath = os.path.join(docs_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        print(f"\n{'='*60}")
        print(f"  📄 {fname}")
        print(f"{'='*60}")
        lines = content.strip().split("\n")
        for line in lines[:30]:
            print(f"  {line}")
        if len(lines) > 30:
            print(f"  ... и ещё {len(lines) - 30} строк")
        print()


def show_code(architect: ChatArchitect):
    """Показывает последний сгенерированный код."""
    if not architect.last_code:
        print("  ⚠️  Код ещё не сгенерирован. Сначала напишите задачу.")
        return

    preview = architect._truncate_code(architect.last_code)
    print(f"\n{'='*60}")
    print(f"  📄 Последний сгенерированный код")
    print(f"{'='*60}\n")
    print(preview)
    if architect.last_code_truncated:
        print(f"\n  (показаны первые {MAX_CODE_PREVIEW_LINES} строк)")
    print()


def show_sandbox():
    """Показывает файлы в sandbox/ с размерами."""
    sandbox_dir = os.path.join(os.path.dirname(__file__), "sandbox")
    if not os.path.isdir(sandbox_dir):
        print("  ⚠️  Папка sandbox/ не найдена.")
        return

    items = os.listdir(sandbox_dir)
    if not items:
        print("  📁 sandbox/ пуста.")
        return

    print(f"\n  📁 sandbox/ ({len(items)} файлов)")
    print(f"  {'─'*40}")
    for item in sorted(items):
        fpath = os.path.join(sandbox_dir, item)
        size = os.path.getsize(fpath)
        if os.path.isfile(fpath):
            print(f"  📄 {item:<30} {size:>8} B")
        else:
            print(f"  📂 {item}/")
    print()


def chat_loop():
    architect = ChatArchitect()
    print(BANNER)

    while True:
        try:
            prompt = input("\n💬 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nДо свидания! 👋")
            break

        if not prompt:
            continue

        cmd = prompt.lower()

        if cmd in ("/exit", "/quit", "exit", "quit"):
            print("До свидания! 👋")
            break

        if cmd == "/help":
            print(HELP)
            continue

        if cmd == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
            continue

        if cmd == "/docs":
            show_docs()
            continue

        if cmd == "/code":
            show_code(architect)
            continue

        if cmd == "/sandbox":
            show_sandbox()
            continue

        # ── Выполнение задачи ──
        print(f"\n{'─'*60}")
        print(f"  🧠 Думаю...")
        print(f"{'─'*60}\n")

        start = datetime.now()
        try:
            result = architect.run(prompt)
            elapsed = (datetime.now() - start).total_seconds()

            print(f"\n{'='*60}")
            print(f"  ✅ ГОТОВО  ({elapsed:.1f} сек)")
            print(f"{'='*60}")

        except Exception as e:
            elapsed = (datetime.now() - start).total_seconds()
            print(f"\n{'!'*60}")
            print(f"  ❌ ОШИБКА  ({elapsed:.1f} сек)")
            print(f"{'!'*60}")
            print(f"  {e}")

        finally:
            print(f"\n{'─'*60}")
            print(f"  Команды: /help  /docs  /code  /sandbox  /clear  /exit")
            print(f"{'─'*60}")


def main():
    # Однострочный режим: python chat.py "задача"
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        print(f"🧩 Задача: {task}\n")
        architect = ChatArchitect()
        try:
            result = architect.run(task)
            print(f"\n{'='*60}")
            print(f"✅ Результат:")
            print(f"{'='*60}\n{result}")
        except Exception as e:
            print(f"\n❌ Ошибка: {e}")
        return

    chat_loop()


if __name__ == "__main__":
    main()
