import os

from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv

from tools.coding_tool import LangGraphCodingTool
from tools.knowledge_reader import ReadKnowledgeTool

load_dotenv()

pro_llm = LLM(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
)

knowledge_tool = ReadKnowledgeTool()
coding_tool = LangGraphCodingTool()


def main():
    architect = Agent(
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
        verbose=True,
        allow_delegation=False,
        llm=pro_llm,
        tools=[knowledge_tool, coding_tool],
    )

    # ═══════════════════════════════════════════════════════════════
    # ПРИМЕР ЗАДАЧИ — замените на свою
    # ═══════════════════════════════════════════════════════════════
    task = Task(
        description=(
            "Реализовать webhook-эндпоинт на Express.js для приёма входящих "
            "сообщений. Сверься с api-contracts.md, чтобы понять, какой формат "
            "JSON мы ожидаем на входе, и с style-guide.md для правильного "
            "оформления контроллеров. Напиши ТЗ и заставь кодера реализовать "
            "и протестировать скрипт."
        ),
        expected_output="Рабочий файл server.js (или контроллер), прошедший валидацию кодером.",
        agent=architect,
    )

    crew = Crew(agents=[architect], tasks=[task], process=Process.sequential)

    print("=== ЗАПУСК ИИ-РАЗРАБОТЧИКОВ ===")
    result = crew.kickoff()
    print("\n=== ИТОГОВЫЙ РЕЗУЛЬТАТ ===")
    print(result)


if __name__ == "__main__":
    main()
