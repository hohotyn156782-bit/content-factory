"""Воркер MOSS-TTS-Nano — ЗАПУСКАЕТСЯ В .venv-moss (не в системном Python).

Локальный TTS без GPU: OpenMOSS MOSS-TTS-Nano (0.1B, ONNX, CPU-realtime, RU + клон
голоса из 6-сек сэмпла). Снимает потолок 8 ключей ElevenLabs и даёт разнообразие
голосов локально и бесплатно.

Мост между фабрикой (system python) и MOSS (venv): принимает JSON со списком кусков,
референс-голосом и рабочей папкой, генерит по wav на кусок, отдаёт JSON с путями.
Контракт ИДЕНТИЧЕН xtts_worker (тот же in/out, те же exit-коды).

Вызов: <venv>/bin/python moss_worker.py input.json output.json
input  = {"speaker": "<имя/путь референса или ''>", "ref_audio": "/path/to/6sec.wav",
          "lang": "ru", "workdir": "/...", "chunks": [{"text": "..."}]}
output = {"chunks": [{"audio": "/.../voice_00.wav"}, ...]}   (+ "error" при сбое, exit 1)

Модели тянутся huggingface_hub в кэш при первом запуске (предзагружены в setup_moss.sh):
  • OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX        — LLM + текст-фронтенд (sentencepiece)
  • OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX — аудио-токенайзер (кодек 48kHz)
"""
import os
import sys
import json

# Потоки CPU: MOSS-realtime на CPU, но даём столько же, сколько XTTS (управляемо через env).
os.environ.setdefault("OMP_NUM_THREADS", os.environ.get("MOSS_THREADS", "12"))

# Ленивые и тяжёлые импорты — только при реальном вызове (onnxruntime/torch-io/numpy/soundfile).
# Рантайм MOSS-TTS-Nano (onnx_tts_runtime) ставится pip-пакетом moss-tts-nano из репозитория;
# он сам грузит ONNX-сессии через onnxruntime и при отсутствии весов докачивает их
# huggingface_hub.snapshot_download. См. README: https://github.com/OpenMOSS/MOSS-TTS-Nano
from onnx_tts_runtime import OnnxTtsRuntime  # noqa: E402


def main():
    inp, outp = sys.argv[1], sys.argv[2]
    d = json.load(open(inp, encoding="utf-8"))
    speaker = d.get("speaker", "")          # имя пресета/путь — опционально для MOSS
    ref_audio = d.get("ref_audio", "")      # 6-сек образец для клона голоса (главный путь MOSS)
    # lang в MOSS-TTS-Nano определяется по тексту/референсу автоматически (мультиязычная модель),
    # отдельного параметра нет — держим в контракте ради совместимости с xtts_worker.
    lang = d.get("lang", "ru")              # noqa: F841 — для совместимости контракта
    workdir = d["workdir"]
    os.makedirs(workdir, exist_ok=True)

    out = []
    err = None
    try:
        threads = int(os.environ.get("MOSS_THREADS", "12"))
        # model_dir=None → рантайм сам подхватит локальный кэш или докачает веса с HF.
        # execution_provider="cpu" — без GPU (см. setup_moss.sh, CPU-only).
        runtime = OnnxTtsRuntime(
            model_dir=os.environ.get("MOSS_MODEL_DIR") or None,
            thread_count=threads,
            execution_provider="cpu",
            output_dir=workdir,
        )

        # Источник голоса: 6-сек референс-сэмпл (voice-clone). Если его нет — пробуем speaker
        # как путь к wav; если и его нет — генерим дефолтным голосом модели (prompt_audio=None).
        prompt_audio = None
        if ref_audio and os.path.exists(ref_audio):
            prompt_audio = ref_audio
        elif speaker and os.path.exists(speaker):
            prompt_audio = speaker

        for i, ch in enumerate(d["chunks"]):
            wav = os.path.join(workdir, f"voice_{i:02d}.wav")
            # TODO(MOSS): сверить точную сигнатуру с README репо OpenMOSS/MOSS-TTS-Nano
            # (файл onnx_tts_runtime.py, метод OnnxTtsRuntime.synthesize). По состоянию репо:
            # synthesize(text=..., prompt_audio_path=<ref wav для клона>, output_audio_path=<out>)
            # пишет wav сам (нативно 48kHz, 2 канала) и возвращает dict с audio_path/sample_rate.
            res = runtime.synthesize(
                text=ch["text"],
                prompt_audio_path=prompt_audio,
                output_audio_path=wav,
            )
            # Подстраховка: рантайм мог записать файл по своему пути (res["audio_path"]).
            audio_path = wav
            if not (os.path.exists(wav) and os.path.getsize(wav) > 0):
                rp = (res or {}).get("audio_path")
                if rp and os.path.exists(rp):
                    audio_path = rp
                else:
                    raise RuntimeError(f"MOSS не записал аудио для куска {i}")
            out.append({"audio": audio_path})
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
