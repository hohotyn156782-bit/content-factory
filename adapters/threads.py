"""Публикация в Threads (Meta) через официальный Threads API.

Аккаунт связки даёт креды: ext_id = Threads user id, secret_ref = имя env с long-lived токеном.
Текстовый пост — двухшаговый: создать контейнер (threads, media_type=TEXT) → опубликовать
(threads_publish). Видео (REELS) — задел на потом: нужен публичный HTTPS-URL на mp4.

Токен long-lived живёт ~60 дней, обновляется через /refresh_access_token (см. refresh()).
"""
import os
import pathlib

import requests

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

API = "https://graph.threads.net/v1.0"


def _creds(account: dict) -> tuple[str, str] | None:
    """(threads_user_id, token) из аккаунта панели: ext_id = id, secret_ref = имя env."""
    if not account:
        return None
    uid = str(account.get("ext_id") or "").strip()
    token = os.environ.get(str(account.get("secret_ref") or "").strip(), "")
    if uid and token:
        return uid, token
    return None


def _post(url: str, **params):
    r = requests.post(url, data=params, timeout=40)
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        return None, f"Threads HTTP {r.status_code}"
    if isinstance(j, dict) and j.get("error"):
        e = j["error"]
        return None, f"Threads error: {e.get('message') or e}"
    return j, None


def verify(account: dict) -> tuple[bool, str]:
    """Проверить токен — кто это."""
    cr = _creds(account)
    if not cr:
        return False, "нет кред Threads (ext_id/secret_ref)"
    uid, token = cr
    r = requests.get(f"{API}/me", params={"fields": "id,username", "access_token": token}, timeout=20)
    j = r.json()
    if j.get("error"):
        return False, str(j["error"].get("message"))
    return True, "@" + j.get("username", "?")


def publish_text(message: str, account: dict) -> tuple[bool, dict | str]:
    """Опубликовать текстовый тред (kind=text)."""
    cr = _creds(account)
    if not cr:
        return False, "нет кред Threads (ext_id/secret_ref) у аккаунта"
    uid, token = cr
    msg = (message or "").strip()
    if not msg:
        return False, "пустой текст"
    # 1) контейнер
    cont, err = _post(f"{API}/{uid}/threads", media_type="TEXT", text=msg[:500], access_token=token)
    if err:
        return False, err
    cid = cont.get("id")
    if not cid:
        return False, f"нет creation_id: {cont}"
    # 2) публикация
    pub, err = _post(f"{API}/{uid}/threads_publish", creation_id=cid, access_token=token)
    if err:
        return False, err
    post_id = pub.get("id")
    return True, {"url": f"https://www.threads.net/@{account.get('display_name','').lstrip('@')}/post/{post_id}",
                  "post_id": post_id}


def publish_video(video_url: str, caption: str, account: dict) -> tuple[bool, dict | str]:
    """Опубликовать видео-тред из ПУБЛИЧНОГО HTTPS-URL на mp4."""
    import time
    cr = _creds(account)
    if not cr:
        return False, "нет кред Threads"
    uid, token = cr
    if not (video_url or "").startswith("https://"):
        return False, "нужен публичный https URL на видео"
    cont, err = _post(f"{API}/{uid}/threads", media_type="VIDEO", video_url=video_url,
                      text=(caption or "")[:500], access_token=token)
    if err:
        return False, err
    cid = cont.get("id")
    if not cid:
        return False, f"нет creation_id: {cont}"
    # Обработка видео у Meta асинхронная: публиковать можно только после FINISHED, иначе
    # threads_publish отдаёт «media not ready». Поллинг с backoff — как в instagram.py.
    for attempt in range(20):
        time.sleep(min(8 + attempt * 4, 30))
        try:
            st = requests.get(f"{API}/{cid}", params={"fields": "status,error_message",
                                                      "access_token": token}, timeout=30).json()
        except Exception:  # noqa: BLE001
            st = {}
        code = st.get("status") or st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            return False, f"обработка видео не удалась: {st.get('error_message') or code}"
    else:
        return False, "контейнер не дошёл до FINISHED за отведённое время"
    # публикация (ретрай на транзиентный «not ready» даже после FINISHED — как в instagram.py)
    pub, err = None, None
    for attempt in range(5):
        pub, err = _post(f"{API}/{uid}/threads_publish", creation_id=cid, access_token=token)
        if pub and pub.get("id"):
            return True, {"post_id": pub.get("id")}
        time.sleep(5 * (attempt + 1))
    return False, err or f"публикация не удалась: {pub}"


SECRETS_ENV = pathlib.Path("~/.config/content-factory/secrets.env").expanduser()


def _persist_secret(key: str, value: str) -> None:
    """Безопасно перезаписать одну строку KEY=value в secrets.env (остальное сохранить),
    выставить режим 600. Если ключа нет — дописать. Бросает при сбое записи."""
    SECRETS_ENV.parent.mkdir(parents=True, exist_ok=True)
    lines = SECRETS_ENV.read_text(encoding="utf-8").splitlines() if SECRETS_ENV.exists() else []
    out, replaced = [], False
    for ln in lines:
        stripped = ln.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped \
                and stripped.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{key}={value}")
    SECRETS_ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(SECRETS_ENV, 0o600)


def refresh(account: dict) -> tuple[bool, str]:
    """Обновить long-lived токен (делать раз в ~50 дней).
    Новый токен НЕ возвращается наружу: сохраняется в secrets.env (ключ = secret_ref)
    и в os.environ, во втором элементе — только статус."""
    cr = _creds(account)
    if not cr:
        return False, "нет кред"
    _, token = cr
    r = requests.get(f"{API}/refresh_access_token",
                     params={"grant_type": "th_refresh_token", "access_token": token}, timeout=20)
    j = r.json()
    if j.get("error"):
        return False, str(j["error"].get("message"))
    new_token = j.get("access_token", "")
    if not new_token:
        return False, "Threads не вернул access_token"
    ref = str(account.get("secret_ref") or "").strip()
    if not ref:
        return False, "нет secret_ref у аккаунта — некуда сохранить токен"
    try:
        _persist_secret(ref, new_token)
        os.environ[ref] = new_token
    except Exception as e:  # noqa: BLE001
        os.environ[ref] = new_token   # хотя бы в памяти процесса, чтобы текущий цикл работал
        core.log_error("threads.refresh._persist", e)
        return True, "токен обновлён, но не удалось сохранить в secrets.env — обнови вручную"
    return True, "токен Threads обновлён (+60д)"


# единый интерфейс адаптера (видео-публикация пока требует публичный URL — не из файла)
def publish(video_path: str, meta: dict, account: dict | None = None):
    return False, "Threads видео: нужен публичный https URL (R2). Используй publish_text для текста."
