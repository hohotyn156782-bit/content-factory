"""Human-in-the-loop approval-гейт через Telegram Bot API.

Перед автопубликацией шлём владельцу в TG превью ролика с inline-кнопками
Approve/Reject + (опционально) выбор лучшего из 2-3 хуков (A/B заголовков).
Решение владельца читается планировщиком: публикуется ТОЛЬКО одобренное,
а выбранный хук подставляется в текст.

Тот же бот, что у reporter.py: креды TG_BOT_TOKEN / TG_CHAT_ID (владелец).
Зависимостей нет, кроме requests (он уже в проекте — threads.py/vk_video.py его
импортируют): через него проще собрать multipart для sendVideo.

Поток:
  • send_for_approval(content_id, video_path, caption, hooks) → sendVideo с кнопками.
  • poll_decisions() → getUpdates (long-poll), разбор callback_query, ответ
    answerCallbackQuery (убрать «часики»), снятие кнопок, возврат решений.
    Идемпотентно: offset (last update_id + 1) хранится в файле → один и тот же
    callback не вернётся дважды.

Короткий id под лимит callback_data (≤64 байта): если строковый content_id
длинный — хешируем в короткий код и ведём map в файле под core.DATA_ROOT.
"""
import json
import hashlib
import pathlib

import requests

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

API = "https://api.telegram.org"

# Файлы состояния (на диске данных, рядом с остальной историей фабрики).
OFFSET_FILE = core.DATA_ROOT / "tg_review_offset.json"
MAP_FILE = core.DATA_ROOT / "tg_review_map.json"

# Запас под префикс/разделители: callback_data целиком ≤ 64 байта.
# Формат самого длинного варианта: "h:{code}:{idx}" → код держим коротким.
MAX_CALLBACK = 64
HOOK_BTN_LEN = 40   # сколько символов хука показываем на кнопке


# ──────────────────────────── Креды / низкоуровневый вызов ────────────────────────────

def _creds() -> tuple[str, str] | None:
    """(token, chat_id) владельца или None, если бот не настроен."""
    token = core.secret("TG_BOT_TOKEN", required=False)
    chat = core.secret("TG_CHAT_ID", required=False)
    if not token or not chat:
        return None
    return token, chat


def _api(token: str, method: str, **params) -> dict | None:
    """Вызов Bot API (application/json). Возвращает result или None (с логом)."""
    try:
        r = requests.post(f"{API}/bot{token}/{method}", json=params, timeout=40)
        j = r.json()
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace(token, "***")   # belt-and-suspenders: не светим bot-токен из URL
        core.log_error(f"tg_review._api {method}", RuntimeError(msg))
        return None
    if not j.get("ok"):
        core.log(f"tg_review {method}: {str(j.get('description'))[:200]}", level="warn")
        return None
    return j.get("result")


# ──────────────────────────── Короткие id (под лимит callback_data) ────────────────────────────

def _load_map() -> dict:
    if MAP_FILE.exists():
        try:
            return json.loads(MAP_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_map(m: dict) -> None:
    try:
        MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        MAP_FILE.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        core.log_error("tg_review._save_map", e)


def _short_code(content_id) -> str:
    """Короткий код для callback_data. Короткий content_id оставляем как есть
    (тогда обратное преобразование не нужно), длинный — хешируем и пишем в map."""
    cid = str(content_id)
    # «c:{code}» + «:{idx до 2 знаков}» влезает в 64, если код ≤ ~12 символов и сам id короткий.
    if len(cid) <= 12 and cid.replace("-", "").replace("_", "").isalnum():
        return cid
    code = "h" + hashlib.sha1(cid.encode("utf-8")).hexdigest()[:10]
    m = _load_map()
    if m.get(code) != cid:
        m[code] = cid
        _save_map(m)
    return code


def _resolve_code(code: str) -> str:
    """Код обратно в content_id (через map; если кода нет — это и есть сам id)."""
    return _load_map().get(code, code)


# ──────────────────────────── Отправка на одобрение ────────────────────────────

def _keyboard(code: str, hooks: list[str] | None) -> dict:
    """inline_keyboard: ряд Approve/Reject + по ряду на каждый хук."""
    rows = [[
        {"text": "✅ Одобрить", "callback_data": f"ok:{code}"},
        {"text": "❌ Отклонить", "callback_data": f"no:{code}"},
    ]]
    for idx, hook in enumerate(hooks or []):
        cb = f"h:{code}:{idx}"
        if len(cb.encode("utf-8")) > MAX_CALLBACK:   # подстраховка: пропускаем слишком длинный
            continue
        label = (hook or "").strip().replace("\n", " ")
        if len(label) > HOOK_BTN_LEN:
            label = label[:HOOK_BTN_LEN - 1] + "…"
        rows.append([{"text": f"🅰 {label}", "callback_data": cb}])
    return {"inline_keyboard": rows}


def _fallback_message(token: str, chat: str, content_id, video_path: str,
                      caption: str, markup: dict) -> bool:
    """Видео не ушло (большое/недоступно) → шлём текст со ссылкой на файл + те же кнопки."""
    text = (f"🎬 <b>На одобрение</b> (видео не отправилось превью)\n"
            f"id: <code>{content_id}</code>\n"
            f"файл: <code>{video_path}</code>\n\n{(caption or '')[:600]}")
    res = _api(token, "sendMessage", chat_id=chat, text=text, parse_mode="HTML",
               disable_web_page_preview=True, reply_markup=markup)
    return res is not None


def send_for_approval(content_id, video_path: str, caption: str,
                      hooks: list[str] | None = None) -> bool:
    """Отправить владельцу превью ролика с кнопками Approve/Reject (+ выбор хука).

    sendVideo (multipart) с reply_markup. Если видео большое/не шлётся — фолбэк на
    sendPhoto обложки (если есть .jpg рядом), иначе sendMessage со ссылкой на файл.
    Возвращает True при успехе.
    """
    cr = _creds()
    if not cr:
        core.log("tg_review: бот не настроен (TG_BOT_TOKEN/TG_CHAT_ID) — пропуск", level="warn")
        return False
    token, chat = cr

    code = _short_code(content_id)
    markup = _keyboard(code, hooks)
    markup_json = json.dumps(markup, ensure_ascii=False)
    cap = (caption or "")[:1024]   # лимит caption в Telegram

    vp = pathlib.Path(video_path)
    if not vp.exists() or vp.stat().st_size == 0:
        core.log(f"tg_review: нет видео {video_path} — фолбэк-сообщение", level="warn")
        return _fallback_message(token, chat, content_id, video_path, caption, markup)

    # 1) основной путь — sendVideo как multipart
    try:
        with vp.open("rb") as f:
            r = requests.post(
                f"{API}/bot{token}/sendVideo",
                data={"chat_id": chat, "caption": cap, "parse_mode": "HTML",
                      "supports_streaming": "true", "reply_markup": markup_json},
                files={"video": (vp.name, f, "video/mp4")},
                timeout=300,
            )
        j = r.json()
        if j.get("ok"):
            core.log(f"tg_review: ролик {content_id} отправлен на одобрение", level="info")
            return True
        core.log(f"tg_review sendVideo: {str(j.get('description'))[:200]}", level="warn")
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace(token, "***")   # belt-and-suspenders: не светим bot-токен из URL
        core.log_error("tg_review.sendVideo", RuntimeError(msg))

    # 2) фолбэк — обложка (cover.jpg рядом с видео), если есть
    cover = vp.with_suffix(".jpg")
    if not cover.exists():
        cover = vp.parent / "cover.jpg"
    if cover.exists() and cover.stat().st_size > 0:
        try:
            with cover.open("rb") as f:
                r = requests.post(
                    f"{API}/bot{token}/sendPhoto",
                    data={"chat_id": chat, "caption": cap, "parse_mode": "HTML",
                          "reply_markup": markup_json},
                    files={"photo": (cover.name, f, "image/jpeg")},
                    timeout=120,
                )
            if r.json().get("ok"):
                core.log(f"tg_review: ролик {content_id} — фолбэк на обложку", level="info")
                return True
        except Exception as e:  # noqa: BLE001
            msg = str(e).replace(token, "***")   # belt-and-suspenders: не светим bot-токен из URL
            core.log_error("tg_review.sendPhoto", RuntimeError(msg))

    # 3) последний фолбэк — текст со ссылкой на файл
    return _fallback_message(token, chat, content_id, video_path, caption, markup)


# ──────────────────────────── Чтение решений ────────────────────────────

def _load_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return int(json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("offset", 0))
        except Exception:  # noqa: BLE001
            return 0
    return 0


def _save_offset(offset: int) -> None:
    try:
        OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        core.log_error("tg_review._save_offset", e)


def _parse_callback(data: str) -> dict | None:
    """callback_data → {content_id, decision, hook_idx}.
      ok:{code}        → approve
      no:{code}        → reject
      h:{code}:{idx}   → approve + выбранный хук idx
    """
    parts = (data or "").split(":")
    tag = parts[0] if parts else ""
    if tag == "ok" and len(parts) >= 2:
        return {"content_id": _resolve_code(parts[1]), "decision": "approve", "hook_idx": None}
    if tag == "no" and len(parts) >= 2:
        return {"content_id": _resolve_code(parts[1]), "decision": "reject", "hook_idx": None}
    if tag == "h" and len(parts) >= 3:
        try:
            idx = int(parts[2])
        except ValueError:
            idx = None
        # выбор хука = одобрение этого варианта
        return {"content_id": _resolve_code(parts[1]), "decision": "approve", "hook_idx": idx}
    return None


def poll_decisions(timeout: int = 0) -> list[dict]:
    """Забрать решения владельца через getUpdates (long-poll при timeout>0).

    Возвращает список {"content_id":..., "decision":"approve"|"reject", "hook_idx":int|None}.
    На каждый callback шлём answerCallbackQuery (убрать «часики») и снимаем кнопки
    (editMessageReplyMarkup). Идемпотентно: offset (last update_id + 1) сохраняется
    в файл, поэтому уже обработанные апдейты не вернутся повторно.
    """
    cr = _creds()
    if not cr:
        return []
    token, chat = cr

    offset = _load_offset()
    res = _api(token, "getUpdates", offset=offset, timeout=timeout,
               allowed_updates=["callback_query"])
    if not res:
        return []

    decisions: list[dict] = []
    max_update_id = offset - 1
    for upd in res:
        uid = upd.get("update_id", 0)
        if uid > max_update_id:
            max_update_id = uid
        cq = upd.get("callback_query")
        if not cq:
            continue

        # 1) обязательно гасим «часики» на кнопке
        parsed = _parse_callback(cq.get("data", ""))
        toast = ("Одобрено ✅" if parsed and parsed["decision"] == "approve"
                 else "Отклонено ❌" if parsed else "")
        _api(token, "answerCallbackQuery", callback_query_id=cq.get("id"), text=toast)

        if not parsed:
            continue

        # 2) снимаем кнопки у сообщения, чтобы по нему нельзя было нажать второй раз
        msg = cq.get("message") or {}
        if msg.get("message_id"):
            _api(token, "editMessageReplyMarkup",
                 chat_id=msg.get("chat", {}).get("id", chat),
                 message_id=msg["message_id"], reply_markup={"inline_keyboard": []})

        decisions.append(parsed)
        core.log(f"tg_review: решение {parsed['decision']} по {parsed['content_id']}"
                 + (f" (хук {parsed['hook_idx']})" if parsed["hook_idx"] is not None else ""),
                 level="info")

    # 3) сдвигаем offset за последний обработанный апдейт → идемпотентность
    if res:
        _save_offset(max_update_id + 1)
    return decisions


# ──────────────────────────── CLI ────────────────────────────

if __name__ == "__main__":
    core.load_local_secrets()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if cmd == "poll":
        to = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        for d in poll_decisions(timeout=to):
            print(d)
    elif cmd == "send":
        if len(sys.argv) < 3:
            print("usage: python3 -m adapters.tg_review send <video> [caption]")
            sys.exit(1)
        video = sys.argv[2]
        caption = sys.argv[3] if len(sys.argv) > 3 else "Тестовое превью на одобрение"
        ok = send_for_approval("test-1", video, caption,
                               hooks=["Хук А — первый вариант заголовка",
                                      "Хук Б — второй вариант заголовка"])
        print("sent:", ok)
    else:
        print("commands: poll [timeout] | send <video> [caption]")
