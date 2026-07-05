"""Календарные хуки: подмешать в сценарий актуальный инфоповод (праздник/сезон) под нишу.

Предсказуемые пики (Новый год, 8 марта, Чёрная пятница, 1 сентября…) стабильно собирают охват,
если выложить контент за 2-4 дня ДО. Данные — assets/calendar.json (офлайн, без сети).
Мягкий модуль: нет файла/совпадения → пустая строка, генерация не страдает.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

_CAL = core.ROOT / "assets" / "calendar.json"
_cache = None


def _load() -> list:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CAL.read_text(encoding="utf-8")).get("events", [])
        except Exception:  # noqa: BLE001
            _cache = []
    return _cache


def _in_window(md: str, frm: str, to: str) -> bool:
    """md, frm, to — 'MM-DD'. Учитывает окна через Новый год (from > to)."""
    if frm <= to:
        return frm <= md <= to
    return md >= frm or md <= to           # окно пересекает 31.12→01.01


def angle_for(niche_id: str, today: str) -> str:
    """Актуальный календарный угол для ниши на дату today (ISO 'YYYY-MM-DD...'), иначе ''."""
    md = today[5:10]
    if len(md) != 5:
        return ""
    for e in _load():
        ns = e.get("niches", [])
        if "*" not in ns and niche_id not in ns:
            continue
        if _in_window(md, e.get("from", ""), e.get("to", "")):
            return e.get("angle", "")
    return ""
