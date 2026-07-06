"""Предполётная проверка токенов площадок — ловит протухшие/битые ДО дня публикации.

Meta-токены (IG/Threads) живут ~60 дней и являются причиной №1 тихой смерти автопилота:
пост молча не уходит, а владелец узнаёт лишь по отсутствию контента. Этот скрипт вызывает
verify() каждого connected+auto аккаунта, и при любой проблеме шлёт КРИТИЧНЫЙ алерт в TG.

Запуск:  python3 tools/preflight.py           # проверить все connected+auto аккаунты
         python3 tools/preflight.py --quiet   # только код возврата (0 ок, 1 есть проблемы)
Рекомендуется гонять раз в сутки (отдельный лёгкий cron), НЕ на каждый постинг.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402


def check() -> list[dict]:
    """Вернуть список проблем: [{platform, account, error}]. Пусто = всё живо."""
    core.load_local_secrets()
    from panel import db
    from adapters import instagram, threads, vk_video
    verifiers = {"instagram": instagram.verify, "threads": threads.verify, "vk": vk_video.verify}
    problems = []
    for b in db.list_bundles():
        if b.get("status", "active") != "active":
            continue
        allowed = set(core.get_niche(b.get("niche_id", "")).get("platforms", []) or [])
        for a in b.get("accounts", []):
            p = a.get("platform")
            if p not in verifiers or a.get("status") != "connected" or not a.get("auto_post"):
                continue
            if allowed and p not in allowed:      # площадка не используется этой нишей — пропускаем
                continue
            name = a.get("display_name") or a.get("ext_id")
            try:
                ok, msg = verifiers[p](a)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)[:160]
            if not ok:
                problems.append({"platform": p, "account": name, "error": msg})
    return problems


def main() -> int:
    quiet = "--quiet" in sys.argv
    problems = check()
    if not problems:
        if not quiet:
            print("✅ preflight: все токены живы")
        return 0
    import reporter
    lines = ["🛑 <b>Preflight: проблемы с токенами площадок</b>", ""]
    for pr in problems:
        lines.append(f"❌ {reporter.esc(pr['platform'])} · {reporter.esc(pr['account'])}: "
                     f"{reporter.esc(pr['error'])}")
    lines.append("\nПочини токен (Meta ~60 дней) — иначе публикации молча не уйдут.")
    text = "\n".join(lines)
    if not quiet:
        print(text)
    try:
        reporter.critical(text)
    except Exception as e:  # noqa: BLE001
        core.log_error("preflight.alert", e)
    return 1


if __name__ == "__main__":
    sys.exit(main())
