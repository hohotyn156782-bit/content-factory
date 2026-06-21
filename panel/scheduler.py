"""Кроновый автопилот фабрики — ЗАГОТОВКА под полную реализацию.

Логика дня:
  • Утро 06:00 МСК  →  morning(): у КАЖДОЙ связки строится план дня и стартует генерация.
       Темы сейчас предлагает Groq (_parser_topics). TODO: заменить на реальный парсер
       новостей/трендов ДЛЯ КАЖДОЙ СВЯЗКИ (свой источник под нишу).
  • Каждые 15 мин →  tick(): дотягивает генерацию готовности и выкладывает слоты,
       чьё время наступило (авто-площадки; TikTok всегда вручную). Постинг включится,
       когда аккаунты будут подключены (status=connected).

Установка крона (crontab -e):
  0 6   * * *  cd ~/projects/content-factory && source ~/.config/content-engine/secrets.env && python3 -m panel.scheduler morning >> ~/.cache/cf-cron.log 2>&1
  */15 * * * *  cd ~/projects/content-factory && source ~/.config/content-engine/secrets.env && python3 -m panel.scheduler tick   >> ~/.cache/cf-cron.log 2>&1

Запуск вручную:  python3 -m panel.scheduler morning | tick
"""
import sys
import copy
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import core  # noqa: E402
import db  # noqa: E402
import reporter  # noqa: E402
from pipeline import build as builder, textpost, script as scriptmod, selector  # noqa: E402

core.load_local_secrets()
db.init_db()


def _caps() -> dict:
    """Дневные лимиты площадок из schedule.json (caps.{platform}.per_day) — анти-бан."""
    try:
        return json.loads((ROOT / "schedule.json").read_text(encoding="utf-8")).get("caps", {})
    except Exception:  # noqa: BLE001
        return {}


def _parser_topics(niche: dict, n: int = 1) -> list[str]:
    """ЗАГЛУШКА ПАРСЕРА: пока темы даёт Groq. Здесь будет реальный парсер новостей/трендов под нишу."""
    is_ru = niche.get("lang", "ru") == "ru"
    sysp = (f"Подбери {n} актуальных цепляющих тем для коротких видео в нише "
            f"«{niche.get('title')}» — {niche.get('topic_brief')}. JSON {{\"topics\":[\"...\"]}}.") if is_ru else (
            f"Suggest {n} trending topics for '{niche.get('title')}'. JSON {{\"topics\":[\"...\"]}}.")
    try:
        return [t for t in json.loads(scriptmod._groq(sysp, "Темы.", max_tokens=300)).get("topics", []) if t][:n] or [""]
    except Exception:  # noqa: BLE001
        return [""] * n


def morning(date: str | None = None) -> None:
    core.beat("morning")
    date = date or db.today()
    core.hc_ping("morning", "start")       # dead-man's switch: «утро запустилось»
    try:
        core.cleanup_cache(7)
        core.cleanup_outputs(21)
        core.cleanup_media(45)
    except Exception as e:  # noqa: BLE001 — сбой уборки не должен блокировать генерацию
        core.log_error("scheduler.morning.cleanup", e)
    reaped = db.reap_stuck_generating()    # вычистить зависшие после прошлых крашей
    if reaped:
        core.log(f"reaped зависших generating: {reaped}", level="warn")
    bundles = [b for b in db.list_bundles() if b.get("status", "active") == "active"]
    print(f"[morning] {date}: связок {len(bundles)}")
    built = 0
    for b in bundles:
        try:
            # идемпотентность: если план на дату уже есть и не доделан — не пересобираем (не жжём LLM-квоты)
            existing = db.list_plan(b["id"], date)
            if existing and any(it["status"] in ("planned", "ready", "awaiting_approval", "posted") for it in existing):
                print(f"  ↻ {b['name']}: план уже есть ({len(existing)} слотов), пропускаю")
                continue
            niche = core.get_niche(b["niche_id"])
            recent = core.recent_topics(b["niche_id"], days=14)
            picked = selector.pick_topics(niche, n=1, recent=recent)  # реальные тренды → тема
            topic = (picked[0] if picked else _parser_topics(niche, 1)[0])
            db.clear_plan(b["id"], date)
            for acc in b["accounts"]:
                kind = acc.get("kind") or db.KIND_DEFAULT.get(acc["platform"], "video")
                slot = db.slot_for(acc["platform"], date)  # время по дню недели из schedule.json
                db.create_plan_item(b["id"], date, acc["platform"], kind, slot, topic, source="parser")
            built += 1
            print(f"  ✓ {b['name']}: тема «{topic[:50]}» · слотов {len(b['accounts'])}")
        except Exception as e:  # noqa: BLE001 — падение одной связки не валит остальные
            core.log_error("scheduler.morning.bundle", e)
            continue
    # обратная связь: снять метрики вчерашних публикаций и пересчитать веса ниш (что заходит).
    # Мягко — без живых публикаций/токенов просто ничего не делает, пайплайн не страдает.
    try:
        from pipeline import analytics, yt_analytics
        analytics.collect()
        yt_analytics.enrich_performance_log()   # реальный APV/удержание (если есть yt-analytics scope)
        analytics.recalibrate()                 # веса ниш с учётом APV-фактора
    except Exception as e:  # noqa: BLE001
        core.log_error("scheduler.analytics", e)
    generate_planned(date)
    reporter.send(f"🌅 <b>Автопилот: утро {date}</b>\nСвязок активно: {len(bundles)} · план собран для {built} · генерация запущена.")
    core.hc_ping("morning", "success")          # «утро успешно завершилось» — иначе healthchecks поднимет алерт


def generate_planned(date: str | None = None) -> None:
    """Сгенерировать всё запланированное (видео + текст) — чтобы к временам было готово."""
    date = date or db.today()
    for b in db.list_bundles():
        niche = core.get_niche(b["niche_id"])
        for it in db.list_plan(b["id"], date):
            if it["status"] != "planned":
                continue
            cid = None
            try:
                if it["kind"] == "text":
                    txt = textpost.generate_text(niche, it["platform"], it["topic"])
                    db.update_plan_item(it["id"], text=txt, status="ready")
                    print(f"  📝 {b['name']}/{it['platform']}: текст готов")
                else:
                    cid = db.create_content(b["id"], b["niche_id"], it["topic"])
                    db.update_plan_item(it["id"], status="generating", content_id=cid)
                    res = builder.build_video(b["niche_id"], topic=it["topic"] or None)
                    meta = res["meta"]
                    qa_ok = res.get("qa", {}).get("ok", True)
                    fin_ok = db.finalize_content(cid, video_path=res["video"], dir=res["dir"], duration=res["duration"],
                                        caption=meta.get("captions", {}).get("instagram", {}).get("caption", ""),
                                        meta=meta, targets={},
                                        status="queued" if qa_ok else "qa_failed",
                                        topic=meta.get("topic", ""))
                    if not fin_ok:
                        # строку уже зарипали (напр. reap_stuck_generating параллельным процессом) —
                        # не помечаем слот ready, чтобы не публиковать осиротевший контент
                        core.log(f"{b['name']}/{it['platform']}: content {cid} зарипан, пропуск", level="warn")
                        continue
                    # QA не прошёл (даже после пересборки) → на ревью, НЕ публикуем.
                    # Если у связки включён ручной гейт (require_approval) — шлём владельцу в TG на одобрение
                    # (+ A/B заголовков кнопками); до «ок» слот в публикацию НЕ уходит.
                    if qa_ok and b.get("require_approval"):
                        try:
                            from adapters import tg_review
                            hooks = (meta.get("title_variants") or meta.get("hook_variants") or [])[:3]
                            cap = meta.get("captions", {}).get("instagram", {}).get("caption", "") or it["topic"]
                            tg_review.send_for_approval(cid, res["video"], cap, hooks=hooks)
                            db.update_plan_item(it["id"], status="awaiting_approval")
                        except Exception as e:  # noqa: BLE001 — гейт не должен ломать генерацию
                            core.log_error("tg_review.send", e)
                            db.update_plan_item(it["id"], status="ready")
                    else:
                        db.update_plan_item(it["id"], status="ready" if qa_ok else "qa_failed")
                    mark = "🎬" if qa_ok else "🛑"
                    note = f"({res['duration']:.0f}с)" if qa_ok else f"QA брак: {res['qa']['issues']}"
                    print(f"  {mark} {b['name']}/{it['platform']}: {note}")
            except Exception as e:  # noqa: BLE001
                if cid:
                    db.fail_content(cid, f"{type(e).__name__}: {e}")
                db.update_plan_item(it["id"], status="failed")
                print(f"  ✗ {b['name']}/{it['platform']}: {e}")


def _crosslinks(bundle: dict, exclude: str) -> str:
    parts = [f"{db.PLATFORM_LABEL.get(a['platform'], a['platform'])}: {a['url']}"
             for a in bundle["accounts"] if a["platform"] != exclude and a.get("url")]
    return ("\n\nМы тут — " + " · ".join(parts)) if parts else ""


def _meta_with_xlink(content: dict, platform: str, xlink: str) -> dict:
    m = copy.deepcopy(content.get("meta", {}) or {})
    if not xlink:
        return m
    caps = m.setdefault("captions", {}).setdefault(platform, {})
    base = caps.get("caption") or m.get("topic") or ""
    caps["caption"] = base + xlink
    return m


def _post_slot(it: dict, acc: dict, bundle: dict | None = None) -> tuple[bool, str]:
    """Реальная выкладка одного слота. Возвращает (ok, url_or_err).
    Вшиты VK (текст+видео) и Threads (текст). IG/YouTube — ждут адаптеров/кред."""
    platform = it["platform"]
    xlink = _crosslinks(bundle, platform) if bundle else ""

    # защита: видео, не прошедшее QA, НЕ публикуем (даже если статус слота где-то стал ready)
    if it["kind"] == "video" and it.get("content_id"):
        _c = db.get_content(it["content_id"])
        if _c and not _c.get("meta", {}).get("qa", {}).get("ok", True):
            return False, "QA не пройден — на ревью"

    if platform == "vk":
        from adapters import vk_video
        if it["kind"] == "text":
            ok, res = vk_video.publish_text((it.get("text") or "") + xlink, acc)
        else:
            content = db.get_content(it["content_id"]) if it.get("content_id") else None
            if not content or not content.get("video_path"):
                return False, "нет готового видео"
            ok, res = vk_video.publish(content["video_path"], _meta_with_xlink(content, "vk", xlink), acc)
        url = res.get("url") if isinstance(res, dict) else None
        return ok, (url or "ок") if ok else str(res)

    if platform == "threads":
        from adapters import threads as th
        if it["kind"] == "text":
            ok, res = th.publish_text((it.get("text") or "") + xlink, acc)
            url = res.get("url") if isinstance(res, dict) else None
            return ok, (url or "ок") if ok else str(res)
        return False, "Threads видео требует публичный URL — пока только текст"

    if platform == "telegram":
        from adapters import telegram as tg
        if it["kind"] == "text":
            ok, res = tg.publish_text((it.get("text") or "") + xlink, acc)
        else:
            content = db.get_content(it["content_id"]) if it.get("content_id") else None
            if not content or not content.get("video_path"):
                return False, "нет готового видео"
            ok, res = tg.publish(content["video_path"], _meta_with_xlink(content, "telegram", xlink), acc)
        url = res.get("url") if isinstance(res, dict) else None
        return ok, (url or "ок") if ok else str(res)

    if platform in ("instagram", "youtube"):
        content = db.get_content(it["content_id"]) if it.get("content_id") else None
        if not content or not content.get("video_path"):
            return False, "нет готового видео"
        from adapters import instagram, youtube
        m = instagram if platform == "instagram" else youtube
        ok, res = m.publish(content["video_path"], _meta_with_xlink(content, platform, xlink), acc)
        url = res.get("url") or res.get("id") if isinstance(res, dict) else None
        return ok, (url or "ок") if ok else str(res)

    return False, f"адаптер {platform} ещё не вшит"


def _mirror_to_content(it: dict, url: str) -> None:
    """B2: после успешного поста зеркалим статус/url в content — иначе analytics.collect() их не видит
    и петля обратной связи (метрики→веса ниш) на cron-пути молча мертва."""
    cid = it.get("content_id")
    if not cid:
        return
    c = db.get_content(cid)
    if not c:
        return
    tg = c.get("targets") or {}
    prev = tg.get(it["platform"], {}) or {}
    tg[it["platform"]] = {"status": "published",
                          "url": url if str(url).startswith("http") else prev.get("url", "")}
    auto = [v for k, v in tg.items() if k != "tiktok"]
    st = "published" if (auto and all(v.get("status") == "published" for v in auto)) else "partial"
    db.set_targets(cid, tg, st)


def _apply_approvals() -> None:
    """Забрать решения владельца из Telegram (approval-гейт) и применить: одобрено → ready
    (выбранный A/B-хук → в текст слота), отклонено → rejected (tick не публикует)."""
    try:
        from adapters import tg_review
        decisions = tg_review.poll_decisions(timeout=0)
    except Exception as e:  # noqa: BLE001
        core.log_error("tg_review.poll", e)
        return
    if not decisions:
        return
    today = db.today()
    for d in decisions:
        cid = str(d.get("content_id"))
        for b in db.list_bundles():
            for it in db.list_plan(b["id"], today):
                if it.get("status") != "awaiting_approval" or str(it.get("content_id")) != cid:
                    continue
                if d.get("decision") == "approve":
                    hi = d.get("hook_idx")
                    if hi is not None:
                        c = db.get_content(it["content_id"]) or {}
                        variants = ((c.get("meta", {}) or {}).get("title_variants")
                                    or (c.get("meta", {}) or {}).get("hook_variants") or [])
                        if isinstance(hi, int) and 0 <= hi < len(variants):
                            db.update_plan_item(it["id"], text=variants[hi])
                    db.update_plan_item(it["id"], status="ready")
                    print(f"  👍 одобрено: {b['name']}/{it['platform']} (cid {cid})")
                else:
                    db.update_plan_item(it["id"], status="rejected")
                    print(f"  👎 отклонено: {b['name']}/{it['platform']} (cid {cid})")


def tick(date: str | None = None) -> None:
    """Выложить слоты, чьё время наступило. Соблюдает дневные капы площадок (анти-бан),
    зеркалит результат в content (для аналитики), шлёт алерт в TG при полном простое."""
    core.beat("tick")
    core.hc_ping("tick", "start")
    date = date or db.today()
    # reap: под flock конкурентных tick нет → любой 'posting' = осиротевший после прошлого краха
    conn = db.get_conn()
    conn.execute("UPDATE plan SET status='ready' WHERE status='posting'")
    conn.commit()
    conn.close()
    _apply_approvals()             # применить решения владельца (approval-гейт) до раскладки
    # catch-up: если ПК спал и morning не отработал (плана на сегодня нет вовсе) — собрать его сейчас.
    if not any(db.list_plan(b["id"], date) for b in db.list_bundles()
               if b.get("status", "active") == "active"):
        core.log("tick: плана на сегодня нет — запускаю catch-up morning()", level="warn")
        try:
            morning(date)
        except Exception as e:  # noqa: BLE001
            core.log_error("tick.catchup_morning", e)
    now = db._now()[11:16]  # HH:MM МСК
    caps = _caps()
    posted = 0
    due_ready = 0          # сколько слотов были готовы и подошли по времени (для алерта о простое)
    fails = []
    # счётчик уже выложенного за день по (связка, площадка) — для соблюдения caps.per_day
    posted_cnt: dict = {}
    for b in db.list_bundles():
        for it in db.list_plan(b["id"], date):
            if it["status"] == "posted":
                posted_cnt[(b["id"], it["platform"])] = posted_cnt.get((b["id"], it["platform"]), 0) + 1
    for b in db.list_bundles():
        acc_by_p = {a["platform"]: a for a in b["accounts"]}
        for it in db.list_plan(b["id"], date):
            if it["status"] != "ready" or it["slot_time"] > now:
                continue
            p = it["platform"]
            acc = acc_by_p.get(p, {})
            if p == "tiktok":
                db.update_plan_item(it["id"], status="manual_pending")
                continue
            if acc.get("status") != "connected" or not acc.get("auto_post"):
                continue  # аккаунт не подключён — ждём настройки
            cap = (caps.get(p, {}) or {}).get("per_day")
            if cap is not None and posted_cnt.get((b["id"], p), 0) >= cap:
                print(f"  ⛔ {b['name']}/{p}: дневной кап {cap} достигнут — пропуск (анти-бан)")
                continue
            due_ready += 1
            # атомарный claim слота ТОЛЬКО после всех skip-проверок (анти-двойная-публикация)
            if not db.claim_plan_item(it["id"], "ready", "posting"):
                continue
            try:
                ok, info = _post_slot(it, acc, b)
            except Exception as e:  # noqa: BLE001
                ok, info = False, str(e)
            if ok:
                db.update_plan_item(it["id"], status="posted")
                _mirror_to_content(it, info)                       # B2: для аналитики
                posted_cnt[(b["id"], p)] = posted_cnt.get((b["id"], p), 0) + 1
                posted += 1
                print(f"  ✅ {b['name']}/{p}: {info}")
            else:
                db.update_plan_item(it["id"], status="ready")      # вернуть в очередь, ретрай на след. тике
                fails.append(f"{b['name']}/{p}: {info}")
                print(f"  ⏳ {b['name']}/{p}: {info}")
    print(f"[tick] {now}: выложено {posted}")
    # B3: тихий простой автопилота не должен оставаться незамеченным
    if due_ready and posted == 0:
        reporter.send(f"⚠️ <b>Автопилот {now}</b>: {due_ready} слотов готовы, но НИЧЕГО не выложено.\n"
                      + "\n".join(fails[:6]))
    elif posted:
        reporter.send(f"📤 <b>Автопилот {now}</b>: выложено {posted}"
                      + (f", сбоев {len(fails)}" if fails else ""))
    core.hc_ping("tick", "success")


if __name__ == "__main__":
    fh = core.acquire_lock()
    if fh is None:
        core.log("autopilot уже выполняется — пропуск", level="warn")
        sys.exit(0)
    try:
        cmd = sys.argv[1] if len(sys.argv) > 1 else "morning"
        try:
            {"morning": morning, "generate": generate_planned, "tick": tick}.get(cmd, morning)()
        except Exception as e:  # noqa: BLE001 — крон не должен падать молча
            core.log_error(f"scheduler.{cmd}", e)
            if cmd in ("morning", "tick"):
                core.hc_ping(cmd, "fail")     # healthchecks поднимет алерт о падении
            reporter.critical(f"🛑 <b>Автопилот упал</b> ({cmd}): {type(e).__name__}: {str(e)[:300]}")
            raise
    finally:
        core.release_lock(fh)
