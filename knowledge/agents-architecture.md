# Архитектура ролей CrewAI — план развития

## Статус

Черновик. Предложен 2026-06-10 по результатам анализа проекта и исследования CrewAI vs LangGraph.

---

## 1. Текущее состояние

Сейчас в CrewAI **один агент** — `Architect`. Код и тесты генерируются внутри `LangGraphCodingTool` (узлы LangGraph-графа, не агенты CrewAI).

```
CrewAI:
  └─ Architect (DeepSeek-V4-Pro)
       └─ LangGraphCodingTool (инструмент)
            ├─ [LangGraph] generate (V4-Flash)
            ├─ [LangGraph] test (Docker)
            └─ [LangGraph] conditional retry (max 3)
```

**Проблемы текущей архитектуры:**
- Coder и Tester — не агенты, у них нет доступа к инструментам CrewAI (не могут читать `knowledge/`).
- Весь цикл — чёрный ящик для Architect; он не видит промежуточные результаты.
- LangGraph-цикл нельзя переиспользовать в других сценариях.

---

## 2. Целевая архитектура (Вариант Б — рекомендованный)

Три CrewAI-агента, sequential process, инструменты для каждого.

### 2.1 Роли

| № | Роль | LLM | Инструменты | Ответственность |
|---|---|---|---|---|
| 1 | **Architect** | DeepSeek-V4-Pro | `ReadProjectDocs` | Читает `knowledge/`, анализирует требования, пишет детальное ТЗ |
| 2 | **Coder** | DeepSeek-V4-Flash | `CodeWriter`\* | Пишет JS-код строго по ТЗ, соблюдает `style-guide.md` |
| 3 | **Tester** | DeepSeek-V4-Flash | `NodeTestRunner`\*, `ReadProjectDocs` | Пишет тесты (сверяясь с `api-contracts.md`), запускает в Docker |

\* — новые инструменты, требуется реализация.

### 2.2 Поток выполнения

```
Architect ──(ТЗ)──→ Coder ──(код)──→ Tester ──(успех)──→ END
                              ↑              │
                              └──(ошибка)─────┘
                                    (retry)
```

Процесс: **`Process.hierarchical`** — Manager-agent решает, прошёл ли тест, и отправляет код обратно Coder'у при ошибке.

### 2.3 Параметры retry

- Максимум итераций (code → test → fix): **3** (как сейчас в LangGraph).
- Если тест не пройден за 3 попытки — финальный ответ с последней версией кода и ошибкой.

---

## 3. Дополнительные роли (опционально, для следующих итераций)

| № | Роль | LLM | Когда подключается | Зачем |
|---|---|---|---|---|
| 4 | **Reviewer** | DeepSeek-V4-Pro | После Tester, перед финалом | Code review: style-guide compliance, security, duplications. Может отправить на доработку. |
| 5 | **Documenter** | DeepSeek-V4-Flash | После Reviewer | Генерация README, JSDoc, swagger-спецификации |
| 6 | **PM (Product Manager)** | DeepSeek-V4-Pro | Перед Architect (для сложных задач) | Декомпозиция задач, распределение между несколькими Coder'ами |

---

## 4. Новые инструменты (требуется имплементация)

### `CodeWriter`
- Записывает сгенерированный код в файл внутри `sandbox/`.
- Принимает: `filename`, `code`, `task_description`.
- Возвращает: путь к файлу.

### `NodeTestRunner`
- Обёртка над `NodeSandbox.execute_test()`.
- Принимает: `code`, `test_code`.
- Возвращает: `{"passed": bool, "output": str}`.
- Coder может вызвать его сам для быстрой проверки перед отправкой Tester'у.

---

## 5. Что останется от текущей архитектуры

| Компонент | Судьба |
|---|---|
| `docker_manager.py` | Останется без изменений. |
| `graph_worker.py` | Можно удалить (цикл переезжает в CrewAI). Либо оставить как вспомогательный инструмент для Coder'а (Вариант В). |
| `tools/coding_tool.py` | Можно удалить (заменяется на `CodeWriter` + `NodeTestRunner`). |
| `tools/knowledge_reader.py` | Останется, будет использоваться Architect'ом и Tester'ом. |
| `knowledge/` | Без изменений. |

---

## 6. План реализации (очередность)

1. Создать `tools/code_writer.py` — инструмент записи кода в sandbox.
2. Создать `tools/node_test_runner.py` — инструмент Docker-тестирования.
3. Переписать `main.py` — добавить Coder и Tester агентов, настроить `Process.hierarchical`.
4. Удалить `graph_worker.py` и `tools/coding_tool.py` (если не нужны как fallback).
5. Обновить `QWEN.md`.
6. Протестировать end-to-end: запустить `main.py` с тестовой задачей.

---

## 7. Принятые решения

- **CrewAI + LangGraph вместе** — нецелесообразно. Либо всё на CrewAI (роли + sequential/hierarchical), либо всё на LangGraph. CrewAI для ролевой модели и делегирования, LangGraph — избыточен при трёх агентах.
- **Process.hierarchical** предпочтительнее кастомного retry через callback — меньше кода, встроенная логика менеджера.
- **Все три роли на разных LLM:** Architect — Pro (качество), Coder и Tester — Flash (скорость и стоимость).
