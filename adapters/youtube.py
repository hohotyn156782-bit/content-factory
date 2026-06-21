"""Загрузка Shorts на YouTube через Data API v3 (полная автоматизация, СЕТЬ каналов).

Мульти-канальность (как vk_video._account_target): у каждого аккаунта панели токен
живёт в отдельном файле core.DATA_ROOT/"yt_tokens"/<name>.json, где
name = account['secret_ref'] (или account['name'], если secret_ref пуст).
Если per-channel файла нет — ФОЛБЭК на одноканальный yt_token.json (обратная совместимость).

Требует OAuth Installed-App токен (см. youtube_auth.py — запустить один раз локально
на КАЖДЫЙ канал сети: python3 adapters/youtube_auth.py <name>).
Env:
  YT_CLIENT_SECRET_FILE — путь к client_secret.json (GCP OAuth Desktop client)
  YT_TOKEN_FILE         — путь к одноканальному token.json (дефолт/фолбэк)

КРИТИЧНО (из ресёрча):
  • Пока проект не прошёл YouTube API Compliance Audit — ВСЕ загрузки молча приватные
    (insert вернёт 201, но privacyStatus=private). Подать форму аудита сразу, ждать 2-4 недели.
  • Скрытый лимит ~7 загрузок/день/канал (с мая 2026). Фабрика держит кап на стороне оркестратора.
  • Shorts классифицируется автоматически по 9:16 + ≤180с + #Shorts в описании. Спец-эндпоинта нет.
"""
import os
import pathlib

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CHUNK = 262144 * 4  # 1 MiB, кратно 256 KiB (требование resumable upload)


def _imports():
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        return build, MediaFileUpload, Credentials, Request
    except ImportError:
        raise RuntimeError("Нет google-клиента. Установи: pip install --break-system-packages "
                           "google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2")


def _account_name(account: dict | None) -> str:
    """Имя per-channel токена из аккаунта панели: secret_ref (приоритет) или name. Пусто — нет имени."""
    if not account:
        return ""
    return str(account.get("secret_ref") or account.get("name") or "").strip()


def _token_path(account: dict | None) -> pathlib.Path:
    """Путь к токену канала. Если у аккаунта есть имя и файл core.DATA_ROOT/yt_tokens/<name>.json
    существует — берём его (сеть каналов). Иначе ФОЛБЭК на одноканальный YT_TOKEN_FILE."""
    name = _account_name(account)
    if name:
        per = core.DATA_ROOT / "yt_tokens" / f"{name}.json"
        if per.exists():
            return per
    return pathlib.Path(os.environ.get(
        "YT_TOKEN_FILE", str(pathlib.Path("~/.config/content-factory/yt_token.json").expanduser())))


def _service(account: dict | None = None):
    build, _, Credentials, Request = _imports()
    token_file = _token_path(account)
    if not token_file.exists():
        name = _account_name(account)
        hint = f" python3 adapters/youtube_auth.py {name}" if name else " python3 -m adapters.youtube_auth"
        raise RuntimeError(f"Нет {token_file}. Запусти один раз локально:{hint}")
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def verify(account: dict | None = None):
    try:
        yt = _service(account)
        ch = yt.channels().list(part="snippet", mine=True).execute()
        items = ch.get("items", [])
        if not items:
            return False, "канал не найден"
        return True, items[0]["snippet"]["title"]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# категория YouTube по нише (27=Образование, 28=Наука/Техника, 24=Развлечения) — было хардкод 28
_YT_CAT = {"ai_lifehacks": "28", "ai_lifehacks_en": "28", "personal_brand": "28",
           "mind_facts": "27", "money_facts": "27", "history_facts": "27",
           "business_stories": "27", "soviet_things": "27", "psy_stories": "27",
           "talking_objects": "24", "mystic_stories": "24", "what_if": "24"}


def publish(video_path: str, meta: dict, account: dict | None = None):
    _, MediaFileUpload, _, _ = _imports()
    yt = _service(account)
    cap = meta.get("captions", {}).get("youtube", {})
    title = cap.get("title") or meta.get("topic", "Short")
    description = cap.get("description", "")
    if "#shorts" not in description.lower():
        description = (description + "\n\n#Shorts").strip()
    tags = [t.lstrip("#") for t in meta.get("hashtags", [])][:15]
    body = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "tags": tags, "categoryId": _YT_CAT.get(meta.get("niche"), "27")},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_path, chunksize=CHUNK, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)  # либа сама делает exp-backoff на 5xx/socket
    vid = resp["id"]
    privacy = resp.get("status", {}).get("privacyStatus", "?")
    # Кастомная обложка — главный CTR-рычаг. scope youtube.upload позволяет thumbnails().set
    # на своём только что загруженном видео. НИКОГДА не валим публикацию: видео уже загружено.
    thumb_path = meta.get("thumbnail")
    if thumb_path and os.path.exists(thumb_path):
        try:
            yt.thumbnails().set(
                videoId=vid,
                media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg"),
            ).execute()
        except Exception as e:  # noqa: BLE001
            core.log_error("youtube.thumbnail", e, vid=vid)
    return True, {"id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": privacy}


if __name__ == "__main__":
    core.load_local_secrets()
    ok, msg = verify()
    print(("✓ YouTube: " if ok else "✗ YouTube: ") + str(msg))
