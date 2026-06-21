#!/usr/bin/env bash
# XTTS v2 в изолированном venv. Ключ: CPU-torch ставим с ОФИЦИАЛЬНОГО индекса pytorch
# (чистые wheel), потом coqui-tts. Системный Python с боевыми ботами не трогаем.
set -u
PROJ=/home/baronpavel/projects/content-factory
VENV=$PROJ/.venv-xtts
DEST="/mnt/c/Users/BaronPavel/Desktop/Голоса — сравнение"
mkdir -p "$DEST"
PY="$VENV/bin/python"

echo "[1] venv (uv) + pip…"
[ -x "$PY" ] || uv venv "$VENV" --python 3.12
uv pip install --python "$PY" pip 2>&1 | tail -1

echo "[2] CPU torch/torchaudio с pytorch-индекса (долго)…"
"$PY" -m pip install --disable-pip-version-check "torch==2.5.1" "torchaudio==2.5.1" \
  --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -4
echo "    torch: $("$PY" -c 'import torch;print(torch.__version__)' 2>&1 | tail -1)"

echo "[3] coqui-tts (torch уже стоит)…"
"$PY" -m pip install --disable-pip-version-check coqui-tts "numpy<2.1" 2>&1 | tail -8
# coqui-tts тянет transformers>=4.57, но его XTTS-код ломается на 4.57 (нет isin_mps_friendly).
# 4.56.2 — последняя, где есть и isin_mps_friendly, и is_torch_greater_or_equal. Фиксируем.
"$PY" -m pip install --disable-pip-version-check "transformers==4.56.2" 2>&1 | tail -2
echo "    TTS: $("$PY" -c 'from TTS.api import TTS;print(\"import ok\")' 2>&1 | tail -1)"

echo "[4] модель XTTS v2 + генерация мужских сэмплов…"
COQUI_TOS_AGREED=1 "$PY" - "$DEST" <<'PY'
import os, sys
os.environ["COQUI_TOS_AGREED"]="1"
import torch
torch.set_num_threads(12)
from TTS.api import TTS
dest=sys.argv[1]
tts=TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
sm=tts.synthesizer.tts_model.speaker_manager
allspk=list(sm.name_to_id.keys())
print("встроенных голосов:", len(allspk))
cands=["Damien Black","Viktor Menelaos","Aaron Dreschner","Baldur Sanjin","Luis Moray",
       "Marcos Rudaski","Gilberto Mathias","Wulf Carlevaro","Ludvig Milivoj","Viktor Eka",
       "Eugenio Mataracı","Kazuhiko Atallah","Nelson Dupont","Ferran Simen"]
male=[s for s in cands if s in allspk][:5] or allspk[:5]
print("выбраны:", male)
text=("Успех — это не везение. Это система привычек, которую ты выстраиваешь каждый день. "
      "Начни с малого — и результат не заставит себя ждать.")
for i,sp in enumerate(male,1):
    try:
        out=f"{dest}/XTTS {i} — {sp}.wav"
        tts.tts_to_file(text=text, speaker=sp, language="ru", file_path=out, split_sentences=True)
        print(f"  OK XTTS {i}: {sp}")
    except Exception as e:
        print(f"  FAIL {sp}: {str(e)[:140]}")
print("DONE")
PY
echo "Файлы:"; ls "$DEST" | grep -i xtts || echo "(XTTS-сэмплов нет)"