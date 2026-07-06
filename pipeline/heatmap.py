"""YouTube «Most Replayed» heatmap топ-видео конкурента → сид для генератора хуков.

Идея: у роликов ниши на YouTube есть кривая удержания (heatmap, «чаще всего
пересматриваемое»). Её пик — это объективно самый «цепляющий» момент чужого
успешного видео. Берём заголовок главы в точке пика как сид темы/хука для
pipeline/script.py — учимся у того, что УЖЕ удержало зрителя.

Скачивание видео НЕ нужно — только метаданные через yt_dlp (skip_download=True).
Часть видео не имеет heatmap (старые/мелкие) — это нормально, перебираем дальше.

Возвращаемый сид:
  {"peak_time", "peak_label", "title", "url", "duration", "source": "yt_heatmap"}
"""
import os
import re
import sys
import json
import time
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402


# yt_dlp иногда зависает на отдельном видео → ограничиваем число ПОЛНЫХ extract'ов,
# каждый под своим try/except, общий таймаут на сокет задаём в опциях.
_FLAT_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": "in_playlist",   # плоский список результатов поиска (быстро, без heatmap)
    "socket_timeout": 20,
    "retries": 1,                    # #10: режем встроенные ретраи yt-dlp до одного
    "extractor_retries": 1,
    "fragment_retries": 1,
    "noplaylist": True,
    "nocheckcertificate": True,
}
_FULL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "socket_timeout": 20,            # БЕЗ extract_flat — иначе heatmap не придёт
    "retries": 1,                    # #10: ограничиваем ретраи, чтобы не подвисать на видео
    "extractor_retries": 1,
    "fragment_retries": 1,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
}


def _yt_available() -> bool:
    """Стоит ли вообще дёргать yt_dlp. На GitHub Actions YouTube отдаёт бот-капчу
    ('Sign in to confirm you're not a bot') и extract может зависнуть на отдельном видео
    (наблюдали висяк ~75 мин → джоб убивался по 2-часовому лимиту). На CI heatmap-сид всё
    равно не придёт, поэтому пропускаем сеть и отдаём фолбэк мгновенно. Можно принудительно
    выключить где угодно через CF_NO_YTDLP=1."""
    if os.environ.get("CF_NO_YTDLP", "").strip().lower() in ("1", "true", "yes"):
        return False
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return False
    return True


def _chapter_label(chapters, peak_time: float) -> str:
    """Заголовок главы, в которую попадает момент пика. '' если глав нет/не попали."""
    if not chapters:
        return ""
    for ch in chapters:
        try:
            start = float(ch.get("start_time", 0) or 0)
            end = float(ch.get("end_time", 0) or 0)
        except (TypeError, ValueError):
            continue
        if start <= peak_time < end:
            return (ch.get("title") or "").strip()
    return ""


def _peak_from_info(info: dict) -> dict | None:
    """Из полного info одного видео достать пик heatmap. None если heatmap пуст/нет."""
    heatmap = info.get("heatmap") or []
    if not heatmap:
        return None
    # argmax по value (доля пересмотров) → start_time соответствующего сегмента
    best = None
    for seg in heatmap:
        try:
            val = float(seg.get("value", 0) or 0)
        except (TypeError, ValueError):
            continue
        if best is None or val > best[0]:
            best = (val, seg)
    if best is None:
        return None
    seg = best[1]
    try:
        peak_time = float(seg.get("start_time", 0) or 0)
    except (TypeError, ValueError):
        peak_time = 0.0
    try:
        duration = float(info.get("duration") or 0) or 0.0
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "peak_time": peak_time,
        "peak_label": _chapter_label(info.get("chapters"), peak_time),
        "title": (info.get("title") or "").strip(),
        "url": info.get("webpage_url") or info.get("original_url") or "",
        "duration": duration,
        "source": "yt_heatmap",
    }


def hook_seed(query: str, lang: str = "ru", max_videos: int = 3) -> dict | None:
    """Найти по `query` топ-видео YouTube и вернуть «момент пика удержания» как сид хука.

    Перебираем первые max_videos результатов; у первого же видео с непустым heatmap
    берём argmax по value. None — если ни у одного видео heatmap нет.
    `lang` пока носит характер подсказки (поиск идёт строкой запроса как есть).
    """
    query = (query or "").strip()
    if not query:
        return None
    if not _yt_available():          # CI/принудительно выкл. → без сети, без риска зависнуть
        return None
    try:
        import yt_dlp  # ленивый импорт внешней либы
    except Exception as e:  # noqa: BLE001
        core.log_error("heatmap.hook_seed import yt_dlp", e)
        return None

    max_videos = max(1, int(max_videos))
    # 1) плоский поиск — получить список кандидатов (id/url), без тяжёлых метаданных
    try:
        with yt_dlp.YoutubeDL(_FLAT_OPTS) as ydl:
            res = ydl.extract_info(f"ytsearch{max_videos}:{query}", download=False)
    except Exception as e:  # noqa: BLE001
        core.log_error(f"heatmap.hook_seed search '{query[:60]}'", e)
        return None

    entries = (res or {}).get("entries") or []
    if not entries:
        core.log(f"heatmap: по запросу '{query[:60]}' ничего не найдено", level="warn")
        return None

    # 2) для каждого кандидата отдельный ПОЛНЫЙ extract (БЕЗ extract_flat) — там heatmap.
    #    Не более max_videos полных extract'ов; каждый под своим try/except.
    checked = 0
    unavailable = 0                      # штатно недоступные видео — агрегируем, не спамим error
    for ent in entries:
        if checked >= max_videos:
            break
        if not ent:
            continue
        video_url = ent.get("url") or ent.get("webpage_url") or ent.get("id")
        if not video_url:
            continue
        if video_url and not str(video_url).startswith("http"):
            # extract_flat иногда отдаёт голый id → достроим watch-URL
            video_url = f"https://www.youtube.com/watch?v={video_url}"
        checked += 1
        try:
            with yt_dlp.YoutubeDL(_FULL_OPTS) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except Exception as e:  # noqa: BLE001 — битое/удалённое/гео-видео → пробуем следующее
            if _is_unavailable_err(e):   # штатная недоступность → тихо
                unavailable += 1
            else:                        # неожиданный сбой → видимый
                core.log_error(f"heatmap.hook_seed extract {core._safe_url(str(video_url))}", e)
            continue
        if not info:
            continue
        seed = _peak_from_info(info)
        if seed:
            core.log(
                "heatmap: найден пик удержания",
                level="info",
                query=query[:60],
                title=seed["title"][:80],
                peak_time=round(seed["peak_time"], 1),
                peak_label=seed["peak_label"][:60],
                url=seed["url"],
            )
            return seed
        # heatmap пуст у этого видео — это нормально, идём к следующему

    if unavailable:                      # одна агрегированная строка вместо N error'ов
        core.log(f"heatmap: пропущено {unavailable} недоступных видео по '{query[:60]}'",
                 level="warn")
    core.log(f"heatmap: ни у одного из {checked} видео по '{query[:60]}' нет heatmap",
             level="warn")
    return None


def peak_hooks(niche: dict, n: int = 2) -> list[dict]:
    """Собрать до n heatmap-сидов по ключевикам ниши (дедуп по url).

    Источник ключевиков: niche['keywords'] (список) либо niche['broll_hint'] (CSV).
    """
    n = max(0, int(n))
    if n == 0:
        return []
    keywords = niche.get("keywords")
    if not keywords:
        hint = niche.get("broll_hint", "") or ""
        keywords = [k.strip() for k in hint.split(",") if k.strip()]
    keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if not keywords:
        core.log("heatmap.peak_hooks: у ниши нет keywords/broll_hint", level="warn",
                 niche=niche.get("id", ""))
        return []

    seeds: list[dict] = []
    seen_urls: set[str] = set()
    for kw in keywords:
        if len(seeds) >= n:
            break
        seed = hook_seed(kw)
        if not seed:
            continue
        url = seed.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        seeds.append(seed)
    return seeds


# ──────────────────────────────────────────────────────────────────────────
# ВИРАЛЬНЫЙ БРИФ: реверс-инжиниринг ЛЕГАЛЬНЫХ сигналов чужих топ-Shorts.
#
# ЛЕГАЛЬНО: анализируем ТОЛЬКО метаданные (заголовки/просмотры/лайки/длительность/
# теги/heatmap) и публичные авто-субтитры — это идеи и факты, не охраняемое выражение.
# ПОД ЗАПРЕТОМ (бан площадок/копирайт): скачивание видеофайлов и реаплоад чужих
# пикселей. Здесь skip_download=True ВЕЗДЕ — ни один видеопоток не качается.
# ──────────────────────────────────────────────────────────────────────────

# Поиск-кандидаты берём плоско (быстро), полные метаданные добираем только у топа.
_BRIEF_FLAT_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": True,            # только список результатов поиска, без тяжёлого extract
    "socket_timeout": 20,
    "retries": 1,                    # #10: ограничиваем ретраи yt-dlp
    "extractor_retries": 1,
    "fragment_retries": 1,
    "noplaylist": True,
    "nocheckcertificate": True,
}
# Полный extract топ-кандидата (для heatmap нельзя extract_flat). Субтитры — метаданные ссылок,
# сам текст качаем точечно через core.http_json (см. _sub_first_lines), без видеопотока.
_BRIEF_FULL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "socket_timeout": 20,
    "writesubtitles": False,
    "writeautomaticsub": False,
    "retries": 1,                    # #10: ограничиваем ретраи, чтобы не подвисать на видео
    "extractor_retries": 1,
    "fragment_retries": 1,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
}

# TTL кэша viral_brief: содержательный бриф живёт сутки, деградированный (title-only
# фолбэк) — лишь ~2ч, чтобы один неудачный поиск не отравлял сид хуков на день.
_BRIEF_TTL_FULL = 24 * 3600
_BRIEF_TTL_DEGRADED = 2 * 3600

# Типовые «хук-каркасы»: вычищаем из заголовка конкретику, оставляя приём/начало фразы.
_HOOK_LEADS = (
    "как", "почему", "что", "топ", "сколько", "когда", "где", "кто", "зачем",
    "это", "вот", "если", "никогда", "хватит", "правда", "секрет", "ошибка",
    "how", "why", "what", "top", "the", "this", "stop", "never", "you", "your",
    "i ", "we ", "they ", "everyone", "nobody", "secret", "mistake", "truth",
)
_STOP_WORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все",
    "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по",
    "это", "от", "для", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вот",
    "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "the", "a", "an", "of", "to", "and", "in", "is", "it", "you", "for", "on",
    "with", "this", "that", "are", "as", "be", "at", "or", "your", "how", "why",
    "what", "shorts", "short", "video", "youtube", "viral",
}


def _is_viral_entry(info: dict, max_age_days: int) -> bool:
    """Грубый фильтр виральности по метаданным: короткое + много просмотров/хороший engagement."""
    try:
        dur = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur and dur > 65:                 # только Shorts-формат (запас на округление)
        return False
    try:
        views = float(info.get("view_count") or 0)
    except (TypeError, ValueError):
        views = 0.0
    try:
        likes = float(info.get("like_count") or 0)
    except (TypeError, ValueError):
        likes = 0.0
    ratio = (likes / views) if views else 0.0
    # «реально вирусится»: либо большой охват, либо сильный engagement на меньшем охвате
    return views >= 50_000 or (views >= 10_000 and ratio >= 0.04)


def _hook_pattern(title: str) -> str:
    """Из заголовка вытащить «каркас хука»: первые слова до первой конкретики/числа.
    Возвращает приём (как НАЧИНАЕТСЯ зацепка), не сам заголовок. '' если непохоже на хук."""
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if not t:
        return ""
    low = t.lower()
    lead_ok = any(low.startswith(w) for w in _HOOK_LEADS) or bool(re.search(r"\d", t[:25]))
    if not lead_ok:
        return ""
    # обрезаем до ~6 слов и нормализуем числа в плейсхолдер (это приём, а не точная цифра)
    words = t.split()[:6]
    frag = " ".join(words)
    frag = re.sub(r"\d+([.,]\d+)?", "N", frag)        # 7 фактов → N фактов (паттерн, не копия)
    return frag.strip(" .—-:")[:60]


def _title_pattern(title: str) -> str:
    """Структурный «скелет» ВСЕГО заголовка топ-ролика (числа→N, ~9 слов) — проверенная ФОРМА
    заголовка ниши для вдохновения генератора, не копия. '' для слишком коротких."""
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if len(t.split()) < 3:
        return ""
    frag = " ".join(t.split()[:9])
    frag = re.sub(r"\d+([.,]\d+)?", "N", frag)
    return frag.strip(" .—-:")[:70]


def _subtopics(titles: list[str], top_k: int = 8) -> list[str]:
    """Частотные значимые слова из заголовков виральных роликов = горячие подтемы ниши."""
    freq: dict[str, int] = {}
    for t in titles:
        for w in re.findall(r"[\w']{3,}", str(t or "").lower(), flags=re.UNICODE):
            if w in _STOP_WORDS or w.isdigit():
                continue
            freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, c in ranked if c >= 2][:top_k] or [w for w, _ in ranked][:top_k]


def _sub_first_lines(info: dict, lang: str, max_lines: int = 4) -> str:
    """Первые строки авто-субтитров (= хук чужого ролика) через core.http_json-сосед: качаем
    ТОЛЬКО текстовый трек субтитров (json3), не видео. Дёшево и легально. '' при любом сбое."""
    try:
        subs = (info.get("automatic_captions") or {})
        # ищем дорожку нужного языка (ru/en/ru-orig/en-orig), иначе любую
        track = subs.get(lang) or subs.get(f"{lang}-orig") or subs.get("en") or subs.get("ru")
        if not track:
            for v in subs.values():
                if v:
                    track = v
                    break
        if not track:
            return ""
        url = ""
        for fmt in track:                                  # предпочитаем json3 (легко парсить)
            if fmt.get("ext") == "json3":
                url = fmt.get("url") or ""
                break
        url = url or (track[0].get("url") or "")
        if not url:
            return ""
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "ignore")
        lines: list[str] = []
        if "json3" in url or raw.lstrip().startswith("{"):
            data = json.loads(raw)
            for ev in (data.get("events") or []):
                seg = "".join(s.get("utf8", "") for s in (ev.get("segs") or []))
                seg = re.sub(r"\s+", " ", seg).strip()
                if seg:
                    lines.append(seg)
                if len(lines) >= max_lines:
                    break
        else:                                              # vtt/srt fallback: строки без таймкодов
            for ln in raw.splitlines():
                ln = ln.strip()
                if not ln or "-->" in ln or ln.isdigit() or ln.upper().startswith("WEBVTT"):
                    continue
                lines.append(re.sub(r"<[^>]+>", "", ln))
                if len(lines) >= max_lines:
                    break
        return core.sanitize_external(" ".join(lines[:max_lines]))
    except Exception:  # noqa: BLE001 — субтитры приятный бонус, не критичны
        return ""


# Маркеры ШТАТНОЙ недоступности видео (гео/удалено/приват/возраст): шум, не регрессия.
_UNAVAILABLE_MARKERS = (
    "not available",
    "video unavailable",
    "private video",
    "this video is private",
    "removed by the uploader",
    "account associated with this video has been terminated",
    "video has been removed",
    "who has blocked it",
    "not available in your country",
    "sign in to confirm your age",
    "age-restricted",
    "members-only",
    "join this channel",
    # #16: штатная недоступность/троттлинг — шум перебора, в агрегированный warn, не error-спам
    "unavailable",
    "not a bot",
    "http error 429",
    "http error 403",
    "too many requests",
    "requested format is not available",
    "unable to download webpage",
    "precondition check failed",
    "this video is no longer available",
    "content isn't available",
)


def _is_unavailable_err(exc: Exception) -> bool:
    """True для штатно недоступных видео (DownloadError 'video not available' и т.п.).

    Такие ошибки — ожидаемый шум перебора кандидатов, их логируем агрегированно,
    а не через core.log_error. Любые иные сбои считаем неожиданными → видимыми."""
    try:
        msg = str(exc).lower()
    except Exception:  # noqa: BLE001
        return False
    return any(m in msg for m in _UNAVAILABLE_MARKERS)


def viral_brief(niche_query: str, lang: str = "ru", n: int = 6, max_age_days: int = 30) -> dict:
    """ЛЕГАЛЬНЫЙ реверс-инжиниринг чужого вирала: по нише собрать сигналы (заголовки, горячие
    подтемы, хук-паттерны, пики удержания, примеры) для ВДОХНОВЕНИЯ нашей оригинальной генерации.

    Анализируем ТОЛЬКО метаданные + публичные авто-субтитры (skip_download=True) — НИКОГДА не
    качаем и не реаплоадим видео. Результат кэшируется на сутки в CACHE_DIR/viral_<slug>.json.
    Любой сбой → пустой dict (генерация НИКОГДА не падает из-за этого модуля).

    Return: {"top_titles":[...], "hot_subtopics":[...], "hook_patterns":[...],
             "peak_seconds":[...], "examples":[{title,views,url}]}
    """
    empty = {"top_titles": [], "hot_subtopics": [], "hook_patterns": [],
             "peak_seconds": [], "examples": []}
    query = (niche_query or "").strip()
    if not query:
        return empty

    # 1) КЭШ на сутки — не долбить yt-dlp на каждый ролик
    cache_path = None
    try:
        core.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = core.CACHE_DIR / f"viral_{core.slugify(query + '-' + lang)}.json"
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict):
                # _ttl записан в файле (сутки для полного, ~2ч для деградированного брифа)
                ttl = cached.get("_ttl") or _BRIEF_TTL_FULL
                try:
                    ttl = float(ttl)
                except (TypeError, ValueError):
                    ttl = _BRIEF_TTL_FULL
                if age < ttl:
                    cached.pop("_ttl", None)
                    return {**empty, **cached}
    except Exception:  # noqa: BLE001 — кэш необязателен, идём в сеть
        pass

    if not _yt_available():          # CI/принудительно выкл. → отдаём пустой бриф, не виснем на yt_dlp
        return empty

    try:
        import yt_dlp
    except Exception as e:  # noqa: BLE001
        core.log_error("heatmap.viral_brief import yt_dlp", e)
        return empty

    n = max(1, int(n))
    search_n = min(20, n * 3)            # берём с запасом, потом фильтруем по виральности

    # 2) Плоский поиск свежих Shorts ниши
    try:
        with yt_dlp.YoutubeDL(_BRIEF_FLAT_OPTS) as ydl:
            res = ydl.extract_info(f"ytsearch{search_n}:{query} shorts", download=False)
    except Exception as e:  # noqa: BLE001
        core.log_error(f"heatmap.viral_brief search '{query[:60]}'", e)
        return empty

    entries = [e for e in ((res or {}).get("entries") or []) if e]
    if not entries:
        core.log(f"viral_brief: по '{query[:60]}' ничего не найдено", level="warn")
        # пусто = деградированный результат → кэшируем лишь на ~2ч, не на сутки
        if cache_path is not None:
            try:
                payload = {**empty, "_ttl": _BRIEF_TTL_DEGRADED}
                core.safe_write(cache_path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return empty

    # 3) Полные метаданные у топа кандидатов (≤ n*2 extract'ов, каждый изолирован)
    #    #10: мягкий дедлайн всего цикла — что собрали к 45с, кэшируем (degraded), остальное пропускаем.
    full_infos: list[dict] = []
    max_full = min(len(entries), n * 2)
    checked = 0
    unavailable = 0                      # штатно недоступные (гео/удалённые/приват) — агрегируем
    deadline_hit = False                 # #10: сработал мягкий дедлайн → бриф деградированный
    _BRIEF_DEADLINE = 45                 # секунд на весь цикл extract'ов
    loop_start = time.time()
    for ent in entries:
        if len(full_infos) >= n or checked >= max_full:
            break
        if time.time() - loop_start > _BRIEF_DEADLINE:   # #10: общий дедлайн брифа
            deadline_hit = True
            break
        vid = ent.get("url") or ent.get("webpage_url") or ent.get("id")
        if not vid:
            continue
        if not str(vid).startswith("http"):
            vid = f"https://www.youtube.com/watch?v={vid}"
        checked += 1
        try:
            with yt_dlp.YoutubeDL(_BRIEF_FULL_OPTS) as ydl:
                info = ydl.extract_info(vid, download=False)
        except Exception as e:  # noqa: BLE001 — битое/гео/удалённое → следующее
            if _is_unavailable_err(e):   # штатная недоступность → тихо, не засоряем журнал
                unavailable += 1
            else:                        # неожиданный сбой → оставляем видимым (error-уровень)
                core.log_error(f"heatmap.viral_brief extract {core._safe_url(str(vid))}", e)
            continue
        if not info:
            continue
        if not _is_viral_entry(info, max_age_days):
            continue
        full_infos.append(info)

    if unavailable:                      # одна агрегированная строка вместо N error'ов
        core.log(f"viral_brief: пропущено {unavailable} недоступных видео по '{query[:60]}'",
                 level="warn")
    if deadline_hit:                     # #10: дедлайн → собрали частично, помечаем как degraded
        core.log(f"viral_brief: дедлайн {_BRIEF_DEADLINE}с по '{query[:60]}', "
                 f"собрано {len(full_infos)} видео — кэшируем частичный бриф",
                 level="warn")

    degraded = deadline_hit              # title-only фолбэк ИЛИ обрыв по дедлайну → деградированный
    if not full_infos:                   # фильтр всё срезал — мягко используем сырые заголовки
        full_infos = [e for e in entries[:n] if e.get("title")]
        degraded = True

    # 4) Извлекаем ЛЕГАЛЬНЫЕ сигналы
    titles, examples, peaks, hook_pats = [], [], [], []
    for info in full_infos:
        title = core.sanitize_external((info.get("title") or "").strip())
        if not title:
            continue
        titles.append(title)
        try:
            views = int(float(info.get("view_count") or 0))
        except (TypeError, ValueError):
            views = 0
        examples.append({
            "title": title[:120],
            "views": views,
            "url": info.get("webpage_url") or info.get("original_url") or "",
        })
        hp = _hook_pattern(title)
        if hp:
            hook_pats.append(hp)
        peak = _peak_from_info(info)     # переиспользуем существующий argmax по heatmap
        if peak and peak.get("peak_time"):
            peaks.append(round(float(peak["peak_time"]), 1))

    # 5) Авто-субтитры (= хук) у 1-2 верхних — дёшево и легально (текстовый трек, не видео)
    for info in full_infos[:2]:
        line = _sub_first_lines(info, lang)
        if line:
            hp = _hook_pattern(line)
            if hp:
                hook_pats.append(hp)

    def _dedup(seq):
        out, seen = [], set()
        for x in seq:
            k = x.lower() if isinstance(x, str) else x
            if k in seen:
                continue
            seen.add(k)
            out.append(x)
        return out

    title_pats = [p for p in (_title_pattern(t) for t in titles) if p]
    brief = {
        "top_titles": _dedup(titles)[:n],
        "hot_subtopics": _subtopics(titles),
        "hook_patterns": _dedup(hook_pats)[:8],
        "title_patterns": _dedup(title_pats)[:6],
        "peak_seconds": _dedup(peaks)[:6],
        "examples": examples[:n],
    }

    # 6) ГЕЙТ КЭША: содержательный бриф (есть примеры ИЛИ хук-паттерны) кэшируем на сутки;
    #    деградированный title-only фолбэк — лишь на ~2ч, чтобы не отравлять сид хуков на день.
    substantive = bool(brief["examples"] or brief["hook_patterns"])
    ttl = _BRIEF_TTL_FULL if (substantive and not degraded) else _BRIEF_TTL_DEGRADED
    if cache_path is not None:
        try:
            payload = {**brief, "_ttl": ttl}
            core.safe_write(cache_path, json.dumps(payload, ensure_ascii=False, indent=0).encode("utf-8"))
        except Exception:  # noqa: BLE001
            pass

    core.log("viral_brief: собран бриф ниши", level="info", query=query[:60],
             titles=len(brief["top_titles"]), subtopics=len(brief["hot_subtopics"]),
             hooks=len(brief["hook_patterns"]), peaks=len(brief["peak_seconds"]))
    return brief


if __name__ == "__main__":
    core.load_local_secrets()
    demo = hook_seed("нейросети факты")
    if demo:
        print("\nСид хука по heatmap:")
        for k, v in demo.items():
            print(f"  {k}: {v}")
    else:
        print("heatmap-сид не найден (ни у одного видео нет heatmap или нет сети)")
