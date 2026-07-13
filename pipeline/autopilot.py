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
    """Видео (формат ig_vk) → Instagram Reels + VK Клипы + Threads (тем же роликом)."""
    accs = _accounts(niche_id, ("instagram", "vk", "threads"))
    # VK-клипы льём только в video-kind сообщества. У text-kind групп video.save недоступен
    # (постят wall-текстом, их обслуживает post_text) → без фильтра туда летит видео и гарантированно
    # падает в None. IG-аккаунты kind не касается.
    accs = [a for a in accs
            if a.get("platform") != "vk" or (a.get("kind") or "").strip().lower() != "text"]
    if not accs:
        return []
    # Threads — последним: Meta качает mp4 по публичному https, и IG-адаптер к этому моменту
    # уже залил файл на хостинг → переиспользуем его URL вместо второй заливки.
    accs.sort(key=lambda a: a.get("platform") == "threads")
    res = builder.build_video(niche_id, platform="ig_vk")
    out_dir = pathlib.Path(res["dir"])
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    if (res.get("qa") or {}) and not (res.get("qa") or {}).get("ok", True):
        _qa_alert("ig_vk", niche_id, res.get("qa") or {})
        return [(f"ig_vk/{niche_id}", False, "QA не пройден")]
    video = meta["video"]
    r, led, pub_url = [], [], None
    for a in accs:
        p = a["platform"]
        if p == "instagram":
            from adapters import instagram
            ok, info = _retry_pub(instagram.publish, video, meta, a)
            if ok and isinstance(info, dict) and str(info.get("url") or "").startswith("https://"):
                pub_url = info["url"]
        elif p == "threads":
            from adapters import threads as th
            try:
                if not pub_url:
                    from adapters import media_host
                    pub_url = media_host.public_url(video)
            except Exception as e:  # noqa: BLE001
                ok, info = False, f"хостинг для Threads: {str(e)[:120]}"
            else:
                cap = ((meta.get("captions", {}).get("instagram", {}) or {}).get("caption")
                       or meta.get("topic", ""))
                ok, info = _retry_pub(th.publish_video, pub_url, cap, a)
        else:
            from adapters import vk_video
            ok, info = _retry_pub(vk_video.publish, video, meta, a)
        r.append((f"{p}/{a.get('display_name') or a.get('ext_id')}", ok, _url(info)))
        led.append(_entry(p, a, ok, info))
        # Леджер и анти-дубль — сразу после каждой успешной ноги: Threads-хвост может ждать
        # обработку у Meta до ~10 мин, и SIGALRM-обрыв там не должен терять уже сделанные
        # IG/VK-публикации (иначе назавтра передубль).
        if ok:
            _ledger("ig_vk", niche_id, meta.get("topic", ""), [led[-1]])
            _mark_posted("ig_vk", niche_id)
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
    # ОБА видео (youtube и tiktok) шлём ОБОИМ админам (Паша + Даша)
    chats = tg_queue.admin_chats() or [""]   # [""] → send_item сам возьмёт дефолт по target
    deliveries = [tg_queue.send_item(target, meta["video"], title, tags, _eng_question(sc),
                                     channel=core.get_niche(niche_id).get("title", ""),
                                     niche=niche_id, extras=extras, chat_override=ch)
                  for ch in chats]
    ok = any(d[0] for d in deliveries)
    info = "; ".join(f"{ch or 'def'}:{'ok' if d[0] else d[1]}"
                     for ch, d in zip(chats, deliveries)) or "нет адресатов"
    # в леджер — как «в очереди» (URL появится позже, когда владелец выложит вручную)
    _ledger(target, niche_id, sc.get("topic", ""),
            [{"platform": target, "account": "tg-queue", "ok": ok, "url": None, "ref": None,
              "queued": True}])
    return [(f"{target}/{niche_id}", ok, info)]


# ───────── жёсткий стоп по времени на нишу + порядок ниш (давность → вес) ─────────


class _NicheTimeout(BaseException):
    """Ниша превысила жёсткий лимит времени. Наследуем BaseException (а не Exception),
    чтобы прервать нишу СКВОЗЬ внутренние `except Exception` сборки/публикации, которые иначе
    проглотили бы обычное исключение и продолжили пересборку."""


def _raise_niche_timeout(signum, frame):    # обработчик SIGALRM
    raise _NicheTimeout()


_ATTEMPTS = _STATE / "attempts.json"   # день последней ПОПЫТКИ по (output, niche) — для порядка ниш


def _load_attempts() -> dict:
    try:
        return json.loads(_ATTEMPTS.read_text(encoding="utf-8")) if _ATTEMPTS.exists() else {}
    except Exception as e:  # noqa: BLE001
        core.log_error("autopilot._load_attempts", e)
        return {}


def _mark_attempt(output: str, niche_id: str) -> None:
    """Пометить, что ниша сегодня получила слот (даже если публикация не удалась).
    Порядок ниш строится по дню последней ПОПЫТКИ: по дню успеха стабильно падающая ниша
    (протухший токен/QA-трэшинг) вечно стояла бы первой и каждый ран съедала голову бюджета."""
    day = core.today_str()[:10]
    d = _load_attempts()
    d.setdefault(day, {}).setdefault(output, [])
    if niche_id not in d[day][output]:
        d[day][output].append(niche_id)
    for old in sorted(d.keys())[:-7]:      # держим только последние 7 дней
        d.pop(old, None)
    try:
        _atomic_write(_ATTEMPTS, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        core.log_error("autopilot._mark_attempt", e)


def _last_touch_day(output: str) -> dict:
    """niche → день, когда выход последний раз касался ниши: успехи — из леджера posts.jsonl
    (история до появления attempts.json), попытки — из attempts.json."""
    out: dict = {}
    if _LEDGER.exists():
        for ln in _LEDGER.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if e.get("output") != output or not e.get("ok") or not e.get("niche"):
                continue
            day = str(e.get("ts") or "")[:10]
            if day > out.get(e["niche"], ""):
                out[e["niche"]] = day
    for day, per_out in _load_attempts().items():
        for n in per_out.get(output, []) or []:
            if day > out.get(n, ""):
                out[n] = day
    return out


def _order_niches(output: str, ids: list) -> list:
    """Порядок обработки ниш: сперва давность (кто дольше не получал слот — анти-голодание,
    роль прежнего round-robin-курсора), при равной давности — вес ниши из аналитики
    (data/niche_weights.json: что реально набирает просмотры). Бюджет времени отсекает
    ХВОСТ списка → в хвост попадают слабые по метрикам ниши, а не случайные."""
    from pipeline import analytics
    last = _last_touch_day(output)
    return sorted(ids, key=lambda n: (last.get(n, ""), -analytics.weight_for(n), n))


def run(output: str, niche: str | None = None) -> list:
    fn = {"ig_vk": post_ig_vk, "text": post_text,
          "youtube": lambda n: queue_video(n, "youtube"),
          "tiktok": lambda n: queue_video(n, "tiktok")}.get(output)
    if not fn:
        raise SystemExit(f"неизвестный output: {output} (ig_vk|text|youtube|tiktok)")
    single = niche is not None
    niches = [niche] if single else [n["id"] for n in core.load_niches(only_enabled=True)]
    if output in ("youtube", "tiktok"):
        niches = [n for n in niches if core.get_niche(n).get("has_yt_tiktok")]
    # Порядок ниш: давность публикации → вес из аналитики (_order_niches). Анти-голодание
    # прежнего round-robin-курсора сохраняется: отсечённая бюджетом ниша назавтра «старше»
    # всех и встаёт в начало. Точечный --niche порядок не трогает.
    if not single:
        niches = _order_niches(output, niches)

    # Бюджет по времени: не начинать новую нишу под конец лимита GitHub (timeout-minutes).
    # Иначе джоб убивают ПОСРЕДИ сборки → шаг commit-back не успевает сохранить состояние
    # (сериалы/история тем/posted.json/курсор) и назавтра дубли. Дефолт 80 мин при лимите
    # джоба 120: t0 стартует ПОСЛЕ setup (~3 мин), а после цикла ещё commit-back (~2 мин).
    import os
    import signal
    import time
    try:
        budget_s = float(os.environ.get("CF_RUN_BUDGET_S") or 4800)
    except ValueError:                    # мусор в env не должен ронять весь автопилот
        budget_s = 4800.0
    # Жёсткий лимит на ОДНУ нишу: QA-трэшинг (пересборка до 4× при браке AI-картинок) или
    # зависшая сеть не должны съедать весь джоб и ронять его в timeout-minutes, голодя остальные
    # ниши. 25 мин при бюджете 80 → худший старт 80-й мин + 25 = 105 + setup(~3) + commit-back(~2)
    # ≈ 110 < 120, есть запас. Работает только в главном потоке на Unix (CI = ubuntu).
    try:
        niche_cap_s = max(0, int(float(os.environ.get("CF_NICHE_CAP_S") or 1500)))
    except ValueError:
        niche_cap_s = 1500
    use_alarm = niche_cap_s > 0 and hasattr(signal, "SIGALRM")

    t0 = time.time()
    allr, skipped = [], []
    for n in niches:
        if time.time() - t0 > budget_s:
            skipped.append(n)
            continue
        print(f"— {output} · {n} —")
        _mark_attempt(output, n)
        if already_posted(output, n):
            print(f"  ⏭ уже опубликовано сегодня ({output}/{n}) — пропуск (анти-дубль)")
            continue
        prev_handler, armed = None, False
        if use_alarm:
            try:
                prev_handler = signal.signal(signal.SIGALRM, _raise_niche_timeout)
                signal.alarm(niche_cap_s)
                armed = True
            except (ValueError, OSError):     # не главный поток → без жёсткого таймаута
                armed = False
        try:
            rr = fn(n)
            if armed:
                signal.alarm(0)               # ниша собрана/опубликована — снимаем таймер до пост-обработки
            for lbl, ok, url in rr:
                print(f"  {'✅' if ok else '❌'} {lbl}: {url}")
            allr += rr
            if any(ok for _, ok, _ in rr):     # хоть одна площадка успешна → метим день
                _mark_posted(output, n)
        except _NicheTimeout:
            print(f"  ⏱ ниша {n} превысила лимит {niche_cap_s // 60} мин — прервана, идём дальше")
            core.log_error(f"autopilot.{output}.niche_timeout",
                           RuntimeError(f"{n} > {niche_cap_s}s (QA-трэшинг/зависание)"), niche=n)
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {n}: {str(e)[:160]}")
            core.log_error(f"autopilot.{output}", e, niche=n)
        finally:
            if armed:
                signal.alarm(0)
                try:
                    signal.signal(signal.SIGALRM, prev_handler if prev_handler is not None else signal.SIG_DFL)
                except (ValueError, OSError, TypeError):
                    pass
    if skipped:
        # НЕ молча: явно сообщаем, до каких ниш не дошли в бюджете времени (получат слот в след. запуске).
        print(f"⏳ бюджет времени исчерпан — пропущено ниш: {len(skipped)}: {', '.join(skipped)}")
        try:
            import reporter
            reporter.critical(f"⏳ Автопилот {output}: не хватило времени на {len(skipped)} ниш "
                              f"({', '.join(skipped)}). Пойдут в следующий запуск.")
        except Exception:  # noqa: BLE001
            pass
    okn = sum(1 for _, ok, _ in allr if ok)
    try:
        import reporter
        reporter.send(f"🤖 <b>Автопилот v2: {output}</b> — ок {okn}/{len(allr)}")
    except Exception:  # noqa: BLE001
        pass
    return allr
