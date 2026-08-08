# Python API Contracts (arch-code)

> Контракты API для Python-проектов (ai-core и аналогичные).
> Для Node.js — см. `api-contracts.md`.

## Общие требования

- Эндпоинты возвращают **JSON** (если не указано иное).
- Обязательные поля всегда присутствуют; опциональные — помечены.
- Ошибки: `{"error": {"code": str, "message": str}}` с соответствующим HTTP-статусом.
- Успех: `{"data": ...}` с HTTP 2xx.

## Типовые эндпоинты

### GET /health

Проверка живости сервиса. Должен быть в каждом веб-приложении.

```json
200 OK
{"status": "ok", "service": "ai-core", "version": "1.0.0"}
```

### POST /api/v1/process

Приём данных на обработку.

```json
// Request
{"data": {"text": "string"}, "options": {"verbose": false}}

// Response 200
{"data": {"task_id": "uuid", "status": "queued"}}

// Response 422
{"error": {"code": "validation_error", "message": "Описание ошибки"}}
```

## Соглашения FastAPI

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="My Service")

class ProcessRequest(BaseModel):
    text: str
    verbose: bool = False

@app.get("/health")
def health():
    return {"status": "ok", "service": "my-service"}

@app.post("/api/v1/process")
def process(req: ProcessRequest):
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="text is empty")
    return {"data": {"status": "ok"}}
```

- Используй Pydantic-модели для валидации входа.
- `@app.get`/`@app.post` — декларативные роуты.

## Соглашения Flask

```python
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/v1/process", methods=["POST"])
def process():
    data = request.get_json(force=True)
    ...
    return jsonify({"data": {...}})
```

## Ограничения

- НЕ используй внешние API/БД в smoke-тестах (run_app работает без сети).
- Приложение должно стартовать с `uvicorn main:app --host 0.0.0.0 --port 8000`.
- Health-check: `curl -sf http://127.0.0.1:8000/health` должен вернуть 2xx.
