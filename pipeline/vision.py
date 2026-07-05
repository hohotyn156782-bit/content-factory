"""Общий хелпер Gemini-зрения (image input) — БЕСПЛАТНО на free-tier (в отличие от генерации).

Один вход для всех фич, которым нужно «показать картинку модели и получить ответ»:
QA-обложка, авто-выбор обложки по кликабельности и т.п. Мульти-ключ (GEMINI_API_KEY через запятую),
ротация при 429/сбое, fail-open (нет ключей/квоты → None, вызывающий сам решает что делать).

ВАЖНО: ключи Google `AQ.…` работают ТОЛЬКО через заголовок x-goog-api-key (не ?key=).
"""
import os
import json
import base64
import pathlib
import urllib.request
import urllib.error

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

MODEL = "gemini-2.5-flash"
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"


def keys() -> list[str]:
    return [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]


def _mime(p: pathlib.Path) -> str:
    return "image/png" if p.suffix.lower() == ".png" else "image/jpeg"


def ask_json(prompt: str, images: list[pathlib.Path], max_tokens: int = 500) -> dict | None:
    """Показать модели картинки + промпт, получить JSON-объект. None при недоступности/ошибке.
    Промпт ОБЯЗАН требовать чистый JSON. Ротация ключей на 429/сбое."""
    ks = keys()
    if not ks:
        return None
    parts = [{"text": prompt}]
    for img in images:
        try:
            parts.append({"inline_data": {"mime_type": _mime(img),
                                          "data": base64.b64encode(img.read_bytes()).decode()}})
        except Exception:  # noqa: BLE001 — битый/отсутствующий файл пропускаем
            continue
    if len(parts) == 1:          # ни одной картинки не прочли
        return None
    payload = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens},
    }).encode()
    for key in ks:
        url = f"{_ENDPOINT}/{MODEL}:generateContent"
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=90).read())
        except urllib.error.HTTPError:
            continue            # 429/4xx → следующий ключ (fail-open)
        except Exception:  # noqa: BLE001 — сеть/таймаут → следующий ключ
            continue
        cand = (r.get("candidates") or [{}])[0]
        pieces = (cand.get("content") or {}).get("parts") or [{}]
        txt = (pieces[0].get("text") or "").strip()
        if not txt:
            continue
        try:
            d = json.loads(txt)
            if isinstance(d, dict):
                return d
        except (json.JSONDecodeError, TypeError):
            # ключ рабочий, но JSON битый — один strict-retry тем же ключом
            strict = json.dumps({"contents": [{"parts": [
                {"text": prompt + " Верни ТОЛЬКО валидный JSON без markdown, без пояснений."}] + parts[1:]}]}
            ).encode()
            try:
                req2 = urllib.request.Request(url, data=strict,
                                              headers={"Content-Type": "application/json",
                                                       "x-goog-api-key": key})
                r2 = json.loads(urllib.request.urlopen(req2, timeout=90).read())
                t2 = (((r2.get("candidates") or [{}])[0].get("content") or {}).get("parts")
                      or [{}])[0].get("text", "")
                d = json.loads(t2)
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
    return None


if __name__ == "__main__":
    core.load_local_secrets()
    print("Gemini-ключей:", len(keys()))
    if len(sys.argv) > 1:
        res = ask_json('Опиши картинку. Верни JSON {"desc": "..."}.', [pathlib.Path(sys.argv[1])])
        print(json.dumps(res, ensure_ascii=False))
