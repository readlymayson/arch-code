# Python Style Guide (arch-code)

> Применяется при генерации/рефакторинге Python-кода (ai-core и аналогичные проекты).
> Если проект — Node.js, используй `style-guide.md`.

## Общие принципы

- Python 3.10+ (типизация через `from __future__ import annotations` если нужно).
- Стиль: **PEP 8** (4 пробела, 79-99 символов в строке).
- Кодировка: UTF-8. НЕ используй cp1251-специфичные конструкции.
- Комментарии и docstrings — на русском или английском, но единообразно в рамках файла.

## Типизация

```python
from typing import Optional, TypedDict, Any

def process_item(item: dict, *, timeout: float = 30.0) -> Optional[str]:
    ...
```

- Всегда аннотируй аргументы функций и возвращаемые значения.
- Используй `TypedDict` для структур с фиксированными ключами.
- `Optional[X]` вместо `X | None` для совместимости с Python 3.10.

## Структура файла

1. Docstring модуля (задача файла).
2. Импорты: stdlib → third-party → локальные (с пустой строкой между группами).
3. Константы (UPPER_SNAKE_CASE).
4. Функции и классы.

## Обработка ошибок

```python
try:
    result = risky_call()
except ValueError as e:
    logger.warning(f"Некорректные данные: {e}")
    return None
except Exception as e:
    logger.error(f"Неожиданная ошибка: {e}", exc_info=True)
    raise
```

- НЕ глотай исключения (`except Exception: pass`). Всегда логируй или пробрасывай.
- Используй `loguru` (`from loguru import logger`).

## Асинхронность

- `async def` для I/O-операций (сеть, БД, файлы).
- НЕ блокируй event loop синхронными вызовами — используй `asyncio.to_thread()`.
- Таймауты: `asyncio.wait_for(..., timeout=...)`.

## Конфигурация

- Читай настройки из окружения: `os.getenv("KEY", "default")`.
- Никогда не хардкодь секреты и пути в коде.
- `.env` загружается через `python-dotenv` (`load_dotenv()`).

## Запрещено

- Секреты/токены в коде.
- `print()` для логирования (используй loguru).
- Мёртвый код и неиспользуемые импорты.
- Зависимости, которых нет в `requirements.txt`.
