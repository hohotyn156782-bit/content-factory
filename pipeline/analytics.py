"""Обратная связь по эффективности (этап после публикации) — закрывает главный пробел бенчмарка.

Цикл: опубликованный контент → тянем РЕАЛЬНЫЕ метрики (просмотры/лайки/коммент) с площадок
по НАШИМ токенам (бесплатно) → пишем снапшоты в data/performance_log.jsonl → пересчитываем
веса ниш (какие реально растут) в data/niche_weights.json и сверяем прогноз Virality Score с
фактом. selector/factory читают веса и смещают генерацию в сторону того, что заходит.

Без живых публикаций модуль ничего не ломает: при отсутствии данных/токенов мягко пропускает.

CLI:
    python3 pipeline/analytics.py collect        # снять метрики по всем опубликованным
    python3 pipeline/analytics.py recalibrate     # пересчитать веса ниш
    python3 pipeline/analytics.py report          # сводка эффективности
"""
import re
import json
import urllib.parse
import urllib.request

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

DATA_DIR = core.DATA_ROOT / "data"   # на диск D (раньше было ROOT=диск C — рассинхрон/риск переполнения)
PERF_LOG = DATA_DIR / "performance_log.jsonl"
WEIGHTS = DATA_DIR / "niche_weights.json"

UA = {"User-Agent": "content-factory analytics"}


def _http_json(url: str, headers: dict | None = None) -> dict | None:
    return core.http_json(url, headers=headers)   # с повторами + логом


# ──────────────────────────── метрики по площадкам ────────────────────────────

def _youtube_metrics(url: str) -> dict | None:
    """Публичная статистика ролика по YOUTUBE_API_KEY (хватает videos.list?part=statistics)."""
    key = core.secret("YOUTUBE_API_KEY", required=False)
    if not key:
        return None
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/|/v/)([\w-]{11})", url)
    if not m:
        return None
    vid = m.group(1)
    api = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(
        {"part": "statistics", "id": vid, "key": key})
    data = _http_json(api)
    if not data or not data.get("items"):
        return None
    s = data["items"][0].get("statistics", {})
    return {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0))}


def _vk_metrics(url: str) -> dict | None:
    """VK wall.getById / video.get по нашему токену (VK_USER_TOKEN или VK_TOKEN)."""
    token = core.secret("VK_USER_TOKEN", required=False) or core.secret("VK_TOKEN", required=False)
    if not token:
        return None
    ver = "5.199"
    mw = re.search(r"wall(-?\d+_\d+)", url)
    mv = re.search(r"(?:clip|video)(-?\d+_\d+)", url)
    if mw:
        api = "https://api.vk.com/method/wall.getById?" + urllib.parse.urlencode(
            {"posts": mw.group(1), "access_token": token, "v": ver})
        data = _http_json(api)
        items = (data or {}).get("response", {})
        items = items.get("items", items) if isinstance(items, dict) else items
        if items:
            it = items[0]
            return {"views": (it.get("views") or {}).get("count", 0),
                    "likes": (it.get("likes") or {}).get("count", 0),
                    "comments": (it.get("comments") or {}).get("count", 0)}
    if mv:
        api = "https://api.vk.com/method/video.get?" + urllib.parse.urlencode(
            {"videos": mv.group(1), "access_token": token, "v": ver})
        data = _http_json(api)
        items = ((data or {}).get("response", {}) or {}).get("items", [])
        if items:
            it = items[0]
            return {"views": it.get("views", 0), "likes": (it.get("likes") or {}).get("count", 0),
                    "comments": (it.get("comments") or {}).get("count", 0)}
    return None


_FETCHERS = {"youtube": _youtube_metrics, "vk": _vk_metrics}


def metrics_for(platform: str, url: str) -> dict | None:
    fn = _FETCHERS.get(platform)
    if not fn or not url:
        return None
    try:
        return fn(url)
    except Exception:  # noqa: BLE001
        return None


# ──────────────────────────── сбор ────────────────────────────

def collect() -> int:
    """Снять метрики по всем опубликованным таргетам, дописать снапшоты в performance_log.jsonl."""
    core.load_local_secrets()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from panel import db
        rows = db.list_content()
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  нет доступа к базе: {e}")
        return 0
    ts = core._now().isoformat()
    n = 0
    with PERF_LOG.open("a", encoding="utf-8") as f:
        for c in rows:
            if c.get("status") not in ("published", "partial", "pending_manual"):
                continue
            targets = c.get("targets") or {}
            virality = (c.get("meta") or {}).get("virality", {}).get("score")
            for platform, t in targets.items():
                url = (t or {}).get("url")
                m = metrics_for(platform, url) if url else None
                if not m:
                    continue
                f.write(json.dumps({
                    "ts": ts, "content_id": c["id"], "niche": c.get("niche_id"),
                    "platform": platform, "url": url, "virality": virality, **m,
                }, ensure_ascii=False) + "\n")
                n += 1
    print(f"✓ снято метрик: {n}")
    return n


# ──────────────────────────── пересчёт весов ────────────────────────────

def _latest_per_target(rows: list[dict]) -> dict:
    """Последний снапшот по каждому (content_id, platform)."""
    out = {}
    for r in rows:
        out[(r["content_id"], r["platform"])] = r   # jsonl по порядку → последний перезапишет
    return out


def recalibrate(smoothing: float = 0.5) -> dict:
    """Веса ниш по среднему числу просмотров (нормировано к 1.0). Сглажены к 1.0, чтобы
    редкие выбросы не убивали нишу. Пишет data/niche_weights.json. Возвращает {niche: weight}."""
    if not PERF_LOG.exists():
        print("нет performance_log — нечего пересчитывать")
        return {}
    rows = [json.loads(ln) for ln in PERF_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
    latest = _latest_per_target(rows)
    agg: dict[str, list[int]] = {}
    by_niche: dict[str, list[dict]] = {}     # для APV-фактора удержания (YouTube Analytics API)
    for r in latest.values():
        if r.get("niche"):
            agg.setdefault(r["niche"], []).append(int(r.get("views", 0)))
            by_niche.setdefault(r["niche"], []).append(r)
    if not agg:
        print("нет данных по нишам")
        return {}
    avg = {k: (sum(v) / len(v)) for k, v in agg.items()}
    mean = (sum(avg.values()) / len(avg)) or 1.0

    def _apv_factor(niche_rows: list[dict]) -> float:
        """Множитель удержания: средний APV ниши / 50% (бенчмарк), клип 0.7..1.4.
        Записи без apv (площадки без удержания / нет yt-analytics scope) игнорируются."""
        apvs = [r["apv"] for r in niche_rows if r.get("apv") is not None]
        if not apvs:
            return 1.0
        return round(min(1.4, max(0.7, (sum(apvs) / len(apvs)) / 50.0)), 3)

    # вес = сглаженное отношение средних просмотров ниши к среднему × фактор удержания; клип 0.4..2.0
    weights = {}
    for k, a in avg.items():
        raw = a / mean
        w = (1 - smoothing) * 1.0 + smoothing * raw
        w *= _apv_factor(by_niche.get(k, []))     # удержание (APV) докручивает охват
        weights[k] = round(min(2.0, max(0.4, w)), 3)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WEIGHTS.write_text(json.dumps({"updated": core._now().isoformat(), "weights": weights,
                                   "avg_views": {k: round(v) for k, v in avg.items()},
                                   "samples": {k: len(v) for k, v in agg.items()}},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    print("✓ веса ниш:", weights)
    return weights


def weight_for(niche_id: str) -> float:
    """Множитель приоритета ниши (1.0 по умолчанию) — для selector/factory."""
    if not WEIGHTS.exists():
        return 1.0
    try:
        return float(json.loads(WEIGHTS.read_text(encoding="utf-8")).get("weights", {}).get(niche_id, 1.0))
    except Exception:  # noqa: BLE001
        return 1.0


def next_niche(exclude_recent: int = 0) -> str:
    """Выбрать нишу для следующего ролика взвешенно: вес из аналитики (что заходит) ×
    лёгкий штраф за недавно снятую нишу (разнообразие ленты). Без данных = равновероятно."""
    import random
    niches = core.load_niches(only_enabled=True)
    if not niches:
        return "ai_lifehacks"
    recent = []
    if exclude_recent:
        recent = [e.get("niche") for e in core.load_history()[-exclude_recent:]]
    weighted = []
    for n in niches:
        nid = n["id"]
        w = weight_for(nid)
        w *= 0.45 if nid in recent else 1.0   # недавнюю нишу слегка придерживаем
        weighted.append((nid, max(0.05, w)))
    total = sum(w for _, w in weighted)
    pick = random.uniform(0, total)
    acc = 0.0
    for nid, w in weighted:
        acc += w
        if pick <= acc:
            return nid
    return weighted[-1][0]


def summary() -> dict:
    """Структурированная сводка для панели: по нишам (просмотры/прогноз/вес) + итог.
    Пусто/нет токенов → пустой список (панель покажет «данных пока нет»)."""
    out = {"niches": [], "totals": {"snapshots": 0, "videos": 0, "views": 0}, "updated": None}
    if not PERF_LOG.exists():
        return out
    rows = [json.loads(ln) for ln in PERF_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
    latest = list(_latest_per_target(rows).values())
    by_niche: dict[str, list[dict]] = {}
    for r in latest:
        by_niche.setdefault(r.get("niche") or "?", []).append(r)
    for niche, items in by_niche.items():
        views = [i.get("views", 0) for i in items]
        vir = [i["virality"] for i in items if i.get("virality")]
        out["niches"].append({
            "niche": niche, "videos": len(items),
            "avg_views": round(sum(views) / len(views)) if views else 0,
            "total_views": sum(views),
            "avg_virality": round(sum(vir) / len(vir)) if vir else None,
            "weight": weight_for(niche),
        })
    out["niches"].sort(key=lambda x: -x["total_views"])
    out["totals"] = {"snapshots": len(rows), "videos": len(latest),
                     "views": sum(i.get("views", 0) for i in latest)}
    if WEIGHTS.exists():
        try:
            out["updated"] = json.loads(WEIGHTS.read_text(encoding="utf-8")).get("updated")
        except Exception:  # noqa: BLE001
            pass
    return out


def report() -> None:
    if not PERF_LOG.exists():
        print("performance_log пуст — ещё нет снятых метрик (публикаций/токенов)")
        return
    rows = [json.loads(ln) for ln in PERF_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
    latest = list(_latest_per_target(rows).values())
    by_niche: dict[str, list[dict]] = {}
    for r in latest:
        by_niche.setdefault(r.get("niche") or "?", []).append(r)
    print(f"Снапшотов: {len(rows)} | уникальных таргетов: {len(latest)}\n")
    for niche, items in sorted(by_niche.items(), key=lambda kv: -sum(i.get("views", 0) for i in kv[1])):
        views = [i.get("views", 0) for i in items]
        vir = [i["virality"] for i in items if i.get("virality")]
        avg_v = sum(views) / len(views) if views else 0
        avg_vir = sum(vir) / len(vir) if vir else 0
        print(f"  {niche:18} n={len(items):3}  avg_views={avg_v:8.0f}  "
              f"avg_virality={avg_vir:4.0f}  weight={weight_for(niche)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    core.load_local_secrets()
    if cmd == "collect":
        collect()
    elif cmd == "recalibrate":
        recalibrate()
    else:
        report()
