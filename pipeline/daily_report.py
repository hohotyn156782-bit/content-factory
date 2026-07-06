"""Ежедневный отчёт по каналам в Telegram (владельцу, 21:00 МСК).

Собирает по КАЖДОЙ нише её подключённые VK/IG/Threads-аккаунты и тянет:
  • подписчиков   (VK members_count / IG followers_count / Threads insights),
  • суммарные просмотры и самое популярное видео ниши (из performance_log/леджера),
  • дневной прирост (+дельта) по сравнению со вчерашним снапшотом (state/report_snapshot.json).
Отправляет один аккуратный месседж через reporter (TG_BOT_TOKEN/TG_CHAT_ID) — лично владельцу.

YouTube/TikTok в авто-отчёт НЕ входят: TikTok без публичного API; YouTube — когда появится YOUTUBE_API_KEY.

CLI:
    python3 pipeline/daily_report.py          # dry: собрать и НАПЕЧАТАТЬ (без отправки)
    python3 pipeline/daily_report.py --send    # собрать и ОТПРАВИТЬ в TG + сохранить снапшот
"""
import os
import sys
import json
import urllib.parse
import urllib.request
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

SNAP = core.ROOT / "state" / "report_snapshot.json"
VK_VER = "5.199"

# эмодзи по нишам (для читаемости; неизвестная ниша → 📺)
_EMOJI = {
    "ai_lifehacks": "🤖", "ai_lifehacks_en": "🤖", "mind_facts": "🧠", "psy_stories": "🧠",
    "history_facts": "📜", "money_facts": "💰", "personal_brand": "🅿️", "talking_objects": "🎭",
    "business_stories": "📉", "mystic_stories": "🌑", "what_if": "❓", "soviet_things": "🇷🇺",
}


def _get(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cf-daily-report"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


# ──────────────────────── подписчики по площадкам ────────────────────────

def _vk_subs(gid: str, tok: str) -> int | None:
    d = _get("https://api.vk.com/method/groups.getById?" + urllib.parse.urlencode(
        {"group_id": str(gid).lstrip("-"), "fields": "members_count", "access_token": tok, "v": VK_VER}))
    if not d:
        return None
    resp = d.get("response")
    if isinstance(resp, dict):
        resp = resp.get("groups", [resp])
    if isinstance(resp, list) and resp:
        return int(resp[0].get("members_count") or 0)
    return None


def _ig_subs(ig_id: str, tok: str) -> int | None:
    # IG-аккаунты на Instagram Login → домен graph.instagram.com (НЕ graph.facebook.com).
    # /me с токеном аккаунта отдаёт его followers_count (тот же способ, что instagram.verify).
    from adapters import instagram
    d = _get(f"{instagram.GRAPH}/me?" + urllib.parse.urlencode(
        {"fields": "followers_count", "access_token": tok}))
    if d and "followers_count" in d:
        return int(d.get("followers_count") or 0)
    return None


def _threads_subs(tid: str, tok: str) -> int | None:
    d = _get(f"https://graph.threads.net/v1.0/{tid}/threads_insights?" + urllib.parse.urlencode(
        {"metric": "followers_count", "access_token": tok}))
    items = (d or {}).get("data") or []
    if items:
        tv = items[0].get("total_value") or {}
        if "value" in tv:
            return int(tv.get("value") or 0)
        vals = items[0].get("values") or [{}]
        return int((vals[-1] or {}).get("value") or 0)
    return None


_SUBS = {"vk": _vk_subs, "instagram": _ig_subs, "threads": _threads_subs}
_SHORT = {"vk": "VK", "instagram": "IG", "threads": "Th"}


def _subs_for(account: dict) -> int | None:
    plat = account.get("platform")
    fn = _SUBS.get(plat)
    tok = os.environ.get((account.get("secret_ref") or "").strip(), "").strip()
    ext = str(account.get("ext_id") or "").strip()
    if not fn or not tok or not ext:
        return None
    try:
        return fn(ext, tok)
    except Exception:  # noqa: BLE001
        return None


# ──────────────────────── просмотры / топ-видео из аналитики ────────────────────────

def _views_by_niche() -> dict:
    """{niche: {'total_views': int, 'videos': int, 'top': {'topic','views','platform'}|None}}
    из performance_log (обновляем метрики из леджера перед чтением). Пусто → {}."""
    from pipeline import analytics
    try:
        analytics.collect_ledger()
    except Exception as e:  # noqa: BLE001
        core.log_error("daily_report.collect_ledger", e)
    out: dict = {}
    if not analytics.PERF_LOG.exists():
        return out
    rows = [json.loads(ln) for ln in analytics.PERF_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
    latest = list(analytics._latest_per_target(rows).values())
    for r in latest:
        nid = r.get("niche") or "?"
        v = int(r.get("views") or 0)
        d = out.setdefault(nid, {"total_views": 0, "videos": 0, "top": None})
        d["total_views"] += v
        d["videos"] += 1
        if not d["top"] or v > d["top"]["views"]:
            d["top"] = {"topic": r.get("topic") or r.get("content_id") or "?",
                        "views": v, "platform": r.get("platform")}
    return out


# ──────────────────────── сбор + рендер ────────────────────────

def gather() -> dict:
    core.load_local_secrets()
    from panel import db
    views = _views_by_niche()
    niches: dict = {}
    for b in db.list_bundles():
        if b.get("status", "active") != "active":
            continue
        nid = b.get("niche_id") or "?"
        n = niches.setdefault(nid, {"subs": {}, "subs_total": 0})
        for a in b.get("accounts", []):
            if a.get("status") != "connected" or a.get("platform") not in _SUBS:
                continue
            s = _subs_for(a)
            if s is None:
                continue
            plat = a["platform"]
            n["subs"][plat] = n["subs"].get(plat, 0) + s
            n["subs_total"] += s
    # приклеиваем просмотры/топ
    for nid, v in views.items():
        niches.setdefault(nid, {"subs": {}, "subs_total": 0})
        niches[nid].update({"views": v["total_views"], "videos": v["videos"], "top": v["top"]})
    return niches


def _fmt(n: int) -> str:
    return f"{n/1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def _delta(cur: int, prev: int | None) -> str:
    if prev is None:
        return ""
    d = cur - prev
    if d == 0:
        return " (±0)"
    return f" (+{_fmt(d)})" if d > 0 else f" (−{_fmt(-d)})"


def render(niches: dict, prev: dict | None, date_str: str) -> str:
    prev = prev or {}
    pn = prev.get("niches", {})
    tot_subs = sum(v.get("subs_total", 0) for v in niches.values())
    tot_views = sum(v.get("views", 0) for v in niches.values())
    tot_videos = sum(v.get("videos", 0) for v in niches.values())
    p_subs = prev.get("totals", {}).get("subs")
    p_views = prev.get("totals", {}).get("views")
    lines = [f"📊 <b>CONTENT FACTORY · сводка за {date_str}</b>", ""]
    lines.append(f"ИТОГО: 👥 <b>{_fmt(tot_subs)}</b> подписчиков{_delta(tot_subs, p_subs)}"
                 f" · 👁 <b>{_fmt(tot_views)}</b> просмотров{_delta(tot_views, p_views)}")
    lines.append(f"Роликов в базе: {tot_videos}")
    lines.append("")
    order = sorted(niches.items(), key=lambda kv: -kv[1].get("subs_total", 0))
    for nid, v in order:
        if not v.get("subs") and not v.get("views"):
            continue
        title = (core.get_niche(nid) or {}).get("title") or nid
        emoji = _EMOJI.get(nid, "📺")
        subs = v.get("subs", {})
        subs_str = " · ".join(f"{_SHORT.get(p, p)} {_fmt(subs[p])}" for p in ("vk", "instagram", "threads") if p in subs)
        st = v.get("subs_total", 0)
        p_st = (pn.get(nid, {}) or {}).get("subs_total")
        line = f"{emoji} <b>{title}</b> — {subs_str or '—'}  (Σ {_fmt(st)}{_delta(st, p_st)})"
        vv = v.get("views")
        if vv:
            p_vv = (pn.get(nid, {}) or {}).get("views")
            top = v.get("top") or {}
            tline = f"  👁 {_fmt(vv)}{_delta(vv, p_vv)}"
            if top.get("topic"):
                tline += f" · 🔥 «{str(top['topic'])[:40]}» {_fmt(top.get('views', 0))}"
            line += "\n" + tline
        lines.append(line)
    lines.append("")
    lines.append("▶️ YouTube / 🎵 TikTok — публикуются вручную (в авто-отчёт не входят)")
    return "\n".join(lines)


def _snapshot(niches: dict, date_str: str) -> dict:
    return {"date": date_str,
            "totals": {"subs": sum(v.get("subs_total", 0) for v in niches.values()),
                       "views": sum(v.get("views", 0) for v in niches.values())},
            "niches": {nid: {"subs_total": v.get("subs_total", 0), "views": v.get("views", 0)}
                       for nid, v in niches.items()}}


def run(send: bool) -> str:
    core.load_local_secrets()
    date_str = core.today_str() if hasattr(core, "today_str") else core._now().strftime("%d.%m")
    # красивая дата дд.мм
    try:
        date_str = core._now().strftime("%d.%m")
    except Exception:  # noqa: BLE001
        pass
    prev = None
    if SNAP.exists():
        try:
            prev = json.loads(SNAP.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = None
    niches = gather()
    text = render(niches, prev, date_str)
    if send:
        import reporter
        ok = reporter.send(text)
        SNAP.parent.mkdir(parents=True, exist_ok=True)
        SNAP.write_text(json.dumps(_snapshot(niches, date_str), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print("отправлено:" , ok)
    return text


if __name__ == "__main__":
    core.load_local_secrets()
    send = "--send" in sys.argv
    out = run(send)
    print(out)
