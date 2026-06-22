"""Content Factory — CLI оркестратор.

  python3 factory.py doctor                 # что готово, чего не хватает
  python3 factory.py niches                 # список ниш
  python3 factory.py build [niche] [-t TEMA] # собрать 1 ролик (без постинга)
  python3 factory.py batch N [niche]        # собрать N роликов
  python3 factory.py post DIR [платформы]   # запостить уже собранный ролик
  python3 factory.py run [niche] [-t TEMA] [--dry]  # собрать + запостить + отчёт в TG
"""
import os
import sys
import json
import argparse
import pathlib

import core
import reporter
from pipeline import build as builder

YT_DAILY_CAP = 7  # скрытый лимит YouTube (ресёрч) — держим консервативно


def _adapter(platform: str):
    if platform == "youtube":
        from adapters import youtube as m
    elif platform == "instagram":
        from adapters import instagram as m
    elif platform == "tiktok":
        from adapters import tiktok as m
    elif platform == "vk":
        from adapters import vk_video as m
    else:
        return None
    return m


def _configured(platform: str) -> bool:
    if platform == "youtube":
        tf = os.environ.get("YT_TOKEN_FILE", str(pathlib.Path("~/.config/content-factory/yt_token.json").expanduser()))
        return pathlib.Path(tf).exists()
    if platform == "instagram":
        return bool(os.environ.get("IG_USER_ID") and os.environ.get("IG_ACCESS_TOKEN"))
    if platform == "tiktok":
        return True  # всегда хотя бы бандл
    if platform == "vk":
        from adapters import vk_video
        return bool(vk_video._targets())
    return False


def _yt_posted_today() -> int:
    day = core.today_str()
    return sum(1 for e in core.load_history()
               if e.get("type") == "post" and e.get("platform") == "youtube"
               and e.get("ok") and str(e.get("ts", "")).startswith(day))


def cmd_doctor(_args):
    core.load_local_secrets()
    print("=== ИНСТРУМЕНТЫ ===")
    import shutil
    for t in ("ffmpeg", "ffprobe"):
        print(f"  {t:10} {'✓' if shutil.which(t) else '✗ НЕТ'}")
    try:
        import edge_tts  # noqa
        print(f"  edge-tts   ✓ {edge_tts.__version__}")
    except ImportError:
        print("  edge-tts   ✗ НЕТ (pip install edge-tts)")
    print("\n=== КЛЮЧИ / ДОСТУПЫ ===")
    checks = [
        ("GROQ_API_KEY (сценарии)", bool(os.environ.get("GROQ_API_KEY")), "обязателен"),
        ("PEXELS_API_KEY (сток-видео)", bool(os.environ.get("PEXELS_API_KEY")), "иначе генеративный фон"),
        ("PIXABAY_API_KEY (сток-фолбэк)", bool(os.environ.get("PIXABAY_API_KEY")), "опц."),
        ("YouTube (yt_token.json)", _configured("youtube"), "полная автопубликация"),
        ("Instagram (IG_USER_ID+TOKEN)", _configured("instagram"), "+ хостинг R2"),
        ("VK (targets+токены)", _configured("vk"), "переиспользует паблики"),
        ("TG отчёты", bool(os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHAT_ID")), "опц."),
    ]
    for name, ok, note in checks:
        print(f"  {'✓' if ok else '○'} {name:34} {'' if ok else '— ' + note}")
    print(f"\n  YouTube сегодня загружено: {_yt_posted_today()}/{YT_DAILY_CAP}")
    print("\n=== LLM-ПРОВАЙДЕРЫ (каскад/фолбэк) ===")
    try:
        from pipeline import llm
        active = llm.configured()
        for s in llm.status():
            mark = {"ready": "✓", "cooldown": "⏳", "no_key": "○"}[s["state"]]
            print(f"  {mark} {s['name']:12} {s['state']}")
        if len(active) < 2:
            print("  ⚠️  активен только 1 провайдер — добавь CEREBRAS_API_KEY (работает из РФ без VPN, ~1М токенов/день) для устойчивости")
    except Exception as e:  # noqa: BLE001
        print(f"  (ошибка чтения провайдеров: {e})")


def cmd_niches(_args):
    for n in core.load_niches(only_enabled=False):
        flag = "✓" if n.get("enabled") else "○"
        print(f"  {flag} {n['id']:18} [{n['lang']}] {n['title']} → {', '.join(n.get('platforms', []))}")


def cmd_build(args):
    core.load_local_secrets()
    res = builder.build_video(args.niche, topic=args.topic)
    print(f"\n✅ {res['video']}  ({res['duration']:.1f}s)\n   {res['dir']}")


def cmd_batch(args):
    core.load_local_secrets()
    auto = str(args.niche).lower() == "auto"   # "auto" = взвешенная ротация ниш по аналитике
    if auto:
        from pipeline import analytics
    for i in range(args.n):
        nid = analytics.next_niche(exclude_recent=max(2, args.n // 2)) if auto else args.niche
        print(f"\n── ролик {i + 1}/{args.n} ── ниша: {nid}")
        try:
            res = builder.build_video(nid, topic=None)
            print(f"✅ {res['dir']}")
        except Exception as e:  # noqa: BLE001
            print(f"❌ {e}")


def _post_dir(out_dir: pathlib.Path, platforms: list[str] | None, dry: bool) -> dict:
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    video = meta["video"]
    # QA-гейт: не публикуем брак (как в панели) — анти-артефакты
    qa = meta.get("qa", {})
    if qa and not qa.get("ok", True) and not dry:
        issues = "; ".join(qa.get("issues", [])[:3]) or "не пройден"
        core.log(f"CLI-постинг отменён: QA не пройден ({issues})", level="warn", dir=out_dir.name)
        return {"_qa": {"ok": False, "error": f"QA не пройден ({issues})"}}, meta
    targets = platforms or meta.get("platforms", [])
    results: dict = {}
    for p in targets:
        if not _configured(p):
            results[p] = {"ok": False, "error": "не настроено"}
            continue
        # идемпотентность: уже успешно опубликовано в эту площадку → не дублируем (анти-double-post,
        # защита от ретрая/повторного запуска cmd_post на той же папке → шадоубан/дубль).
        prev = meta.get("posted", {}).get(p)
        if isinstance(prev, dict) and prev.get("ok") and not dry:
            results[p] = {**prev, "note": "уже опубликовано ранее (пропуск)"}
            continue
        if p == "youtube" and _yt_posted_today() >= YT_DAILY_CAP:
            results[p] = {"ok": False, "error": f"дневной кап {YT_DAILY_CAP} достигнут"}
            continue
        if dry:
            results[p] = {"ok": True, "note": "dry-run (пропущено)"}
            continue
        adapter = _adapter(p)
        try:
            ok, res = adapter.publish(video, meta)
            results[p] = {"ok": ok, **(res if isinstance(res, dict) else {"result": res})}
        except Exception as e:  # noqa: BLE001
            results[p] = {"ok": False, "error": str(e)[:200]}
        core.append_history({"type": "post", "platform": p, "niche": meta.get("niche"),
                             "ok": results[p].get("ok"), "dir": str(out_dir)})
    meta["posted"] = {**meta.get("posted", {}), **results}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return results, meta


def cmd_post(args):
    core.load_local_secrets()
    out_dir = pathlib.Path(args.dir)
    results, meta = _post_dir(out_dir, args.platforms or None, dry=False)
    for p, r in results.items():
        print(f"  {'✅' if r.get('ok') else '❌'} {p}: {r.get('url') or r.get('note') or r.get('error') or ''}")


def cmd_run(args):
    core.load_local_secrets()
    res = builder.build_video(args.niche, topic=args.topic)
    out_dir = pathlib.Path(res["dir"])
    print(f"🎬 собрано: {out_dir}")
    results, meta = _post_dir(out_dir, None, dry=args.dry)
    for p, r in results.items():
        print(f"  {'✅' if r.get('ok') else '❌'} {p}: {r.get('url') or r.get('note') or r.get('error') or ''}")
    reporter.report_run(meta, results)


def _niche_accounts(niche_id: str) -> list:
    """Все connected+auto_post аккаунты ниши (vk/instagram/threads) из ВСЕХ её бандлов."""
    from panel import db
    out = []
    for b in db.list_bundles():
        if b.get("niche_id") != niche_id or b.get("status", "active") != "active":
            continue
        for acc in b.get("accounts", []):
            if acc.get("platform") not in ("vk", "instagram", "threads"):
                continue
            if acc.get("status") != "connected" or not acc.get("auto_post"):
                continue
            out.append({**acc, "_bundle": b.get("name", "")})
    return out


def cmd_autopost(args):
    """Headless пер-нишевый автопостинг (для GitHub Actions крона): собрать ролик ниши →
    запостить в ВСЕ её connected-аккаунты VK+IG+Threads (panel.db) → отчёт в TG.
    --dry: только показать цели (без сборки и постинга)."""
    core.load_local_secrets()
    niche_id = args.niche
    accs = _niche_accounts(niche_id)
    if args.dry:
        print(f"[dry] цели автопоста «{niche_id}»:")
        for a in accs:
            print(f"  • {a['platform']:10} {a.get('display_name') or a.get('ext_id')}  "
                  f"(токен {a.get('secret_ref')}, бандл «{a['_bundle']}»)")
        if not accs:
            print("  ⚠️ нет подключённых аккаунтов")
        return
    if not accs:
        print(f"⚠️ у ниши «{niche_id}» нет connected-аккаунтов vk/instagram/threads — нечего постить")
        return

    # Фаза 4: серийный контент — МАКС 1 серийный эпизод/день/ниша (часть1 → завтра часть2 → новый сериал)
    from pipeline import serials
    today = core.today_str()
    ser = serials.plan_episode(niche_id, today)
    topic = args.topic or (ser.get("topic") if (ser and ser.get("part") == 2) else None)
    print(f"📺 серийный план «{niche_id}»: {ser}")
    res = builder.build_video(niche_id, topic=topic, serial=ser)
    out_dir = pathlib.Path(res["dir"])
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    video = meta["video"]
    print(f"🎬 собрано: {out_dir}")
    # QA-гейт: не публикуем брак
    qa = res.get("qa") or {}
    if qa and not qa.get("ok", True):
        print(f"⛔ QA не пройден — НЕ публикую: {qa.get('issues')}")
        return
    # зафиксировать серийное состояние ПОСЛЕ успешной сборки (advance сериала)
    if ser:
        try:
            sc_b = json.loads((out_dir / "script.json").read_text(encoding="utf-8"))
            if ser["part"] == 1:
                serials.record(niche_id, 1, today, topic=sc_b.get("topic", ""),
                               premise=f"{sc_b.get('topic', '')} — {sc_b.get('hook', '')}")
            else:
                serials.record(niche_id, 2, today)
            print(f"📺 серия зафиксирована: part {ser['part']}")
        except Exception as e:  # noqa: BLE001
            core.log_error("autopost.serial_record", e)
    cap = ((meta.get("captions", {}).get("instagram", {}) or {}).get("caption")
           or (meta.get("captions", {}).get("vk", {}) or {}).get("caption") or meta.get("topic", ""))

    results = []
    for acc in accs:
        p = acc["platform"]
        label = f"{p}/{acc.get('display_name') or acc.get('ext_id')}"
        try:
            if p == "vk":
                from adapters import vk_video
                ok, info = vk_video.publish(video, meta, acc)
            elif p == "instagram":
                from adapters import instagram
                ok, info = instagram.publish(video, meta, acc)
            else:  # threads: видео по публичному URL (тот же, что для IG), фолбэк на текст
                from adapters import threads as th, media_host
                try:
                    ok, info = th.publish_video(media_host.public_url(video), cap, acc)
                    if not ok:
                        ok, info = th.publish_text(cap, acc)
                except Exception:  # noqa: BLE001
                    ok, info = th.publish_text(cap, acc)
        except Exception as e:  # noqa: BLE001
            ok, info = False, str(e)[:200]
        url = info.get("url") if isinstance(info, dict) else info
        results.append((label, ok, url))
        print(f"  {'✅' if ok else '❌'} {label}: {url}")

    ok_n = sum(1 for _, ok, _ in results if ok)
    try:
        reporter.send(f"🤖 <b>Автопост: {niche_id}</b> — выложено {ok_n}/{len(results)}\n"
                      + "\n".join(f"{'✅' if ok else '❌'} {lbl}" for lbl, ok, _ in results))
    except Exception:  # noqa: BLE001
        pass


def cmd_v2(args):
    """v2-автопостинг по выходу: ig_vk|text|youtube|tiktok (крон разносит по дню)."""
    core.load_local_secrets()
    from pipeline import autopilot
    autopilot.run(args.output, args.niche)


def main():
    ap = argparse.ArgumentParser(description="Content Factory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("niches").set_defaults(func=cmd_niches)
    b = sub.add_parser("build"); b.add_argument("niche", nargs="?", default="ai_lifehacks")
    b.add_argument("-t", "--topic", default=None); b.set_defaults(func=cmd_build)
    ba = sub.add_parser("batch"); ba.add_argument("n", type=int)
    ba.add_argument("niche", nargs="?", default="ai_lifehacks"); ba.set_defaults(func=cmd_batch)
    po = sub.add_parser("post"); po.add_argument("dir"); po.add_argument("platforms", nargs="*")
    po.set_defaults(func=cmd_post)
    r = sub.add_parser("run"); r.add_argument("niche", nargs="?", default="ai_lifehacks")
    r.add_argument("-t", "--topic", default=None); r.add_argument("--dry", action="store_true")
    r.set_defaults(func=cmd_run)
    apo = sub.add_parser("autopost"); apo.add_argument("niche")
    apo.add_argument("-t", "--topic", default=None); apo.add_argument("--dry", action="store_true")
    apo.set_defaults(func=cmd_autopost)
    v2 = sub.add_parser("v2"); v2.add_argument("output")   # ig_vk|text|youtube|tiktok
    v2.add_argument("niche", nargs="?", default=None); v2.set_defaults(func=cmd_v2)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
