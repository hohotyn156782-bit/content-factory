"""Бесплатный публичный хостинг видео для постинга (IG Reels / Threads-видео качают mp4 по URL).

Заливает mp4 в публичный GitHub-репо через Contents API и отдаёт ПРЯМУЮ ссылку
raw.githubusercontent.com на конкретный коммит (immutable, доступна сразу после пуша).
Без карты, на инфре, что уже есть. (jsDelivr НЕ используем — он 403-ит на свежих коммитах
из-за задержки синка; raw отдаёт мгновенно.)

Конфиг (env, есть дефолты):
  MEDIA_REPO          = owner/repo            (дефолт hohotyn156782-bit/cf-media)
  GITHUB_TOKEN        = токен с repo-scope    (иначе берётся из ~/.git-credentials)
Лимит jsDelivr — 50 МБ на файл (наши shorts ~5-15 МБ, ок).
"""
import os
import base64
import pathlib

import requests

REPO_DEFAULT = "hohotyn156782-bit/cf-media"
API = "https://api.github.com"


def _token() -> str | None:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    cred = pathlib.Path.home() / ".git-credentials"
    if cred.exists():
        for ln in cred.read_text(encoding="utf-8").splitlines():
            if "github.com" in ln and "@" in ln and ":" in ln:
                try:
                    return ln.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
                except Exception:  # noqa: BLE001
                    pass
    return None


def public_url(video_path: str, dest_name: str | None = None) -> str:
    """Залить файл в репо и вернуть публичный jsDelivr URL (immutable, @commit_sha).
    Бросает RuntimeError при проблеме."""
    p = pathlib.Path(video_path)
    if not p.exists():
        raise RuntimeError(f"файла нет: {video_path}")
    size_mb = p.stat().st_size / 1e6
    if size_mb > 50:
        raise RuntimeError(f"файл {size_mb:.1f}МБ > 50МБ (лимит jsDelivr) — пережми видео")

    repo = os.environ.get("MEDIA_REPO", REPO_DEFAULT).strip()
    token = _token()
    if not token:
        raise RuntimeError("нет GitHub-токена (env GITHUB_TOKEN или ~/.git-credentials)")

    name = dest_name or p.name
    gitpath = f"reels/{name}"
    content_b64 = base64.b64encode(p.read_bytes()).decode()
    h = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

    # если файл уже есть — нужен его sha для перезаписи
    sha = None
    g = requests.get(f"{API}/repos/{repo}/contents/{gitpath}", headers=h, timeout=30)
    if g.status_code == 200:
        sha = g.json().get("sha")

    body = {"message": f"add {name}", "content": content_b64}
    if sha:
        body["sha"] = sha
    r = requests.put(f"{API}/repos/{repo}/contents/{gitpath}", headers=h, json=body, timeout=120)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub upload {r.status_code}")
    commit_sha = r.json().get("commit", {}).get("sha")
    if not commit_sha:
        raise RuntimeError("нет commit sha в ответе GitHub")
    # immutable ссылка на конкретный коммит, доступна сразу
    return f"https://raw.githubusercontent.com/{repo}/{commit_sha}/{gitpath}"
