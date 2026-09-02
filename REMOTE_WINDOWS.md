# Перенос arch-code воркера на Windows (2026-09-01)

## Архитектура после переноса

```
VPS (Linux, 217.12.38.121)          Windows (локальный ПК / сервер)
┌──────────────────────────┐        ┌───────────────────────────┐
│ ai-core (бот, оркестратор)│        │ arch-code worker (RQ)     │
│ Redis :6379 (только      │        │ Playwright-скрейпер бирж   │
│  127.0.0.1 — безопасно)  │◄──────►│                           │
│ Watchdog (remote-режим)  │  SSH   │ REDIS_URL через туннель   │
│ OrderExecutor → RQ       │  тунель│                           │
└──────────────────────────┘        └───────────────────────────┘
```

## 1. Подключение к Redis через SSH-туннель

Redis на VPS слушает **только** `127.0.0.1:6379` (firewall не нужен).

На Windows-хосте (в PowerShell / CMD):

```powershell
ssh -N -L 6379:127.0.0.1:6379 dev@217.12.38.121
```

Держать окно открытым. Проверка (другое окно):

```powershell
redis-cli -h 127.0.0.1 ping   # → PONG
```

### Автоподдержание туннеля (рекомендуется)

**Вариант A — `autossh` (Git Bash / WSL):**

```bash
autossh -M 0 -N -L 6379:127.0.0.1:6379 dev@217.12.38.121
```

**Вариант B — Планировщик задач Windows:**

1. Сохранить команду в `.bat`:
   ```bat
   @echo off
   ssh -N -L 6379:127.0.0.1:6379 dev@217.12.38.121
   ```
2. Планировщик → «При входе в систему» → запуск скрипта, «Перезапускать при сбое».

**Вариант C — PuTTY:**

- Session: host `217.12.38.121`, user `dev`
- Connection → SSH → Tunnels: Source port `6379`, Destination `127.0.0.1:6379`, Local, Auto
- Save session → Open

## 2. Переменные окружения воркера

```
REDIS_URL=redis://127.0.0.1:6379/0     # личный инстанс (db 0)
# REDIS_URL=redis://127.0.0.1:6379/1  # публичный (db 1), если нужен
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
```

> ВАЖНО: db 0 — личный инстанс AI Core (очереди `high_priority` + `coding_tasks`).
> db 1 — публичный (только `coding_tasks`).

## 3. Запуск воркера на Windows

```powershell
cd C:\path\to\arch-code
python rq_worker.py
```

Или через RQ CLI:

```powershell
rq worker coding_tasks --url redis://127.0.0.1:6379/0
```

## 4. Playwright-скрейпер бирж (Windows)

Скрейпер Kwork/FL.ru (SPA-биржи) тоже переезжает на Windows, т.к. нужен
Chromium. Собранные заказы он кладёт в Redis **LIST** `freelance:remote:orders`
(JSON-элементы) — ai-core на VPS читает их через `ExchangePlaywrightReceiver`
(remote-режим, `playwright_remote: true` в config.json).

Формат элемента листа (JSON):

```json
{
  "title": "Название заказа",
  "url": "https://kwork.ru/projects/123",
  "budget": "5 000 ₽",
  "budget_amount": 5000,
  "budget_currency": "RUB",
  "description": "Описание",
  "deadline": "1-3 дня",
  "source": "Kwork",
  "tags": ["python", "telegram"]
}
```

Скрипт скрейпера, пишущего в Redis-лист: `playwright_worker.py` (в корне
arch-code, запускается на Windows-хосте).

⚠️ Ключ LIST — `freelance:remote:orders` (должен совпадать с
`remote_list_key` в ai-core `ExchangePlaywrightReceiver`).

## 5. Проверка после переноса

- [ ] `redis-cli ping` на Windows (через туннель) → `PONG`
- [ ] `systemctl status arch-code-worker` на VPS → **не запущен** (SETNX lock)
- [ ] Задача в Telegram → ZIP доставляется (адаптер находит файл на VPS)
- [ ] Freelance-заказ (HITL approve) → исполняется через RQ
- [ ] `ps aux | grep chromium` на VPS → пусто (браузер на Windows)

## 6. Откат

Вернуть `arch_code_remote: false` в config.json, `playwright_remote: false`,
перезапустить `arch-code-worker.service` на VPS, закрыть туннель.
