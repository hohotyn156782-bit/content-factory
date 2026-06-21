"""Слой данных панели (SQLite, stdlib).

Сущности:
  bundles  — связки (1 тема на все площадки): имя, ниша, статус.
  accounts — по строке на площадку внутри связки (instagram/threads/vk/tiktok/youtube):
             отображаемое имя, ссылка, статус подключения, авто-постинг, подписчики (ручной ввод до API).
  content  — единицы контента (ролик): тема, видео, подпись, статус; targets — JSON со статусом по каждой площадке.

Статистика день/неделя/месяц по ПУБЛИКАЦИЯМ считается из нашей же базы (реальные данные).
Просмотры/охваты — позже, по мере подключения API площадок.
"""
import json
import sqlite3
import pathlib
import datetime as dt

DB_PATH = pathlib.Path(__file__).resolve().parent / "panel.db"
SCHEDULE_FILE = pathlib.Path(__file__).resolve().parent.parent / "schedule.json"

PLATFORMS = ["instagram", "threads", "vk", "tiktok", "youtube"]
# tiktok — всегда ручной (анти-бан); остальные авто по умолчанию
AUTO_DEFAULT = {"instagram": 1, "threads": 1, "vk": 1, "tiktok": 0, "youtube": 1}
# тип контента площадки: видео (IG/TikTok/YouTube) или текст (Threads/VK)
KIND_DEFAULT = {"instagram": "video", "threads": "text", "vk": "text",
                "tiktok": "video", "youtube": "video"}
PLATFORM_LABEL = {"instagram": "Instagram", "threads": "Threads", "vk": "VK",
                  "tiktok": "TikTok", "youtube": "YouTube"}
# дефолтные времена выкладки в плане дня (МСК), под крон
SLOT_DEFAULT = {"instagram": "12:00", "tiktok": "13:00", "youtube": "15:00",
                "threads": "10:00", "vk": "18:00"}

TZ = dt.timezone(dt.timedelta(hours=3))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone(TZ).isoformat()


def _mask_secrets(obj):
    """Рекурсивно маскировать секреты (токены/ключи/пароли) перед записью в БД (defence-in-depth)."""
    import re as _re
    if isinstance(obj, dict):
        return {k: ("***" if _re.search(r"(?i)(access_token|token|secret|api_?key|password)", str(k)) else _mask_secrets(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_secrets(x) for x in obj]
    return obj


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")   # панель пишет из нескольких потоков → ждём, а не падаем
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS bundles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        niche_id TEXT,
        status TEXT DEFAULT 'active',
        created TEXT
    );
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bundle_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        url TEXT DEFAULT '',
        kind TEXT DEFAULT 'video',              -- video|text
        status TEXT DEFAULT 'pending',          -- pending|connected|manual
        auto_post INTEGER DEFAULT 1,
        subscribers INTEGER DEFAULT 0,
        secret_ref TEXT DEFAULT '',             -- имя env-переменной с токеном (сам токен в secrets.env)
        ext_id TEXT DEFAULT '',                 -- owner_id (VK) / user_id (Threads/IG) / channel (YouTube)
        created TEXT,
        FOREIGN KEY(bundle_id) REFERENCES bundles(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS content (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bundle_id INTEGER NOT NULL,
        niche_id TEXT,
        topic TEXT DEFAULT '',
        video_path TEXT DEFAULT '',
        dir TEXT DEFAULT '',
        duration REAL DEFAULT 0,
        caption TEXT DEFAULT '',
        meta_json TEXT DEFAULT '{}',
        targets TEXT DEFAULT '{}',              -- {platform: {status,url,posted_at}}
        status TEXT DEFAULT 'generating',       -- generating|queued|published|partial|pending_manual|failed
        error TEXT DEFAULT '',
        created TEXT,
        FOREIGN KEY(bundle_id) REFERENCES bundles(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bundle_id INTEGER NOT NULL,
        date TEXT NOT NULL,                     -- YYYY-MM-DD
        platform TEXT NOT NULL,
        kind TEXT DEFAULT 'video',              -- video|text
        slot_time TEXT DEFAULT '12:00',         -- HH:MM МСК
        topic TEXT DEFAULT '',
        text TEXT DEFAULT '',                   -- для текстовых постов
        content_id INTEGER,                     -- для видео-слотов (ссылка на content)
        status TEXT DEFAULT 'planned',          -- planned|generating|ready|posted|manual_pending|failed
        source TEXT DEFAULT 'manual',           -- manual|ai|parser
        created TEXT,
        FOREIGN KEY(bundle_id) REFERENCES bundles(id) ON DELETE CASCADE
    );
    """)
    # миграции для уже существующей базы
    cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)")]
    if "kind" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN kind TEXT DEFAULT 'video'")
        for p in PLATFORMS:
            conn.execute("UPDATE accounts SET kind=? WHERE platform=?", (KIND_DEFAULT[p], p))
    if "secret_ref" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN secret_ref TEXT DEFAULT ''")
    if "ext_id" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN ext_id TEXT DEFAULT ''")
    cols_b = [r[1] for r in conn.execute("PRAGMA table_info(bundles)")]
    if "require_approval" not in cols_b:
        conn.execute("ALTER TABLE bundles ADD COLUMN require_approval INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


# ──────────────────────────── Bundles ────────────────────────────

def create_bundle(name: str, niche_id: str) -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO bundles(name, niche_id, created) VALUES (?,?,?)",
                       (name, niche_id, _now()))
    bid = cur.lastrowid
    for p in PLATFORMS:
        conn.execute(
            "INSERT INTO accounts(bundle_id, platform, auto_post, kind, status, created) VALUES (?,?,?,?,?,?)",
            (bid, p, AUTO_DEFAULT[p], KIND_DEFAULT[p], "manual" if p == "tiktok" else "pending", _now()))
    conn.commit()
    conn.close()
    return bid


def add_account(bundle_id: int, platform: str) -> int:
    """Добавить ещё один аккаунт на платформу в связке (можно несколько на платформу)."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO accounts(bundle_id, platform, auto_post, kind, status, created) VALUES (?,?,?,?,?,?)",
        (bundle_id, platform, AUTO_DEFAULT.get(platform, 1), KIND_DEFAULT.get(platform, "video"),
         "manual" if platform == "tiktok" else "pending", _now()))
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    return aid


def delete_account(aid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
    conn.commit()
    conn.close()


def list_bundles() -> list[dict]:
    conn = get_conn()
    bundles = [dict(r) for r in conn.execute("SELECT * FROM bundles ORDER BY id")]
    for b in bundles:
        b["accounts"] = [dict(r) for r in conn.execute(
            "SELECT * FROM accounts WHERE bundle_id=? ORDER BY id", (b["id"],))]
        b["stats"] = _bundle_stats(conn, b["id"])
    conn.close()
    return bundles


def get_bundle(bid: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM bundles WHERE id=?", (bid,)).fetchone()
    if not row:
        conn.close()
        return None
    b = dict(row)
    b["accounts"] = [dict(r) for r in conn.execute(
        "SELECT * FROM accounts WHERE bundle_id=? ORDER BY id", (bid,))]
    b["stats"] = _bundle_stats(conn, bid)
    conn.close()
    return b


def update_bundle(bid: int, **fields) -> None:
    allowed = {"name", "niche_id", "status", "require_approval"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = get_conn()
    conn.execute(f"UPDATE bundles SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                 (*sets.values(), bid))
    conn.commit()
    conn.close()


def delete_bundle(bid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM bundles WHERE id=?", (bid,))
    conn.commit()
    conn.close()


# ──────────────────────────── Accounts ────────────────────────────

def update_account(aid: int, **fields) -> None:
    allowed = {"display_name", "url", "status", "auto_post", "subscribers", "kind", "secret_ref", "ext_id"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = get_conn()
    conn.execute(f"UPDATE accounts SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                 (*sets.values(), aid))
    conn.commit()
    conn.close()


# ──────────────────────────── Content ────────────────────────────

def create_content(bundle_id: int, niche_id: str, topic: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO content(bundle_id, niche_id, topic, status, created) VALUES (?,?,?,?,?)",
        (bundle_id, niche_id, topic, "generating", _now()))
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    return cid


def finalize_content(cid: int, *, video_path: str, dir: str, duration: float,
                     caption: str, meta: dict, targets: dict, status: str, topic: str = "") -> bool:
    """Атомарно завершить генерацию. True=успех, False=строку уже зарипали (status != 'generating')."""
    conn = get_conn()
    cur = conn.execute("""UPDATE content SET topic=COALESCE(NULLIF(?,''), topic), video_path=?, dir=?,
                    duration=?, caption=?, meta_json=?, targets=?, status=? WHERE id=? AND status='generating'""",
                       (topic, video_path, dir, duration, caption,
                        json.dumps(_mask_secrets(meta), ensure_ascii=False),
                        json.dumps(_mask_secrets(targets), ensure_ascii=False), status, cid))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def fail_content(cid: int, error: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE content SET status='failed', error=? WHERE id=?", (error[:500], cid))
    conn.commit()
    conn.close()


def set_targets(cid: int, targets: dict, status: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE content SET targets=?, status=? WHERE id=?",
                 (json.dumps(_mask_secrets(targets), ensure_ascii=False), status, cid))
    conn.commit()
    conn.close()


def update_target(cid: int, platform: str, payload: dict) -> None:
    """Атомарно записать targets[platform]=payload и пересчитать сводный статус content — без RMW-гонки."""
    conn = get_conn()
    try:
        conn.isolation_level = None  # для явного BEGIN IMMEDIATE
        conn.execute("BEGIN IMMEDIATE")
        safe = _mask_secrets(payload)
        conn.execute(
            "UPDATE content SET targets=json_set(COALESCE(NULLIF(targets,''),'{}'), '$.'||?, json(?)) WHERE id=?",
            (platform, json.dumps(safe, ensure_ascii=False), cid))
        row = conn.execute("SELECT targets FROM content WHERE id=?", (cid,)).fetchone()
        tg = json.loads((row["targets"] if row and row["targets"] else "{}") or "{}")
        auto = [v for k, v in tg.items() if k != "tiktok" and isinstance(v, dict)]
        if auto and all(v.get("status") == "published" for v in auto):
            st = "published"
        elif any(isinstance(v, dict) and v.get("status") == "published" for v in tg.values()):
            st = "partial"
        elif tg.get("tiktok"):
            st = "pending_manual"
        else:
            st = "queued"
        conn.execute("UPDATE content SET status=? WHERE id=?", (st, cid))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"[db.update_target] {type(e).__name__}: {e}")
    finally:
        conn.close()


def get_content(cid: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM content WHERE id=?", (cid,)).fetchone()
    conn.close()
    return _content_row(row) if row else None


def list_content(bundle_id: int | None = None, status: str | None = None) -> list[dict]:
    conn = get_conn()
    q, args = "SELECT * FROM content", []
    where = []
    if bundle_id:
        where.append("bundle_id=?"); args.append(bundle_id)
    if status:
        where.append("status=?"); args.append(status)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC"
    rows = [_content_row(r) for r in conn.execute(q, args)]
    conn.close()
    return rows


def delete_content(cid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM content WHERE id=?", (cid,))
    conn.commit()
    conn.close()


def _content_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["targets"] = json.loads(d.get("targets") or "{}")
    d["meta"] = json.loads(d.get("meta_json") or "{}")
    d.pop("meta_json", None)
    return d


# ──────────────────────────── Статистика ────────────────────────────

def _bundle_stats(conn: sqlite3.Connection, bid: int) -> dict:
    """Подписчики (сумма ручных) + публикации за день/неделю/месяц (реально из базы)."""
    subs = conn.execute("SELECT COALESCE(SUM(subscribers),0) FROM accounts WHERE bundle_id=?",
                        (bid,)).fetchone()[0]
    now = dt.datetime.now(dt.timezone.utc).astimezone(TZ)
    windows = {"day": 1, "week": 7, "month": 30}
    posted = {}
    for key, days in windows.items():
        cutoff = (now - dt.timedelta(days=days)).isoformat()
        # считаем опубликованные таргеты (по posted_at в targets) — приближённо по content.created
        n = conn.execute(
            "SELECT COUNT(*) FROM content WHERE bundle_id=? AND status IN ('published','partial','pending_manual') AND created>=?",
            (bid, cutoff)).fetchone()[0]
        posted[key] = n
    queued = conn.execute(
        "SELECT COUNT(*) FROM content WHERE bundle_id=? AND status='queued'", (bid,)).fetchone()[0]
    pending_manual = conn.execute(
        "SELECT COUNT(*) FROM content WHERE bundle_id=? AND status='pending_manual'", (bid,)).fetchone()[0]
    return {"subscribers": subs, "posted": posted, "queued": queued, "pending_manual": pending_manual}


def reap_stuck_generating(older_than_min: int = 180) -> int:
    """Перевести в 'failed' контент, зависший в 'generating' дольше N минут (краш во время сборки).
    Иначе панель показывает вечный спиннер. Также чистит зависшие слоты плана. Возвращает число вычищенных content."""
    conn = get_conn()
    cutoff = (dt.datetime.now(dt.timezone.utc).astimezone(TZ) - dt.timedelta(minutes=older_than_min)).isoformat()
    cur = conn.execute("UPDATE content SET status='failed', error='зависло в generating (краш сборки)' "
                       "WHERE status='generating' AND created < ?", (cutoff,))
    n = cur.rowcount
    conn.execute("UPDATE plan SET status='failed' WHERE status='generating' AND created < ?", (cutoff,))
    conn.commit()
    conn.close()
    return n


def overview() -> dict:
    conn = get_conn()
    nb = conn.execute("SELECT COUNT(*) FROM bundles").fetchone()[0]
    subs = conn.execute("SELECT COALESCE(SUM(subscribers),0) FROM accounts").fetchone()[0]
    queued = conn.execute("SELECT COUNT(*) FROM content WHERE status='queued'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM content WHERE status='pending_manual'").fetchone()[0]
    generating = conn.execute("SELECT COUNT(*) FROM content WHERE status='generating'").fetchone()[0]
    qa_failed = conn.execute("SELECT COUNT(*) FROM content WHERE status='qa_failed'").fetchone()[0]
    conn.close()
    return {"bundles": nb, "subscribers": subs, "queued": queued, "qa_failed": qa_failed,
            "pending_manual": pending, "generating": generating}


# ──────────────────────────── План дня ────────────────────────────

def today() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def slot_for(platform: str, date: str) -> str:
    """Время выкладки площадки по дню недели из schedule.json (фолбэк — SLOT_DEFAULT)."""
    try:
        wd = dt.date.fromisoformat(date).weekday()  # 0=пн
        sl = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8")).get("weekday_slots", {}).get(platform)
        if sl and len(sl) == 7:
            return sl[wd]
    except Exception:  # noqa: BLE001
        pass
    return SLOT_DEFAULT.get(platform, "12:00")


def create_plan_item(bundle_id: int, date: str, platform: str, kind: str,
                     slot_time: str, topic: str = "", source: str = "manual") -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO plan(bundle_id, date, platform, kind, slot_time, topic, source, created)
           VALUES (?,?,?,?,?,?,?,?)""",
        (bundle_id, date, platform, kind, slot_time, topic, source, _now()))
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def list_plan(bundle_id: int, date: str) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM plan WHERE bundle_id=? AND date=? ORDER BY slot_time, id", (bundle_id, date))]
    # подтянуть инфо о видео-контенте
    for r in rows:
        if r.get("content_id"):
            c = conn.execute("SELECT video_path, duration, status FROM content WHERE id=?",
                             (r["content_id"],)).fetchone()
            r["content"] = dict(c) if c else None
    conn.close()
    return rows


def get_plan_item(pid: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM plan WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_plan_item(pid: int, **fields) -> None:
    allowed = {"kind", "slot_time", "topic", "text", "content_id", "status", "source"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = get_conn()
    conn.execute(f"UPDATE plan SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                 (*sets.values(), pid))
    conn.commit()
    conn.close()


def claim_plan_item(pid: int, expect: str, new: str) -> bool:
    """Атомарно перевести слот expect→new. True только если статус был expect (иначе другой процесс уже забрал)."""
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE plan SET status=? WHERE id=? AND status=?", (new, pid, expect))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def delete_plan_item(pid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM plan WHERE id=?", (pid,))
    conn.commit()
    conn.close()


def clear_plan(bundle_id: int, date: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM plan WHERE bundle_id=? AND date=?", (bundle_id, date))
    conn.commit()
    conn.close()
