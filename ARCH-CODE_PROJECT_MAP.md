# 🏗️ ARCH-CODE — PROJECT MAP

> **Назначение:** Гибридная AI-система автоматической генерации Node.js/Python кода на базе CrewAI + LangGraph + DeepSeek V4 Flash с изолированным исполнением в Docker.
> **Технологии:** Python 3.10+, CrewAI, LangGraph, LangChain, DeepSeek-V4-Flash (RouterAI), Docker SDK, RQ + Redis
> **Запуск:** `python chat.py` (интерактивный) или `python main.py` (пакетный)

---

## 📂 СТРУКТУРА ДИРЕКТОРИЙ

```
arch-code/
│
├── main.py                          # ★ Пакетный режим: CrewAI Agent + Task
├── chat.py                          # ★ РЕКОМЕНДУЕМЫЙ вход: интерактивный чат
├── graph_worker.py                  # ★ LangGraph: explore → execute → test (max 3 итерации)
├── docker_manager.py                # ★ Docker-песочница (NodeSandbox + ProjectSandbox)
├── worker.py                        # ★ Основной исполнитель: sync/async задачи
├── rq_worker.py                     #   RQ-воркер фоновых задач (Redis Queue)
│
├── tools/                           # Инструменты CrewAI + LangChain
│   ├── coding_tool.py               #   LangGraphCodingTool — обёртка графа в CrewAI Tool
│   ├── knowledge_reader.py          #   ReadKnowledgeTool — чтение knowledge/ агентом
│   ├── file_tools.py                #   Функции управления файлами в sandbox:
│   │                                 #   list_files, read_file, write_file, make_coding_tools()
│   └── test_generator.py            #   ★ TDD Agent — генерация pytest до написания кода
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
│   ├── task_*/                      #   Копии ai-core проекта (Python, с tests/)
│   └── ...                          #   Всего ~13 sandbox-директорий
│
├── tests/                           # ★ Тесты (12 файлов, ~147+ тестов)
│   ├── conftest.py                  #   Общие фикстуры (temp_sandbox, mock_env)
│   ├── test_chat_full.py            #   15 тестов — ChatArchitect
│   ├── test_coding_tool.py          #   10 тестов — LangGraphCodingTool
│   ├── test_docker_manager_mock.py  #   14 тестов — Docker SDK mocked
│   ├── test_docker_manager_pure.py  #   12 тестов — Pure functions
│   ├── test_file_tools.py           #   27 тестов — File operations
│   ├── test_graph_worker_nodes.py   #   18 тестов — LangGraph nodes
│   ├── test_graph_worker_pure.py    #   14 тестов — Pure functions
│   ├── test_knowledge_reader.py     #   8 тестов — ReadKnowledgeTool
│   ├── test_rq_worker.py            #   9 тестов — RQ worker
│   ├── test_worker_and_chat.py      #   14 тестов — Worker + Chat integration
│   └── test_worker_pure.py          #   6 тестов — Pure functions
│
├── logs/                            # Логи
│   ├── worker.log                   #   RQ worker — операции
│   ├── worker_error.log             #   RQ worker — ошибки
│   ├── worker_new.log               #   Обновлённый лог операций
│   └── worker_runtime.log           #   Runtime логи
│
├── htmlcov/                         # Coverage HTML отчёты (16 файлов)
│
├── .qwen/                           # Qwen skills (3 шт.)
│   └── skills/
│       ├── ai-agent-framework-decision/SKILL.md
│       ├── hybrid-ai-coder-system/SKILL.md
│       └── qwen-md-generation-workflow/SKILL.md
│
├── .github/                         # CI/CD
│   └── workflows/
│       └── tests.yml                #   GitHub Actions: pytest + Codecov
│
├── .env                             # API-ключи (gitignored)
├── requirements.txt                 # 10 зависимостей (crewai, langgraph, docker, rq, loguru)
├── requirements-test.txt            # Тестовые зависимости (pytest, fakeredis)
├── pyproject.toml                   # Coverage config (fail_under=50)
├── pytest.ini                       # asyncio_mode=auto
├── Makefile                         # test targets
├── arch-code-worker.service         # systemd unit для RQ worker
├── repomix-output.xml               # Полный дамп репозитория для AI
├── QWEN.md                          # Контекст для AI-ассистента
├── README.md
├── ARCH-CODE_PROJECT_MAP.md         # ★ Этот файл
├── =0.7.0 / =1.15.0                 # Бинарные файлы (версии)
├── nohup.out                        # Вывод фонового запуска
└── venv/                            # Виртуальное окружение Python
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
                    │  │ TestGen      │   │
                    │  └──────────────┘   │
                    └──────────┬───────────┘
                               │ LangGraphCodingTool
                    ┌──────────▼───────────┐
                    │     LangGraph        │
                    │  ┌─────────────────┐ │
                    │  │ explore_project │ │
                    │  └────────┬────────┘ │
                    │           │          │
                    │  ┌────────▼────────┐ │
                    │  │ execute_actions │─┐│
                    │  │ (max 25 tool    │ ││
                    │  │  calls, цикл    │ ││
                    │  │  до DONE)       │ ││
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
   │               ├── explore_project — изучение проекта (дерево)
   │               ├── execute_actions — чтение/запись файлов (bind_tools, цикл до DONE)
   │               └── test_code — запуск тестов в Docker (авто-детект Python/Node)
   └── Сохраняет результат в last_code

3. LangGraph: explore (bind_tools) → execute (max 25 tool calls) → test (Docker)
    └── До 3 итераций при ошибках
    └── Docker: pip install / npm install → pytest / node --test
```

---

## 🧩 КЛЮЧЕВЫЕ КОМПОНЕНТЫ

### `chat.py` — ChatArchitect (рекомендуемый вход)
| Метод | Назначение |
|-------|-----------|
| `__init__()` | LLM (DeepSeek-V4-Flash), tools (knowledge + coding + file), история |
| `_build_context()` | Сборка контекста: sandbox-файлы + история + код + user input |
| `_create_agent()` | Создание CrewAI Architect Agent |
| `_render_stream()` | Стриминг токенов (TEXT, TOOL_CALL) в реальном времени |
| `run()` | Основной entry: контекст → crew → kickoff → сохранение |
| `chat_loop()` | Интерактивный REPL: `/docs`, `/code`, `/sandbox`, `/clear`, `/exit` |
| Одноразовый режим | `python chat.py "task description"` — без цикла |

### `graph_worker.py` — LangGraph

| Узел (Node) | Тип | Описание |
|-------------|-----|----------|
| `explore_project` | LangGraph Node | Показывает AI дерево проекта |
| `execute_actions` | LangGraph Node | AI читает/пишет файлы (LangChain bind_tools, цикл до DONE, макс 25 tool calls) |
| `test_code` | LangGraph Node | Детект типа проекта → запуск тестов в Docker |
| `route_next_step` | Conditional Edge | END если success или ≥3 итераций, иначе execute_actions |

**AgentState (TypedDict):** 17 полей: `task_id`, `sandbox_dir`, `project_dir`, `task`, `code`, `test_code`, `test_passed`, `error`, `iterations`, `success`, `changed_files`, `thought_steps`, `action_steps`, `chain_of_thought`, `prompt_tokens`, `completion_tokens`, `model`

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
| | `_run_python_tests()` | pip install лишь pytest+pytest-timeout → pytest -x в python:3.12-slim |
| | `_run_node_tests()` | npm install → npm test в node:alpine |
| | `cleanup_containers()` | Остановка и удаление Docker контейнеров |

### `worker.py` — Исполнитель задач

| Функция | Описание |
|---------|----------|
| `sync_project_to_sandbox()` | Rsync проекта (ai-core по умолч.) в sandbox с exclude-паттернами |
| `compute_sandbox_diff()` | Git diff изменений в sandbox |
| `execute_coding_task_sync()` | ★ Основная: sync → git init → LangGraph → diff → валидация Python → результат |
| `execute_coding_task()` | async-обёртка для thread pool |
| Расчёт CU | `(prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000` |
| Graceful shutdown | SIGTERM handler, _cleanup_resources() |

### `rq_worker.py` — Фоновый воркер

| Функция | Описание |
|---------|----------|
| `start_worker()` | Redis → RQ Worker(queue=`coding_tasks`), stale-keys cleanup |
| `cleanup_orphaned_jobs()` | Чистка Docker + sandbox ресурсов |
| Exception handler | Fallback cleanup при сбоях |
| Retry | 3 попытки при коллизии имени воркера |

### `main.py` — Пакетный режим
- CrewAI: Architect Agent + hardcoded Task (Express.js webhook)
- `Process.sequential` — последовательное выполнение

### `tools/test_generator.py` — TDD Agent
- Генерация pytest-тестов ДО написания кода (RED → GREEN)
- LLM-генерация, валидация `ast.parse()`, graceful degradation (2 retries)

---

## 🧠 LLM-КОНФИГУРАЦИЯ

| Параметр | Значение |
|----------|----------|
| Провайдер | RouterAI (`api.routerai.ru/api/v1`) |
| Модель | `deepseek/deepseek-v4-flash` (все файлы) |
| Температура | По умолчанию LLM |
| Язык промптов | Русский (все agent/role/task инструкции) |
| API-ключ | `ROUTERAI_API_KEY` в `.env` |

---

## 🧪 ТЕСТЫ (12 файлов, ~147+ тестов)

| Файл | Тип | Кол-во |
|------|-----|--------|
| `test_file_tools.py` | Pure functions | 27 |
| `test_graph_worker_pure.py` | Pure functions | 14 |
| `test_docker_manager_pure.py` | Pure functions | 12 |
| `test_worker_pure.py` | Pure functions | 6 |
| `test_rq_worker.py` | Pure functions | 9 |
| `test_knowledge_reader.py` | Pure functions | 8 |
| `test_coding_tool.py` | Pure functions | 10 |
| `test_graph_worker_nodes.py` | Mocked (LangGraph) | 18 |
| `test_docker_manager_mock.py` | Mocked (Docker SDK) | 14 |
| `test_worker_and_chat.py` | Integration | 14 |
| `test_chat_full.py` | Integration | 15 |

> CI: `.github/workflows/tests.yml` — `pytest -m "not integration and not slow"` + Codecov

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
