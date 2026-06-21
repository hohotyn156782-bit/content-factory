# Content Factory 🎬

Фабрика коротких вертикальных видео (YouTube Shorts / TikTok / Instagram Reels / VK)
**на 100% легальном материале** и без бюджета на генерацию видео.

Конвейер: **AI-сценарий (Groq) → бесплатная озвучка (edge-tts) → бесплатный сток-B-roll
(Pexels/Pixabay) → крупные субтитры → сборка в ffmpeg → автопостинг по площадкам.**

Если сток-ключей нет — фон собирается из фирменного анимированного градиента, и ролик
всё равно выходит. Один Groq-ключ (уже есть) — и фабрика работает.

## Почему это легально и не словит бан

Это **не** реаплоад чужих видео. Каждый ролик оригинален: свой сценарий + своя озвучка +
лицензионный сток (Pexels/Pixabay разрешают коммерческое использование без атрибуции).
Платформы такое монетизируют. Чтобы оставаться в правилах (см. `originality_rules` ресёрча):

- сценарии оригинальные, вариативность структуры **>20%** между роликами;
- AI-озвучка разрешена при оригинальном сценарии; на TikTok включать метку «AI-generated»;
- аудио стоковых клипов всегда вырезается (`-an`) — чужая музыка в клипах не утекает;
- без водяных знаков чужих площадок при кросс-постинге (грузим нативный файл на каждую);
- YouTube — **2-5 роликов/неделю**, не daily-спам (иначе холистический бан канала);
- источники стока пишутся в `CREDITS.txt` — доказательство лицензии для споров Content ID.

## Установка

```bash
pip install --break-system-packages -r requirements.txt   # ffmpeg ставится отдельно
cp secrets.env.example ~/.config/content-factory/secrets.env && chmod 600 ~/.config/content-factory/secrets.env
# GROQ/TG/VK наследуются из ~/.config/content-engine/secrets.env автоматически
python3 factory.py doctor      # покажет, что готово
```

## Использование

```bash
python3 factory.py build ai_lifehacks            # собрать 1 ролик (без постинга)
python3 factory.py build ai_lifehacks -t "промпт-инжиниринг"   # на заданную тему
python3 factory.py batch 5                        # собрать 5 роликов
python3 factory.py run ai_lifehacks               # собрать + запостить везде + отчёт в TG
python3 factory.py run ai_lifehacks --dry         # собрать, постинг сэмулировать
python3 factory.py post output/<папка> youtube vk # запостить уже собранный в выбранные
```

Результат каждого ролика — папка в `output/`:
`video.mp4` · `subs.ass` · `meta.json` · `script.json` · `POST.txt` (подписи под площадки) · `CREDITS.txt`.

## Ниши

Контент-стратегия живёт в `niches.json` (язык, голос, тип тем, тон, палитра, хэштеги, площадки).
Менять нишу = править JSON, код не трогать. По умолчанию активна `ai_lifehacks` (RU).
`edge-tts --list-voices` — список голосов.

## Что автоматизируется, а что нет (по ресёрчу 2026)

| Площадка | Статус | Что нужно |
|----------|--------|-----------|
| **YouTube Shorts** | ✅ полная авто | OAuth (`youtube_auth.py`), пройти Compliance Audit (до него загрузки приватны!), кап 7/день |
| **VK** | ✅ полная авто | переиспользует токены/паблики content-engine |
| **Instagram Reels** | ✅ полная авто | IG Business + FB Page + Meta app + публичный хостинг видео (Cloudflare R2 free) |
| **TikTok** | 🟡 полу-авто | inbox-загрузка + ручной тап «Опубликовать» (авто-публикацию соло не дают — гнаться нельзя) |

### YouTube — первичная настройка
1. `console.cloud.google.com` → проект → включить **YouTube Data API v3**.
2. OAuth consent screen: **External + Production** (иначе токен живёт 7 дней).
3. Credentials → OAuth client → **Desktop** → скачать `client_secret.json`.
4. `export YT_CLIENT_SECRET_FILE=...` → `python3 -m adapters.youtube_auth` (откроет браузер).
5. После первой (приватной) загрузки — подать форму **YouTube API Compliance Audit**; ждать 2-4 недели. До этого все загрузки молча приватные.

## Музыка (опционально)

Положи треки в `assets/music/` — будут подмешаны фоном под голос (−10 дБ).
**Только** YouTube Audio Library («Attribution not required») для YouTube и Mixkit для TikTok/IG.
Pixabay Music для Shorts не использовать (Content ID).

## Планирование

Cron или GitHub Actions, например 3 ролика/неделю:
```
0 12 * * 1,3,5  cd ~/projects/content-factory && source ~/.config/content-engine/secrets.env && python3 factory.py run ai_lifehacks
```

## Структура

```
core.py            конфиг, секреты, ниши, история, утилиты
niches.json        контент-стратегия (ниши)
pipeline/          script → voice → broll → subtitles → assemble → build
adapters/          youtube · instagram · tiktok · vk_video · youtube_auth
factory.py         CLI: doctor / niches / build / batch / post / run
reporter.py        отчёты в Telegram
```
