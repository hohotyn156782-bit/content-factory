"""Генерация ТЕКСТОВЫХ постов (Threads / VK) под нишу + тему через Groq.

Видео-площадки делает основной пайплайн (build.py); текстовые — этот модуль.
Кросс-ссылки на другие площадки связки добавляются на стороне постинга.
"""
import re

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline import script as S  # noqa: E402

STYLE = {
    "threads": "Пост в Threads: 300-450 знаков, разговорно, от первого лица, 1 сильный хук в первой строке, без хэштег-простыни (1-2 тега максимум).",
    "vk": "Пост ВКонтакте: 600-1000 знаков, абзацы, 2-4 эмодзи к месту, структура хук → польза → мягкий вывод. Можно списком.",
}
STYLE_EN = {
    "threads": "Threads post: 300-450 chars, conversational, first person, one strong first-line hook, max 1-2 hashtags.",
    "vk": "Community post: 600-1000 chars, paragraphs, 2-4 emoji where fitting, hook → value → soft takeaway.",
}


def day_topic(niche: dict) -> str:
    """Одна тема истории на день — чтобы РАЗНЫЕ площадки (Threads/VK) рассказывали ОДНУ историю
    (иначе часть 2 продолжит только одну из двух завязок). Возвращает короткую тему (3-8 слов)."""
    is_ru = niche.get("lang", "ru") == "ru"
    sys_p = (f"Ниша «{niche.get('title')}» — {niche.get('topic_brief')}. Придумай ОДНУ конкретную тему "
             f"для истории-поста (3-8 слов), необычный цепляющий угол. Только тема, без кавычек и пояснений."
             if is_ru else
             f"Niche '{niche.get('title')}' — {niche.get('topic_brief')}. Invent ONE specific story topic "
             f"(3-8 words), an unusual hook angle. Topic only, no quotes.")
    try:
        t = S._groq(sys_p, ("Тема:" if is_ru else "Topic:"), temp=0.9, max_tokens=40, json_mode=False)
        return re.sub(r'^["«»\s]+|["«»\s]+$', "", t or "")[:120]
    except Exception:  # noqa: BLE001
        return ""


def generate_text(niche: dict, platform: str, topic: str = "", serial: dict | None = None) -> str:
    is_ru = niche.get("lang", "ru") == "ru"
    style = (STYLE if is_ru else STYLE_EN).get(platform, (STYLE if is_ru else STYLE_EN)["vk"])
    anti = S.ANTI_SLOP if is_ru else S.ANTI_SLOP_EN
    lang_rule = "Пиши по-русски, живым языком." if is_ru else "Write in natural English only."
    topic_line = (f"\nТЕМА поста: {topic}" if topic else "") if is_ru else (f"\nTOPIC: {topic}" if topic else "")
    # СЕРИАЛ: текст-история на 2 дня (часть 1 = завязка+клиффхэнгер, часть 2 = развязка по premise)
    serial_line = ""
    if serial and serial.get("part") == 1:
        serial_line = ("\n📺 Это ЧАСТЬ 1 истории-сериала: расскажи завязку и нагнетание, оборви на самом "
                       "интересном и в КОНЦЕ позови вернуться за продолжением завтра («Продолжение завтра»)."
                       if is_ru else
                       "\n📺 PART 1 of a serial story: set up and build tension, cut on a cliffhanger, and at "
                       "the END invite to come back tomorrow for the continuation.")
    elif serial and serial.get("part") == 2:
        prem = S._clean_line(str(serial.get("premise", "")))[:300]
        serial_line = (f"\n📺 Это ЧАСТЬ 2 (финал) истории. В 1-й части было: «{prem}». Кратко напомни "
                       "контекст и доведи историю до развязки." if is_ru else
                       f"\n📺 PART 2 (finale). Part 1 was: '{prem}'. Briefly recap and resolve the story.")
    system = (
        f"Ты — контент-мейкер ниши «{niche.get('title')}» — {niche.get('topic_brief')}\n"
        f"ТОН: {niche.get('tone')}\n{lang_rule}\n"
        f"ФОРМАТ: {style}\n"
        f"Первая строка — сильный хук, который останавливает листающего.{topic_line}{serial_line}\n\n"
        f"{anti}\n\n"
        f"Верни ТОЛЬКО текст поста — без пояснений и кавычек."
    )
    user = ("Напиши один цепляющий пост." if is_ru else "Write one scroll-stopping post.")
    txt = S._groq(system, user, temp=0.85, max_tokens=700, json_mode=False)
    return re.sub(r'^["«»\s]+|["«»\s]+$', "", txt)
