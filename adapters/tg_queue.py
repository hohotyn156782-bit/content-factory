"""TG-очередь ручной выкладки YouTube/TikTok.

Бот (TG_QUEUE_BOT_TOKEN) шлёт админу: видеофайл + сообщение с готовой копией (заголовок/теги/
1-й коммент) и inline-кнопкой «✅ Опубликовано». YouTube → Паше, TikTok → Даше.
Кнопку обрабатывает Vercel-вебхук (см. tg-queue-webhook). Сам отправитель работает из CI без вебхука.
"""
import os
import json
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

_API = "https://api.telegram.org/bot{token}/{method}"
# дефолтные чаты (можно переопределить env TG_QUEUE_CHAT_YOUTUBE / _TIKTOK)
_ADMINS = {"youtube": "964216249", "tiktok": "1692866818"}


def _token() -> str:
    return os.environ.get("TG_QUEUE_BOT_TOKEN", "").strip()


def _chat(target: str) -> str:
    return (os.environ.get(f"TG_QUEUE_CHAT_{target.upper()}", "").strip()
            or _ADMINS.get(target, "")).strip()


def send_item(target: str, video_path: str, title: str, tags: str,
              first_comment: str, channel: str = "", niche: str = "") -> tuple[bool, str]:
    """target: 'youtube' (→Паше) | 'tiktok' (→Даше). Шлёт видео + копию + кнопку. Возвращает (ok, info)."""
    tok = _token()
    if not tok:
        return False, "нет TG_QUEUE_BOT_TOKEN"
    chat = _chat(target)
    if not chat:
        return False, f"нет chat_id для {target}"
    head = {"youtube": "▶️ YouTube Shorts", "tiktok": "🎵 TikTok"}.get(target, target.upper())
    try:
        # 1) видеофайл (короткая подпись)
        with open(video_path, "rb") as f:
            rv = requests.post(_API.format(token=tok, method="sendVideo"),
                               data={"chat_id": chat, "caption": f"{head} · {channel or niche}",
                                     "supports_streaming": "true"},
                               files={"video": f}, timeout=240).json()
        if not rv.get("ok"):
            return False, "sendVideo: " + str(rv.get("description"))[:160]
        # 2) готовая копия + кнопка «опубликовано»
        body = (f"{head} · <b>{channel or niche}</b>\n\n"
                f"📌 <b>Заголовок:</b>\n<code>{_esc(title)}</code>\n\n"
                f"🏷 <b>Теги:</b>\n<code>{_esc(tags)}</code>\n\n"
                f"💬 <b>1-й коммент (закрепить):</b>\n<code>{_esc(first_comment)}</code>")[:4096]
        kb = {"inline_keyboard": [[{"text": "✅ Опубликовано",
                                    "callback_data": f"done:{target}:{niche}"[:60]}]]}
        rm = requests.post(_API.format(token=tok, method="sendMessage"),
                           data={"chat_id": chat, "text": body, "parse_mode": "HTML",
                                 "reply_markup": json.dumps(kb)}, timeout=60).json()
        if not rm.get("ok"):
            return True, "видео ушло, копия — ошибка: " + str(rm.get("description"))[:120]
        return True, f"в TG ({target})"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:160]


def _esc(s: str) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
