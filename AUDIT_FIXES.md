# Исправления по аудиту 2026-06-17 — ПРОГРЕСС (чиним всё кроме установки cron)

## ✅ СДЕЛАНО в этот заход

### Публикация (B4)
- [x] B4a: ветка `threads` в `_adapter()` (server.py)
- [x] B4b: kind=text → `publish_text`, video → `publish` (server.py routing)
- [x] B4c: VK-видео через `VK_USER_TOKEN*` с авто-резолвом по группе (vk_video `_user_token_for`)
- [x] B4d: factory `_post_dir` — QA-гейт

### Автономность (без cron)
- [x] B2: tick() зеркалит content.status+targets.url после поста (`_mirror_to_content`) → аналитика видит
- [x] B3: scheduler __main__ try/except→reporter; сводка morning/tick; алерт при простое (posted==0)

### Квоты/диск
- [x] #1: tick() соблюдает caps.per_day (анти-бан)
- [x] #2: morning() идемпотентна (не пересобирает готовый план)
- [x] #3: core.cleanup_outputs() + вызов в build_video

### Анти-бан/качество
- [x] критик#3: дедуп b-roll МЕЖДУ роликами (topics_db.used_media, префикс-ключи pexels:/pixabay:/coverr:)
- [x] youtube categoryId по нише (было хардкод 28)

### Надёжность/логика
- [x] #4: _apply_alignment — слово в ОДИН кусок (нет дублей в субтитрах)
- [x] #5: qa visual_unverified + warn при недоступном Gemini
- [x] #6: voice._concat детект пустой озвучки + qa детект тишины (volumedetect)
- [x] #7: core.run_retry + применён к ffmpeg в assemble

### Данные/конкурентность
- [x] panel/db.py: busy_timeout=5000
- [x] ★ analytics.py: DATA_DIR на диск D (был C — рассинхрон)
- [x] qa_failed счётчик в overview()
- [x] reap_stuck_generating() при старте панели + в morning()

### Безопасность
- [x] #8: маскировка ключей в логах (_safe_url)
- [x] VK-токен в POST-body (parser, не query)
- [x] санитизация контента парсера перед LLM (selector._sanitize, анти-prompt-injection)

## ⏳ ОСТАЛОСЬ (medium/low — следующий заход)
- [ ] критик#1: фетчеры метрик IG/TikTok/Threads в analytics (сейчас только YT+VK) — нужны insights-API
- [ ] критик#5: дневной persist-счётчик генераций картинок (квота FLUX/NVIDIA)
- [ ] критик#4: разнообразие фолбэк-голоса — ОГРАНИЧЕНИЕ edge (всего 2 RU-голоса); реальный фикс = не жечь EL (8 ключей) / Piper
- [ ] TOCTOU тема-гонка между потоками (process-lock+read-back); targets RMW per-cid lock
- [ ] #9 панель: X-Panel-Token auth (локальная — низкий риск); #10 IG: persist creation_id (анти-дубль Reels)
- [ ] Группа 9 (DRY/архитектура, 22 находки, техдолг): единый keyring, HTTP-фасад, _NICHE_CATEGORY→json, мёртвый код

## НЕ делаем
- B1 (установка cron) — по решению пользователя (запуск 24/7 — отдельно).
