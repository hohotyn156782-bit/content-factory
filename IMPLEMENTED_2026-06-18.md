# Внедрение по рыночному анализу — 2026-06-18

Источник: `RESEARCH_MARKET_2026-06-18.md` (воркфлоу 21 агент). Реализовано **«абсолютно всё»**
(по решению владельца): быстрые победы + надёжность 24/7 + Telegram-дистрибуция + большие ставки.
Сквозная сборка ролика после правок — ✅ (mind_facts, 25.4с, QA ok, intro_cuts=3, 5 A/B-заголовков, обложка).

## ✅ Готово и проверено (работает само, без действий владельца)

| Улучшение | Файлы | Статус |
|---|---|---|
| Семантический ре-ранкинг b-roll под смысл сцены (overlap по slug/тегам, бесплатно) | `pipeline/broll.py` | ✅ в проде |
| Плотное интро ≥3 смены/3с (pattern-interrupt) | `pipeline/broll.py` `_slots` | ✅ (intro_cuts=3) |
| Punch-in зум фона на акцентных словах | `pipeline/assemble.py` + `subtitles.accent_times` | ✅ рендер ок |
| Визуальный луп (xfade конец→начало, replay=view) | `pipeline/assemble.py` `_apply_loop` | ✅ рендер ок |
| Караоке-субтитры `\kf` (режим `subtitle_mode:"karaoke"` у ниши) | `pipeline/subtitles.py` | ✅ (по умолчанию popin) |
| Гейт читаемости ключевых слов (≥0.46с) | `pipeline/subtitles.py` | ✅ |
| Cut-rate метрика + алерт о разреженном интро | `pipeline/qa.py` | ✅ (cuts/cut_rate/intro_cuts в meta) |
| A/B заголовков YouTube (5 вариантов, выбор лучшего) | `pipeline/script.py` `_title_variants` | ✅ (meta.title_variants) |
| Сид хука из YouTube Most Replayed (heatmap конкурента) | `pipeline/heatmap.py` + `script.py` | ✅ (вкл; CF_HEATMAP=0 выключает) |
| Авто-обложки (кадр + заголовок, Pillow) | `pipeline/thumbnail.py` + `build.py` | ✅ (meta.thumbnail) |
| Источники трендов: Google Autocomplete + Wikimedia RU + trendspyg | `pipeline/parser.py` | ✅ (48 кандидатов) |
| VK авто-первый-коммент (буст + воронка в TG) | `adapters/vk_video.py` | ✅ (env VK_FIRST_COMMENT) |
| Catch-up morning при спящем ПК (если плана нет — собрать в tick) | `panel/scheduler.py` | ✅ |
| APV-фактор удержания в весах ниш | `pipeline/analytics.py` recalibrate | ✅ (когда появится APV) |

## 🔧 Готово в коде — нужна разовая активация владельцем

### 1. Healthchecks.io — dead-man's switch (главная дыра 24/7)
- Зарегаться на healthchecks.io (free, 20 проверок). Создать 2 чека: `morning` (период день) и `tick` (15 мин).
- В `~/.config/content-factory/secrets.env` добавить базовые ping-URL чеков:
  `HC_PING_MORNING=https://hc-ping.com/<uuid-morning>`
  `HC_PING_TICK=https://hc-ping.com/<uuid-tick>`
- Код уже шлёт start/success/fail (`core.hc_ping`). Не пришёл пинг → healthchecks сам алертит (даже если ПК спал).

### 2. Apprise — резервный канал критичных алертов (push на телефон)
- `pip install apprise` (уже стоит).
- В secrets.env: `APPRISE_URLS=ntfy://ntfy.sh/<секретный-топик>,tgram://<bot>/<chat>` (любые каналы через запятую).
- Критичные падения (`reporter.critical`) уйдут на независимый канал помимо TG. Поставить приложение ntfy на телефон, подписаться на топик.

### 3. Telegram-дистрибуция (Kurigram, видео ~2ГБ + нативное превью)
1. my.telegram.org → API development tools → создать приложение → `TG_API_ID`, `TG_API_HASH` в secrets.env.
2. `pip install kurigram tgcrypto`.
3. `python3 adapters/telegram.py login` (под **user**-аккаунтом, не ботом) → вывод `TG_SESSION_STRING=...` в secrets.env.
4. `python3 adapters/telegram.py verify` (должно показать @username).
5. В панели завести аккаунт связки `platform='telegram'`, `ext_id`=@канал (userbot — админ канала). Опц. `TG_FIRST_COMMENT` (нужна привязанная группа обсуждений).

### 4. Approval-гейт (ручное одобрение + A/B хуков в TG)
- Уже работает на `TG_BOT_TOKEN`/`TG_CHAT_ID` (как reporter). Включается флагом `require_approval: true` у связки (в panel.db).
- Тогда готовый ролik уходит владельцу в TG с кнопками Одобрить/Отклонить + варианты заголовков; публикуется только одобренное.

### 5. MOSS-TTS-Nano — локальный TTS + клон голоса (снимает потолок 8 ключей ElevenLabs)
- `bash setup_moss.sh` (соберёт `.venv-moss`, скачает ONNX-веса; долго на CPU-torch).
- У ниши выставить `engine:"moss"`; для клона — `MOSS_REF_AUDIO=/путь/к/6сек.wav` в secrets.env.
- Фолбэк на edge-tts уже встроен. ⚠️ В `moss_worker.py` оставлен `# TODO(MOSS)` — при первом запуске сверить сигнатуру `synthesize` с `.moss-tts-nano-src/onnx_tts_runtime.py`.

### 6. YouTube Analytics API — реальный APV/удержание (вместо голых просмотров)
- Нужен ОДНОРАЗОВЫЙ повторный OAuth с scope `yt-analytics.readonly` (перезапусти `adapters/youtube_auth.py`).
- Дальше `morning()` сам дотягивает APV (`yt_analytics.enrich_performance_log`) и веса ниш учитывают удержание.
- ⚠️ YouTube заблокирован в РФ → актуально для EN-ниш/диаспоры/аккаунта из Армении.

### 7. Modal — serverless GPU видео-генерация Wan2.2 (оживление кадров) — ПЛАТНО ~$30/мес
1. `pip install modal` → `modal token new` (без карты на старте).
2. `modal deploy video_gpu.py` → скопировать URL эндпоинта в secrets.env: `MODAL_VIDEO_URL=...`
3. Лимит расхода: `MODAL_MONTHLY_SEC=180` (≈36 клипов 5с). Тратится ТОЛЬКО на топ-сцены.
4. Подключить `video_gpu.animate_image` в `broll.py` (сниппет в задаче) — оставлено как опция, по умолчанию выключено (нет URL → фолбэк DepthFlow/Ken Burns).

### 8. APScheduler (опц., вместо голого cron) — catch-up уже частично решён в tick
- Полный вариант: запускать `BackgroundScheduler` (3.x) с `misfire_grace_time`+`coalesce`+`max_instances=1` внутри панели. Сейчас catch-up morning срабатывает в tick, если плана нет — практичный минимум без демона.

## 🔴 Сознательно НЕ делали (анти-ловушки из ресёрча)
Postiz/n8n-оркестратор (тяжёлый Docker, не поддерживают VK/TG) · aiograpi/upload-post (бан-риск/лимиты) ·
Дзен-видео (не платит за просмотры в 2026) · YouTube Test&Compare (только long-form) ·
Pixabay Music API (не существует + Content-ID) · Silero v5 (NC-лицензия).

## Зависимости
`pip install`: Pillow, yake, yt-dlp, trendspyg, apprise, APScheduler (добавлены в requirements.txt).
Опционально под аккаунты владельца: kurigram tgcrypto, modal, MOSS (свой venv).
