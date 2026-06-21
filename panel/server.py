"""Панель управления Content Factory — FastAPI (локально, открывается как сайт).

Запуск:  bash run_panel.sh   →  http://127.0.0.1:8765
Управляет связками (IG+Threads+VK+TikTok+YouTube), очередью контента и публикацией.
Генерация роликов — в фоновом потоке (XTTS медленный), UI опрашивает статус.
"""
import sys
import json
import pathlib
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import JSONResponse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import core  # noqa: E402
import db  # noqa: E402
from pipeline import build as builder  # noqa: E402
from pipeline import textpost  # noqa: E402
from pipeline import script as scriptmod  # noqa: E402
from pipeline import selector  # noqa: E402

STATIC = pathlib.Path(__file__).resolve().parent / "static"

core.load_local_secrets()
db.init_db()
db.reap_stuck_generating()   # при старте чистим контент, зависший в 'generating' после прошлых крашей

app = FastAPI(title="Content Factory Panel")


# ──────────────────────────── анти-CSRF (локальная панель) ────────────────────────────
# Браузер шлёт Origin на cross-site fetch — страница атакующего не может его подделать.
# Мутирующие методы пускаем только при совпадении Origin/Referer с локальным хостом.
ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}


@app.middleware("http")
async def _csrf_guard(request, call_next):
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        ref = request.headers.get("referer", "")
        ok = (origin in ALLOWED_ORIGINS) or any(ref.startswith(o) for o in ALLOWED_ORIGINS)
        if not ok:
            return JSONResponse({"error": "CSRF: bad origin"}, status_code=403)
    return await call_next(request)


# ──────────────────────────── модели запросов ────────────────────────────

class BundleIn(BaseModel):
    name: str
    niche_id: str


class BundlePatch(BaseModel):
    name: str | None = None
    niche_id: str | None = None
    status: str | None = None


class AccountPatch(BaseModel):
    display_name: str | None = None
    url: str | None = None
    status: str | None = None
    auto_post: int | None = None
    subscribers: int | None = None
    kind: str | None = None
    secret_ref: str | None = None
    ext_id: str | None = None


class GenerateIn(BaseModel):
    bundle_id: int
    topic: str | None = None


class MarkPostedIn(BaseModel):
    platform: str


# ──────────────────────────── статика / индекс ────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ──────────────────────────── API: справочники ────────────────────────────

@app.get("/api/niches")
def api_niches():
    # только активные ниши — деактивированные не предлагаем для новых связок (вернуть = enabled:true)
    return [{"id": n["id"], "title": n["title"], "lang": n["lang"],
             "engine": n.get("engine", "edge"), "voice": n.get("voice", "")}
            for n in core.load_niches(only_enabled=True)]


@app.get("/api/overview")
def api_overview():
    return db.overview()


@app.get("/api/analytics")
def api_analytics():
    from pipeline import analytics
    return analytics.summary()


@app.get("/api/health")
def api_health():
    """Здоровье системы: диск, LLM-каскад, последняя сборка, БД тем. Для мониторинга/алертов."""
    from pipeline import llm, topics_db
    hist = core.load_history()
    builds = [e for e in hist if e.get("status") == "built"]
    last = builds[-1] if builds else None
    llm_ready = sum(1 for s in llm.status() if s["ready_keys"])
    free = core.free_space_mb()
    try:
        topics_n = topics_db.stats().get("total", 0)
    except Exception:  # noqa: BLE001
        topics_n = 0
    # Этот эндпоинт без аутентификации → не раскрываем имена провайдеров и число ключей.
    # Только сводка: ok / диск / число готовых LLM-провайдеров (число, не имена).
    return {
        "ok": free > core.MIN_FREE_MB and llm_ready > 0,
        "disk_free_mb": free,
        "disk_ok": free > core.MIN_FREE_MB,
        "llm_ready_providers": llm_ready,
        "topics_in_db": topics_n,
        "last_build": ({"topic": last.get("topic"), "niche": last.get("niche"), "ts": last.get("ts")}
                       if last else None),
        "generating_now": db.overview().get("generating", 0),
    }


@app.get("/api/heartbeat")
def api_heartbeat():
    """Свежесть последнего цикла автопилота (для мониторинга/алертов)."""
    return core.check_heartbeat(max_age_sec=1800)


@app.get("/api/platforms")
def api_platforms():
    return [{"key": p, "label": db.PLATFORM_LABEL[p], "auto": bool(db.AUTO_DEFAULT[p])}
            for p in db.PLATFORMS]


# ──────────────────────────── API: связки ────────────────────────────

@app.get("/api/bundles")
def api_bundles():
    return db.list_bundles()


@app.post("/api/bundles")
def api_create_bundle(body: BundleIn):
    bid = db.create_bundle(body.name.strip(), body.niche_id)
    return db.get_bundle(bid)


@app.get("/api/bundles/{bid}")
def api_bundle(bid: int):
    b = db.get_bundle(bid)
    if not b:
        raise HTTPException(404, "связка не найдена")
    return b


@app.patch("/api/bundles/{bid}")
def api_update_bundle(bid: int, body: BundlePatch):
    db.update_bundle(bid, **{k: v for k, v in body.dict().items() if v is not None})
    return db.get_bundle(bid)


@app.delete("/api/bundles/{bid}")
def api_delete_bundle(bid: int):
    db.delete_bundle(bid)
    return {"ok": True}


@app.patch("/api/accounts/{aid}")
def api_update_account(aid: int, body: AccountPatch):
    db.update_account(aid, **{k: v for k, v in body.dict().items() if v is not None})
    return {"ok": True}


# ──────────────────────────── API: очередь / контент ────────────────────────────

@app.get("/api/queue")
def api_queue(bundle_id: int | None = None, status: str | None = None):
    return db.list_content(bundle_id=bundle_id, status=status)


def _seed_targets(bundle: dict) -> dict:
    targets = {}
    for acc in bundle["accounts"]:
        p = acc["platform"]
        if p == "tiktok":
            targets[p] = {"status": "pending_manual"}      # всегда вручную
        elif acc["auto_post"]:
            targets[p] = {"status": "queued"}
    return targets


def _cleanup_artifact(res: dict) -> None:
    """Удалить артефакты сборки (res dir/video), когда финализация не удалась — чтобы не копить мусор."""
    import shutil
    try:
        d = res.get("dir")
        if d and pathlib.Path(d).is_dir():
            shutil.rmtree(d, ignore_errors=True)
            return
        v = res.get("video")
        if v and pathlib.Path(v).exists():
            pathlib.Path(v).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _generate_worker(cid: int, bundle: dict, niche_id: str, topic: str | None):
    try:
        res = builder.build_video(niche_id, topic=topic or None)
        meta = res["meta"]
        caption = (meta.get("captions", {}).get("instagram", {}).get("caption")
                   or meta.get("topic", ""))
        targets = _seed_targets(bundle)
        ok = db.finalize_content(cid, video_path=res["video"], dir=res["dir"],
                                 duration=res["duration"], caption=caption, meta=meta,
                                 targets=targets, status="queued", topic=meta.get("topic", ""))
        if not ok:
            # строку уже зарипал reap (был краш/таймаут) → не ставим queued, убираем мусорный артефакт
            core.log("finalize: строка зарипана, пропуск", level="warn")
            _cleanup_artifact(res)
    except Exception as e:  # noqa: BLE001
        db.fail_content(cid, f"{type(e).__name__}: {e}")


@app.post("/api/content/generate")
def api_generate(body: GenerateIn):
    bundle = db.get_bundle(body.bundle_id)
    if not bundle:
        raise HTTPException(404, "связка не найдена")
    niche_id = bundle["niche_id"]
    cid = db.create_content(body.bundle_id, niche_id, topic=body.topic or "")
    threading.Thread(target=_generate_worker, args=(cid, bundle, niche_id, body.topic),
                     daemon=True).start()
    return {"id": cid, "status": "generating"}


def _crosslinks(bundle: dict, exclude: str) -> str:
    """Строка кросс-ссылок на остальные площадки связки (у кого задан url)."""
    parts = []
    for acc in bundle["accounts"]:
        if acc["platform"] == exclude:
            continue
        if acc.get("url"):
            parts.append(f"{db.PLATFORM_LABEL[acc['platform']]}: {acc['url']}")
    return ("\n\nМы тут: " + " · ".join(parts)) if parts else ""


def _adapter(platform: str):
    if platform == "youtube":
        from adapters import youtube as m
    elif platform == "instagram":
        from adapters import instagram as m
    elif platform == "threads":
        from adapters import threads as m
    elif platform == "vk":
        from adapters import vk_video as m
    elif platform == "tiktok":
        from adapters import tiktok as m
    else:
        return None
    return m


@app.post("/api/content/{cid}/publish")
def api_publish(cid: int):
    content = db.get_content(cid)
    if not content:
        raise HTTPException(404, "контент не найден")
    if content["status"] == "generating":
        raise HTTPException(409, "ещё генерируется")
    # QA-гейт: не публикуем ролик, не прошедший проверку (артефакты/глюки) — анти-брак
    qa = (content.get("meta") or {}).get("qa", {})
    if qa and not qa.get("ok", True):
        issues = "; ".join(qa.get("issues", [])[:3]) or "не пройден"
        raise HTTPException(409, f"QA не пройден ({issues}) — публикация заблокирована. Пересоздай ролик.")
    bundle = db.get_bundle(content["bundle_id"])
    acc_by_p = {a["platform"]: a for a in bundle["accounts"]}
    meta = content["meta"]
    targets = content["targets"]

    # Атомарная запись по каждой площадке (db.update_target сам пересчитывает сводный статус
    # content) — без RMW-гонки целой структуры targets.
    for p in list(targets.keys()):
        if p == "tiktok":
            db.update_target(cid, p, {"status": "pending_manual",
                                      "note": "выложить вручную (анти-бан): файл готов"})
            continue
        acc = acc_by_p.get(p, {})
        if acc.get("status") != "connected" or not acc.get("auto_post"):
            db.update_target(cid, p, {"status": "not_configured", "note": "аккаунт не подключён"})
            continue
        # подмешиваем кросс-ссылки в подпись этой площадки
        m = json.loads(json.dumps(meta))  # копия
        cap = m.get("captions", {}).get(p, {})
        link = _crosslinks(bundle, exclude=p)
        if isinstance(cap, dict):
            if "caption" in cap:
                cap["caption"] = (cap.get("caption", "") + link)[:2100]
            if "description" in cap:
                cap["description"] = (cap.get("description", "") + link)[:4900]
        adapter = _adapter(p)
        if adapter is None:
            db.update_target(cid, p, {"status": "failed", "error": f"нет адаптера {p}"})
            continue
        # маршрутизация по типу контента: текстовые площадки (Threads/VK-текст) → publish_text,
        # видео (Instagram/YouTube/VK-видео) → publish(video). Это чинит немой провал Threads/текста.
        acc_kind = acc.get("kind") or db.KIND_DEFAULT.get(p, "video")
        try:
            if acc_kind == "text" and hasattr(adapter, "publish_text"):
                text = (cap.get("caption") if isinstance(cap, dict) else "") or m.get("topic", "")
                ok, res = adapter.publish_text(text, acc)
            else:
                ok, res = adapter.publish(content["video_path"], m, acc)
            db.update_target(cid, p, {"status": "published" if ok else "failed",
                                      **(res if isinstance(res, dict) else {"result": str(res)})})
        except Exception as e:  # noqa: BLE001
            db.update_target(cid, p, {"status": "failed", "error": str(e)[:200]})

    return db.get_content(cid)


@app.post("/api/content/{cid}/mark_posted")
def api_mark_posted(cid: int, body: MarkPostedIn):
    content = db.get_content(cid)
    if not content:
        raise HTTPException(404, "контент не найден")
    targets = content["targets"]
    targets[body.platform] = {"status": "published", "note": "отмечено вручную"}
    auto = [v for k, v in targets.items() if k != "tiktok"]
    status = "published" if (auto and all(v.get("status") == "published" for v in auto)
                             and targets.get("tiktok", {}).get("status") == "published") else content["status"]
    db.set_targets(cid, targets, status if status == "published" else content["status"])
    return db.get_content(cid)


@app.delete("/api/content/{cid}")
def api_delete_content(cid: int):
    db.delete_content(cid)
    return {"ok": True}


@app.get("/api/content/{cid}/video")
def api_video(cid: int):
    content = db.get_content(cid)
    if not content or not content.get("video_path") or not pathlib.Path(content["video_path"]).exists():
        raise HTTPException(404, "видео не найдено")
    return FileResponse(content["video_path"], media_type="video/mp4",
                        filename=pathlib.Path(content["video_path"]).name)


# ──────────────────────────── API: аккаунты (мульти) ────────────────────────────

class AddAccountIn(BaseModel):
    platform: str


@app.post("/api/bundles/{bid}/accounts")
def api_add_account(bid: int, body: AddAccountIn):
    if body.platform not in db.PLATFORMS:
        raise HTTPException(400, "неизвестная платформа")
    db.add_account(bid, body.platform)
    return db.get_bundle(bid)


@app.delete("/api/accounts/{aid}")
def api_delete_account(aid: int):
    db.delete_account(aid)
    return {"ok": True}


# ──────────────────────────── API: план дня ────────────────────────────

class PlanBuildIn(BaseModel):
    date: str | None = None


class PlanPatch(BaseModel):
    topic: str | None = None
    slot_time: str | None = None
    text: str | None = None
    status: str | None = None


def _suggest_topics(niche: dict, n: int = 1) -> list[str]:
    """Заглушка под утренний парсер: пока темы предлагает Groq. Позже — новости/тренды."""
    is_ru = niche.get("lang", "ru") == "ru"
    sysp = ((f"Подбери {n} КОНКРЕТНЫХ актуальных тем для коротких видео в нише "
             f"«{niche.get('title')}» — {niche.get('topic_brief')}. Темы должны цеплять и могут залететь сегодня. "
             'Верни JSON {"topics": ["...", ...]}.') if is_ru else
            (f"Suggest {n} concrete trending topics for short videos in the niche "
             f"'{niche.get('title')}' — {niche.get('topic_brief')}. Return JSON {{\"topics\": [\"...\"]}}."))
    try:
        raw = scriptmod._groq(sysp, "Темы." if is_ru else "Topics.", max_tokens=300, json_mode=True)
        topics = [t for t in (json.loads(raw).get("topics") or []) if t]
        return topics[:n] or [""]
    except Exception:  # noqa: BLE001
        return [""] * n


@app.get("/api/bundles/{bid}/plan")
def api_plan(bid: int, date: str | None = None):
    return {"date": date or db.today(), "items": db.list_plan(bid, date or db.today())}


@app.post("/api/bundles/{bid}/plan/build")
def api_plan_build(bid: int, body: PlanBuildIn):
    bundle = db.get_bundle(bid)
    if not bundle:
        raise HTTPException(404, "связка не найдена")
    date = body.date or db.today()
    niche = core.get_niche(bundle["niche_id"])
    recent = core.recent_topics(bundle["niche_id"], days=14)
    picked = selector.pick_topics(niche, n=1, recent=recent)  # реальные тренды → тема
    topic = picked[0] if picked else _suggest_topics(niche, 1)[0]
    db.clear_plan(bid, date)
    for acc in bundle["accounts"]:
        kind = acc.get("kind") or db.KIND_DEFAULT.get(acc["platform"], "video")
        slot = db.slot_for(acc["platform"], date)  # время по дню недели из schedule.json
        db.create_plan_item(bid, date, acc["platform"], kind, slot, topic, source="parser")
    return {"date": date, "items": db.list_plan(bid, date)}


def _gen_plan_video(pid: int, bundle: dict, niche_id: str, topic: str):
    cid = db.create_content(bundle["id"], niche_id, topic)
    db.update_plan_item(pid, status="generating", content_id=cid)
    try:
        res = builder.build_video(niche_id, topic=topic or None)
        meta = res["meta"]
        caption = (meta.get("captions", {}).get("instagram", {}).get("caption") or meta.get("topic", ""))
        ok = db.finalize_content(cid, video_path=res["video"], dir=res["dir"], duration=res["duration"],
                                 caption=caption, meta=meta, targets=_seed_targets(bundle),
                                 status="queued", topic=meta.get("topic", ""))
        if not ok:
            # строку уже зарипал reap → не ставим ready, чистим артефакт
            core.log("finalize: строка зарипана, пропуск", level="warn")
            _cleanup_artifact(res)
            db.update_plan_item(pid, status="failed")
        else:
            db.update_plan_item(pid, status="ready", topic=meta.get("topic", topic))
    except Exception as e:  # noqa: BLE001
        db.fail_content(cid, f"{type(e).__name__}: {e}")
        db.update_plan_item(pid, status="failed")


@app.post("/api/plan/{pid}/generate")
def api_plan_generate(pid: int):
    item = db.get_plan_item(pid)
    if not item:
        raise HTTPException(404, "слот не найден")
    bundle = db.get_bundle(item["bundle_id"])
    niche = core.get_niche(bundle["niche_id"])
    if item["kind"] == "text":
        txt = textpost.generate_text(niche, item["platform"], item["topic"])
        db.update_plan_item(pid, text=txt, status="ready")
        return db.get_plan_item(pid)
    threading.Thread(target=_gen_plan_video, args=(pid, bundle, bundle["niche_id"], item["topic"]),
                     daemon=True).start()
    db.update_plan_item(pid, status="generating")
    return {"id": pid, "status": "generating"}


@app.patch("/api/plan/{pid}")
def api_plan_patch(pid: int, body: PlanPatch):
    db.update_plan_item(pid, **{k: v for k, v in body.dict().items() if v is not None})
    return db.get_plan_item(pid)


@app.post("/api/plan/{pid}/mark_posted")
def api_plan_mark_posted(pid: int):
    db.update_plan_item(pid, status="posted")
    return db.get_plan_item(pid)


@app.delete("/api/plan/{pid}")
def api_plan_delete(pid: int):
    db.delete_plan_item(pid)
    return {"ok": True}


# ──────────────────────────── запуск (жёстко на 127.0.0.1) ────────────────────────────

if __name__ == "__main__":
    import uvicorn
    # только loopback — панель не должна слушать на 0.0.0.0
    uvicorn.run(app, host="127.0.0.1", port=8765)
