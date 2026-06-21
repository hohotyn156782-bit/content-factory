# Автономный заход укрепления — 2026-06-20

Запрос владельца: «делай только то, что можешь без моего участия — проверки, модернизация,
изучение инструментов, придумай сам задания по усилению структуры, делай всё что можешь сам».

Метод (ultracode): воркфлоу 67 агентов (аудит кода 6 измерений + ресёрч инструментов 4 измерения →
адверсариальная проверка каждой находки против реального кода → синтез в приоритизированный план).
56 находок → 48 подтверждено к автономному внедрению, 6 отложено на владельца. Внедрение — 8 агентов
по непересекающимся файлам (фундамент `core.py`+`db.py` первым, затем 6 зависимых) + сквозная проверка.

Бэкап перед правками: `/mnt/d/content-factory-data/_code_backup_2026-06-19/` (проект не под git).

## Сквозная сборка после всех правок — ✅
`mind_facts` → video.mp4 27.2с/12.8МБ, **qa.ok=True**, technical: cuts=11 / intro_cuts=3 / cut_rate=0.4,
res 1080×1920, audio/video синхронны; visual-QA (Gemini, 4 кадра) ok; 5 A/B-заголовков, 3 хука,
обложка, тема закоммичена (reserve→commit), persist-cooldown создан, маскировка секретов в логе доказана.

## Что внедрено (всё no-key, автономно, проверено)

### Надёжность 24/7 автопилота
- **flock-замок** (`core.acquire_lock/release_lock`) на morning/tick в `__main__` — перекрытия cron больше нет (дубли плана, двойной пост, сожжённые квоты).
- **Атомарный claim слота** ready→posting (`db.claim_plan_item`) в tick — TOCTOU двойной публикации закрыт; reap осиротевших 'posting' в начале tick (под flock).
- **Осиротевший content** при сбое сборки теперь помечается failed (`db.fail_content`); `reap_stuck_generating` чистит и план; morning не считает 'generating' валидным планом → пересоберёт.
- **Падение одной связки** в morning() не валит остальные (per-bundle try/except).
- **Самовосстановление диска**: `_ensure_disk()` перед broll/render (агрессивная уборка + повтор), `MIN_FREE_MB` 500→1500.
- **Валидация озвучки**: `_concat` проверяет каждый кусок (реальный файл + длительность) — рассинхрон субтитров ловится как ошибка.
- **finalize_content атомарен** (WHERE status='generating', →bool) — reap не воскрешает живую генерацию.

### Безопасность
- **Маскировка секретов в логах** (`core._scrub` + `_SECRETS`): bot-токены/ключи не утекают в factory.log; `_safe_url` маскирует и `/bot<token>/`.
- **Анти-prompt-injection**: `core.sanitize_external` для topic + heatmap-сид перед LLM; `selector._sanitize` сведён к единой точке.
- **Панель**: анти-CSRF middleware (Origin/Referer-гард на мутирующих методах), host-pin 127.0.0.1 (код + run_panel.sh без `$@`), `/api/health` без раскрытия имён провайдеров/числа ключей.
- **Адаптеры**: `threads.refresh` пишет токен в secrets.env (600), не возвращает наружу; тела ответов (Threads/IG/media_host) не эхаются в логи/БД; `db.set_targets`/`update_target` маскируют секреты.

### Ресурсы и квоты
- **Персист cooldown** между cron-запусками (`core.load/save_cooldown`, merge+atomic) для LLM/ElevenLabs/imagegen — ключи хешируются (сырых секретов на диске нет).
- **Дневной лимит картинок** (`imagegen_quota.json`: nvidia 40 / pollinations 120 / gemini 0) — квоты не жгутся молча.
- **`cleanup_media`** (45д) — MEDIA_DIR больше не растёт вечно; уборка перенесена и в morning() (не зависит от факта сборки).
- **Дедуп музыки** по содержимому + мягкий потолок банка 500МБ.
- **Ротация логов**: `TimedRotatingFileHandler` (midnight, 30 бэкапов, gzip); `HISTORY_FILE` перенесён на диск D (.bak больше не копятся на переполненном C); fsync в append_history.

### Целостность данных (панель пишет из потоков)
- **Атомарный резерв тем** (`topics_db.reserve_topic`/`commit_topic`/`release_topic`, BEGIN IMMEDIATE) — параллельные сборки не плодят дубль-ролики; коммит только при qa.ok, релиз при браке; +idx_topics_created.
- **`db.update_target`** (json_set, атомарно) в `_mirror_to_content` и api_publish — read-modify-write гонка targets устранена.

### Корректность публикации
- **Approval-гейт ожил**: добавлена колонка `bundles.require_approval` (была мёртвым кодом — гейт не срабатывал); включается per-bundle.
- **Crosslinks для видео** (`_meta_with_xlink`) — кросс-промо между площадками теперь и у видео, не только текста.
- **VK**: провал `wall.post` = провал слота (был ложный 'posted' без поста на стене).

### Наблюдаемость
- **Heartbeat** (`core.beat`/`check_heartbeat` + `/api/heartbeat`) — локальный dead-man's switch без внешних сервисов; пульс в morning/tick.

### Модернизация / новые источники
- **LLM-каскад**: 11 провайдеров (+gemini-lite overflow на тех же GEMINI_API_KEY, +openrouter-gemma `google/gemma-4-26b-a4b-it:free`); groq → `openai/gpt-oss-120b` (официальная замена депрецируемой llama-3.3-70b, shutdown 2026-08-16).
- **Новые футаж-источники** (бесплатно, без ключа, с фолбэком): **NASA SVS** (public domain, только science/space/tech-темы) + **Internet Archive** (только PD/CC-BY, лицензии by-nc/nd/sa отбраковываются) — встроены в каскад после Coverr, рендер не встаёт при их сбое.
- **Цвет-грейдинг** (`unsharp`+`eq`, отдельно для stock/image) в финальном vf — один проход кодирования, кинематографичный тон; + punch-фиксы (loop-guard, min-count gate), чистка мёртвого `_build_sfx_bed`.

## ⚠️ Найдено, требует владельца
- **GROQ_API_KEY content-factory отдаёт 403 (forbidden) на ВСЕХ моделях** — ключ отозван/заблокирован (не 429, не транзиент). groq-слот мёртв независимо от модели. НЕ блокер (каскад работает на github-models/mistral/gemini/gemini-lite/openrouter). Действие: заменить groq-ключ (это отдельный ключ от ботов — у них свой в `~/.config/tg-bots/tokens.env`).

## Сознательно отложено
- **xfade-переходы между клипами** — переписывают concat-логику рендера (высокий риск без git-отката); отдельной аккуратной итерацией с тестами.
- **SFX-bed НЕ включён** (в прошлом whoosh лагал — оставлен `sfx_bed=None`).
- На владельца (ключи/загрузка моделей 1-5ГБ на переполненный C): gpt-oss-120b на OpenRouter, Cerebras, Pollinations-video, Silero (NC-лицензия), Chatterbox, WhisperX, Piper, stable-ts.

Полный план/находки: воркфлоу-вывод сессии. Прошлый заход: `IMPLEMENTED_2026-06-18.md`.
