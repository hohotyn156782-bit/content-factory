"""Воркер XTTS — ЗАПУСКАЕТСЯ В .venv-xtts (не в системном Python).

Мост между фабрикой (system python) и XTTS (venv): принимает JSON со списком кусков,
голосом-диктором и рабочей папкой, генерит по wav на кусок, отдаёт JSON с путями.

Вызов: <venv>/bin/python xtts_worker.py input.json output.json
input  = {"speaker": "Luis Moray", "lang": "ru", "workdir": "/...", "chunks": [{"text": "..."}]}
output = {"chunks": [{"audio": "/.../voice_00.wav"}, ...]}
"""
import os
import sys
import json

os.environ.setdefault("COQUI_TOS_AGREED", "1")

# ленивый и тяжёлый импорт — только при реальном вызове
import torch  # noqa: E402
torch.set_num_threads(int(os.environ.get("XTTS_THREADS", "12")))
from TTS.api import TTS  # noqa: E402

_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


def main():
    inp, outp = sys.argv[1], sys.argv[2]
    d = json.load(open(inp, encoding="utf-8"))
    speaker = d["speaker"]
    lang = d.get("lang", "ru")
    workdir = d["workdir"]
    os.makedirs(workdir, exist_ok=True)

    out = []
    err = None
    try:
        tts = TTS(_MODEL, progress_bar=False)
        for i, ch in enumerate(d["chunks"]):
            wav = os.path.join(workdir, f"voice_{i:02d}.wav")
            tts.tts_to_file(text=ch["text"], speaker=speaker, language=lang,
                            file_path=wav, split_sentences=True)
            out.append({"audio": wav})
    except Exception as e:  # noqa: BLE001 — отдаём причину наружу, а не падаем без следа
        err = f"{type(e).__name__}: {e}"

    payload = {"chunks": out}
    if err:
        payload["error"] = err
    json.dump(payload, open(outp, "w", encoding="utf-8"), ensure_ascii=False)
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
