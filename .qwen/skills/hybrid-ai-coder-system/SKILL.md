---
name: hybrid-ai-coder-system
description: "Архитектура гибридной системы генерации кода: CrewAI + LangGraph + DeepSeek + Docker-песочница"
source: auto-skill
extracted_at: '2026-06-03T00:00:00.000Z'
---

# Гибридная система AI-кодинга (CrewAI + LangGraph + DeepSeek + Docker)

## Зачем

Построить multi-agent систему, где один LLM (архитектор, DeepSeek-V4-Pro) проектирует и декомпозирует задачу, а быстрый LLM (DeepSeek-V4-Flash) в цикле пишет и исправляет код до успешного теста — всё в изолированной Docker-песочнице.

## Архитектура

```
┌──────────────────────────────────┐
│         CrewAI (Оркестратор)      │
│  ┌────────────────────────────┐  │
│  │ Архитектор (DeepSeek-V4-Pro)│  │
│  │  Инструменты:              │  │
│  │  ├─ ReadKnowledgeTool      │  │
│  │  └─ LangGraphCodingTool    │  │
│  └─────────┬──────────────────┘  │
└────────────┼─────────────────────┘
             │ вызов инструмента
             ▼
┌──────────────────────────────────┐
│    LangGraph (Мини-мозг кодера)   │
│  generate → test → (generate)*   │
│  Модель: DeepSeek-V4-Flash       │
└────────────┬─────────────────────┘
             ▼
┌──────────────────────────────────┐
│   Docker-песочница (node:18-alpine)│
│  Монтирует ./sandbox → /app      │
└──────────────────────────────────┘
```

## Компоненты

### 1. Docker-менеджер (`docker_manager.py`)
- Запускает `node:18-alpine` контейнер с монтированием локальной `sandbox/` папки
- Два метода: `execute_code` (просто `node script.js`) и `execute_test` (через `node --test`)
- Контейнер удаляется после выполнения (`remove=True`)
- Ошибки выполнения ловятся через `docker.errors.ContainerError`

### 2. LangGraph-цикл (`graph_worker.py`)
- **Состояние графа:** `task`, `code`, `test_code`, `test_passed`, `error`, `iterations`, `success`
- **Узел `generate`:** отправляет задачу + ошибку (если была) в DeepSeek-V4-Flash, очищает ответ от markdown-разметки
- **Узел `test`:** если передан `test_code` — склеивает код + тест и запускает `node --test`; иначе просто `node app.js`
- **Маршрутизация:** success → END; iterations >= 3 → END; иначе → generate
- Жёсткий лимит — 3 итерации

### 3. CrewAI-обёртка (`tools/coding_tool.py`)
- Наследуется от `crewai.tools.BaseTool`
- Принимает `task_description` (обязательно) и `test_code` (опционально)
- Вызывает `coding_graph.invoke()` с начальным состоянием
- Возвращает строку с результатом (успех/неудача + код)

### 4. Инструмент чтения документации (`tools/knowledge_reader.py`)
- Читает статические `.md` файлы из папки `knowledge/`
- Ищет файл в нескольких местах (текущая директория и `../knowledge/`)
- Агент сам решает, когда и какой файл прочитать (YAGNI-принцип)

### 5. CrewAI-оркестратор (`main.py`)
- Один Agent (Архитектор) с двумя инструментами: `ReadKnowledgeTool` и `LangGraphCodingTool`
- Архитектор использует DeepSeek-V4-Pro (более умная модель)
- Process.sequential — одна задача за раз

## Ключевые решения

### Почему ReadKnowledgeTool вместо inline-описаний или MCP-сервера
- **Inline-описания:** раздувают контекст каждой задачи, не переиспользуются
- **MCP-сервер:** избыточен для статической документации
- **ReadKnowledgeTool:** агент сам решает, когда читать; 0 инфраструктуры; файлы легко менять

### Как обрабатывать логические ошибки (не только crash)
- Архитектор передаёт не только ТЗ, но и тестовый скрипт (`test_code`)
- LangGraph-цикл запускает `node --test`, и если assert не проходит — это ошибка
- Кодер видит вывод теста и исправляет логику, а не только синтаксис

### Двухуровневая модель
- Pro-модель (V4-Pro) — дороже, умнее — для архитектора
- Flash-модель (V4-Flash) — быстрее, дешевле — для итеративного кодинга (до 3 вызовов на задачу)

## Структура проекта

```
my-ai-coder/
├── .env                     # DEEPSEEK_API_KEY
├── requirements.txt
├── docker_manager.py        # NodeSandbox
├── graph_worker.py          # LangGraph-цикл
├── main.py                  # CrewAI-оркестратор
├── tools/
│   ├── knowledge_reader.py  # ReadKnowledgeTool
│   └── coding_tool.py       # LangGraphCodingTool
├── knowledge/
│   ├── style-guide.md
│   └── api-contracts.md
└── sandbox/
    └── .gitkeep
```

## Когда применять

- Нужно генерировать код на Node.js под конкретные контракты и стиль
- Есть набор статической документации (style guides, API contracts), которую агент должен учитывать
- Требуется изолированное выполнение кода (Docker)
- Нужна валидация не только синтаксиса, но и бизнес-логики (через тесты)
- Есть доступ к DeepSeek V4 через OpenAI-совместимый API
