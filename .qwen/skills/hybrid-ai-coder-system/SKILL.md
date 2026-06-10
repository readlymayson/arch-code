---
name: hybrid-ai-coder-system
description: "Архитектура гибридной системы генерации кода: CrewAI + LangGraph + DeepSeek + Docker-песочница"
source: auto-skill
extracted_at: '2026-06-10T15:25:47.385Z'
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
│   Docker-песочница (node:alpine)   │
│  Монтирует ./sandbox → /app      │
└──────────────────────────────────┘
```

## Известные проблемы и их решения

### CrewAI 1.x: LLM больше не принимает ChatOpenAI

CrewAI 1.x полностью независим от LangChain. Параметр `llm` агента больше не принимает `ChatOpenAI` из `langchain_openai`.

**Ошибка:**
```
pydantic_core._pydantic_core.ValidationError: 2 validation errors for Agent
llm.str
  Input should be a valid string
llm.BaseLLM
  Input should be a valid dictionary or instance of BaseLLM
```

**Решение — использовать `crewai.LLM`:**
```python
from crewai import LLM

llm = LLM(
    model="deepseek-chat",            # имя модели
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",  # обязательно /v1
)
```

**Или через переменные окружения:**
```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."
os.environ["OPENAI_API_BASE"] = "https://api.deepseek.com/v1"

from crewai import Agent
agent = Agent(..., llm="deepseek-chat")
```

**Важно:** 
- Для OpenAI-совместимых API укажите `base_url` с `/v1` на конце.
- LangGraph всё ещё использует `langchain_openai.ChatOpenAI` — это НЕ конфликтует, т.к. пакет `langchain-openai` остаётся установлен.

### Windows-кодировка (cp1251 vs UTF-8)

На Windows с русской локалью Python по умолчанию использует cp1251. Это ломает:
- Чтение файлов с кириллицей (`knowledge/*.md`)
- Вывод emoji (✅, ❌) в консоль
- Любые не-ASCII символы в stdout/stderr

**Ошибка:**
```
'charmap' codec can't decode byte 0x98 in position...
'charmap' codec can't encode character '\u2713'...
```

**Решения:**

1. **Всегда открывайте файлы с `encoding="utf-8"`:**
```python
with open(path, "r", encoding="utf-8") as f:
    ...
```

2. **Уберите emoji из возвращаемых строк** в инструментах CrewAI / LangGraph — они попадают в консоль через вывод агента:
```python
# ПЛОХО: return "✅ Success!"
# ХОРОШО: return "[OK] Success!"
```

3. **Добавьте `_sanitize()` в узлы LangGraph и инструменты CrewAI** — фильтр на границе LLM → система. Это самый надёжный способ, т.к. любая LLM может вернуть непредсказуемые Unicode-символы:

```python
def _sanitize(text: str) -> str:
    """Заменяет символы, не поддерживаемые cp1251, на '?'."""
    return text.encode("cp1251", errors="replace").decode("cp1251")
```

Применяйте к:
- `response.content` в узлах LangGraph (`generate_code`)
- Ошибкам (`result["output"]`) в узле `test_code`
- Возвращаемым строкам из `BaseTool._run()`

4. **Запускайте с `PYTHONIOENCODING=utf-8`:**
```bash
set PYTHONIOENCODING=utf-8 && python main.py
```

Проверено на Python 3.13 + Windows 10/11 + CrewAI 1.14.6.

### Python 3.14: crewai не устанавливается

На момент 2026 года `crewai` требует Python < 3.14. Если на системе Python 3.14, создавайте venv с Python 3.13:

```bash
py --list              # проверить доступные версии
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### Docker не может скачать образ (TLS timeout)

На корпоративных машинах Docker Desktop часто настроен через прокси, который может не работать.

**Диагностика:**
```bash
docker info | findstr /I "proxy mirror registry"
# Ищет: HTTP Proxy, HTTPS Proxy, Registry Mirrors
```

**Типичные причины:**
- Прокси `http.docker.internal:3128` настроен в Docker Desktop, но недоступен
- Реестр `docker.io` блокирован корпоративным файрволом
- Нет TLS-терминации на прокси

**Решения:**
- Проверить `Settings → Resources → Proxies` в Docker Desktop
- Указать реальный адрес корпоративного прокси
- Использовать внутренний mirror-реестр (если есть)
- Если Registry Mirror указан — проверить его доступность: `curl http://mirror:port/v2/_catalog`

### Docker не скачивает образы через TUN/VPN (Throne Tun, v2ray, Clash, Nekoray)

TUN-клиенты (Throne Tun, v2ray, Clash, Sing-box и др.) работают на уровне виртуального сетевого адаптера. Docker Desktop использует WSL 2, трафик которого не всегда автоматически проходит через TUN.

**Диагностика:**
```bash
docker info | findstr /I "proxy mirror registry"
# Если показывает "HTTP Proxy: http.docker.internal:3128" — Docker Desktop настроен на прокси
```

**Решение — добавить процессы WSL и Docker в маршрутизацию TUN-клиента:**

```
processName:com.docker.backend.exe
processName:com.docker.service.exe
processName:com.docker.supervisor.exe
processName:docker.exe
processName:docker-buildx.exe
processName:docker-compose.exe
processName:vmms.exe
processName:vmwp.exe
processName:wsl.exe
processName:wslhost.exe
processName:wsltray.exe
```

Главный канал трафика — **wslhost.exe** (через него идёт весь сетевой обмен WSL).

**Дополнительно — настроить `%USERPROFILE%\.wslconfig` для mirrored networking:**
```
[wsl2]
networkingMode=mirrored
```
Это заставляет WSL 2 наследовать сетевой стек Windows, включая TUN-адаптер. После изменений — перезапустить WSL: `wsl --shutdown`.

---

## Компоненты

### 1. Docker-менеджер (`docker_manager.py`)
- Запускает `node:alpine` контейнер с монтированием локальной `sandbox/` папки (можно переключиться на конкретную версию: `node:18-alpine`, `node:20-alpine`)
- Два метода: `execute_code` (просто `node script.js`) и `execute_test` (через `node --test`)
- Контейнер удаляется после выполнения (`remove=True`)
- Ошибки выполнения ловятся через `docker.errors.ContainerError`

### 1a. Установка npm-зависимостей в Docker

Генерируемый код часто использует внешние пакеты (express и др.), которых нет в `node:alpine`. Без их установки `require('express')` упадёт с `Error: Cannot find module 'express'`, что сломает LangGraph-цикл.

**Решение — установка npm-пакетов перед запуском кода:**

```python
import docker
import json
import os

class NodeSandbox:
    # Распространённые npm-зависимости для генерируемого кода
    COMMON_DEPS = {"express": "^4.18.2"}

    def _ensure_package_json(self):
        """Создаёт package.json в sandbox/, если его ещё нет."""
        pkg_path = os.path.join(self.sandbox_dir, "package.json")
        if not os.path.exists(pkg_path):
            pkg = {
                "name": "sandbox-app",
                "version": "1.0.0",
                "private": True,
                "dependencies": dict(self.COMMON_DEPS),
            }
            with open(pkg_path, "w") as f:
                json.dump(pkg, f, indent=2)

    def _run_in_container(self, command: str) -> dict:
        """npm install → выполнение команды."""
        self._ensure_package_json()

        # Установка зависимостей
        try:
            self.client.containers.run(
                "node:alpine",
                command="npm install",
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
            )
        except docker.errors.ContainerError:
            pass  # npm install может выдавать warnings

        # Запуск команды
        try:
            logs = self.client.containers.run(
                "node:alpine",
                command=command,
                volumes={self.sandbox_dir: {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                remove=True,
                stderr=True, stdout=True,
            )
            return {"status": "success", "output": logs.decode("utf-8")}
        except docker.errors.ContainerError as e:
            return {"status": "error", "output": e.stderr.decode("utf-8")}
```

**Важно:**
- `npm install` выполняется **каждый раз** перед запуском кода — это гарантирует, что зависимости актуальны, но замедляет выполнение (~5-15 сек).
- `package.json` создаётся с common-зависимостями. Если LLM генерирует код с другими пакетами — их нужно добавить в `COMMON_DEPS` или передавать через `task_description`.
- `sandbox/package-lock.json` и `sandbox/node_modules/` нужно добавить в `.gitignore`.
- `os.makedirs(os.path.dirname(file_path), exist_ok=True)` в `execute_code/execute_test` — чтобы поддерживать вложенные пути (`controllers/webhook.controller.js`).

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

## Вариант архитектуры: Multi-agent (CrewAI без LangGraph)

Если требуется больше контроля и ролевая модель (отдельные агенты для кода и тестов), CrewAI может полностью заменить LangGraph-цикл.

### Роли

| Роль | LLM | Инструменты | Ответственность |
|---|---|---|---|
| **Architect** | DeepSeek-V4-Pro | `ReadKnowledgeTool` | Читает документацию, пишет детальное ТЗ |
| **Coder** | DeepSeek-V4-Flash | `CodeWriter` (пишет файл в sandbox/) | Генерирует JS-код по ТЗ |
| **Tester** | DeepSeek-V4-Flash | `NodeTestRunner` (Docker), `ReadKnowledgeTool` | Пишет тесты, запускает в Docker |

### Retry-цикл

Используется `Process.hierarchical` — Manager-agent решает:
- тест пройден → финал
- тест не пройден → вернуть Coder'у с ошибкой (до 3 попыток)

### Когда выбирать эту архитектуру

- Нужен доступ агентов к инструментам CrewAI (чтение доков, запись файлов)
- Архитектор должен видеть промежуточные результаты
- В будущем появятся дополнительные роли (Reviewer, Documenter)

### Когда оставить CrewAI + LangGraph (текущую)

- Достаточно одного Architect, а Coder/Tester — просто узлы конвейера
- Важна скорость — LangGraph легковеснее, меньше токенов на оверхед
- Кодовая база стабильна и не требует multi-agent расширения

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
