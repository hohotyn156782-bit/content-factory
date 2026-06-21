"""Селектор тем (этап 3): тренды парсера → готовые темы видео.

Берёт кандидатов из parser.gather(), скорит, отсекает дубли и недавнее, и через
LLM-каскад выбирает N самых перспективных трендов, переформулируя каждый в КОНКРЕТНУЮ
цепляющую тему ролика под нишу (а не сухой новостной заголовок). Политику/чернуху/
демонетизируемое отсеивает. Если парсер пуст или LLM недоступен — фолбэк на чистый LLM.
"""
import json
import re

import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402
from pipeline import parser, llm  # noqa: E402


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip().strip('"«»')


_INJECT = re.compile(r"(?i)(ignore (all |the )?previous|disregard (above|previous)|system\s*:|"
                     r"ты теперь|забудь (все )?(инструкции|предыдущ)|new instructions|act as|"
                     r"<\|?(system|im_start|im_end)\|?>)")


def _sanitize(t: str) -> str:
    """Очистить текст из ПАРСЕРА (чужой контент) перед подачей в LLM — анти-prompt-injection.
    Тонкая обёртка над core.sanitize_external (единый паттерн для всего пайплайна) + локальная
    доп.строгость: наш _INJECT ловит ru-инъекции/«act as»/«new instructions», которых нет в core."""
    t = core.sanitize_external(_clean(t))   # базовая очистка (общий паттерн)
    t = _INJECT.sub("[…]", t)               # поверх: дополнительные паттерны перехвата (строже core)
    t = re.sub(r"[`{}<>\\]", "", t)          # спецсимволы разметки/скобки
    return t[:140]


def pick_topics(niche: dict, n: int = 2, recent: list[str] | None = None) -> list[str]:
    is_ru = niche.get("lang", "ru") != "en"
    cands = []
    try:
        cands = parser.gather(niche)
    except Exception:  # noqa: BLE001 — источники не должны ронять пайплайн
        cands = []
    top = sorted(cands, key=lambda x: -x.get("weight", 0))[:28]
    trends_lines = [f"- [{c['source']}] {_sanitize(c['title'])}" for c in top]

    # H3: горячие подтемы из ЛЕГАЛЬНОГО вираль-брифа ниши (метаданные чужих топ-Shorts) как
    # доп.кандидаты с пометкой источника. Гейт CF_VIRAL (как в script.py), мягко, не роняет селектор.
    import os
    if os.environ.get("CF_VIRAL", "1") != "0":
        try:
            from pipeline import heatmap
            vq = (niche.get("broll_hint", "") or niche.get("title", "")).split(",")[0].strip()
            brief = heatmap.viral_brief(vq, lang=niche.get("lang", "ru")) if vq else {}
            for st in (brief.get("hot_subtopics") or [])[:8]:
                st = _sanitize(st)
                if st:
                    trends_lines.append(f"- [viral_brief] {st}")
        except Exception:  # noqa: BLE001 — доп.кандидаты необязательны
            pass

    trends_block = "\n".join(trends_lines) or "(тренды недоступны — придумай сам актуальные темы ниши)"

    # #15 дедуп против durable topics_db: подмешиваем уже выпущенные/зарезервированные темы
    # ниши за 60 дней к списку избегания → LLM-продюсер сразу обходит дубли (меньше коллизий
    # reserve_topic в generate(), больше attempt-итераций на докрутку хука/удержания/петли).
    # Fallback-safe: любой сбой импорта/вызова → текущее поведение (только переданный recent).
    avoid = list(recent or [])
    try:
        from pipeline import topics_db
        avoid += topics_db.recent_titles(niche=niche.get("id"), days=60) or []
    except Exception:  # noqa: BLE001 — durable-дедуп необязателен, не роняет селектор
        pass

    recent_block = ""
    if avoid:
        seen, uniq = set(), []
        for r in avoid:
            r = _clean(r)[:60]
            k = r.lower()
            if r and k not in seen:
                seen.add(k)
                uniq.append(r)
        if uniq:
            recent_block = "\nНЕ повторяй недавнее: " + " | ".join(uniq[:25])

    lang_word = "русском" if is_ru else "английском"
    system = (
        f"Ты — продюсер виральных коротких видео (Shorts/TikTok/Reels) в нише «{niche.get('title')}» — "
        f"{niche.get('topic_brief')}\n"
        f"Тебе дают свежие тренды/новости. Выбери {n} САМЫХ перспективных для роста просмотров и подписчиков "
        f"и переформулируй КАЖДЫЙ в конкретную цепляющую ТЕМУ РОЛИКА под нишу (не новостной заголовок, "
        f"а угол подачи, который зайдёт). Бери то, что вызывает любопытство/пользу/эмоцию. "
        f"ИЗБЕГАЙ: политики, трагедий/чернухи, NSFW, узких местных новостей, всего что демонетизируется. "
        f"Темы должны быть на {lang_word} языке.{recent_block}\n"
        f'Верни СТРОГО JSON: {{"topics": [{", ".join(["\"...\""] * n)}]}} — ровно {n} тем.'
    )
    user = "Свежие тренды:\n" + trends_block
    try:
        raw = llm.chat(system, user, json_mode=True, max_tokens=500)
        topics = [_clean(t) for t in (json.loads(_extract(raw)).get("topics") or []) if _clean(t)]
        if topics:
            return topics[:n]
    except Exception:  # noqa: BLE001
        pass
    # фолбэк: топовые заголовки как темы (чужой контент → _sanitize, не _clean: анти-injection)
    return [_sanitize(c["title"]) for c in top[:n] if _sanitize(c["title"])] or [""]


def _extract(raw: str) -> str:
    try:
        json.loads(raw)
        return raw
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return m.group(0) if m else '{"topics": []}'


if __name__ == "__main__":
    core.load_local_secrets()
    nid = sys.argv[1] if len(sys.argv) > 1 else "ai_lifehacks"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    topics = pick_topics(core.get_niche(nid), n=n)
    print(f"[{nid}] выбранные темы:")
    for t in topics:
        print("  •", t)
