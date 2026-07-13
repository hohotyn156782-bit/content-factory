#!/usr/bin/env python3
"""Git merge-driver для JSON-словарей в state/ (attempts.json, posted.json, serials.json...).

Параллельные CI-раны (например, youtube и tiktok в один день) коммитят state/ независимо —
обычный текстовый merge даёт конфликт, коммит-бэк падает и state рана теряется
(кейс 2026-07-13: tiktok-ран доставил видео, но потерял attempts/posted/history).

Семантика merge (три версии: base %O, ours %A, theirs %B):
- dict: объединение ключей, рекурсивно; ключ, удалённый одной стороной и не тронутый
  другой, остаётся удалённым (ротация старых дней в attempts.json);
- list: ours + элементы theirs, которых нет в ours (порядок сохраняем — списки здесь
  накопительные: ниши за день, попытки);
- скаляр: настоящий three-way — theirs не менял относительно base → берём ours,
  иначе theirs. Без сравнения с base сторона, коммитящая устаревшую копию файла
  (serials.py перезаписывает весь serials.json), тихо откатывала бы чужой свежий state.

Использование (настраивается в шаге коммит-бэка autopilot.yml + .gitattributes):
  git config merge.cfjson.driver "python3 tools/merge_state.py %O %A %B"
Выход 0 = смержено (результат записан в %A), не-0 = пусть git оставит конфликт.
"""
import json
import sys


def merge(base, ours, theirs):
    if isinstance(ours, dict) and isinstance(theirs, dict):
        out = dict(ours)
        b = base if isinstance(base, dict) else {}
        for k, tv in theirs.items():
            if k in ours:
                out[k] = merge(b.get(k), ours[k], tv)
            elif k in b and tv == b[k]:
                pass  # ours удалил ключ, theirs его не трогал — оставить удалённым
            else:
                out[k] = tv
        return out
    if isinstance(ours, list) and isinstance(theirs, list):
        return ours + [x for x in theirs if x not in ours]
    return ours if theirs == base else theirs


def main():
    base_p, ours_p, theirs_p = sys.argv[1:4]

    def load(p):
        try:
            with open(p, encoding="utf-8") as f:
                txt = f.read().strip()
            return json.loads(txt) if txt else {}
        except (OSError, json.JSONDecodeError):
            return None

    base, ours, theirs = load(base_p), load(ours_p), load(theirs_p)
    if ours is None or theirs is None:
        return 1
    merged = merge(base if base is not None else {}, ours, theirs)
    with open(ours_p, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
