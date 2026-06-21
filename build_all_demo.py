"""Собрать по одному ролику в КАЖДОЙ нише (демо уровня) + сложить готовые на рабочий стол."""
import sys, pathlib, shutil, traceback, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import core
from pipeline import build as builder

DESK = pathlib.Path("/mnt/c/Users/BaronPavel/Desktop/ДЕМО_все_ниши")
DESK.mkdir(parents=True, exist_ok=True)

core.load_local_secrets()
niches = core.load_niches(only_enabled=False)
results = []
for n in niches:
    nid = n["id"]
    mode = n.get("broll_mode", "stock")
    print(f"\n{'='*60}\n▶  {nid}  (format={n.get('format','fact')}, broll={mode})\n{'='*60}", flush=True)
    try:
        res = builder.build_video(nid)            # max_attempts=2, режим b-roll из ниши
        m = res["meta"]
        # копия на рабочий стол с понятным именем
        dst = DESK / f"{nid}__{core.slugify(m['topic'])}.mp4"
        shutil.copy2(res["video"], dst)
        results.append({"niche": nid, "ok": res["qa"]["ok"], "topic": m["topic"],
                        "dur": m["duration"], "virality": m.get("virality", {}).get("score"),
                        "broll": mode, "file": dst.name})
        print(f"✅ {nid}: {m['topic']} | {m['duration']}s | вирусность {m.get('virality',{}).get('score')} | QA {'ok' if res['qa']['ok'] else 'FAIL'}", flush=True)
    except Exception as e:  # noqa: BLE001
        results.append({"niche": nid, "ok": False, "error": str(e)[:160]})
        print(f"❌ {nid}: {e}", flush=True)
        traceback.print_exc()

# сводка
print("\n\n" + "="*60 + "\nИТОГО:\n" + "="*60, flush=True)
for r in results:
    if r.get("error"):
        print(f"  ❌ {r['niche']:18} ОШИБКА: {r['error']}")
    else:
        print(f"  {'✅' if r['ok'] else '⚠️ '} {r['niche']:18} вир={r.get('virality')} {r['dur']:.0f}с [{r['broll']}] — {r['topic'][:40]}")
(DESK / "_сводка.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nГотовые ролики: {DESK}")
