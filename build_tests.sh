#!/usr/bin/env bash
# Собирает по 1 тестовому ролику на нишу, чистит папку публикации, кладёт только свежие 5.
cd /home/baronpavel/projects/content-factory
source ~/.config/content-engine/secrets.env 2>/dev/null
DEST="/mnt/c/Users/BaronPavel/Desktop/Видео для публикации"
mkdir -p "$DEST"
declare -A RU=( [ai_lifehacks]="AI-фишки" [mind_facts]="Психология" [history_facts]="История" [money_facts]="Деньги" [ai_lifehacks_en]="AI-EN" )
NEW=()
for n in ai_lifehacks mind_facts history_facts money_facts ai_lifehacks_en; do
  echo "── $n ──"
  python3 factory.py build "$n" 2>&1 | grep -E "✅|❌|откат|Error" | tail -2
  V=$(ls -t output/*/video.mp4 | head -1)
  NEW+=("$V|$n")
done

echo "=== чистка папки (видео+подписи; «Голоса — сравнение» не трогаем) ==="
rm -f "$DEST"/*.mp4 "$DEST"/*.txt

echo "=== копирование свежих ==="
for item in "${NEW[@]}"; do
  V="${item%|*}"; n="${item#*|}"; D=$(dirname "$V")
  topic=$(python3 -c "import json;print(json.load(open('$D/meta.json'))['topic'])" 2>/dev/null)
  dur=$(python3 -c "import json;print(round(json.load(open('$D/meta.json'))['duration']))" 2>/dev/null)
  name="${RU[$n]} — ${topic} (${dur}с)"; name=$(echo "$name" | tr -d '/\\:*?"<>|')
  cp "$V" "$DEST/$name.mp4"
  cp "$D/POST.txt" "$DEST/$name — подписи.txt" 2>/dev/null
  echo "  ✅ $name.mp4"
done
echo "=== ИТОГ ==="; ls "$DEST"/*.mp4 | sed 's#.*/##'