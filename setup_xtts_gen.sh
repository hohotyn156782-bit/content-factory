#!/usr/bin/env bash
# Фикс несовместимости transformers + генерация XTTS-сэмплов. venv уже собран.
set -u
PROJ=/home/baronpavel/projects/content-factory
VENV=$PROJ/.venv-xtts
PY="$VENV/bin/python"
DEST="/mnt/c/Users/BaronPavel/Desktop/Голоса — сравнение"

echo "[fix] pin transformers 4.56.2 (последняя с isin_mps_friendly И is_torch_greater_or_equal)…"
"$PY" -m pip install --disable-pip-version-check "transformers==4.56.2" 2>&1 | tail -4

echo "[test] импорт TTS…"
"$PY" -c "from TTS.api import TTS; print('TTS import OK')" 2>&1 | tail -3

echo "[gen] модель XTTS v2 + мужские сэмплы…"
COQUI_TOS_AGREED=1 "$PY" - "$DEST" <<'PY'
import os, sys
os.environ["COQUI_TOS_AGREED"]="1"
try:
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
            tts.tts_to_file(text=text, speaker=sp, language="ru",
                            file_path=f"{dest}/XTTS {i} — {sp}.wav", split_sentences=True)
            print(f"  OK XTTS {i}: {sp}")
        except Exception as e:
            print(f"  FAIL {sp}: {str(e)[:140]}")
    print("DONE")
except Exception as e:
    import traceback; traceback.print_exc()
    print("GEN FAILED:", str(e)[:200])
PY
echo "Файлы:"; ls "$DEST" | grep -i xtts || echo "(XTTS-сэмплов нет)"