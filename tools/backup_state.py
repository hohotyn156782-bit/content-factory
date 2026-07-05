"""Снапшот критичного состояния: serials.json / posts.jsonl / posted.json / history.jsonl.

Эти файлы живут в state/ (в репо — CI-раннеры эфемерны). Порча ОДНОГО из них молча ломает
сериалы (одна история на две площадки), идемпотентность (анти-дубль) или леджер аналитики.
Атомарная запись уже есть, но не спасёт от логической порчи/ошибочного коммита — снапшоты дают откат.

factory.db НЕ бэкапим: он регенерится из history.jsonl (gitignored, DATA_ROOT).

  python3 tools/backup_state.py              # снапшот + подчистка старых (хранит последние KEEP)
  python3 tools/backup_state.py list         # список снапшотов
  python3 tools/backup_state.py restore <стамп>   # восстановить файлы из снапшота
"""
import sys
import json
import shutil
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

STATE = core.ROOT / "state"
BACKUPS = STATE / "backups"
KEEP = 14                                     # ~2 недели ежедневных снапшотов
_FILES = ("serials.json", "posts.jsonl", "posted.json", "history.jsonl")


def _state_files() -> list[pathlib.Path]:
    return [STATE / f for f in _FILES if (STATE / f).exists()]


def _integrity(p: pathlib.Path) -> bool:
    """Грубая проверка целостности: JSON парсится, JSONL — каждая непустая строка парсится."""
    try:
        if p.suffix == ".json":
            json.loads(p.read_text(encoding="utf-8") or "null")
        elif p.suffix == ".jsonl":
            for ln in p.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    json.loads(ln)
        return True
    except Exception:  # noqa: BLE001
        return False


def backup() -> pathlib.Path | None:
    files = _state_files()
    if not files:
        print("нет state-файлов для бэкапа")
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = core.stamp()
    dst = BACKUPS / stamp
    dst.mkdir(parents=True, exist_ok=True)
    saved, bad = [], []
    for p in files:
        shutil.copy2(p, dst / p.name)
        (saved if _integrity(p) else bad).append(p.name)
    (dst / "_manifest.json").write_text(json.dumps(
        {"stamp": stamp, "files": saved, "corrupt_at_backup": bad},
        ensure_ascii=False, indent=2), encoding="utf-8")
    _prune()
    msg = f"✅ снапшот state → {dst.name}: {', '.join(saved)}"
    if bad:
        msg += f"  ⚠️ битые на момент бэкапа: {', '.join(bad)}"
    print(msg)
    core.log("backup_state: снапшот сделан", stamp=stamp, files=saved, corrupt=bad)
    return dst


def _prune() -> None:
    snaps = sorted([d for d in BACKUPS.iterdir() if d.is_dir()], key=lambda d: d.name)
    for old in snaps[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def list_snapshots() -> None:
    if not BACKUPS.exists():
        print("снапшотов нет")
        return
    for d in sorted([x for x in BACKUPS.iterdir() if x.is_dir()], key=lambda x: x.name):
        files = [p.name for p in d.iterdir() if p.name != "_manifest.json"]
        print(f"  {d.name}: {', '.join(files)}")


def restore(stamp: str) -> int:
    src = BACKUPS / stamp
    if not src.is_dir():
        print(f"снапшот {stamp} не найден")
        return 1
    # перед перезаписью — защитный снапшот текущего состояния
    backup()
    n = 0
    for p in src.iterdir():
        if p.name == "_manifest.json":
            continue
        shutil.copy2(p, STATE / p.name)
        n += 1
    print(f"✅ восстановлено файлов: {n} из снапшота {stamp}")
    core.log("backup_state: восстановление", stamp=stamp, files=n)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        backup()
        return 0
    if args[0] == "list":
        list_snapshots()
        return 0
    if args[0] == "restore" and len(args) > 1:
        return restore(args[1])
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
