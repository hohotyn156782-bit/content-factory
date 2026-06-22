"""Серийный контент: 2-частный сериал на нишу, МАКСИМУМ 1 серийный эпизод в день на нишу.

День 1 → Часть 1 (завязка + клиффхэнгер), День 2 → Часть 2 (финал/развязка), затем новый сериал.
Состояние — в state/serials.json (в РЕПО, не в DATA_ROOT): CI-раннеры эфемерны, поэтому раннер
коммитит файл обратно в репо после прогона (workflow). Локально лежит там же.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

STATE = core.ROOT / "state" / "serials.json"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def plan_episode(niche_id: str, today: str) -> dict | None:
    """Что строить серийного для ниши СЕГОДНЯ (НЕ мутирует состояние — вызвать record() после сборки):
      • {'part': 1}                          — начать новый сериал (Часть 1, клиффхэнгер)
      • {'part': 2, 'topic', 'premise'}      — продолжить (Часть 2, развязка)
      • None                                 — серийный эпизод сегодня уже был → строить ОБЫЧНОЕ видео
    Гарантия: не более 1 серийного эпизода в день на нишу; Часть 2 — строго в ДРУГОЙ день, чем Часть 1.
    """
    st = _load().get(niche_id, {})
    if st.get("serial_date") == today:          # серийный эпизод на сегодня уже сделан
        return None
    if st.get("part2_pending") and st.get("date_part1") != today:
        return {"part": 2, "topic": st.get("topic", ""), "premise": st.get("premise", "")}
    return {"part": 1}


def record(niche_id: str, part: int, today: str, topic: str = "", premise: str = "") -> None:
    """Зафиксировать выполненный серийный эпизод. Часть 1 → ждём Часть 2; Часть 2 → сериал закрыт."""
    d = _load()
    if part == 1:
        d[niche_id] = {"part2_pending": True, "topic": topic, "premise": premise[:300],
                       "date_part1": today, "serial_date": today}
    else:
        d[niche_id] = {"part2_pending": False, "serial_date": today}
    _save(d)
