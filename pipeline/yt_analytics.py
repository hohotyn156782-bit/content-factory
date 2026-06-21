"""Реальное удержание YouTube (APV / retention) через YouTube Analytics API v2.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ: pipeline/analytics.py тянет YouTube **Data** API v3
(просмотры/лайки/комменты) — этого хватает для весов ниш по охвату, но там НЕТ
удержания. averageViewPercentage (APV — какой % ролика в среднем досматривают) и
estimatedMinutesWatched/averageViewDuration отдаёт ТОЛЬКО YouTube **Analytics** API v2
(youtubeAnalytics.v2.reports.query). Для коротких видео APV — главный сигнал качества:
именно удержание тащит ролик в рекомендации, просмотры — лишь следствие.

⚠️ НУЖЕН ОДНОРАЗОВЫЙ ПОВТОРНЫЙ OAuth-КОНСЕНТ. Текущий токен (adapters/youtube_auth.py)
выдан только под scope `youtube.upload` — Analytics API под ним вернёт 403 и APV не отдаст.
Нужен дополнительный scope `https://www.googleapis.com/auth/yt-analytics.readonly`.
ЧИНИТСЯ ЗА ОДИН РАЗ: добавить scope в adapters/youtube_auth.py (SCOPES) и переавторизоваться
локально (`python3 -m adapters.youtube_auth`) — браузер запросит согласие ещё раз, новый
yt_token.json будет содержать оба scope. Этот модуль НЕ падает без нужного scope: _service()
вернёт None и залогирует понятную подсказку, fetch()/channel_summary() вернут пусто.

⚠️ КОНТЕКСТ РФ: YouTube заблокирован в России → реальные APV-данные актуальны для англоязычных
ниш (ai_lifehacks_en и др.), диаспоры и аккаунта, оформленного из доступной юрисдикции
(напр. Армения). Для RU-аудитории под VK Analytics API смотри pipeline/analytics._vk_metrics.

CLI:
    python3 pipeline/yt_analytics.py          # печать channel_summary() за 28 дней
"""
import os

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

from pipeline import analytics  # noqa: E402 — PERF_LOG/DATA_DIR + регэксп video_id из url

# Scope, без которого Analytics API не отдаёт удержание. Должен присутствовать в yt_token.json.
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
# Все scope, под которыми мог быть выдан токен (upload — от загрузчика, analytics — нужный нам).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    ANALYTICS_SCOPE,
]

# YouTube Analytics API ограничивает video==... ~500 id за запрос — режем пачками с запасом.
_MAX_VIDEOS_PER_QUERY = 200


# ──────────────────────────── сервис / креды ────────────────────────────

def _imports():
    """Ленивые импорты google-клиента (тяжёлые, не нужны при импорте модуля)."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        return build, Credentials, Request
    except ImportError:
        raise RuntimeError(
            "Нет google-клиента. Установи: pip install --break-system-packages "
            "google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2")


def _token_file() -> pathlib.Path:
    """Тот же токен-файл, что у загрузчика (adapters/youtube_auth.py / adapters/youtube.py)."""
    return pathlib.Path(os.environ.get(
        "YT_TOKEN_FILE", str(pathlib.Path("~/.config/content-factory/yt_token.json").expanduser())))


def _has_analytics_scope(creds) -> bool:
    """Есть ли в кредах нужный scope. У старого upload-токена его НЕТ → надо переавторизоваться."""
    scopes = set(getattr(creds, "scopes", None) or [])
    return ANALYTICS_SCOPE in scopes


def _service():
    """Построить сервис youtubeAnalytics v2 на кредах из adapters/youtube_auth.py.

    Возвращает None (мягко, без падения), если:
      • google-клиент не установлен;
      • токен-файла нет (загрузчик ещё не авторизован);
      • в токене НЕТ scope yt-analytics.readonly — в этом случае логируем понятную подсказку.
    """
    try:
        build, Credentials, Request = _imports()
    except RuntimeError as e:
        core.log_error("yt_analytics._service.imports", e)
        return None

    tf = _token_file()
    if not tf.exists():
        core.log(f"yt_analytics: нет токена {tf} — сначала авторизуй загрузчик "
                 f"(python3 -m adapters.youtube_auth)", level="warn")
        return None

    try:
        # Грузим с расширенным набором scope: если токен под них и выдан — ок,
        # если нет — поймаем это явной проверкой _has_analytics_scope ниже.
        creds = Credentials.from_authorized_user_file(str(tf), SCOPES)
    except Exception as e:  # noqa: BLE001
        core.log_error("yt_analytics._service.load", e)
        return None

    if not _has_analytics_scope(creds):
        core.log(
            "yt_analytics: в токене нет scope yt-analytics.readonly → APV недоступен. "
            "Нужен повторный OAuth с этим scope: добавь его в adapters/youtube_auth.py (SCOPES) "
            "и запусти один раз локально: python3 -m adapters.youtube_auth",
            level="warn")
        return None

    try:
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            tf.write_text(creds.to_json(), encoding="utf-8")  # сохраним обновлённый access-токен
    except Exception as e:  # noqa: BLE001
        core.log_error("yt_analytics._service.refresh", e)
        return None

    try:
        return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    except Exception as e:  # noqa: BLE001
        core.log_error("yt_analytics._service.build", e)
        return None


# ──────────────────────────── даты / парсинг ────────────────────────────

def _date_range(days: int) -> tuple[str, str]:
    """startDate/endDate в формате YYYY-MM-DD (требование Analytics API). endDate = сегодня."""
    import datetime as dt
    end = core._now().date()
    start = end - dt.timedelta(days=max(1, days))
    return start.isoformat(), end.isoformat()


def _video_id(url_or_id: str) -> str | None:
    """Вытащить 11-символьный id ролика из url ИЛИ принять готовый id (как в analytics.py)."""
    import re
    if not url_or_id:
        return None
    if re.fullmatch(r"[\w-]{11}", url_or_id):
        return url_or_id
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/|/v/)([\w-]{11})", url_or_id)
    return m.group(1) if m else None


def _row_to_metrics(headers: list[str], row: list) -> dict:
    """Строка ответа reports.query → {views, apv, avg_view_sec, minutes} по columnHeaders."""
    idx = {h: i for i, h in enumerate(headers)}

    def g(name):
        i = idx.get(name)
        return row[i] if (i is not None and i < len(row)) else None

    return {
        "views": int(g("views") or 0),
        "apv": round(float(g("averageViewPercentage") or 0.0), 2),       # % досмотра (главный сигнал)
        "avg_view_sec": round(float(g("averageViewDuration") or 0.0), 1),  # средняя длит. просмотра, сек
        "minutes": round(float(g("estimatedMinutesWatched") or 0.0), 1),
    }


_METRICS = "views,averageViewPercentage,estimatedMinutesWatched,averageViewDuration"


# ──────────────────────────── публичный API ────────────────────────────

def fetch(video_ids: list[str], days: int = 28) -> dict:
    """APV/удержание по конкретным роликам за последние `days` дней.

    video_ids — список id ИЛИ url (распарсим). Возвращает
    {video_id: {"views":.., "apv":.., "avg_view_sec":.., "minutes":..}}.
    Пустой dict при недоступности (нет кредов/scope/клиента) — пайплайн не страдает.
    """
    ids = [v for v in (_video_id(x) for x in (video_ids or [])) if v]
    ids = list(dict.fromkeys(ids))  # дедуп, сохранив порядок
    if not ids:
        return {}
    svc = _service()
    if svc is None:
        return {}
    start, end = _date_range(days)
    out: dict[str, dict] = {}
    # бьём на пачки — фильтр video==... ограничен по числу id
    for i in range(0, len(ids), _MAX_VIDEOS_PER_QUERY):
        batch = ids[i:i + _MAX_VIDEOS_PER_QUERY]
        try:
            resp = svc.reports().query(
                ids="channel==MINE",
                startDate=start,
                endDate=end,
                metrics=_METRICS,
                dimensions="video",
                filters="video==" + ",".join(batch),
                maxResults=len(batch),
            ).execute()
        except Exception as e:  # noqa: BLE001
            core.log_error("yt_analytics.fetch.query", e, ids=len(batch))
            continue
        headers = [h.get("name") for h in resp.get("columnHeaders", [])]
        vidx = headers.index("video") if "video" in headers else 0
        for row in resp.get("rows", []) or []:
            vid = row[vidx]
            out[vid] = _row_to_metrics(headers, row)
    return out


def channel_summary(days: int = 28) -> dict:
    """Агрегат по всему каналу за `days` дней (без разбивки по роликам), те же метрики.

    Возвращает {"views":.., "apv":.., "avg_view_sec":.., "minutes":..} или {} при недоступности.
    """
    svc = _service()
    if svc is None:
        return {}
    start, end = _date_range(days)
    try:
        resp = svc.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics=_METRICS,
        ).execute()
    except Exception as e:  # noqa: BLE001
        core.log_error("yt_analytics.channel_summary.query", e)
        return {}
    headers = [h.get("name") for h in resp.get("columnHeaders", [])]
    rows = resp.get("rows", []) or []
    if not rows:
        return {}
    return _row_to_metrics(headers, rows[0])


# ──────────────────────────── обогащение лога ────────────────────────────

def enrich_performance_log(days: int = 28, recent: int = 200) -> int:
    """Дотянуть APV к недавним YouTube-записям в performance_log.jsonl.

    Совместимо с тем, как pipeline/analytics.py читает лог (jsonl, поля
    ts/content_id/niche/platform/url/views/...). Берём последние `recent` записей,
    из тех что platform=="youtube" и без поля "apv" вытаскиваем video_id из url,
    одним батчем дёргаем fetch() и ДОПИСЫВАЕМ снапшот-строки (apv + удержание).

    Дописываем НОВЫЕ строки (а не правим старые) — так дешевле и согласуется с
    _latest_per_target() в analytics.py: последний снапшот по (content_id, platform)
    перезапишет прежний, поэтому у ниши окажется запись уже с полем apv.

    Возвращает число обогащённых записей. 0 — если кредов/scope/данных нет (мягко).
    """
    perf_log = analytics.PERF_LOG
    if not perf_log.exists():
        return 0

    lines = [ln for ln in perf_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return 0

    import json
    rows = []
    for ln in lines[-recent:]:
        try:
            rows.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue

    # последняя YouTube-запись по content_id, у которой ещё нет apv → её и обогащаем
    want: dict[str, dict] = {}
    for r in rows:
        if r.get("platform") != "youtube" or not r.get("url"):
            continue
        if r.get("apv") is not None:   # уже обогащена ранее
            continue
        want[r.get("content_id")] = r

    if not want:
        return 0

    url_by_cid = {cid: r.get("url") for cid, r in want.items()}
    data = fetch(list(url_by_cid.values()), days=days)
    if not data:
        return 0

    ts = core._now().isoformat()
    n = 0
    analytics.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with perf_log.open("a", encoding="utf-8") as f:
        for cid, r in want.items():
            vid = _video_id(r.get("url"))
            m = data.get(vid)
            if not m:
                continue
            snap = {
                "ts": ts,
                "content_id": cid,
                "niche": r.get("niche"),
                "platform": "youtube",
                "url": r.get("url"),
                "virality": r.get("virality"),
                "views": m["views"] or r.get("views", 0),  # из Analytics, фолбэк на прежний Data-замер
                "likes": r.get("likes", 0),
                "comments": r.get("comments", 0),
                "apv": m["apv"],
                "avg_view_sec": m["avg_view_sec"],
                "minutes": m["minutes"],
            }
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            n += 1
    if n:
        core.log(f"yt_analytics: обогащено APV-записей: {n}", level="info")
    return n


if __name__ == "__main__":
    core.load_local_secrets()
    s = channel_summary()
    if not s:
        print("APV недоступен: нет токена / нет scope yt-analytics.readonly / нет данных. "
              "См. docstring модуля (нужен повторный OAuth с yt-analytics.readonly).")
    else:
        print(f"Канал за 28 дней: views={s['views']}  APV={s['apv']}%  "
              f"avg_view={s['avg_view_sec']}с  minutes={s['minutes']}")
