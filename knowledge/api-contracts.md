# API Contracts

## Входящий Webhook (Twilio-совместимый)

**POST /api/webhook/incoming**

### body (application/json)
```json
{
  "from": "+1234567890",
  "to": "+0987654321",
  "text": "текст сообщения",
  "messageSid": "SMxxxxxxxx"
}
```

### response (200)
```json
{
  "status": "ok",
  "messageId": "SMxxxxxxxx"
}
```

### response (400)
```json
{
  "status": "error",
  "error": "описание ошибки валидации"
}
```

## База данных (MongoDB)
- Коллекция: `messages`
- Схема: `{ from, to, text, messageSid, createdAt }`
