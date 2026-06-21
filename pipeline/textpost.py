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


def generate_text(niche: dict, platform: str, topic: str = "") -> str:
    is_ru = niche.get("lang", "ru") == "ru"
    style = (STYLE if is_ru else STYLE_EN).get(platform, (STYLE if is_ru else STYLE_EN)["vk"])
    anti = S.ANTI_SLOP if is_ru else S.ANTI_SLOP_EN
    lang_rule = "Пиши по-русски, живым языком." if is_ru else "Write in natural English only."
    topic_line = (f"\nТЕМА поста: {topic}" if topic else "") if is_ru else (f"\nTOPIC: {topic}" if topic else "")
    system = (
        f"Ты — контент-мейкер ниши «{niche.get('title')}» — {niche.get('topic_brief')}\n"
        f"ТОН: {niche.get('tone')}\n{lang_rule}\n"
        f"ФОРМАТ: {style}\n"
        f"Первая строка — сильный хук, который останавливает листающего.{topic_line}\n\n"
        f"{anti}\n\n"
        f"Верни ТОЛЬКО текст поста — без пояснений и кавычек."
    )
    user = ("Напиши один цепляющий пост." if is_ru else "Write one scroll-stopping post.")
    txt = S._groq(system, user, temp=0.85, max_tokens=700, json_mode=False)
    return re.sub(r'^["«»\s]+|["«»\s]+$', "", txt)
