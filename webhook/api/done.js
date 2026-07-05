// Vercel serverless: обработчик кнопки «✅ Опубликовано» в TG-очереди (@youtibetiktok_bot).
// Telegram шлёт сюда callback_query при нажатии кнопки → отвечаем и помечаем сообщение готовым.
// Env: TG_QUEUE_BOT_TOKEN (токен бота), TG_WEBHOOK_SECRET (секрет из setWebhook?secret_token=...).
// Вебхук ставится на URL этой функции: setWebhook?url=.../api/done&secret_token=<TG_WEBHOOK_SECRET>.

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(200).send("ok");
  const token = process.env.TG_QUEUE_BOT_TOKEN;
  if (!token) {
    console.error("done.js: TG_QUEUE_BOT_TOKEN не задан");
    return res.status(500).send("no token");   // 5xx → Telegram повторит доставку позже
  }
  // Аутентификация: Telegram шлёт секрет в заголовке. Если задан TG_WEBHOOK_SECRET — сверяем.
  // Отсекает поддельные POST'ы на публичный URL (снятие кнопок/спам от имени бота).
  const wantSecret = process.env.TG_WEBHOOK_SECRET;
  if (wantSecret) {
    const got = req.headers["x-telegram-bot-api-secret-token"];
    if (got !== wantSecret) return res.status(403).send("forbidden");
  }
  const api = (m, b) =>
    fetch(`https://api.telegram.org/bot${token}/${m}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(b),
    });
  try {
    const upd = req.body || {};
    const cq = upd.callback_query;
    // Обрабатываем только наши callback'и «done:...» (валидация структуры).
    if (cq && typeof cq.data === "string" && cq.data.startsWith("done:")) {
      await api("answerCallbackQuery", {
        callback_query_id: cq.id,
        text: "✅ Отмечено как опубликовано",
      });
      const msg = cq.message;
      if (msg) {
        // убрать кнопку + дописать отметку «опубликовано»
        await api("editMessageReplyMarkup", {
          chat_id: msg.chat.id,
          message_id: msg.message_id,
          reply_markup: { inline_keyboard: [] },
        });
        const who = (cq.from && (cq.from.first_name || cq.from.username)) || "";
        await api("sendMessage", {
          chat_id: msg.chat.id,
          reply_to_message_id: msg.message_id,
          text: `✅ Опубликовано${who ? " · " + who : ""}`,
        });
      }
    } else if (cq) {
      // чужой/неизвестный callback — гасим «часики», но действий не выполняем
      await api("answerCallbackQuery", { callback_query_id: cq.id });
    }
  } catch (e) {
    console.error("done.js error:", e && e.message);
    return res.status(500).send("err");   // 5xx → Telegram повторит (не теряем нажатие)
  }
  return res.status(200).send("ok");
}
