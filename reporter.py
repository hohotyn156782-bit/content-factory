"""Отчёты владельцу в Telegram (тот же бот, что у content-engine — TG_BOT_TOKEN/TG_CHAT_ID)."""
import json
import urllib.request

import core


def send(text: str) -> bool:
    token = core.secret("TG_BOT_TOKEN", required=False)
    chat = core.secret("TG_CHAT_ID", required=False)
    if not token or not chat:
        return False
    body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20)
        return True
    except Exception:  # noqa: BLE001
        return False


def critical(text: str, attach: str | None = None) -> bool:
    """Критичный алерт (падение автопилота, QA-брак, мало места) — дублируем на НЕЗАВИСИМЫЕ каналы
    помимо Telegram, чтобы единая точка отказа (упал TG-бот/сеть до Telegram) не оставила слепым.
    Каналы — Apprise-URL'ы через запятую в env APPRISE_URLS (ntfy/Discord/email/tgram), напр.
    'ntfy://ntfy.sh/мой-секретный-топик,tgram://BOT/CHAT'. Apprise нет/не настроен → фолбэк на TG send().
    attach — путь к превью-ролику/картинке (Apprise умеет вложения)."""
    urls = [u.strip() for u in (core.secret("APPRISE_URLS", required=False) or "").split(",") if u.strip()]
    sent = False
    if urls:
        try:
            import apprise  # ленивый — Apprise опционален
            ap = apprise.Apprise()
            for u in urls:
                ap.add(u)
            sent = bool(ap.notify(body=text, title="⚠️ Content Factory",
                                  attach=attach if attach else None))
        except Exception as e:  # noqa: BLE001
            core.log_error("apprise.critical", e)
    # TG в любом случае (это и есть наш базовый канал) — дублирование, не замена
    tg = send(text)
    return sent or tg


def report_run(meta: dict, results: dict) -> None:
    topic = meta.get("topic", "?")
    dur = meta.get("duration", 0)
    lines = [f"🎬 <b>Фабрика контента</b> · {meta.get('niche')}",
             f"Тема: {topic} · {dur:.0f}с",
             f"B-roll: сток {meta.get('stock_used', 0)} / генер. {meta.get('generated_bg', 0)}", ""]
    for platform, res in results.items():
        ok = res.get("ok")
        icon = "✅" if ok else "❌"
        detail = res.get("url") or res.get("note") or res.get("error") or ""
        lines.append(f"{icon} {platform}: {str(detail)[:120]}")
    send("\n".join(lines))
