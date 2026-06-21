"""Публикация вертикального видео в Telegram-каналы через Kurigram (MTProto).

Почему userbot, а не Bot API: Bot API душит загрузку видео до 50 МБ и не всегда даёт
нативный стриминг/превью. MTProto (kurigram, живой форк Pyrogram) тянет ~2 ГБ и
отдаёт supports_streaming + аккуратный кружок-превью — то, что нужно для Reels-формата.

Аккаунт связки даёт цель: ext_id = id/@username канала, secret_ref — задел под
пер-аккаунтную env (пока креды userbot общие, см. ниже).

КРЕДЫ (env / ~/.config/content-factory/secrets.env, грузятся core.load_local_secrets):
  TG_API_ID, TG_API_HASH   — приложение с my.telegram.org → apps
  TG_SESSION_STRING        — строковая сессия userbot (предпочтительно), ИЛИ
  файл сессии              — ~/.config/content-factory/tg_user.session

Сессия ОБЯЗАТЕЛЬНО user-аккаунтная (не бот): только она снимает лимит 50 МБ.
Разовая генерация session string:  python3 adapters/telegram.py login

ВАЖНО: kurigram ставится как `pip install kurigram tgcrypto`, но импортируется под
namespace `pyrogram` (тот же, что у Pyrogram). Любой импорт pyrogram — ЛЕНИВЫЙ,
внутри функций, чтобы этот файл импортировался даже когда kurigram ещё не установлен.
"""
import os
import pathlib

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

# Папка с кредами/файлом сессии (совпадает с первым SECRET_FILE из core).
CFG_DIR = pathlib.Path("~/.config/content-factory").expanduser()
SESSION_FILE_NAME = "tg_user"  # → ~/.config/content-factory/tg_user.session


def _api() -> tuple[int, str] | None:
    """(api_id, api_hash) из env. None — если кредов нет/битые."""
    aid = os.environ.get("TG_API_ID", "").strip()
    ahash = os.environ.get("TG_API_HASH", "").strip()
    if not aid or not ahash:
        return None
    try:
        return int(aid), ahash
    except ValueError:
        core.log("TG_API_ID не число — проверь secrets.env", level="error")
        return None


def _client():
    """Собрать pyrogram(kurigram) Client из session string или файла сессии.
    Возвращает Client или None (нет кредов / kurigram не установлен).
    Импорт pyrogram — ленивый: файл должен импортироваться без установленного kurigram."""
    api = _api()
    if not api:
        core.log("нет TG_API_ID/TG_API_HASH — Telegram-адаптер не настроен", level="warn")
        return None
    api_id, api_hash = api
    try:
        from pyrogram import Client  # noqa: PLC0415 — ленивый импорт (kurigram == namespace pyrogram)
    except Exception:  # noqa: BLE001 — kurigram ещё не поставлен
        core.log("Kurigram не установлен: pip install kurigram tgcrypto", level="error")
        return None

    session_string = os.environ.get("TG_SESSION_STRING", "").strip()
    if session_string:
        # in_memory=True — ничего не пишем на диск, сессия живёт строкой
        return Client(name="cf", session_string=session_string,
                      api_id=api_id, api_hash=api_hash, in_memory=True)

    # Фолбэк: файл сессии ~/.config/content-factory/tg_user.session
    sess = CFG_DIR / f"{SESSION_FILE_NAME}.session"
    if sess.exists():
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        return Client(name=SESSION_FILE_NAME, workdir=str(CFG_DIR),
                      api_id=api_id, api_hash=api_hash)

    core.log("нет TG_SESSION_STRING и нет файла сессии — выполни: python3 adapters/telegram.py login",
             level="warn")
    return None


def _chat_target(account: dict | None) -> str | None:
    """Цель публикации: ext_id связки (id/@username канала) или env TG_CHANNEL."""
    if account:
        ext = str(account.get("ext_id") or "").strip()
        if ext:
            return ext
    env = os.environ.get("TG_CHANNEL", "").strip()
    return env or None


def _msg_url(chat: str, username: str | None, message_id: int) -> str:
    """Ссылка на пост. Для @username — t.me/username/id; иначе t.me/c/<internal>/id."""
    uname = (username or "").lstrip("@")
    if uname:
        return f"https://t.me/{uname}/{message_id}"
    # приватный канал: id вида -100XXXXXXXXXX → t.me/c/XXXXXXXXXX/<id>
    raw = str(chat).lstrip("@")
    internal = raw[4:] if raw.startswith("-100") else raw.lstrip("-")
    return f"https://t.me/c/{internal}/{message_id}"


def _try_first_comment(app, chat: str, message_id: int, text: str) -> None:
    """Первый комментарий: если у канала есть привязанная discussion-группа, пост туда
    автокопируется — отвечаем на эту копию. Не критично: любой сбой глотаем в лог."""
    text = (text or "").strip()
    if not text:
        return
    try:
        info = app.get_chat(chat)
        linked = getattr(info, "linked_chat", None)
        if not linked:
            return  # нет привязанной группы обсуждений — комментировать некуда
        # копия поста в группе обсуждений (kurigram сам ждёт появления копии)
        disc = app.get_discussion_message(chat, message_id)
        if not disc:
            return
        linked_chat = getattr(linked, "id", linked)
        app.send_message(linked_chat, text[:4096], reply_to_message_id=disc.id)
        core.log("Telegram: первый комментарий отправлен", level="info")
    except Exception as e:  # noqa: BLE001 — первый коммент опционален, пост уже опубликован
        core.log_error("telegram.first_comment", e)


def publish(video_path: str, meta: dict, account: dict | None = None) -> tuple[bool, dict | str]:
    """Опубликовать вертикальное видео в Telegram-канал.

    chat    = account['ext_id'] (id/@username) или env TG_CHANNEL.
    caption = meta.captions.telegram.caption или meta.topic.
    Опц. первый комментарий — env TG_FIRST_COMMENT или meta.captions.telegram.first_comment.
    Возврат: (True, {"url": ..., "message_id": ...}).
    """
    chat = _chat_target(account)
    if not chat:
        return False, "нет цели Telegram (account.ext_id или env TG_CHANNEL)"
    if not video_path or not pathlib.Path(video_path).exists():
        return False, f"нет файла видео: {video_path}"

    tg_meta = (meta or {}).get("captions", {}).get("telegram", {}) or {}
    caption = (tg_meta.get("caption") or (meta or {}).get("topic") or "").strip()
    first_comment = (os.environ.get("TG_FIRST_COMMENT", "").strip()
                     or tg_meta.get("first_comment", "")).strip()

    app = _client()
    if app is None:
        return False, "Telegram не настроен (см. лог: креды/сессия/kurigram)"

    try:
        with app:
            msg = app.send_video(chat, video_path, caption=caption[:1024],
                                 supports_streaming=True)
            mid = msg.id
            # имя канала для красивой ссылки (по возможности)
            uname = None
            try:
                ch = msg.chat
                uname = getattr(ch, "username", None)
            except Exception:  # noqa: BLE001
                uname = None
            if first_comment:
                _try_first_comment(app, chat, mid, first_comment)
            url = _msg_url(chat, uname, mid)
            return True, {"url": url, "message_id": mid}
    except Exception as e:  # noqa: BLE001 — постинг не должен ронять автопилот тихо
        core.log_error("telegram.publish", e)
        return False, f"Telegram publish: {type(e).__name__}: {str(e)[:200]}"


def publish_text(message: str, account: dict | None = None) -> tuple[bool, dict | str]:
    """Текстовый пост в Telegram-канал (kind=text)."""
    chat = _chat_target(account)
    if not chat:
        return False, "нет цели Telegram (account.ext_id или env TG_CHANNEL)"
    msg_text = (message or "").strip()
    if not msg_text:
        return False, "пустой текст"

    app = _client()
    if app is None:
        return False, "Telegram не настроен (см. лог: креды/сессия/kurigram)"
    try:
        with app:
            msg = app.send_message(chat, msg_text[:4096])
            uname = None
            try:
                uname = getattr(msg.chat, "username", None)
            except Exception:  # noqa: BLE001
                uname = None
            return True, {"url": _msg_url(chat, uname, msg.id), "message_id": msg.id}
    except Exception as e:  # noqa: BLE001
        core.log_error("telegram.publish_text", e)
        return False, f"Telegram publish_text: {type(e).__name__}: {str(e)[:200]}"


def verify(account: dict | None = None) -> tuple[bool, str]:
    """Проверить сессию — кто это (userbot-аккаунт)."""
    app = _client()
    if app is None:
        return False, "Telegram не настроен (креды/сессия/kurigram — см. лог)"
    try:
        with app:
            me = app.get_me()
            handle = ("@" + me.username) if getattr(me, "username", None) else (me.first_name or str(me.id))
            return True, handle
    except Exception as e:  # noqa: BLE001
        core.log_error("telegram.verify", e)
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def gen_session() -> None:
    """Интерактивный помощник: разово сгенерировать session string (телефон + код в консоли).
    Запускать вручную:  python3 adapters/telegram.py login
    Печатает строку для TG_SESSION_STRING — положи её в secrets.env."""
    api = _api()
    if not api:
        print("❌ Сначала задай TG_API_ID и TG_API_HASH (env или ~/.config/content-factory/secrets.env).")
        print("   Получить: https://my.telegram.org → API development tools → создать приложение.")
        return
    api_id, api_hash = api
    try:
        from pyrogram import Client  # noqa: PLC0415 — ленивый импорт
    except Exception:  # noqa: BLE001
        print("❌ Kurigram не установлен: pip install kurigram tgcrypto")
        return
    print("→ Войди под USER-аккаунтом (не бот): введи телефон и код из Telegram.")
    with Client("cf_login", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        s = app.export_session_string()
        me = app.get_me()
        who = ("@" + me.username) if getattr(me, "username", None) else (me.first_name or str(me.id))
        print("\n✅ Сессия создана для:", who)
        print("\nДобавь в ~/.config/content-factory/secrets.env строку:\n")
        print(f"TG_SESSION_STRING={s}")
        print("\n(Никому не показывай эту строку — это полный доступ к аккаунту.)")


if __name__ == "__main__":
    core.load_local_secrets()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "login":
        gen_session()
    elif cmd == "verify":
        ok, info = verify()
        print(("✅ " if ok else "❌ ") + info)
    else:
        print("Использование: python3 adapters/telegram.py [login|verify]")
