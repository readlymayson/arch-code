# 🏗️ ARCH-CODE — PROJECT MAP

> **Назначение:** Гибридная AI-система автоматической генерации Node.js кода на базе CrewAI + LangGraph + DeepSeek V4 с изолированным исполнением в Docker.
> **Технологии:** Python 3.10+, CrewAI, LangGraph, LangChain, DeepSeek-V4-Flash, Docker SDK, RQ + Redis
> **Запуск:** `python chat.py` (интерактивный) или `python main.py` (пакетный)

---

## 📂 СТРУКТУРА ДИРЕКТОРИЙ

```
arch-code/
│
├── main.py                          # ★ Пакетный режим: CrewAI Agent + Task
├── chat.py                          # ★ РЕКОМЕНДУЕМЫЙ вход: интерактивный чат
├── graph_worker.py                  # ★ LangGraph: generate → test → fix (max 3 итерации)
├── docker_manager.py                # ★ Docker-песочница (NodeSandbox + ProjectSandbox)
├── worker.py                        # ★ Основной исполнитель: sync/async задачи
├── rq_worker.py                     #   RQ-воркер фоновых задач (Redis Queue)
│
├── tools/                           # Инструменты CrewAI
│   ├── coding_tool.py               #   LangGraphCodingTool — обёртка графа в CrewAI Tool
│   ├── knowledge_reader.py          #   ReadKnowledgeTool — чтение knowledge/ агентом
│   └── file_tools.py                #   FileManagementTools — list/read/write в sandbox
│
├── knowledge/                       # ★ База знаний для AI-агентов
│   ├── agents-architecture.md       #   План развития: 1 агент → 3 агента (Architect/Coder/Tester)
│   ├── api-contracts.md             #   API-контракты (Twilio webhook, MongoDB)
│   └── style-guide.md               #   Стайлгайд Node.js/Express.js
│
├── sandbox/                         # Генерируется; монтируется в Docker
│   ├── {task_id}/                   #   Отдельная директория на каждую задачу
│   │   ├── app.js                   #   Сгенерированный код
│   │   ├── package.json             #   package.json (авто)
│   │   └── node_modules/            #   Установленные зависимости
│   └── ...                          #   Множество sandbox-директорий
│
├── logs/                            # Логи
│   ├── worker.log                   #   RQ worker — операции
│   └── worker_error.log             #   RQ worker — ошибки
│
├── .env                             # API-ключи (gitignored)
├── requirements.txt                 # 9 Python-зависимостей
├── QWEN.md                          # Контекст для Qwen Code
├── README.md                        # Полная документация
└── .qwen/                           # Конфигурация Qwen (skills, settings)
```

---

## 🏗️ АРХИТЕКТУРА

```
                    ┌──────────────────────┐
                    │     User Input       │
                    │  (chat.py / main.py) │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  CrewAI Orchestrator │
                    │  ┌────────────────┐  │
                    │  │   Architect    │  │
                    │  │   Agent        │  │
                    │  │ (DeepSeek V4   │  │
                    │  │  Flash)        │  │
                    │  └───────┬────────┘  │
                    │          │ tools:    │
                    │  ┌───────┴──────┐    │
                    │  │ ReadKnowledge │   │
                    │  │ LangGraphCode│   │
                    │  │ FileTools    │   │
                    │  └──────────────┘   │
                    └──────────┬───────────┘
                               │ LangGraphCodingTool
                    ┌──────────▼───────────┐
                    │     LangGraph        │
                    │  ┌─────────────────┐ │
                    │  │  explore_project│ │
                    │  └────────┬────────┘ │
                    │           │          │
                    │  ┌────────▼────────┐ │
                    │  │ execute_actions │──┐
                    │  └────────┬────────┘ ││
                    │           │          ││
                    │  ┌────────▼────────┐ ││
                    │  │   test_code     │ ││
                    │  └────────┬────────┘ ││
                    │           │ error    ││
                    │  ┌────────▼────────┐ ││
                    │  │ route_next_step │─┘│
                    │  │ (max 3 retries) │  │
                    │  └────────┬────────┘  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Docker Sandbox     │
                    │  node:alpine (Node)  │
                    │  python:3.12-slim    │
                    │  (авто-детект типа)  │
                    └──────────────────────┘
```

---

## 🔄 ПОТОК ВЫПОЛНЕНИЯ (chat.py)

```
1. ChatArchitect._build_context()
   ├── Читает sandbox/ — список .js файлов
   ├── Берёт последний сгенерированный код
   ├── Берёт историю (последние 3 оборота)
   └── Добавляет user input

2. ChatArchitect.run()
   ├── Создаёт CrewAI Agent (Architect)
   ├── Crew.kickoff() — streaming вызов LLM
   │   ├── TEXT — вывод токенов пользователю
   │   └── TOOL_CALL — вызов LangGraphCodingTool
   │         └── graph_worker.workflow
   │               ├── explore_project — изучение проекта
   │               ├── execute_actions — чтение/запись файлов
   │               └── test_code — запуск тестов в Docker
   └── Сохраняет результат в last_code

3. LangGraph: generate → test → fix (до 3 итераций)
    └── Docker: npm install → node app.js / node --test
```

---

## 🧩 КЛЮЧЕВЫЕ КОМПОНЕНТЫ

### `chat.py` — ChatArchitect (рекомендуемый вход)
| Метод | Назначение |
|-------|-----------|
| `__init__()` | LLM (DeepSeek-V4-Flash), tools (knowledge + coding), история |
| `_build_context()` | Сборка контекста: sandbox-файлы + история + код + user input |
| `_create_agent()` | Создание CrewAI Architect Agent |
| `_render_stream()` | Стриминг токенов (TEXT, TOOL_CALL) в реальном времени |
| `_extract_code()` | Извлечение JS-кода из markdown |
| `run()` | Основной entry: контекст → crew → kickoff → сохранение |
| `chat_loop()` | Интерактивный REPL: `/docs`, `/code`, `/sandbox`, `/clear`, `/exit` |

### `graph_worker.py` — LangGraph

| Узел (Node) | Тип | Описание |
|-------------|-----|----------|
| `explore_project` | LangGraph Node | Показывает AI дерево проекта |
| `execute_actions` | LangGraph Node | AI читает/пишет файлы (JSON tool calls, цикл до DONE) |
| `test_code` | LangGraph Node | Детект типа проекта → запуск тестов в Docker |
| `route_next_step` | Conditional Edge | END если success или ≥3 итераций, иначе execute |

**AgentState (TypedDict):** `task_id`, `sandbox_dir`, `project_dir`, `task`, `code`, `test_code`, `test_passed`, `error`, `iterations`, `success`, `changed_files`

### `docker_manager.py` — Docker-песочница

| Класс | Метод | Описание |
|-------|-------|----------|
| **NodeSandbox** | `execute_code()` | Запуск одного .js файла в Docker (timeout 5s) |
| | `execute_test()` | Запуск `node --test` в Docker |
| | `execute_code_async()` | Асинхронная версия |
| | `execute_test_async()` | Асинхронная версия тестов |
| | `cleanup()` | Удаление sandbox-директории |
| **ProjectSandbox** | `detect_project_type()` | Авто-детект Python (requirements.txt) / Node (package.json) |
| | `run_project_tests()` | Установка зависимостей + тесты в Docker |
| | `_run_python_tests()` | pip install → pytest -x в python:3.12-slim |
| | `_run_node_tests()` | npm install → npm test в node:alpine |

### `worker.py` — Исполнитель задач

| Функция | Описание |
|---------|----------|
| `sync_project_to_sandbox()` | Rsync проекта (ai-core) в sandbox с exclude-паттернами |
| `compute_sandbox_diff()` | Git diff изменений в sandbox |
| `execute_coding_task_sync()` | ★ Основная: sync → LangGraph → diff → результат |
| `execute_coding_task()` | async-обёртка для thread pool |

### `rq_worker.py` — Фоновый воркер

| Функция | Описание |
|---------|----------|
| `start_worker()` | Redis → RQ Worker(queue=`coding_tasks`) с обработкой ошибок |

### `main.py` — Пакетный режим
- CrewAI: Architect Agent + hardcoded Task (Express.js webhook)
- `Process.sequential` — последовательное выполнение

---

## 🧠 LLM-КОНФИГУРАЦИЯ

| Параметр | Значение |
|----------|----------|
| Провайдер | RouterAI (`api.routerai.ru/api/v1`) + DeepSeek direct |
| Модель | `deepseek-chat` (chat.py, main.py) / `deepseek-v4-flash` (graph_worker.py) |
| Температура | По умолчанию LLM |
| Язык промптов | Русский (все agent/role/task инструкции) |
| API-ключ | `ROUTERAI_API_KEY` в `.env` |

---

## 🛠️ ИНСТРУМЕНТЫ CREWAI

| Инструмент | Файл | Назначение |
|-----------|------|-----------|
| **LangGraphCodingTool** | `tools/coding_tool.py` | Обёртка: task → LangGraph → результат с diff |
| **ReadKnowledgeTool** | `tools/knowledge_reader.py` | Чтение файлов из `knowledge/` AI-агентами |
| **FileManagementTools** | `tools/file_tools.py` | list/read/write в sandbox (с защитой от path traversal) |

---

## 📚 БАЗА ЗНАНИЙ (knowledge/)

| Файл | Назначение |
|------|-----------|
| `agents-architecture.md` | План развития: 1 агент → 3 агента (Architect → Coder → Tester) + роли Reviewer/Documenter/PM |
| `api-contracts.md` | Twilio-совместимый webhook (`POST /api/webhook/incoming`) + MongoDB schema |
| `style-guide.md` | Стайлгайд: camelCase, async/await, kebab-case файлы, Express.js patterns |

---

## 📋 ЗАВИСИМОСТИ

```
crewai>=0.30.0          # Оркестрация AI-агентов
langgraph>=0.0.50       # Граф генерации → тест → фикс
langchain-openai>=0.1.0 # LLM-интеграция
docker>=7.0.0           # Docker SDK для Python
pydantic>=2.0.0         # Валидация данных
python-dotenv>=1.0.0    # .env загрузка
redis>=5.0.0            # Брокер RQ
rq>=1.15.0              # Очередь фоновых задач
loguru>=0.7.0           # Логирование
```

---

## 🎯 СЦЕНАРИИ ИСПОЛЬЗОВАНИЯ

| Сценарий | Команда | Описание |
|----------|---------|----------|
| Интерактивный чат | `python chat.py` | Диалог с AI-архитектором, генерация кода |
| Пакетный режим | `python main.py` | Одноразовая задача (хардкод) |
| Фоновая задача | RQ Worker | Через Redis queue `coding_tasks` |
| Разовый запрос | `python chat.py "request"` | Однострочная задача без чата |

**Команды чата:** `/help`, `/docs`, `/code`, `/sandbox`, `/clear`, `/exit`

---

## 🐛 ИЗВЕСТНЫЕ ПРОБЛЕМЫ

| Проблема | Описание | Статус |
|----------|----------|--------|
| Stale worker registration | После перезапуска RQ: `ValueError: There exists an active worker named 'arch-code-worker' already` | Открыта |
| Две LLM-конфигурации | chat.py/main.py используют RouterAI, graph_worker.py — DeepSeek напрямую | Следует унифицировать |
| Sandbox не в .gitignore | sandbox/ должен быть в .gitignore | Проверить |

---

## 🔄 ПЛАН РАЗВИТИЯ АРХИТЕКТУРЫ (из knowledge/agents-architecture.md)

**Текущее:** 1 Agent (Architect) → LangGraphCodingTool (чёрный ящик)
**Цель:** 3 Agent (Architect → Coder → Tester) + Process.hierarchical

| Шаг | Что сделать |
|-----|------------|
| 1 | Создать `tools/code_writer.py` — запись кода в sandbox |
| 2 | Создать `tools/node_test_runner.py` — запуск тестов |
| 3 | Переписать `main.py` на 3 CrewAI агентов |
| 4 | Удалить `graph_worker.py` (цикл переезжает в CrewAI) |
| 5 | Удалить `tools/coding_tool.py` (замена на CodeWriter + NodeTestRunner) |
