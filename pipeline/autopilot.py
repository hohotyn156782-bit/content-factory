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
    """connected+auto_post аккаунты ниши из panel.db, отфильтрованные по платформам."""
    from panel import db
    out = []
    for b in db.list_bundles():
        if b.get("niche_id") != niche_id or b.get("status", "active") != "active":
            continue
        for a in b.get("accounts", []):
            if (a.get("platform") in platforms and a.get("status") == "connected"
                    and a.get("auto_post")):
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


def post_ig_vk(niche_id: str) -> list:
    """Видео (формат ig_vk) → Instagram Reels + VK Клипы."""
    accs = _accounts(niche_id, ("instagram", "vk"))
    if not accs:
        return []
    res = builder.build_video(niche_id, platform="ig_vk")
    out_dir = pathlib.Path(res["dir"])
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    if (res.get("qa") or {}) and not (res.get("qa") or {}).get("ok", True):
        return [(f"ig_vk/{niche_id}", False, "QA не пройден")]
    video = meta["video"]
    r = []
    for a in accs:
        p = a["platform"]
        try:
            if p == "instagram":
                from adapters import instagram
                ok, info = instagram.publish(video, meta, a)
            else:
                from adapters import vk_video
                ok, info = vk_video.publish(video, meta, a)
        except Exception as e:  # noqa: BLE001
            ok, info = False, str(e)[:160]
        r.append((f"{p}/{a.get('display_name') or a.get('ext_id')}", ok, _url(info)))
    return r


def post_text(niche_id: str) -> list:
    """Текст-история-сериал → Threads + VK сообщество (стена)."""
    accs = _accounts(niche_id, ("threads", "vk"))
    if not accs:
        return []
    today = core.today_str()
    ser = serials.plan_episode(niche_id, today)
    niche = core.get_niche(niche_id)
    topic = ser.get("topic") if (ser and ser.get("part") == 2) else ""
    cache: dict = {}
    r = []
    for a in accs:
        p = a["platform"]
        plat = "threads" if p == "threads" else "vk"
        if plat not in cache:
            cache[plat] = textpost.generate_text(niche, plat, topic=topic, serial=ser)
        text = cache[plat]
        try:
            if p == "threads":
                from adapters import threads as th
                ok, info = th.publish_text(text, a)
            else:
                from adapters import vk_video
                ok, info = vk_video.publish_text(text, a)
        except Exception as e:  # noqa: BLE001
            ok, info = False, str(e)[:160]
        r.append((f"{p}/{a.get('display_name') or a.get('ext_id')}", ok, _url(info)))
    if ser and any(ok for _, ok, _ in r):
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
        return [(f"{target}/{niche_id}", False, "QA не пройден")]
    sc = json.loads((out_dir / "script.json").read_text(encoding="utf-8"))
    cap = meta.get("captions", {})
    if target == "youtube":
        title = (cap.get("youtube", {}) or {}).get("title") or sc.get("topic", "")
    else:
        title = ((cap.get("tiktok", {}) or {}).get("caption", "")[:150]) or sc.get("topic", "")
    tags = " ".join("#" + t.lstrip("#") for t in meta.get("hashtags", [])[:12])
    ok, info = tg_queue.send_item(target, meta["video"], title, tags, _eng_question(sc),
                                  channel=core.get_niche(niche_id).get("title", ""), niche=niche_id)
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
        try:
            rr = fn(n)
            for lbl, ok, url in rr:
                print(f"  {'✅' if ok else '❌'} {lbl}: {url}")
            allr += rr
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
