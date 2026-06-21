#!/usr/bin/env python3
"""Smoke-тест конвейера content-factory — быстрый ОФЛАЙН-прогон критичных узлов после правок пайплайна.

Проверяет: импорт всех модулей, группировку субтитров (MIN_DUR/порядок), гейт сценария (клише),
скоринг хука, тех-QA + асимметричный десинк, наложение VPN-баннера (без потери синхрона/длительности),
генерацию обложки. Ловит регрессии рендера/QA/баннера, которые ломали ролики раньше.

  python3 tools/smoke_test.py          # быстрый офлайн (~20-30с), exit 1 при любом провале
  python3 tools/smoke_test.py --full   # + реальная сборка одного ролика (медленно, сеть/LLM)
  python3 tools/smoke_test.py -v       # с трейсбэками
"""
import sys
import pathlib
import tempfile
import subprocess
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core  # noqa: E402

PASS: list = []
FAIL: list = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  ✅ {name}")
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, str(e)))
        print(f"  ❌ {name}: {e}")
        if "-v" in sys.argv:
            traceback.print_exc()


def _make_clip(path, dur=10):
    """Тестовый ролик 1080×1920: подвижный паттерн (без ложного фриза) + синус-аудио, синхрон."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size={core.W}x{core.H}:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)], check=True, capture_output=True)


def t_imports():
    import importlib
    for m in ("pipeline.script", "pipeline.voice", "pipeline.broll", "pipeline.subtitles",
              "pipeline.assemble", "pipeline.qa", "pipeline.thumbnail", "pipeline.banner",
              "pipeline.imagegen", "pipeline.heatmap", "pipeline.build", "factory"):
        importlib.import_module(m)


def t_subtitles():
    from pipeline import subtitles as S
    words = [{"w": "привет", "start": 0.0, "end": 0.3}, {"w": "мир", "start": 0.3, "end": 0.5},
             {"w": "это", "start": 0.5, "end": 0.7}, {"w": "тест", "start": 0.7, "end": 1.4}]
    g = S._group_words(words)
    assert g, "пустые группы"
    for x in g:
        assert x["end"] - x["start"] >= S.MIN_DUR - 1e-6, f"группа короче MIN_DUR: {x}"
        assert x["text"].strip(), "пустой текст группы"
    for a, b in zip(g, g[1:]):
        assert b["start"] >= a["start"], "группы не отсортированы"


def t_validate():
    from pipeline import script as S
    hook = "Банк тайно списывал деньги у клиента целых три года"
    out = "Подпишись чтобы не попасть на такую же ловушку завтра"
    seg_ok = "Клиент потерял сорок тысяч рублей на скрытой комиссии и узнал об этом лишь спустя полгода случайно"
    seg_bad = "В современном мире банки берут скрытый процент с каждого клиента почти всегда и никому об этом"
    ok, _ = S.validate({"hook": hook, "segments": [{"text": seg_ok}] * 4, "outro": out})
    assert ok, "чистый сценарий должен пройти"
    bad, reason = S.validate({"hook": hook, "segments": [{"text": seg_bad}] * 4, "outro": out})
    assert (not bad) and "клише" in reason, f"клише-гейт не сработал: {reason}"


def t_hook_score():
    from pipeline import script as S
    strong = S._score_hook("Банк украл 3 миллиона за одну подпись")
    weak = S._score_hook("кстати тут есть кое что интересное про деньги наверное всем")
    assert strong > weak, f"скоринг хука сломан: strong={strong} weak={weak}"


def t_hook_title_funcs():
    """_three_hooks/_title_variants без LLM (мок _groq) и без сети (CF_VIRAL=0) — ловит NameError/ссылки."""
    import os
    from pipeline import script as S
    old_groq, old_env = S._groq, os.environ.get("CF_VIRAL")
    os.environ["CF_VIRAL"] = "0"
    S._groq = lambda *a, **k: '{"variants":["Банк украл 3 миллиона за подпись","Хук два короче тут","Третий вариант хука"],"best_index":0}'
    try:
        niche = {"lang": "ru", "title": "Тест", "broll_hint": "money"}
        sc = {"topic": "тест", "hook": "старый хук", "segments": [{"text": "сегмент один тут"}], "outro": "пока"}
        r1 = S._three_hooks(dict(sc), niche)
        assert r1.get("hook_scores"), "hook_scores не выставлены (скоринг не отработал)"
        r2 = S._title_variants(dict(sc), niche)
        assert r2.get("title_variants"), "title_variants не выставлены"
    finally:
        S._groq = old_groq
        if old_env is None:
            os.environ.pop("CF_VIRAL", None)
        else:
            os.environ["CF_VIRAL"] = old_env


def t_qa_and_banner(tmp):
    from pipeline import qa, banner
    clip = tmp / "clip.mp4"
    _make_clip(clip)
    r = qa.check_technical(str(clip))
    assert "ok" in r and r.get("res") == f"{core.W}x{core.H}", f"QA структура/разрешение: {r}"
    bad = [i for i in r["issues"] if "рассинхрон" in i or "длиннее" in i]
    assert not bad, f"ложный десинк на синхронном клипе: {bad}"
    d0 = core.media_duration(str(clip))
    assert banner.overlay(str(clip)), "VPN-баннер не наложился"
    d1 = core.media_duration(str(clip))
    assert abs(d1 - d0) < 0.25, f"баннер изменил длительность: {d0:.2f}->{d1:.2f}"
    r2 = qa.check_technical(str(clip))
    bad2 = [i for i in r2["issues"] if "рассинхрон" in i or "длиннее" in i]
    assert not bad2, f"десинк после баннера: {bad2}"


def t_thumbnail(tmp):
    from pipeline import thumbnail
    clip = tmp / "clip2.mp4"
    _make_clip(clip)
    out = tmp / "thumb.jpg"
    res = thumbnail.make_thumbnail(str(clip), "ТЕСТ ЗАГОЛОВОК", out, niche=None, ai_bg=False)
    assert res and out.exists() and out.stat().st_size > 5000, "обложка не создана"


def main():
    core.load_local_secrets()
    print("SMOKE-ТЕСТ content-factory\n")
    check("imports (все модули)", t_imports)
    check("subtitles._group_words", t_subtitles)
    check("script.validate (клише-гейт)", t_validate)
    check("script._score_hook", t_hook_score)
    check("_three_hooks/_title_variants (офлайн)", t_hook_title_funcs)
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        check("qa + banner overlay (синхрон)", lambda: t_qa_and_banner(tmp))
        check("thumbnail.make_thumbnail", lambda: t_thumbnail(tmp))
    if "--full" in sys.argv:
        def t_full():
            r = subprocess.run([sys.executable, str(ROOT / "factory.py"), "build", "ai_lifehacks"],
                               capture_output=True, text=True, timeout=900)
            assert r.returncode == 0, f"build exit {r.returncode}: {r.stderr[-300:]}"
        check("full build (factory.py build)", t_full)
    print(f"\nИТОГ: {len(PASS)} ✅  /  {len(FAIL)} ❌")
    if FAIL:
        for n, e in FAIL:
            print(f"   ❌ {n}: {e}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
