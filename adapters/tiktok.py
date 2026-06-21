"""TikTok — ПОЛУ-АВТО (честно, по ресёрчу).

Авто-публикация в ленту для соло-оператора недостижима: video.publish требует аудит,
который TikTok отклоняет для 'personal use', а нарушение = вечный бан. Поэтому:
  • Всегда готовим бандл: video.mp4 + tiktok_caption.txt (подпись+хэштеги+напоминание про AI-метку).
  • Если заданы TIKTOK_ACCESS_TOKEN — заливаем в Inbox (scope video.upload, без аудита):
    ролик прилетает в твой TikTok за минуту, ты тапаешь «Опубликовать» в приложении (~60с).

Env (опционально): TIKTOK_ACCESS_TOKEN (живёт 24ч — обновлять через refresh-токен).
"""
import os
import pathlib

import requests

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

INBOX_INIT = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
AI_NOTE = "⚠️ В TikTok включи метку «AI-generated content» при публикации (обязательно для AI-озвучки)."


def _write_caption(video_path: str, meta: dict) -> pathlib.Path:
    cap = meta.get("captions", {}).get("tiktok", {}).get("caption", "") or meta.get("topic", "")
    cap = cap[:2150]
    out = pathlib.Path(video_path).parent / "tiktok_caption.txt"
    out.write_text(cap + "\n\n" + AI_NOTE + "\n", encoding="utf-8")
    return out


def _inbox_upload(video_path: str, token: str):
    size = pathlib.Path(video_path).stat().st_size
    init = requests.post(INBOX_INIT, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                              "chunk_size": size, "total_chunk_count": 1}}, timeout=60).json()
    data = (init or {}).get("data", {})
    upload_url = data.get("upload_url")
    if not upload_url:
        return False, f"init не дал upload_url: {init}"
    with open(video_path, "rb") as f:
        put = requests.put(upload_url, data=f.read(), headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{size - 1}/{size}"}, timeout=300)
    if put.status_code not in (200, 201, 204):
        return False, f"PUT {put.status_code}: {put.text[:200]}"
    return True, {"publish_id": data.get("publish_id")}


def publish(video_path: str, meta: dict, account: dict | None = None):
    cap_file = _write_caption(video_path, meta)
    token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
    if token:
        ok, res = _inbox_upload(video_path, token)
        if ok:
            return True, {"mode": "inbox", "caption_file": str(cap_file),
                          "note": "ролик в TikTok Inbox — открой приложение и опубликуй (вставь подпись, включи AI-метку)",
                          **res}
        return True, {"mode": "bundle", "caption_file": str(cap_file), "warn": res,
                      "note": "inbox не сработал — загрузи video.mp4 вручную"}
    return True, {"mode": "bundle", "caption_file": str(cap_file),
                  "note": "загрузи video.mp4 в TikTok вручную (подпись в tiktok_caption.txt, включи AI-метку)"}


if __name__ == "__main__":
    core.load_local_secrets()
    print("TikTok inbox configured:", bool(os.environ.get("TIKTOK_ACCESS_TOKEN")))
