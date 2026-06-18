# arch-code 🤖

Гибридная система автоматической генерации Node.js кода на базе **CrewAI** + **LangGraph** + **DeepSeek V4** с изолированным исполнением в **Docker**.

---

## Содержание

- [Архитектура](#архитектура)
- [Предварительные требования](#предварительные-требования)
- [Установка](#установка)
- [Настройка (.env)](#настройка-env)
- [Как использовать](#как-использовать)
- [Понимание пайплайна](#понимание-пайплайна)
- [Работа с Docker-песочницей](#работа-с-docker-песочницей)
- [Примеры задач](#примеры-задач)
- [Структура проекта](#структура-проекта)
- [Расширение и кастомизация](#расширение-и-кастомизация)
- [Устранение проблем](#устранение-проблем)

---

## Архитектура

```
┌──────────────────────────────────────────────────┐
│                   CrewAI                         │
│  ┌──────────────────────────────────────────┐    │
│  │   Architect Agent (DeepSeek-V4-Flash)    │    │
│  │   • читает документацию из knowledge/    │    │
│  │   • пишет ТЗ + опциональные тесты        │    │
│  │   • вызывает LangGraphCodingTool         │    │
│  └──────────────┬───────────────────────────┘    │
│                 │                                 │
│  ┌──────────────▼───────────────────────────┐    │
│  │   LangGraphCodingTool (CrewAI Tool)      │    │
│  └──────────────┬───────────────────────────┘    │
└─────────────────┼────────────────────────────────┘
                  │
┌─────────────────▼────────────────────────────────┐
│               LangGraph                          │
│  ┌──────────┐     ┌──────────┐                   │
│  │ generate │────►│   test   │──┐                │
│  └──────────┘     └──────────┘  │ (ошибка)       │
│       ▲                        │                │
│       └────────────────────────┘                │
│   (max 3 итерации, DeepSeek-V4-Flash)           │
└─────────────────┬────────────────────────────────┘
                  │
┌─────────────────▼────────────────────────────────┐
│         Docker (node:alpine)                     │
│   sandbox/ монтируется в /app                    │
│   npm install → node app.js / node --test        │
└──────────────────────────────────────────────────┘
```

---

## Предварительные требования

| Компонент | Версия | Проверка |
|---|---|---|
| **Python** | ≥ 3.10, < 3.14 | `python --version` |
| **Docker** | ≥ 24 | `docker info` |
| **API-ключ DeepSeek** | — | [platform.deepseek.com](https://platform.deepseek.com) |

> ⚠️ **Важно для Windows:** Если установлен Python 3.14, создавайте виртуальное окружение с Python 3.13:
> ```bash
> py --list
> py -3.13 -m venv .venv
> ```

---

## Установка

```bash
# 1. Клонировать репозиторий
git clone https://github.com/readlymayson/arch-code.git
cd arch-code

# 2. Создать виртуальное окружение (рекомендуется)
python -m venv .venv

# 3. Активировать
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (CMD):
.venv\Scripts\activate.bat
# Linux/macOS:
source .venv/bin/activate

# 4. Установить зависимости
pip install -r requirements.txt

# 5. Создать файл .env (см. ниже)
```

---

## Настройка (.env)

Создайте файл `.env` в корне проекта:

```env
DEEPSEEK_API_KEY=sk-ваш_ключ_здесь
```

Где взять ключ: [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)

> 💡 Для других OpenAI-совместимых API достаточно изменить `base_url` и `model` в `main.py` и `graph_worker.py`.

---

## Как использовать

### 0. 🎙️ Чат с AI-архитектором (рекомендовано)

Самый быстрый способ — просто описать задачу на естественном языке:

```bash
python chat.py
```

Откроется интерактивный чат. Просто пишите, что нужно сделать:

```
💬 > напиши Express сервер с GET /health и POST /data
```

**Multi-turn:** можно уточнять и дополнять задачу в следующих сообщениях —
архитектор помнит контекст:

```
💬 > добавь CORS и body-parser
💬 > теперь добавь логирование через middleware
```

**Streaming:** ответ архитектора выводится токен за токеном — вы видите,
как он думает, в реальном времени.

Доступные команды в чате:

| Команда | Действие |
|---|---|
| `/exit` | Выйти |
| `/help` | Показать подсказку |
| `/docs` | Прочитать файлы из `knowledge/` |
| `/code` | Показать последний сгенерированный код |
| `/sandbox` | Показать файлы в папке `sandbox/` с размерами |
| `/clear` | Очистить экран |

**Однострочный режим** (без интерактива):

```bash
python chat.py "напиши Express сервер с health endpoint"
```

### 1. Пакетный режим (с задачей по умолчанию)

```bash
python main.py
```

По умолчанию система сгенерирует **webhook-эндпоинт на Express.js**. Результат появится в папке `sandbox/`.

### 2. Запуск со своей задачей

Откройте `main.py` и найдите блок задачи:

```python
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
```

**Измените `description` на свою задачу.** Например:

```python
task = Task(
    description=(
        "Создать REST API для управления задачами (TODO list) на Express.js. "
        "Эндпоинты: GET /tasks, POST /tasks, DELETE /tasks/:id. "
        "Хранить данные в оперативной памяти (массив). "
        "Сверься со style-guide.md для оформления."
    ),
    expected_output="Файл server.js с рабочим REST API.",
    agent=architect,
)
```

### 3. Запуск с собственным тестом

Если хотите, чтобы система не только сгенерировала код, но и запустила конкретный тест — передайте `test_code` в инструмент. Для этого нужно модифицировать `coding_tool.py` или изменить логику в `main.py`.

Сейчас `LangGraphCodingTool` принимает опциональный параметр `test_code` — он будет передан в LangGraph-цикл. Если тест не задан, система просто проверит, что код не падает с ошибкой (`node app.js`).

### 4. Пошаговый процесс выполнения

```
Шаг 1: Architect читает style-guide.md и api-contracts.md
       ↓
Шаг 2: Architect пишет детальное ТЗ для кодера
       ↓
Шаг 3: Запускается LangGraph-цикл:
       └─ generate  →  тест в Docker  →  ошибка? → generate (до 3 раз)
                                        → успех?  → FINISH
       ↓
Шаг 4: Результат — сгенерированный .js файл в sandbox/
```

---

## Понимание пайплайна

### Роль Architect (CrewAI)

Architect — это CrewAI-агент с двумя инструментами:

1. **ReadKnowledgeTool** — читает файлы из папки `knowledge/`:
   - `style-guide.md` — правила оформления кода
   - `api-contracts.md` — спецификации API
   - `agents-architecture.md` — планы развития ролей

2. **LangGraphCodingTool** — запускает LangGraph-цикл кодинга

### Роль LangGraph-цикла (graph_worker.py)

LangGraph управляет итеративным процессом генерации кода:

| Компонент | Что делает |
|---|---|
| **Узел `generate`** | Отправляет задачу + историю ошибок в DeepSeek-V4-Flash, очищает ответ |
| **Узел `test`** | Записывает код в `sandbox/`, запускает в Docker, проверяет результат |
| **Маршрутизатор** | Если тест пройден → END. Если итераций ≥ 3 → END. Иначе → `generate` |

Состояние графа (`AgentState`):

```python
{
    "task": str,          # Описание задачи
    "code": str,          # Сгенерированный JS-код
    "test_code": str,     # Опциональный тест
    "test_passed": bool,  # Флаг прохождения
    "error": str,         # Последняя ошибка
    "iterations": int,    # Счётчик (max 3)
    "success": bool       # Финальный статус
}
```

---

## Работа с Docker-песочницей

Docker-песочница (`NodeSandbox`) обеспечивает изолированное выполнение кода:

```python
from docker_manager import NodeSandbox

sandbox = NodeSandbox()

# Выполнить код (проверка синтаксиса и импортов)
result = sandbox.execute_code("server.js", "console.log('hello')")
print(result["status"])  # success | error
print(result["output"])  # stdout/stderr

# Запустить тест
result = sandbox.execute_test("test.js", "// node --test код")
```

**Как это работает:**
1. Создаётся/обновляется `package.json` в `sandbox/` со стандартными зависимостями (express)
2. Запускается `npm install` внутри контейнера `node:alpine`
3. Выполняется команда (`node /app/file.js` или `node --test /app/file.js`)
4. Контейнер удаляется после выполнения

---

## Примеры задач

### Пример 1: Простой HTTP-сервер

```python
task = Task(
    description=(
        "Напиши HTTP-сервер на Express.js, который слушает порт 3000 "
        "и отвечает 'Hello World' на GET /."
    ),
    expected_output="Файл app.js с рабочим сервером.",
    agent=architect,
)
```

### Пример 2: API с MongoDB

```python
task = Task(
    description=(
        "Реализовать CRUD API для коллекции 'messages' на Express.js. "
        "Использовать MongoDB (mongoose). Модель: {text: String, createdAt: Date}. "
        "Прочитай api-contracts.md для понимания схемы."
    ),
    expected_output="Файл server.js с полным CRUD.",
    agent=architect,
)
```

### Пример 3: Утилита командной строки

```python
task = Task(
    description=(
        "Напиши Node.js скрипт, который читает файл data.json из той же директории, "
        "сортирует массив по полю 'name' и выводит в консоль."
    ),
    expected_output="Файл sort.js.",
    agent=architect,
)
```

---

## Структура проекта

```
arch-code/
├── main.py                      # Точка входа — CrewAI оркестратор
├── chat.py                      # Multi-turn чат с AI-архитектором
├── graph_worker.py              # LangGraph-цикл (generate → test → fix)
├── docker_manager.py            # Docker-песочница для Node.js
├── requirements.txt             # Python-зависимости
├── .env                         # API-ключ DeepSeek (создать вручную)
├── .gitignore                   # Игнорируемые файлы
│
├── .qwen/                       # Контекст для AI-ассистента Qwen
│
├── tools/
│   ├── coding_tool.py           # LangGraphCodingTool — обёртка графа
│   └── knowledge_reader.py      # ReadKnowledgeTool — чтение knowledge/
│
├── knowledge/
│   ├── style-guide.md           # Стайлгайд для генерируемого кода
│   ├── api-contracts.md         # API-контракты
│   └── agents-architecture.md   # План развития ролей
│
├── sandbox/                     # Монтируется в Docker-контейнер
│   ├── package.json             # Автосоздаётся при запуске
│   ├── *.js                     # Сгенерированные файлы
│   └── node_modules/            # Устанавливаются в Docker при запуске
│
├── README.md                    # Этот файл
└── QWEN.md                      # Контекст для AI-ассистента (устаревший)
```

---

## Расширение и кастомизация

### Смена LLM-провайдера

Отредактируйте `main.py` и `graph_worker.py`:

```python
# main.py — для архитектора
architect_llm = LLM(
    model="gpt-4o",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.openai.com/v1",
)

# graph_worker.py — для кодера
flash_llm = ChatOpenAI(
    model="gpt-4o-mini",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.openai.com/v1",
)
```

### Добавление новых npm-зависимостей

В `docker_manager.py` дополните словарь `COMMON_DEPS`:

```python
class NodeSandbox:
    COMMON_DEPS = {
        "express": "^4.18.2",
        "mongoose": "^8.0.0",       # добавлено
        "axios": "^1.7.0",          # добавлено
        "lodash": "^4.17.21",       # добавлено
    }
```

### Добавление нового инструмента CrewAI

Создайте файл `tools/my_tool.py`:

```python
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

class MyInput(BaseModel):
    param: str = Field(..., description="Описание параметра")

class MyTool(BaseTool):
    name: str = "MyTool"
    description: str = "Что делает инструмент"
    args_schema: type[BaseModel] = MyInput

    def _run(self, param: str) -> str:
        # Ваша логика
        return f"Результат: {param}"
```

Затем добавьте его агенту в `main.py`:

```python
from tools.my_tool import MyTool

architect = Agent(
    ...,
    tools=[knowledge_tool, coding_tool, MyTool()],
)
```

### Изменение максимального числа итераций

В `graph_worker.py` найдите условие маршрутизации и измените лимит:

```python
MAX_ITERATIONS = 5  # было 3

def should_continue(state: AgentState):
    if state["success"] or state["iterations"] >= MAX_ITERATIONS:
        return "end"
    return "continue"
```

---

## Устранение проблем

### Ошибка: `GitHub login failed` / Copilot не работает

Проблема: сессия GitHub в VS Code истекла или не настроена.

**Решение:** В VS Code откройте Accounts (шестерёнка в левом нижнем углу) → Sign in with GitHub. Copilot Chat не влияет на работу arch-code — он использует DeepSeek API напрямую.

### Ошибка: `ValidationError` — `llm` должен быть строкой

**Причина:** CrewAI 1.x не принимает `ChatOpenAI` из LangChain.

**Решение:** Используйте `crewai.LLM` (как уже настроено в проекте):

```python
from crewai import LLM

llm = LLM(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
)
```

### Ошибка: `'charmap' codec can't encode/decode` на Windows

**Причина:** Русская локаль Windows (cp1251) несовместима с UTF-8 выводом.

**Решение:** Запустите с явной UTF-8 кодировкой:

```bash
set PYTHONIOENCODING=utf-8 && python main.py
```

### Ошибка: Docker не может скачать образ

**Диагностика:**
```bash
docker info | findstr /I "proxy mirror registry"
```

**Возможные причины:**
- Docker Desktop настроен на прокси, который недоступен
- Корпоративный файрвол блокирует docker.io
- TUN-клиент (Throne Tun, Clash, Nekoray) не маршрутизирует WSL трафик

**Решение:** Добавьте в настройки TUN-клиента процесс `wslhost.exe` для маршрутизации.

### Ошибка: В sandbox/ пусто после запуска

**Причина:** `sandbox/` добавлена в `.gitignore`. Это нормально — сгенерированные файлы не должны попадать в Git.

**Проверить результат:**
```bash
ls sandbox/
# или
docker run --rm -v %cd%/sandbox:/app -w /app node:alpine node app.js
```

---

## Модели

| Роль | Модель | Назначение |
|---|---|---|
| **Архитектор** | `deepseek-chat` | Чтение документации, составление ТЗ |
| **Кодер (в LangGraph)** | `deepseek-chat` | Генерация и исправление кода |

Обе роли используют **DeepSeek-V4-Flash** — оптимальный баланс скорости и качества.
