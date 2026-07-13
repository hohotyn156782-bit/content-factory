"""Публикация Reels в Instagram через Graph API (полная автоматизация своего аккаунта).

Требования (путь "Instagram API with Instagram Login", 2026): IG Business/Creator аккаунт +
Meta app (БЕЗ Facebook-страницы; Development-режим, без ревью для своего аккаунта).
Scopes: instagram_business_basic + instagram_business_content_publish.
Видео должно лежать на ПОСТОЯННОМ публичном HTTPS — Meta его скачивает.
Бесплатный хостинг: Cloudflare R2 (10 ГБ). Адаптер сам зальёт mp4 в R2, если заданы R2_*.

Env:
  IG_USER_ID, IG_ACCESS_TOKEN (long-lived, 60д — обновлять каждые 50д)
  Хостинг (любой из вариантов):
    R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET, R2_PUBLIC_BASE_URL  (S3-совместимый R2)
    либо PUBLIC_VIDEO_URL передаётся напрямую (видео уже где-то опубликовано)

Поток: upload→public_url → media (REELS) container → poll FINISHED → media_publish.
"""
import os
import time
import pathlib

import requests

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

# Путь "Instagram API with Instagram Login" (2026): без Facebook-страницы, база graph.instagram.com
GRAPH = "https://graph.instagram.com/v22.0"


def _host_to_r2(video_path: str) -> str | None:
    """Залить mp4 в Cloudflare R2 и вернуть публичный URL. None если R2 не настроен."""
    acc = os.environ.get("R2_ACCOUNT_ID"); ak = os.environ.get("R2_ACCESS_KEY")
    sk = os.environ.get("R2_SECRET_KEY"); bucket = os.environ.get("R2_BUCKET")
    base = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")
    if not all([acc, ak, sk, bucket, base]):
        return None
    try:
        import boto3  # lazy
    except ImportError:
        raise RuntimeError("Для R2 установи: pip install --break-system-packages boto3")
    s3 = boto3.client("s3", endpoint_url=f"https://{acc}.r2.cloudflarestorage.com",
                      aws_access_key_id=ak, aws_secret_access_key=sk, region_name="auto")
    key = f"reels/{pathlib.Path(video_path).name}"
    s3.upload_file(video_path, bucket, key, ExtraArgs={"ContentType": "video/mp4"})
    return f"{base}/{key}"


def _creds(account: dict | None) -> tuple[str, str] | None:
    """(ig_user_id, token): из аккаунта связки (ext_id + secret_ref) или из env IG_USER_ID/IG_ACCESS_TOKEN."""
    if account:
        uid = str(account.get("ext_id") or "").strip()
        token = os.environ.get(str(account.get("secret_ref") or "").strip(), "")
        if uid and token:
            return uid, token
    uid = os.environ.get("IG_USER_ID"); token = os.environ.get("IG_ACCESS_TOKEN")
    return (uid, token) if (uid and token) else None


def verify(account: dict) -> tuple[bool, str]:
    cr = _creds(account)
    if not cr:
        return False, "нет кред IG (ext_id/secret_ref)"
    _, token = cr
    r = requests.get(f"{GRAPH}/me", params={"fields": "username,account_type", "access_token": token}, timeout=20).json()
    if r.get("error"):
        return False, str(r["error"].get("message"))
    return True, "@" + r.get("username", "?") + f" ({r.get('account_type','')})"


def publish(video_path: str, meta: dict, account: dict | None = None):
    cr = _creds(account)
    if not cr:
        return False, "нет кред IG (ext_id/secret_ref у аккаунта или IG_USER_ID/IG_ACCESS_TOKEN)"
    uid, token = cr

    video_url = os.environ.get("PUBLIC_VIDEO_URL") or _host_to_r2(video_path)
    if not video_url:
        # бесплатный фолбэк без карты: GitHub + jsDelivr CDN
        try:
            from adapters import media_host
            video_url = media_host.public_url(video_path)
        except Exception as e:  # noqa: BLE001
            return False, f"видео негде хостить (R2/GitHub): {e}"

    caption = meta.get("captions", {}).get("instagram", {}).get("caption", "") or meta.get("topic", "")

    # 1) контейнер
    r = requests.post(f"{GRAPH}/{uid}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": caption,
        "share_to_feed": "true", "access_token": token,
    }, timeout=60).json()
    cid = r.get("id")
    if not cid:
        return False, "контейнер не создан: " + ((r.get("error") or {}).get("message", "")[:120])

    # 2) ждём FINISHED (exp backoff)
    for attempt in range(20):
        time.sleep(min(8 + attempt * 4, 30))
        st = requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": token},
                          timeout=30).json()
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            # поле status несёт подробность («ERROR: причина»), error.message тут обычно пуст
            detail = (st.get("error") or {}).get("message") or st.get("status") or str(st)
            return False, "обработка не удалась: " + detail[:160]
    else:
        return False, "контейнер не дошёл до FINISHED за отведённое время"

    # 3) публикация (ретрай на транзиентный «media not ready» даже после FINISHED)
    mid = None
    pub = {}
    for attempt in range(5):
        pub = requests.post(f"{GRAPH}/{uid}/media_publish", data={
            "creation_id": cid, "access_token": token}, timeout=60).json()
        mid = pub.get("id")
        if mid:
            break
        time.sleep(5 * (attempt + 1))
    if not mid:
        return False, "публикация не удалась: " + ((pub.get("error") or {}).get("message", "")[:120])
    return True, {"id": mid, "url": video_url}


if __name__ == "__main__":
    core.load_local_secrets()
    print("IG configured:", bool(os.environ.get("IG_USER_ID") and os.environ.get("IG_ACCESS_TOKEN")))
