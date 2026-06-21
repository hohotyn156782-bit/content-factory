"""Банк фоновой музыки из ccMixter (CC-лицензии, без API-ключа).

ВНИМАНИЕ по монетизации: ccMixter-треки чаще всего CC-BY → требуют АТРИБУЦИИ.
Мы пишем источник+автора+лицензию в assets/music/CREDITS.txt; при публикации укажи в описании.
Музыка ВЫКЛЮЧЕНА в пайплайне по умолчанию (build._pick_music читает пустую папку assets/music) —
чтобы не ловить Content ID на монетизируемых каналах. Запусти этот модуль вручную, чтобы наполнить
банк, и только тогда музыка начнёт подмешиваться (с sidechain-дакингом под голос).

Использование:
    python3 pipeline/music.py            # скачать ~8 инструменталов в assets/music
    python3 pipeline/music.py 12 chill   # N штук по тегу
"""
import json
import hashlib
import pathlib
import urllib.parse
import urllib.request

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

API = "http://ccmixter.org/api/query"
UA = {"User-Agent": "Mozilla/5.0 (content-factory music fetcher)"}
MUSIC_DIR_MAX_MB = 500       # мягкий потолок банка музыки (НЕ удаляем старое, просто стоп)


def _existing_hashes(mdir: pathlib.Path) -> set[str]:
    """md5[:8] всех уже лежащих в банке аудио-файлов — для дедупа по содержимому."""
    out = set()
    for p in mdir.glob("*"):
        if p.is_file() and p.suffix.lower() in (".mp3", ".m4a", ".ogg"):
            try:
                out.add(hashlib.md5(p.read_bytes()).hexdigest()[:8])
            except Exception:  # noqa: BLE001
                continue
    return out


def _dir_size_mb(mdir: pathlib.Path) -> float:
    total = 0
    for p in mdir.glob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except Exception:  # noqa: BLE001
                continue
    return total / (1024 * 1024)


def _query(tags: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "f": "json", "tags": tags, "limit": limit,
        "sort": "rank", "type": "1",  # только то, что можно скачать
    })
    req = urllib.request.Request(f"{API}?{params}", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _best_file(upload: dict) -> dict | None:
    """Выбрать mp3/ogg-файл из записи ccMixter."""
    for f in upload.get("files", []):
        name = (f.get("download_url") or "").lower()
        if name.endswith((".mp3", ".m4a", ".ogg")):
            return f
    return None


def fetch(n: int = 8, tags: str = "instrumental") -> list[pathlib.Path]:
    core.ensure_dirs()
    mdir = core.MUSIC_DIR
    mdir.mkdir(parents=True, exist_ok=True)
    try:
        uploads = _query(tags, n * 3)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  ccMixter недоступен: {e}")
        return []

    saved, credits = [], []
    seen_hashes = _existing_hashes(mdir)   # дедуп по содержимому (даже если slug другой)
    for up in uploads:
        if len(saved) >= n:
            break
        # Мягкий потолок банка: не удаляем старое, просто прекращаем докачку.
        if _dir_size_mb(mdir) >= MUSIC_DIR_MAX_MB:
            msg = f"банк музыки достиг {MUSIC_DIR_MAX_MB} МБ — докачка остановлена"
            print(f"⚠️  {msg}")
            core.log(msg, level="warn")
            break
        f = _best_file(up)
        if not f:
            continue
        url = f["download_url"]
        ext = pathlib.Path(urllib.parse.urlparse(url).path).suffix or ".mp3"
        slug = core.slugify(up.get("upload_name", up.get("upload_id", "track")))[:40]
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if len(data) < 50_000:        # битый/слишком короткий файл — пропускаем
                continue
            h = hashlib.md5(data).hexdigest()[:8]
            if h in seen_hashes:          # такой же контент уже в банке — пропускаем
                continue
            dest = mdir / f"{slug}-{h}{ext}"
            if dest.exists():
                continue
            dest.write_bytes(data)
            seen_hashes.add(h)
            saved.append(dest)
            credits.append(f"- {up.get('upload_name')} — {up.get('user_name')} "
                           f"({up.get('license_name', 'CC')}): {up.get('file_page_url', '')}")
            print(f"✓ {dest.name}")
        except Exception as e:  # noqa: BLE001
            print(f"  пропуск {url}: {e}")

    if credits:
        cr = mdir / "CREDITS.txt"
        head = ("Фоновая музыка — ccMixter (CC-лицензии). При публикации укажи атрибуцию в описании:\n\n")
        cr.write_text(head + "\n".join(credits) + "\n", encoding="utf-8")
    print(f"\nГотово: {len(saved)} треков в {mdir}")
    return saved


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    tags = sys.argv[2] if len(sys.argv) > 2 else "instrumental"
    fetch(n, tags)
