# my-ai-coder — Гибридная система генерации кода

## Project Overview

Гибридная AI-система автоматической генерации Node.js кода.  
Оркестрация через **CrewAI**, цикл генерации/тестирования — **LangGraph**,  
исполнение — **Docker-песочница (node:18-alpine)**, генерация — **DeepSeek V4**.

### Архитектура (кратко)

```
CrewAI (Architect Agent, DeepSeek-V4-Pro)
  └─ LangGraphCodingTool (CrewAI Tool)
       └─ LangGraph (generate → test → (max 3 iters) → END)
            └─ Docker Sandbox (node:18-alpine)
```

- **Architect Agent** — читает доки из `knowledge/`, пишет ТЗ, передаёт кодерам.
- **LangGraph-цикл** — генерирует код, запускает в Docker, при ошибке исправляет (до 3 попыток).
- **Docker Sandbox** — изолированное исполнение Node.js (18-alpine).

### Используемые технологии

| Компонент | Технология |
|---|---|
| Оркестратор агентов | CrewAI |
| Граф состояний | LangGraph + LangChain |
| LLM | DeepSeek-V4-Pro (архитектор), DeepSeek-V4-Flash (кодер) |
| Песочница | Docker SDK для Python, образ `node:18-alpine` |
| Валидация | `crewai>=0.30.0`, `langgraph>=0.0.50`, `docker>=7.0.0` |

### Модели

- **Архитектор** (`deepseek-v4-pro`) — медленнее, умнее; пишет ТЗ, декомпозирует задачи.
- **Кодер** (`deepseek-v4-flash`) — быстрее; генерирует/исправляет код внутри LangGraph-цикла.

---

## Структура проекта

```
arch-code/
├── main.py                  # Точка входа — запуск CrewAI пайплайна
├── graph_worker.py          # LangGraph-граф: генерация → тест → исправление
├── docker_manager.py        # Класс NodeSandbox — Docker-песочница для Node.js
├── tools/
│   ├── coding_tool.py       # LangGraphCodingTool — обёртка графа в инструмент CrewAI
│   └── knowledge_reader.py  # ReadKnowledgeTool — чтение доков агентами
├── knowledge/
│   ├── style-guide.md           # Правила оформления Node.js кода
│   ├── api-contracts.md         # API-контракты (входящий webhook, MongoDB)
│   └── agents-architecture.md   # План развития ролей CrewAI
├── sandbox/                 # Генерируемая; монтируется в Docker-контейнер
├── requirements.txt         # Python-зависимости
├── README.md                # Полное описание проекта
└── QWEN.md                  # Контекст для ассистента
```

---

## Building and Running

### Установка

```bash
pip install -r requirements.txt
```

### Запуск

```bash
python main.py
```

### Требования

- Python 3.10+
- Docker (доступен через `docker info`)
- API-ключ DeepSeek в `.env`: `DEEPSEEK_API_KEY=sk-...`

### Тестирование

Проект не содержит собственных юнит-тестов (AI-агенты пишут тесты для генерируемого кода и выполняют их в Docker).  
Для ручной проверки после изменений:

```bash
# Включить sandbox-директорию в .gitignore, затем:
python -c "from docker_manager import NodeSandbox; ns = NodeSandbox(); print(ns.execute_code('test.js', 'console.log(\"ok\")'))"
```

---

## Ключевые файлы и их устройство

### `main.py` — CrewAI оркестратор

Создаёт одного **Architect Agent**, даёт ему инструменты `ReadProjectDocs` и `LangGraphCodingTool`, после чего запускает `Crew.kickoff()`.

**Важно:** Описание задачи (объект `Task`) нужно менять под конкретный кейс — проект не содержит готовой логики, это каркас.

### `graph_worker.py` — LangGraph-цикл

Состояние графа (`AgentState`):

| Поле | Назначение |
|---|---|
| `task` | Описание задачи от Архитектора |
| `code` | Сгенерированный JS-код |
| `test_code` | Опциональный тестовый скрипт |
| `test_passed` | Флаг прохождения теста |
| `error` | Текст последней ошибки |
| `iterations` | Счётчик попыток (max 3) |
| `success` | Финальный успех |

**Узлы:**
1. `generate` — генерирует/исправляет код через DeepSeek-V4-Flash. При ошибке прикрепляет её в промпт.
2. `test` — запускает код или тест в Docker. Если тест не задан — просто выполняет код (проверка на crash).

**Маршрутизация:** если `success == True` → END; если `iterations >= 3` → END; иначе → `generate`.

### `docker_manager.py` — Docker sandbox

- `NodeSandbox.execute_code(filename, code)` — записывает файл в `sandbox/`, запускает `node /app/<filename>` в `node:18-alpine`.
- `NodeSandbox.execute_test(filename, code)` — то же, но через `node --test <filename>` (Node 18+).

### `tools/coding_tool.py` — CrewAI Tool

Обёртка LangGraph-графа в `BaseTool` CrewAI. Параметры:
- `task_description: str` — обязательное ТЗ.
- `test_code: str | None` — опциональный тестовый скрипт на Node.js.

Возвращает результат выполнения (успех/неудача + финальный код).

### `tools/knowledge_reader.py` — CrewAI Tool

Читает файлы из `knowledge/`. Принимает имя файла (например, `"style-guide.md"`).  
Ищет относительно `CWD` и относительно `tools/`.

### `knowledge/style-guide.md`

Правила для генерируемого кода:
- `camelCase`, `UPPER_SNAKE_CASE`, `kebab-case.js`.
- `async`-обработчики с `next(err)`.
- Структура контроллеров, порядок импортов.

### `knowledge/api-contracts.md`

API-контракты для webhook (Twilio-совместимый) и схемы MongoDB (`messages`).

---

## Development Conventions

### Код проекта (Python)

- Python-файлы используют стандартные соглашения: snake_case, type hints.
- Классы: `NodeSandbox`, `LangGraphCodingTool`.
- Все зависимости вынесены в `requirements.txt`.
- Конфигурация через `.env` (DeepSeek API key).

### Генерируемый код (Node.js)

- Стиль задаётся `knowledge/style-guide.md` — AI-агент **обязан** прочитать этот файл перед генерацией.
- Контроллеры: `async/await` + `try/catch` + `next(err)`.
- Имена: `camelCase`, `kebab-case.js`, `UPPER_SNAKE_CASE`.
- Тесты: встроенный `node:test` (Node 18+), запускаются в Docker через `node --test`.

### Процесс разработки через систему

1. Архитектор читает `knowledge/style-guide.md` и `knowledge/api-contracts.md`.
2. Пользователь правит `Task.description` в `main.py`.
3. Запуск `python main.py`.
4. Архитектор пишет ТЗ + опциональный тест → передаёт в `LangGraphCodingTool`.
5. LangGraph-цикл генерирует, тестирует, при ошибке исправляет (до 3 раз).
6. Результат — `.js`-файл, прошедший проверку в Docker.

### Известные ограничения

- DeepSeek API (base_url и модель) жёстко зашиты в `main.py` и `graph_worker.py` — смена провайдера требует правок в обоих файлах.
- Docker-контейнер удаляется после каждого запуска (`remove=True`).
- Код генерируется в один файл — нет поддержки multi-file проектов.
- Все Node.js зависимости должны либо быть в образе `node:18-alpine`, либо не использоваться.
