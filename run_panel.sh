#!/usr/bin/env bash
# Запуск админ-панели Content Factory локально. Открой в браузере: http://127.0.0.1:8765
cd "$(dirname "$0")"
source ~/.config/content-engine/secrets.env 2>/dev/null   # GROQ/TG/VK; Pexels подтянется из ~/.config/content-factory
exec python3 -m uvicorn panel.server:app --host 127.0.0.1 --port 8765
