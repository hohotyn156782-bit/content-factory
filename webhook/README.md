# TG-очередь — вебхук кнопки «готово»

Vercel-функция `api/done.js` обрабатывает нажатие inline-кнопки «✅ Опубликовано»
в боте очереди (@youtibetiktok_bot).

## Деплой (CLI залогинен)
```bash
cd webhook
vercel deploy --prod
# задать токен бота как env проекта:
vercel env add TG_QUEUE_BOT_TOKEN production   # вставить токен 8879...
vercel deploy --prod   # пересобрать с env
```

## Поставить вебхук боту (на URL функции, напр. https://<project>.vercel.app/api/done)
```bash
curl "https://api.telegram.org/bot<ТОКЕН>/setWebhook?url=https://<project>.vercel.app/api/done"
```

> Отправка сообщений в очередь идёт из CI напрямую через Bot API (вебхук НЕ нужен для отправки),
> вебхук нужен ТОЛЬКО чтобы кнопка «готово» реагировала.
