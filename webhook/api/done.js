// Vercel serverless: обработчик кнопки «✅ Опубликовано» в TG-очереди (@youtibetiktok_bot).
// Telegram шлёт сюда callback_query при нажатии кнопки → отвечаем и помечаем сообщение готовым.
// Env: TG_QUEUE_BOT_TOKEN. Вебхук ставится на URL этой функции (setWebhook).

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(200).send("ok");
  const token = process.env.TG_QUEUE_BOT_TOKEN;
  const api = (m, b) =>
    fetch(`https://api.telegram.org/bot${token}/${m}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(b),
    });
  try {
    const upd = req.body || {};
    const cq = upd.callback_query;
    if (cq) {
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
    }
  } catch (e) {
    // не роняем вебхук — Telegram повторит при 5xx
  }
  return res.status(200).send("ok");
}
