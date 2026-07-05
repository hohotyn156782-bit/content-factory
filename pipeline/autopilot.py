"""Оркестратор автопостинга v2 — раздельный контент по площадкам + TG-очередь YT/TikTok.

Выходы (output), крон разносит по дню:
  ig_vk   — видео (формат ig_vk) → Instagram Reels + VK Клипы (авто)
  text    — текст-история-СЕРИАЛ → Threads + VK сообщество (авто)
  youtube — видео (формат youtube) → TG Паше (ручная выкладка @youtibetiktok_bot)
  tiktok  — видео (формат tiktok)  → TG Даше

Каждый выход = СВОЯ тема (генератор выбирает) + формат под площадку. Запуск: factory.py v2 <output> [niche].
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402
from pipeline import build as builder, textpost, serials  # noqa: E402


def _accounts(niche_id: str, platforms: tuple) -> list:
    """connected+auto_post аккаунты ниши из panel.db, отфильтрованные по платформам.
    КРИТИЧНО: дополнительно пересекаем с niches.json['platforms'] — иначе бандл может содержать
    connected-аккаунт площадки, которую ниша НЕ должна использовать (напр. money_facts→VK-only,
    но в panel.db у него IG/Threads указывают на ЛИЧНЫЙ аккаунт владельца → утечка контента)."""
    allowed = set(core.get_niche(niche_id).get("platforms", []) or [])
    from panel import db
    out = []
    for b in db.list_bundles():
        if b.get("niche_id") != niche_id or b.get("status", "active") != "active":
            continue
        for a in b.get("accounts", []):
            p = a.get("platform")
            if (p in platforms and (not allowed or p in allowed)
                    and a.get("status") == "connected" and a.get("auto_post")):
                out.append(a)
    return out


def _eng_question(sc: dict) -> str:
    """1-й коммент = вовлекающий вопрос по теме (для YT/TikTok)."""
    from pipeline import script as S
    try:
        q = S._groq("Придумай ОДИН короткий вовлекающий вопрос зрителю по теме видео (чтобы хотелось "
                    "ответить в комментах). Только вопрос, без кавычек.",
                    f"Тема: {sc.get('topic', '')}\nХук: {sc.get('hook', '')}",
                    temp=0.8, max_tokens=80, json_mode=False)
        return (q or "").strip().strip('"') or "А ты что думаешь? Пиши в комментах 👇"
    except Exception:  # noqa: BLE001
        return "А ты что думаешь? Пиши в комментах 👇"


def _url(info):
    return info.get("url") if isinstance(info, dict) else info


# ───────── надёжность: идемпотентность, ретраи, леджер публикаций ─────────
_STATE = core.ROOT / "state"
_POSTED = _STATE / "posted.json"
_LEDGER = _STATE / "posts.jsonl"


def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    import os
    os.replace(tmp, path)


def _load_posted() -> dict:
    try:
        return json.loads(_POSTED.read_text(encoding="utf-8")) if _POSTED.exists() else {}
    except Exception as e:  # noqa: BLE001
        core.log_error("autopilot._load_posted", e)
        return {}


def already_posted(output: str, niche_id: str) -> bool:
    """Этот выход+ниша уже опубликованы СЕГОДНЯ? (анти-дубль при re-run/двойном кроне)."""
    day = core.today_str()[:10]
    return niche_id in (_load_posted().get(day, {}).get(output, []))


def _mark_posted(output: str, niche_id: str) -> None:
    day = core.today_str()[:10]
    d = _load_posted()
    d.setdefault(day, {}).setdefault(output, [])
    if niche_id not in d[day][output]:
        d[day][output].append(niche_id)
    for old in sorted(d.keys())[:-7]:      # держим только последние 7 дней
        d.pop(old, None)
    try:
        _atomic_write(_POSTED, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        core.log_error("autopilot._mark_posted", e)


def _entry(platform: str, account: dict, ok: bool, info) -> dict:
    """Запись леджера с ref (media_id/post_id для метрик) + secret_ref (ИМЯ env-токена, не значение)."""
    url = _url(info)
    ref = None
    if isinstance(info, dict):
        ref = info.get("id") or info.get("post_id")
    return {"platform": platform, "account": account.get("display_name") or account.get("ext_id"),
            "ok": ok, "url": url, "ref": ref,
            "secret_ref": account.get("secret_ref"), "ext_id": account.get("ext_id")}


def _ledger(output: str, niche_id: str, topic: str, entries: list) -> None:
    """Дописать УСПЕШНЫЕ публикации в state/posts.jsonl — фундамент аналитики (метрики по ref/url позже).
    entries — список dict от _entry() (или простых {'ok','url','platform','account'})."""
    day = core.today_str()
    rows = [json.dumps({"ts": day, "output": output, "niche": niche_id, "topic": topic, **e},
                       ensure_ascii=False)
            for e in entries if e.get("ok")]
    if not rows:
        return
    try:
        _STATE.mkdir(parents=True, exist_ok=True)
        with _LEDGER.open("a", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
    except Exception as e:  # noqa: BLE001
        core.log_error("autopilot._ledger", e)


_TRANSIENT = ("timeout", "timed out", "429", "500", "502", "503", "504",
              "temporarily", "connection", "reset", "rate limit", "try again")


def _retry_pub(fn, *args, attempts: int = 3, delays=(15, 45, 120)):
    """Публикация с ретраем ТОЛЬКО транзиентных сбоев (сеть/429/5xx). Постоянные (bad token) не ретраим."""
    import time
    ok, info = False, None
    for i in range(attempts):
        try:
            ok, info = fn(*args)
        except Exception as e:  # noqa: BLE001
            ok, info = False, str(e)[:160]
        if ok:
            return ok, info
        if not any(t in str(info).lower() for t in _TRANSIENT) or i == attempts - 1:
            return ok, info
        time.sleep(delays[min(i, len(delays) - 1)])
    return ok, info


def _qa_alert(output: str, niche_id: str, qa: dict) -> None:
    """Собранный ролик не прошёл QA → слот дня потерян. Критичный алерт владельцу (а не тихий ❌)."""
    try:
        import reporter
        why = ", ".join(qa.get("issues") or []) or "причина не указана"
        reporter.critical(f"⚠️ QA-брак · {output}/{niche_id}: {why}. Ролик собран, но не опубликован.")
    except Exception:  # noqa: BLE001
        pass


def post_ig_vk(niche_id: str) -> list:
    """Видео (формат ig_vk) → Instagram Reels + VK Клипы."""
    accs = _accounts(niche_id, ("instagram", "vk"))
    if not accs:
        return []
    res = builder.build_video(niche_id, platform="ig_vk")
    out_dir = pathlib.Path(res["dir"])
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    if (res.get("qa") or {}) and not (res.get("qa") or {}).get("ok", True):
        _qa_alert("ig_vk", niche_id, res.get("qa") or {})
        return [(f"ig_vk/{niche_id}", False, "QA не пройден")]
    video = meta["video"]
    r, led = [], []
    for a in accs:
        p = a["platform"]
        if p == "instagram":
            from adapters import instagram
            ok, info = _retry_pub(instagram.publish, video, meta, a)
        else:
            from adapters import vk_video
            ok, info = _retry_pub(vk_video.publish, video, meta, a)
        r.append((f"{p}/{a.get('display_name') or a.get('ext_id')}", ok, _url(info)))
        led.append(_entry(p, a, ok, info))
    _ledger("ig_vk", niche_id, meta.get("topic", ""), led)
    return r


def post_text(niche_id: str) -> list:
    """Текст-история-сериал → Threads + VK сообщество (стена)."""
    accs = _accounts(niche_id, ("threads", "vk"))
    if not accs:
        return []
    today = core.today_str()
    ser = serials.plan_episode(niche_id, today)
    niche = core.get_niche(niche_id)
    # ЕДИНАЯ тема истории на день: часть 2 берёт тему из состояния, часть 1 — генерим ОДНУ тему,
    # чтобы Threads и VK рассказывали ОДНУ историю (иначе часть 2 продолжит лишь одну из завязок).
    if ser and ser.get("part") == 2:
        topic = ser.get("topic", "")
    elif ser and ser.get("part") == 1:
        topic = textpost.day_topic(niche)
    else:
        topic = ""
    cache: dict = {}
    r, led = [], []
    for a in accs:
        p = a["platform"]
        plat = "threads" if p == "threads" else "vk"
        if plat not in cache:
            cache[plat] = textpost.generate_text(niche, plat, topic=topic, serial=ser)
        text = cache[plat]
        if p == "threads":
            from adapters import threads as th
            ok, info = _retry_pub(th.publish_text, text, a)
        else:
            from adapters import vk_video
            ok, info = _retry_pub(vk_video.publish_text, text, a)
        r.append((f"{p}/{a.get('display_name') or a.get('ext_id')}", ok, _url(info)))
        led.append(_entry(p, a, ok, info))
    _ledger("text", niche_id, topic or niche.get("title", ""), led)
    # Фиксируем эпизод ТОЛЬКО при полном успехе: частичный провал → назавтра повторим часть 1
    # (лучше, чем часть 2 без части 1 у площадки, которая вчера упала).
    if ser and r and all(ok for _, ok, _ in r):
        try:
            base = next(iter(cache.values()), "")
            if ser["part"] == 1:
                serials.record(niche_id, 1, today, topic=(topic or niche.get("title", "")),
                               premise=base[:300])
            else:
                serials.record(niche_id, 2, today)
        except Exception as e:  # noqa: BLE001
            core.log_error("autopilot.text.serial", e)
    return r


def queue_video(niche_id: str, target: str) -> list:
    """Видео формата target (youtube|tiktok) → TG-очередь админу."""
    from adapters import tg_queue
    res = builder.build_video(niche_id, platform=("youtube" if target == "youtube" else "tiktok"))
    out_dir = pathlib.Path(res["dir"])
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    if (res.get("qa") or {}) and not (res.get("qa") or {}).get("ok", True):
        _qa_alert(target, niche_id, res.get("qa") or {})
        return [(f"{target}/{niche_id}", False, "QA не пройден")]
    sc = json.loads((out_dir / "script.json").read_text(encoding="utf-8"))
    cap = meta.get("captions", {})
    if target == "youtube":
        title = (cap.get("youtube", {}) or {}).get("title") or sc.get("topic", "")
    else:
        title = ((cap.get("tiktok", {}) or {}).get("caption", "")[:150]) or sc.get("topic", "")
    tags = " ".join("#" + t.lstrip("#") for t in meta.get("hashtags", [])[:12])
    extras = {"thumb": meta.get("thumbnail"),
              "title_variants": meta.get("title_variants", []),
              "description": (meta.get("captions", {}).get("youtube", {}) or {}).get("description", "")}
    ok, info = tg_queue.send_item(target, meta["video"], title, tags, _eng_question(sc),
                                  channel=core.get_niche(niche_id).get("title", ""), niche=niche_id,
                                  extras=extras)
    # в леджер — как «в очереди» (URL появится позже, когда владелец выложит вручную)
    _ledger(target, niche_id, sc.get("topic", ""),
            [{"platform": target, "account": "tg-queue", "ok": ok, "url": None, "ref": None,
              "queued": True}])
    return [(f"{target}/{niche_id}", ok, info)]


def run(output: str, niche: str | None = None) -> list:
    fn = {"ig_vk": post_ig_vk, "text": post_text,
          "youtube": lambda n: queue_video(n, "youtube"),
          "tiktok": lambda n: queue_video(n, "tiktok")}.get(output)
    if not fn:
        raise SystemExit(f"неизвестный output: {output} (ig_vk|text|youtube|tiktok)")
    niches = [niche] if niche else [n["id"] for n in core.load_niches(only_enabled=True)]
    if output in ("youtube", "tiktok"):
        niches = [n for n in niches if core.get_niche(n).get("has_yt_tiktok")]
    allr = []
    for n in niches:
        print(f"— {output} · {n} —")
        if already_posted(output, n):
            print(f"  ⏭ уже опубликовано сегодня ({output}/{n}) — пропуск (анти-дубль)")
            continue
        try:
            rr = fn(n)
            for lbl, ok, url in rr:
                print(f"  {'✅' if ok else '❌'} {lbl}: {url}")
            allr += rr
            if any(ok for _, ok, _ in rr):     # хоть одна площадка успешна → метим день
                _mark_posted(output, n)
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {n}: {str(e)[:160]}")
            core.log_error(f"autopilot.{output}", e, niche=n)
    okn = sum(1 for _, ok, _ in allr if ok)
    try:
        import reporter
        reporter.send(f"🤖 <b>Автопилот v2: {output}</b> — ок {okn}/{len(allr)}")
    except Exception:  # noqa: BLE001
        pass
    return allr
