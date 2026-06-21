# Дорожная карта улучшений контент-фабрики

Единая приоритизированная карта по 10 направлениям ресёрча. Все находки сверены с реальным кодом проекта (`/home/baronpavel/projects/content-factory`). Сортировка — по соотношению **ценность / усилие**. Фокус: реально бесплатное, живое, применимое из Армении под Python/ffmpeg/no-build стек.

## Что подтвердилось проверкой кода (база для приоритетов)

| Утверждение находки | Файл | Статус |
|---|---|---|
| b-roll берёт `files[0]`, метаданные клипов не используются | `pipeline/broll.py:54` | ✅ подтверждено — реальный пробел |
| SFX/whoosh на склейках ОТКЛЮЧЁН | `pipeline/assemble.py:108-110` | ✅ отключён |
| Визуального лупа (last≈first кадр) НЕТ | `pipeline/assemble.py` | ✅ пробел |
| Субтитры = pop-in, нет караоке `\k/\kf` | `pipeline/subtitles.py:130` | ✅ пробел |
| anti-flicker есть (min 0.20с группы) | `pipeline/subtitles.py:39` | ✅ есть, нет per-keyword гейта |
| `_is_accent` + word-timings есть (для auto-zoom) | `subtitles.py:86`, `voice.py` | ✅ всё для триггера есть |
| VK `wall.createComment` НЕТ | `adapters/vk_video.py` | ✅ только video.save/wall.post |
| analytics = YouTube **Data** API v3, не Analytics API | `pipeline/analytics.py:47,88` | ✅ APV/retention не тянем; фетчеры только youtube/vk |
| scheduler = голый cron, нет healthcheck/misfire | `panel/scheduler.py:11-13` | ✅ пробел оркестрации |
| Telegram постинг-адаптера НЕТ (TG только парсинг) | `adapters/` | ✅ пробел дистрибуции |
| Папка `assets/music` пуста, музыка отключена | `pipeline/music.py:5` | ✅ Content-ID риск |
| `llm.chat()` реюзабелен (ротация+cooldown) | `pipeline/llm.py:103` | ✅ готов под ранкер b-roll/заголовков |
| voice = edge/xtts/elevenlabs (нет MOSS) | `pipeline/voice.py:313` | ✅ MOSS добавляем |

---

## QUICK WINS (low/medium effort, высокий выигрыш — ближайшие заходы)

Порядок = приоритет внедрения.

### 1. Healthchecks.io — dead-man's switch
**Что:** внешний сервис ждёт ping от morning/tick; нет пинга (ПК спал / WSL не поднялся / cron не сработал) → алерт в TG. Старт на free SaaS (20 проверок).
**Пробел:** scheduler.py алертит только когда tick реально выполнился — тихий не-запуск никто не заметит. Это единственная дыра, которую внутренние алерты не закроют. Критично на WSL-десктопе.
**Усилие:** low (3 строки curl, `core.hc_ping` на urllib, без зависимостей).
**Выигрыш:** обнаружение главного риска 24/7-автопилота. Закрывает TODO оркестрация/мониторинг.
**Как:** регистрация → checks morning(daily)/tick(*/15) → `.../start` в начале, `.../0` при успехе, `.../fail` в except.
🔗 https://github.com/healthchecks/healthchecks

### 2. VK `wall.createComment` — авто-первый-комментарий
**Что:** после `wall.post` сразу постить первый коммент с CTA + ссылкой на TG тем же user-токеном.
**Пробел:** метода нет в `adapters/vk_video.py`. Прямой TODO. VK — основная площадка.
**Усилие:** low (один вызов `_call`, без новой либы).
**Выигрыш:** буст вовлечённости (алгоритм VK любит комменты) + воронка VK→TG.
**Как:** взять `post_id` из ответа wall.post → `_call('wall.createComment', token, owner_id=-<group_id>, post_id=..., message=...)`. Проверить scope `wall`.
🔗 https://dev.vk.com/method/wall.createComment

### 3. Apprise — резервный канал алертов (ntfy push на телефон)
**Что:** критичные алерты дублировать на независимый канал (ntfy/Discord) помимо TG.
**Пробел:** все алерты идут одним каналом (reporter.send → TG) — единая точка отказа.
**Усилие:** low (`pip install apprise`).
**Выигрыш:** устранение SPOF в алертинге + push на телефон. Превью-ролик вложением.
**Как:** `apprise.Apprise()` с `APPRISE_URLS` из env; критичные алерты через него, рутина — как есть.
🔗 https://github.com/caronc/apprise

### 4. Kurigram — дистрибуция в Telegram-каналы
**Что:** MTProto userbot (живой форк заброшенного Pyrogram) → `send_video` ~2ГБ с превью + авто-первый-коммент в linked chat.
**Пробел:** дистрибуции в TG нет (только парсинг). Bot-API душит видео до 50МБ.
**Усилие:** low (`pip install kurigram tgcrypto`, `adapters/telegram.py` по образцу).
**Выигрыш:** новый канал охвата + монетизация (Ads/Stars). Риск бана минимален (свои каналы, низкая частота).
**Как:** Client на user-сессии, `send_video(..., supports_streaming=True)`; коммент — reply в linked discussion. Креды в ~/.config. В scheduler — `platform=='telegram'`.
🔗 https://github.com/KurimuzonAkuma/kurigram

### 5. yt-dlp heatmap (Most Replayed) → сид хуков
**Что:** из info.json топ-видео конкурента достать `heatmap`, argmax → таймкод пика внимания → донор хука.
**Пробел:** уникальный срез (ГДЕ пик удержания, не «что горячо»). yt-dlp уже в стеке.
**Усилие:** low (`pipeline/heatmap.py`, обязателен фолбэк при `None`).
**Выигрыш:** проверенный хук/каркас сцены в Virality Score.
**Как:** `yt-dlp ytsearch` → URL → `--dump-json` → `info['heatmap']` → argmax → `%(chapters)j`. Фолбэк при пустом.
🔗 https://github.com/yt-dlp/yt-dlp · https://github.com/Dorian25/ytb-most-replayed

### 6. Google Autocomplete — генератор тем из языка аудитории
**Что:** `suggestqueries.google.com/complete/search?client=firefox&hl=ru` (HTTPS!) → дерево длиннохвостых тем.
**Пробел:** нет источника «реальные формулировки людей». Питает facts/Q&A на 5 нишах.
**Усилие:** low (функция в parser.py + кэш + sleep).
**Выигрыш:** дешёвый поток тем-под-shorts, источник хуков.
**Как:** `google_suggest(seed, hl='ru', depth=1-2)`, парсить `data[1]`, alphabet-soup + рекурсия с sleep. Вес ~0.7-0.8.
🔗 https://suggestqueries.google.com/complete/search?client=firefox&hl=ru&q=

### 7. Wikimedia Pageviews API — ранний RU-сигнал
**Что:** REST top/per-article → топ статей рунета + динамика всплеска интереса.
**Пробел:** RU-импульса по сути нет (Trends RSS узок, VK слабый, MCP квотируется). Официальный REST.
**Усилие:** low (urllib).
**Выигрыш:** ранний сигнал темы/персоны/события в topics_db + рекалибровка весов.
**Как:** `wiki_top(ru.wikipedia, вчера)`, фильтр служебных. Опц. `wiki_momentum` (рост за 3 дня).
🔗 https://wikimedia.org/api/rest_v1/ · https://pypi.org/project/pageviewapi/

### 8. Семантический ре-ранкинг b-roll через LLM-каскад
**Что:** собрать 10-15 кандидатов Pexels/Pixabay с метаданными (alt/url-slug/tags) → одним промптом `llm.chat()` выбрать лучший под фразу сцены.
**Пробел:** `broll.py` берёт `files[0]`, метаданные игнорит. `llm.chat()` уже реюзабелен → 0 новых зависимостей.
**Усилие:** medium (правка `_pexels/_pixabay` + ranker + кэш + фолбэк).
**Выигрыш:** релевантный видеоряд вместо случайного → рост удержания. Почти бесплатно.
**Как:** `ranker(scene_phrase, candidates)->index` через 8B-модель, кэш, фолбэк на `files[0]`, сохранить дедуп.
🔗 анализ `pipeline/broll.py` + `llm.chat()`

### 9. Авто-zoom / punch-in на акцентном слове (ffmpeg zoompan)
**Что:** второй per-frame zoompan-проход: пульс 1.06-1.12x / 150-250мс в момент ключевого слова по word-timings.
**Пробел:** всё для триггера есть (`_is_accent` + word-timings), приёма нет. Главный приём удержания shorts 2026.
**Усилие:** medium (прокинуть accent-таймкоды в assemble).
**Выигрыш:** высокий — визуальный ритм синхронно с озвучкой, «дорогой» монтаж.
**Как:** subtitles → список accent-таймкодов → assemble.render → zoompan `z='if(between(in_time,T,T+0.22),1.08,1)'` (d=1, pzoom).
🔗 https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Video/zoompan.html

### 10. Караоке active-word fill (`\kf`, фраза 3-5 слов)
**Что:** альт-режим субтитров: группа 3-5 слов, `{\kf<centisec>}` на слово, заливка бежит (стиль Hormozi/Submagic).
**Пробел:** `\k/\kf` — базовые теги libass (из коробки). Тайминги есть. Топ-стиль 2026 + второй пресет для A/B.
**Усилие:** medium (режим build_ass + настройка Secondary/PrimaryColour).
**Выигрыш:** высокий — выше completion, материал для A/B по нишам.
**Как:** группировка 3-5 слов, `\kf` из word-timings, SecondaryColour=белый/Primary=жёлтый. Переключатель в связке ниши.
🔗 http://www.tcax.org/docs/ass-specs.htm

### 11. Визуальный loop (last-frame ≈ first-frame)
**Что:** дублировать первый кадр/клип в конец ИЛИ xfade 0.3-0.5с последнего с первым.
**Пробел:** нарративный луп есть (outro-промпт), визуального в assemble нет. replay=view (с 31.03.2025).
**Усилие:** low (filter_complex).
**Выигрыш:** средний — бесшовный стык повышает replay-rate.
🔗 https://support.sproutsocial.com/hc/en-us/articles/35874991211533

### 12. Pattern-interrupt каденс (≥3 смены в первые 3с) + cut-rate QA
**Что:** форсировать короткие слоты (≤2.5-3.0с) в первой трети + ≥3 смены b-roll в первые 3с; логировать cut-rate.
**Пробел:** вариативный SLOT_CYCLE есть, но нет гарантии плотного интро. Бьёт в дроп 3-12с.
**Усилие:** medium (правка `_slots()` + метрика в qa.py).
**Выигрыш:** высокий и измеримый — уплотнение интро (+40-60% удержания по 2026-источникам).
**Как:** `_slots()` форсирует первую треть; qa.py считает cut-rate. Опц. вернуть SFX при нормальных переходах.
🔗 https://virvid.ai/blog/ai-shorts-increase-retention-watch-time

### 13. QA-гейт читаемости субтитров (ключевик ≥N кадров)
**Что:** поднять min-длительность группы для длинных/ключевых слов (~0.5-0.6с), проверка в qa.py.
**Пробел:** anti-flicker есть (0.20с), per-keyword гейта нет. 80-85% смотрят без звука.
**Усилие:** low.
**Выигрыш:** малый, но дешёвый и в точку — читаемость для sound-off.
🔗 https://www.opus.pro/blog/youtube-shorts-caption-subtitle-best-practices

### 14. Telegram Approval-гейт (QA + A/B хуков) на своём боте
**Что:** sendVideo + inline Approve/Reject + 2-3 хука кнопками → callback_query → статус слота; выбранный хук в plan.text. tick публикует только approved.
**Пробел:** закрывает A/B заголовков И ручной гейт брака без n8n — TG-инфра уже есть (reporter.py, lead-hunter callback_query).
**Усилие:** low (`adapters/tg_review.py` + поллер getUpdates).
**Выигрыш:** высокий — снижение риска брака + A/B почти даром, реюз бота/очереди.
**Как:** `send_for_approval(id)`, статус `awaiting_approval`, поллер → `update_plan_item`. Флаг `require_approval` per-bundle.
🔗 reporter.py + n8n templates 5397/11617 (референс)

### 15. trendspyg — живой Google Trends движок
**Что:** v0.6.1 (08.06.2026, MIT) замена архивированного pytrends: trending_now + related/rising + interest-over-time, async + кэш.
**Пробел:** pytrends заброшен (брать НЕ надо). Может заменить платный Trends MCP (free 100/мес).
**Усилие:** low (`pip install trendspyg`, проверить `list --type countries | grep russia`).
**Выигрыш:** бесплатный кэшируемый движок trend_momentum.
🔗 https://github.com/flack0x/trendspyg

### 16. rife-ncnn-vulkan — frame interpolation (CPU/Vulkan)
**Что:** портативный бинарь (на llvmpipe как DepthFlow): Ken Burns/DepthFlow/сток → гладкие 60fps, slow-mo, без рывков.
**Пробел:** оживление Ken Burns тем же CPU-путём, 0 нового железа.
**Усилие:** low (бинарь + subprocess).
**Выигрыш:** высокий — буст плавности без облака. Тяжёлые батчи → Modal-GPU.
**Как:** `rife-ncnn-vulkan -m rife-v4 -n 2x` ПЕРЕД финальной сборкой, после Ken Burns/DepthFlow.
🔗 https://github.com/nihui/rife-ncnn-vulkan · https://github.com/hzwer/Practical-RIFE

### 17. YouTube Audio Library / NCS — Content-ID-safe музыка
**Что:** пул инструменталов из YouTube Audio Library + NCS; хранить лицензию на трек; дедуп как b-roll.
**Пробел:** `assets/music` пуста (подтверждено), музыка отключена → Content-ID риск.
**⚠️ Поправка к находке:** Pixabay Music API НЕ существует (только images/videos) и НЕ гарантированно free от Content-ID (их же блог про claims). Брать YouTube Audio Library + NCS.
**Усилие:** medium (скрипт скачивания + лицензии + дедуп).
**Выигрыш:** высокий — снимает риск страйков, наполняет sidechain-дакинг. Опц. локальный MusicGen (MIT) для петель.
🔗 https://pixabay.com/blog/posts/how-to-clear-a-youtube-content-id-claim-with-a-pix-190/

### 18. OpenShorts — донор кода (thumbnail + заголовки + job-queue)
**Что:** НЕ деплоить (AI-режим завязан на платный fal.ai). Портировать: PIL-thumbnail, промпт 10-виральных-заголовков+refine, semaphore job-queue, авто-главы из Whisper.
**Пробел:** 3 TODO разом — авто-обложки (НЕТ), A/B заголовков, надёжная очередь. Всё на FastAPI+SQLite без платных deps.
**Усилие:** medium (донор, 3-4 модуля).
**Выигрыш:** закрывает авто-обложки + A/B заголовков + очередь + авто-главы для SEO.
**Как:** thumbnail (Pillow), заголовки/главы через ваш каскад/Groq Whisper, `asyncio.Semaphore` в scheduler. fal.ai/upload-post игнорировать.
🔗 https://github.com/mutonby/openshorts

---

## BIGGER BETS (high effort или стратегические)

### A. APScheduler 3.x — планировщик с catch-up
**Что:** заменить cron на APScheduler с `misfire_grace_time`+`coalesce` (догонит пропущенный morning) + `max_instances=1` (нет наложения ffmpeg). Состояние в SQLite. **БРАТЬ 3.x, НЕ 4.0-alpha.**
**Пробел:** прямой TODO. На WSL-десктопе при спящем ПК cron молча теряет morning → план дня не соберётся.
**Усилие:** medium-high (long-running процесс/демон, тянет SQLAlchemy/tzlocal).
**Выигрыш:** морнинг не теряется при сне/перезагрузке; нет дублей-рендеров.
**Малой кровью:** оставить cron + catch-up-проверку в morning («план не собран → собрать»).
🔗 https://github.com/agronholm/apscheduler

### B. YouTube Analytics API — реальный APV/retention
**Что:** `pipeline/yt_analytics.py` (reports.query: averageViewPercentage/engagedViews) на существующем OAuth + новый scope. APV → performance_log → recalibrate().
**Пробел:** analytics.py тянет **Data** API (просмотры/лайки), не Analytics — реального удержания нет. APV в Data API отсутствует.
**Усилие:** high (новая интеграция + scope `yt-analytics.readonly` + правка recalibrate).
**Выигрыш:** высокий — сигнал удержания вместо голых просмотров; разблокирует APV-гейт ниш, длину-под-хук-фреймворк, псевдо-A/B `title_variants`.
🔗 https://developers.google.com/youtube/analytics/metrics

### C. Modal — $30/мес recurring GPU как видео-движок
**Что:** Wan2.2-TI2V-5B / RIFE / Real-ESRGAN как Modal serverless (gpu='A10G/L40S', веса в Volume, web_endpoint), дёргать по HTTP. ~$30/мес ≈ десятки клипов.
**Пробел:** GPU нет → Wan локально на CPU = часы/ролик. Modal — единственный возобновляемый GPU без железа и без ZeroGPU-очередей. Закрывает главный пробел (видео-генерация на объём).
**Усилие:** high (modal token, video_gpu.py, логика бюджета как у LLM-ключей).
**Выигрыш:** высокий — оживление FLUX-кадров в b-roll + батч-апскейл/интерполяция топ-сцен. **Не бесконечность** — расходовать на bottleneck-шаги по Virality Score.
**Как:** Pollinations/NVIDIA FLUX-кадр → Wan I2V на Modal → ffmpeg. RIFE/ESRGAN батчем.
🔗 https://modal.com/pricing · https://github.com/Wan-Video/Wan2.2 (Apache-2.0)

### D. Монетизация RU-площадок: VK + Rutube + Telegram
**Что:** VK партнёрка (от 5к подп., 50-80%) + Фонд оригинальных авторов (фикс 30к/мес при 1к-100к) + грант клипов; Rutube (20к просмотров, выплаты от 3000₽); Telegram (Ads 50% при 1000+, sendPaidMedia за Stars).
**Пробел:** постим в VK, но не монетизируем. VK/Rutube/TG — единственные RU-площадки, платящие faceless (YouTube заблокирован в РФ).
**⚠️ Кавеат:** для ВЫВОДА нужен РФ-статус самозанятого/ИП (из Армении можно постить и набирать подписчиков; Telegram Fragment-вывод проверить с армянского аккаунта).
**Усилие:** high (рост до порогов + самозанятость РФ + заявки; код — wall.createComment, TG-постер).
**Выигрыш:** высокий долгосрочный. Потолок VK до 200к₽/мес, Rutube до 2.4 млн₽/год.
**Шаги:** KPI «подп. VK→5000» в панели → заявка в Фонд → самозанятость (один статус = VK+Rutube) → усилить VK-дистрибуцию + первый коммент → Rutube-постер → TG-канал + Ads при 1000+.
🔗 https://vk.company/ru/press/releases/11951/ · https://rutube.ru/info/monetization/ · https://telegram.org/blog/monetization-for-channels

### E. MOSS-TTS-Nano — локальный CPU TTS (RU + voice-clone + 48kHz)
**Что:** 0.1B Apache-2.0 (CPU-realtime через ONNX, без PyTorch): русский + клон из 6-сек сэмпла одновременно. `.venv-moss` по образцу XTTS, `pipeline/moss_worker.py`.
**Пробел:** TODO «разнообразие голосов». Ни edge/XTTS/ElevenLabs так не умеют. LICENSE опубликован = реальная Apache-2.0, коммерция ок.
**Усилие:** medium-high (venv + ONNX-веса + worker + ветка engine=='moss').
**Выигрыш:** высокий — безлимит дикторов локально на CPU, снимает зависимость от 8 ключей ElevenLabs. Качество > XTTS-v2.
**Как:** ONNX-веса (HF: MOSS-TTS-Nano-100M-ONNX), worker по образцу xtts_worker.py, фолбэк на edge-tts, сэмплы в assets/voices/, тайминги через `_groq_align`.
🔗 https://github.com/OpenMOSS/MOSS-TTS-Nano

---

## SKIP (заманчиво, но не стоит)

| Находка | Причина |
|---|---|
| **Postiz** | Тяжёлый Docker-кластер (антипод no-build), НЕ поддерживает VK/TG (главные каналы), оборачивает офиц.API (не обходит гейты), дублирует scheduler.py. Доп-платформы дешевле через свой Telegram-Approval. |
| **n8n как оркестратор ядра** | Ядро (ротация ключей, word-timestamps, Gemini QA, ffmpeg) в визуальные ноды переносить невыгодно. Точечная польза дешевле своим Python. Make.com отвергнуть (облако, лимиты, санкции). |
| **aiograpi (IG private API)** | Private API, README сам советует платный аналог для прода, высокий риск бана IG за faceless/AI. Официальный Graph API уже работает. Максимум — read-only insights. |
| **Upload-Post / upload-post.com** | Free 10 загрузок/мес = ~2-3 ролика, дальше платно. Сторонний прокси = SPOF. Аналитика на free не подтверждена. Только pilot, не объём. |
| **Chatterbox TTS (локально)** | 0.5B, на CPU минуты/клип — узкое место без GPU. Разнообразие лучше закрывает MOSS. |
| **Silero TTS v5 (именные RU)** | Основные модели CC-BY-NC (некоммерч.) — юр.риск для монетизации. RU-прод закрыть MOSS. |
| **NVIDIA video / ZeroGPU Wan / Bugsink** | NVIDIA-кредиты ~одноразовые (резерв на топ-сцены). ZeroGPU ~3.5 мин/день (1-2 клипа), мульти-аккаунт против ToS. Bugsink — Polyform (не OSS), не критично при наличии JSON-лог+TG. |
| **Huey / Tenacity / PySceneDetect / rembg / whisperX / SamurAIGPT** | Рефакторы/полировка уже-рабочего или нишевое. Huey — после APScheduler если останется боль. Tenacity — косметика (retry уже есть, jitter = 1 строка). PySceneDetect/rembg/whisperX — точечно после adopt-находок. SamurAIGPT chunk/face-crop — только при сценарии «нарезка лонгов», которого нет. |
| **Pixabay Music API (как механизм)** | Музыкального API НЕТ (только images/videos); музыка может триггерить Content-ID (их же блог). Заголовок ошибочен. Брать YouTube Audio Library + NCS. |
| **Дзен видео ради рекламы** | В 2026 видео в Дзене НЕ платит за просмотры (только дочитывание статей). Бессмысленно. Дзен — максимум охват/текстовые карточки. |
| **YouTube Studio Test & Compare для Shorts** | Раскатан ТОЛЬКО для long-form/десктоп; Shorts исключены. A/B заголовков Shorts решать самим (псевдо-A/B + APV). Ценность находки — анти-ловушка. |

---

## Рекомендованный порядок первых заходов

1. **Заход 1 (надёжность, ~день):** Healthchecks #1 + Apprise #3 + VK first-comment #2.
2. **Заход 2 (дистрибуция+тренды, ~день-два):** Kurigram TG #4 + Autocomplete #6 + Wikimedia #7 + heatmap #5 + trendspyg #15.
3. **Заход 3 (крафт ролика):** b-roll ре-ранкинг #8 + auto-zoom #9 + визуальный loop #11 + caption QA-гейт #13.
4. **Заход 4 (удержание+стиль):** pattern-interrupt #12 + караоке `\kf` #10 + rife #16 + музыка #17.
5. **Заход 5 (гейт+обложки):** Telegram Approval #14 + OpenShorts-донор #18.
6. **Bigger bets:** APScheduler (A) → YouTube Analytics API (B, разблокирует A/B и APV-гейт) → монетизация RU (D, параллельно как рост) → MOSS (E) → Modal-видео (C, когда дойдут руки до видео-генерации).
