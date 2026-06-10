# Style Guide (Node.js / Express.js)

## Общие правила
- Все названия функций и переменных — `camelCase`.
- Константы и конфигурационные значения — `UPPER_SNAKE_CASE`.
- Файлы — `kebab-case.js` (например, `webhook-handler.js`).

## Асинхронность
- Все обработчики маршрутов — `async (req, res, next)`.
- Ошибки обрабатывать через `next(err)` или централизованный error-handler.

## Структура контроллера
```js
// controllers/webhook.controller.js
const handleIncomingMessage = async (req, res, next) => {
  try {
    // логика
    res.status(200).json({ status: "ok" });
  } catch (err) {
    next(err);
  }
};
```

## Импорты
- Сначала внешние пакеты, затем внутренние модули (пустая строка между группами).
