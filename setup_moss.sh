#!/usr/bin/env bash
# MOSS-TTS-Nano (OpenMOSS, Apache-2.0) в изолированном venv .venv-moss.
# Локальный TTS: 0.1B, ONNX, CPU-realtime, RU + клон голоса из 6-сек сэмпла.
# Грабли из XTTS-сетапа учтены: venv через `uv venv` (НЕ python -m venv), внутрь
# ставим pip и им; CPU-only torch с офиц. pytorch-индекса; пины версий; идемпотентно.
# Системный Python с боевыми ботами НЕ трогаем.
set -u
PROJ=/home/baronpavel/projects/content-factory
VENV=$PROJ/.venv-moss
PY="$VENV/bin/python"
REPO="$PROJ/.moss-tts-nano-src"          # исходники рантайма (onnx_tts_runtime не на PyPI)
DEST="/mnt/c/Users/BaronPavel/Desktop/Голоса — сравнение"
mkdir -p "$DEST"

echo "[1] venv (uv) + pip…"
[ -x "$PY" ] || uv venv "$VENV" --python 3.12
# КЛЮЧ (как в XTTS): ставим pip ВНУТРЬ venv и дальше работаем им, не uv-резолвером.
uv pip install --python "$PY" pip 2>&1 | tail -1

echo "[2] CPU torch/torchaudio с pytorch-индекса (рантайм MOSS использует их для аудио-I/O; долго)…"
# Рантаймовый инференс — ONNX (onnxruntime), но onnx_tts_runtime.py импортирует torch/torchaudio
# для чтения/ресемпла аудио. Берём CPU-wheel, чтобы не тянуть CUDA. Пины — по requirements репо.
"$PY" -m pip install --disable-pip-version-check "torch==2.7.0" "torchaudio==2.7.0" \
  --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -4
echo "    torch: $("$PY" -c 'import torch;print(torch.__version__)' 2>&1 | tail -1)"

echo "[3] зависимости MOSS-TTS-Nano (ONNX CPU)…"
# onnxruntime (CPU), huggingface_hub (докачка весов), soundfile/numpy (I/O wav),
# sentencepiece + WeTextProcessing (текст-фронтенд/нормализация), transformers (пин репо).
"$PY" -m pip install --disable-pip-version-check \
  "onnxruntime>=1.20.0" "huggingface_hub>=0.24" "soundfile" "numpy>=1.24,<2.1" \
  "sentencepiece>=0.1.99" "WeTextProcessing>=1.0.4.1" "transformers==4.57.1" 2>&1 | tail -8

echo "[4] исходники рантайма MOSS-TTS-Nano (onnx_tts_runtime не публикуется на PyPI)…"
if [ ! -d "$REPO/.git" ]; then
  rm -rf "$REPO"
  git clone --depth 1 https://github.com/OpenMOSS/MOSS-TTS-Nano.git "$REPO" 2>&1 | tail -3
else
  git -C "$REPO" pull --ff-only 2>&1 | tail -2
fi
# Ставим пакет moss-tts-nano из клона БЕЗ зависимостей (их уже зафиксировали выше CPU-вариантами,
# иначе pyproject притянет GPU/несовместимые версии). Даёт модуль onnx_tts_runtime в venv.
"$PY" -m pip install --disable-pip-version-check --no-deps -e "$REPO" 2>&1 | tail -4
echo "    onnx_tts_runtime: $("$PY" -c 'import onnx_tts_runtime; print("import ok")' 2>&1 | tail -1)"

echo "[5] предзагрузка ONNX-весов в кэш HF (чтобы первый ролик не тормозил)…"
"$PY" - <<'PY'
try:
    from huggingface_hub import snapshot_download
    for repo, patterns in [
        ("OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX", ["*.onnx", "*.data", "*.json", "tokenizer.model"]),
        ("OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX", ["*.onnx", "*.data", "*.json"]),
    ]:
        p = snapshot_download(repo_id=repo, allow_patterns=patterns)
        print(f"  OK {repo} -> {p}")
    print("WEIGHTS DONE")
except Exception as e:
    import traceback; traceback.print_exc()
    print("WEIGHTS FAILED:", str(e)[:200])
PY

echo "[6] смоук-тест: один RU-кусок дефолтным голосом…"
MOSS_THREADS=12 "$PY" - "$DEST" <<'PY'
import os, sys
try:
    from onnx_tts_runtime import OnnxTtsRuntime
    dest = sys.argv[1]
    rt = OnnxTtsRuntime(thread_count=12, execution_provider="cpu", output_dir=dest)
    out = f"{dest}/MOSS smoke.wav"
    res = rt.synthesize(
        text="Успех — это не везение. Это система привычек, которую ты выстраиваешь каждый день.",
        prompt_audio_path=None,
        output_audio_path=out,
    )
    path = (res or {}).get("audio_path", out)
    ok = os.path.exists(path) and os.path.getsize(path) > 0
    print("  OK MOSS smoke:" if ok else "  FAIL: пустой файл:", path)
    print("SMOKE DONE")
except Exception as e:
    import traceback; traceback.print_exc()
    print("SMOKE FAILED:", str(e)[:200])
PY
echo "Файлы:"; ls "$DEST" | grep -i moss || echo "(MOSS-сэмплов нет)"
echo "Готово: $VENV"
