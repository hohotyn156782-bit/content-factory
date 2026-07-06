"""Публикация вертикального видео в VK-сообщества (полная автоматизация).

Переиспользуем уже работающие в проде паблики content-engine: токены сообществ
VK_ACC1_TOKEN/VK_ACC2_TOKEN (наследуются из ~/.config/content-engine/secrets.env),
owner_id берём из content-engine/accounts.json (или из env VK_VIDEO_TARGETS).

Поток VK: video.save → PUT файла на upload_url → wall.post с attachment video{owner}_{id}.

ВАЖНО: токен сообщества должен иметь право на видео. Если video.save вернёт ошибку
прав — перевыпустить community-токен с доступом к видео.
"""
import os
import json
import pathlib

import requests

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

API = "https://api.vk.com/method/"
VER = "5.199"
CE_ACCOUNTS = pathlib.Path("/mnt/c/Users/BaronPavel/Desktop/tg-bots-business/content-engine/accounts.json")


def _call(method: str, token: str, **params):
    params.update({"access_token": token, "v": VER})
    r = requests.post(API + method, data=params, timeout=40)
    j = r.json()
    if "error" in j:
        err = j["error"]
        return None, f"VK error {err.get('error_code')}: {err.get('error_msg')}"
    return j.get("response"), None


_USER_TOKEN_CACHE: dict[str, str] = {}   # group_id -> рабочий user-токен (админ группы)


def _user_tokens() -> list[str]:
    """Все VK user-токены из env: VK_USER_TOKEN, VK_USER_TOKEN2, ... (+ через запятую). Для video.save."""
    out, seen = [], set()
    for k, v in os.environ.items():
        if k.startswith("VK_USER_TOKEN") and v:
            for t in v.split(","):
                t = t.strip()
                if t and t not in seen:
                    seen.add(t)
                    out.append(t)
    return out


def _user_token_for(group_id: str) -> str | None:
    """Найти VK user-токен, чей владелец АДМИН этой группы (community-токены video.save не умеют).
    Кэшируем, чтобы не дёргать API на каждый ролик."""
    gid = str(group_id).lstrip("-")
    if gid in _USER_TOKEN_CACHE:
        return _USER_TOKEN_CACHE[gid]
    for tok in _user_tokens():
        resp, err = _call("groups.getById", tok, group_id=gid, fields="is_admin")
        if err or not resp:
            continue
        groups = resp.get("groups", resp) if isinstance(resp, dict) else resp
        g = groups[0] if groups else {}
        if g.get("is_admin"):
            _USER_TOKEN_CACHE[gid] = tok
            return tok
    return None


def verify(account: dict | None = None) -> tuple[bool, str]:
    """Предполётная проверка готовности VK-аккаунта к автопостингу видео (для tools/preflight):
      (1) community-токен из secret_ref жив (groups.getById),
      (2) есть VK user-токен с правами админа группы — без него video.save невозможен
          (главная тихая причина, по которой VK-видео перестаёт публиковаться).
    Возвращает (ok, msg) — совместимо с instagram.verify / threads.verify."""
    account = account or {}
    gid = str(account.get("ext_id", "")).strip().lstrip("-")
    if not gid:
        return False, "нет ext_id (owner_id) у VK-аккаунта"
    ref = (account.get("secret_ref") or "").strip()
    ctok = os.environ.get(ref, "").strip() if ref else ""
    if ref and not ctok:
        return False, f"нет community-токена {ref} в env"
    if ctok:
        resp, err = _call("groups.getById", ctok, group_id=gid)
        if err:
            return False, f"community-токен не работает: {err}"
    # user-токен-админ нужен ТОЛЬКО для видео (video.save). Текстовые VK-аккаунты постят
    # community-токеном (wall.post) — им админ-токен не требуется, не флагаем ложно.
    if (account.get("kind") or "").strip().lower() == "video" and not _user_token_for(gid):
        return False, "нет VK user-токена-админа группы (video.save невозможен)"
    return True, "ok"


def _first_comment(token: str, owner_id: str, post_id, message: str) -> None:
    """Авто-первый-коммент под свежим постом: буст вовлечённости (алгоритм VK любит комменты) +
    воронка в TG-канал. Текст — из env VK_FIRST_COMMENT (можно с {tg}). Best-effort, пост не роняем."""
    msg = (message or os.environ.get("VK_FIRST_COMMENT", "")).strip()
    if not msg or not post_id:
        return
    try:
        _call("wall.createComment", token, owner_id=owner_id, post_id=post_id,
              from_group=1, message=msg[:1000])
    except Exception:  # noqa: BLE001 — коммент не критичен
        pass


def _account_target(account: dict) -> tuple[str, str] | None:
    """(owner_id, token) из аккаунта панели: ext_id = owner_id, secret_ref = имя env с токеном."""
    if not account:
        return None
    owner = str(account.get("ext_id") or "").strip()
    token = os.environ.get(str(account.get("secret_ref") or "").strip(), "")
    if owner and token:
        return owner, token
    return None


def _targets() -> list[tuple[str, str]]:
    """Список (owner_id, token) куда постим. env VK_VIDEO_TARGETS='-id:TOKEN_ENV,...' или content-engine."""
    raw = os.environ.get("VK_VIDEO_TARGETS", "").strip()
    if raw:
        out = []
        for part in raw.split(","):
            owner, _, tok_env = part.partition(":")
            tok = os.environ.get(tok_env.strip(), "")
            if owner.strip() and tok:
                out.append((owner.strip(), tok))
        return out
    if CE_ACCOUNTS.exists():
        data = json.loads(CE_ACCOUNTS.read_text(encoding="utf-8"))
        out = []
        for a in data.get("accounts", []):
            if a.get("platform") == "vk" and a.get("enabled", True):
                tok = os.environ.get(a["secret_token"], "")
                if tok:
                    out.append((str(a["owner_id"]), tok))
        return out
    return []


def _publish_one(video_path: str, name: str, description: str, owner_id: str, token: str):
    group_id = owner_id.lstrip("-")
    # video.save умеет ТОЛЬКО user-токен с админ-правами на группу (community-токен — нет).
    # Подбираем user-токен по группе; community-токен (token) оставляем для текста-фолбэка.
    user_token = _user_token_for(group_id)
    if not user_token:
        return False, (f"нет VK user-токена с админ-правами на группу {group_id} — видео залить нельзя. "
                       f"Сделай свой аккаунт админом этой группы и добавь VK_USER_TOKEN.")
    token = user_token
    resp, err = _call("video.save", token, name=name[:128], description=description[:1000], group_id=group_id)
    if err:
        return False, err
    upload_url = resp.get("upload_url")
    if not upload_url:
        return False, f"нет upload_url: {resp}"
    with open(video_path, "rb") as f:
        up = requests.post(upload_url, files={"video_file": f}, timeout=300).json()
    # video_id/owner_id приходят из video.save (resp), а НЕ из ответа upload-сервера (up):
    # up отдаёт лишь size/video_hash. Файл POST-им обязательно (запускает обработку на стороне VK).
    vid = resp.get("video_id") or up.get("video_id")
    vid_owner = resp.get("owner_id", f"-{group_id}")
    if not vid:
        return False, f"video.save без video_id: {resp} / upload: {up}"
    attachment = f"video{vid_owner}_{vid}"
    wp, werr = _call("wall.post", token, owner_id=owner_id, from_group=1,
                     message=description, attachments=attachment)
    if werr:
        # видео залито, но поста на стене нет → слот НЕ опубликован, пусть планировщик ретракнет
        return False, f"видео залито ({attachment}), но wall.post не прошёл: {werr}"
    post_id = wp.get("post_id")
    _first_comment(token, owner_id, post_id, "")   # авто-первый-коммент (CTA/воронка в TG)
    return True, {"video": attachment, "url": f"https://vk.com/wall{owner_id}_{post_id}"}


def publish_text(message: str, account: dict) -> tuple[bool, dict | str]:
    """Текстовый пост на стену VK-сообщества (kind=text). Только wall.post, без видео."""
    tgt = _account_target(account)
    if not tgt:
        return False, "нет кред VK (ext_id/secret_ref) у аккаунта"
    owner, token = tgt
    if not (message or "").strip():
        return False, "пустой текст"
    wp, werr = _call("wall.post", token, owner_id=owner, from_group=1, message=message[:15000])
    if werr:
        return False, werr
    post_id = wp.get("post_id")
    _first_comment(token, owner, post_id, "")   # авто-первый-коммент (CTA/воронка в TG)
    return True, {"url": f"https://vk.com/wall{owner}_{post_id}", "post_id": post_id}


def publish(video_path: str, meta: dict, account: dict | None = None):
    """Опубликовать видео в VK. Если передан account связки — постим ТОЛЬКО в него;
    иначе фолбэк на старый список целей (content-engine). Возвращает (ok, dict)."""
    tgt = _account_target(account) if account else None
    if tgt:
        cap = meta.get("captions", {}).get("vk", {}).get("caption", "") or meta.get("topic", "")
        name = meta.get("topic", "video")
        ok, res = _publish_one(video_path, name, cap, tgt[0], tgt[1])
        url = res.get("url") if isinstance(res, dict) else None
        return ok, {"url": url, "ok_count": 1 if ok else 0,
                    "targets": [{"owner": tgt[0], "ok": ok, "result": res}]}
    targets = _targets()
    if not targets:
        return False, "нет VK-целей (account без ext_id/secret_ref, env VK_VIDEO_TARGETS или content-engine/accounts.json + токены)"
    cap = meta.get("captions", {}).get("vk", {}).get("caption", "") or meta.get("topic", "")
    name = meta.get("topic", "video")
    results = []
    ok_any = False
    for owner, token in targets:
        ok, res = _publish_one(video_path, name, cap, owner, token)
        ok_any = ok_any or ok
        results.append({"owner": owner, "ok": ok, "result": res})
    first_url = next((t["result"].get("url") for t in results
                      if t.get("ok") and isinstance(t.get("result"), dict) and t["result"].get("url")), None)
    n_ok = sum(1 for t in results if t.get("ok"))
    return ok_any, {"url": first_url, "ok_count": n_ok, "targets": results}


if __name__ == "__main__":
    core.load_local_secrets()
    print("VK targets:", [t[0] for t in _targets()])
