"""Modal serverless GPU-движок image-to-video: «оживление» AI-кадров для фабрики коротких видео.

У владельца НЕТ своего GPU. Modal (modal.com) даёт ~$30/мес возобновляемых GPU-кредитов
по подписке Starter — этого хватает на ДЕСЯТКИ коротких клипов в месяц (не безлимит!).
Поэтому Modal тратим ТОЛЬКО на топ-сцены ролика (хук, ключевой кадр), а основную массу
по-прежнему оживляет бесплатный локальный DepthFlow / Ken Burns в pipeline/broll.py.

Файл состоит из ДВУХ частей в одном модуле:

  (A) MODAL-ПРИЛОЖЕНИЕ — деплоится в облако Modal командой `modal deploy video_gpu.py`.
      Функция на GPU крутит Wan2.2-TI2V-5B (Apache-2.0, image-to-video) и превращает
      картинку + промпт в короткий вертикальный mp4. Веса кэшируются в Modal Volume.
      Эндпоинт `@modal.fastapi_endpoint` принимает картинку (base64/URL) + prompt + длительность
      и возвращает mp4 в base64. Импорт `modal` нужен ТОЛЬКО здесь и обёрнут в try/except,
      чтобы клиентская часть и сам `import video_gpu` работали БЕЗ установленного modal.

  (B) КЛИЕНТ — вызывается из локального пайплайна (modal на машине НЕ нужен). Функция
      `animate_image()` шлёт POST на задеплоенный эндпоинт (URL из env MODAL_VIDEO_URL),
      сохраняет mp4 и возвращает путь. None при сбое/отсутствии URL/исчерпании бюджета —
      пайплайн мягко падает на DepthFlow/Ken Burns.

──────────────────────────────────────────────────────────────────────────────────────
ЧТО СДЕЛАТЬ ЮЗЕРУ РУКАМИ (один раз):

  1. Установить CLI Modal в любой Python-окружении:
         pip install modal

  2. Привязать аккаунт (бесплатно, БЕЗ карты на старте — Starter-план даёт ~$30/мес кредитов):
         modal token new
     (откроется браузер, логин через GitHub/Google; токен ляжет в ~/.modal.toml)

  3. Задеплоить это приложение в облако Modal:
         modal deploy video_gpu.py
     По завершении Modal напечатает URL веб-эндпоинта вида:
         https://<workspace>--content-factory-video-animate-endpoint.modal.run
     ПЕРВЫЙ вызов скачает веса Wan2.2 (~10-20 ГБ) в Volume — это медленно (минуты);
     последующие вызовы уже из кэша.

  4. Скопировать этот URL в ~/.config/content-factory/secrets.env:
         MODAL_VIDEO_URL=https://<workspace>--content-factory-video-animate-endpoint.modal.run
     (и при необходимости лимит бюджета, см. ниже)

  5. Выставить месячный бюджет в секундах сгенерированного видео (защита от слива кредитов):
         MODAL_MONTHLY_SEC=180
     180 сек ≈ 36 клипов по 5 сек ≈ безопасно под ~$30/мес на A10G. Дефолт — тоже 180.

⚠️  ПРЕДУПРЕЖДЕНИЕ: ~$30/мес = десятки клипов, НЕ безлимит. Wan2.2-TI2V-5B рендерит
    5-сек 720p-клип ~9 мин на одном GPU — это и время, и деньги. Бюджет учитывается
    локально (core.DATA_ROOT/modal_budget.json) и сбрасывается раз в календарный месяц.
    Когда бюджет исчерпан — animate_image() вернёт None, и ролик соберётся на Ken Burns.
──────────────────────────────────────────────────────────────────────────────────────
"""
import os
import json
import base64
import pathlib
import datetime as dt

# ──────────────────────────── Клиентская обвязка (без modal) ────────────────────────────
# Конвенция проекта: добавляем корень в путь и тянем core (пути, секреты, логи, http, W/H).
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import core  # noqa: E402

# requests — для удобного POST с таймаутом; если нет, мягко падаем на urllib (есть в core/stdlib).
try:
    import requests  # noqa: F401
    _HAS_REQUESTS = True
except Exception:  # noqa: BLE001
    _HAS_REQUESTS = False

# Имя приложения Modal и идентификатор модели — общие для обеих частей.
APP_NAME = "content-factory-video"
MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"   # Apache-2.0, image-to-video, 720p@24fps
GPU_TYPE = "A10G"                              # дёшево и достаточно под 5B; для скорости — "A100"
CACHE_DIR_MODAL = "/cache"                     # точка монтирования Volume с весами внутри контейнера


# ════════════════════════════════════════════════════════════════════════════════════════
#  (A) MODAL-ПРИЛОЖЕНИЕ  —  работает в облаке, локально не исполняется
#  Ленивый импорт modal в try/except: без установленного modal клиентская часть жива.
# ════════════════════════════════════════════════════════════════════════════════════════
try:
    import modal

    # Образ контейнера: CUDA-torch + diffusers (Wan2.2 влит в main-ветку diffusers с 28.07.2025,
    # поэтому ставим diffusers из git, а не релиз с pypi) + ffmpeg для экспорта mp4.
    _image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("ffmpeg", "git")
        .pip_install(
            "torch",
            "torchvision",
            "git+https://github.com/huggingface/diffusers",  # TODO: при стабилизации закрепить версию diffusers==X.Y
            "transformers",
            "accelerate",
            "ftfy",
            "imageio",
            "imageio-ffmpeg",
            "Pillow",
            "huggingface_hub",
            "fastapi[standard]",   # нужно для @modal.fastapi_endpoint
        )
        # Кэш HuggingFace внутрь Volume, чтобы веса не качались каждый холодный старт.
        .env({"HF_HOME": CACHE_DIR_MODAL, "HF_HUB_ENABLE_HF_TRANSFER": "1"})
    )

    app = modal.App(APP_NAME)

    # Volume для весов модели (~10-20 ГБ): один раз скачали — дальше из кэша.
    weights_vol = modal.Volume.from_name("content-factory-weights", create_if_missing=True)

    @app.cls(
        gpu=GPU_TYPE,
        image=_image,
        volumes={CACHE_DIR_MODAL: weights_vol},
        timeout=60 * 20,            # рендер 720p может занять ~9-15 мин — даём запас
        scaledown_window=120,       # держим контейнер 2 мин после запроса (тёплый старт для серии кадров)
        # secrets=[modal.Secret.from_name("huggingface")],  # TODO: если веса станут gated — добавить HF-токен
    )
    class WanEngine:
        """Контейнер с загруженной в память моделью Wan2.2-TI2V-5B (image-to-video)."""

        @modal.enter()
        def load(self):
            """Грузим пайплайн один раз на старте контейнера (а не на каждый запрос)."""
            import torch
            from diffusers import WanPipeline, AutoencoderKLWan

            dtype = torch.bfloat16
            # VAE держим в fp32 (рекомендация карточки модели — стабильнее).
            vae = AutoencoderKLWan.from_pretrained(MODEL_ID, subfolder="vae", torch_dtype=torch.float32)
            self.pipe = WanPipeline.from_pretrained(MODEL_ID, vae=vae, torch_dtype=dtype)
            self.pipe.to("cuda")
            # Экономия VRAM на A10G (24 ГБ) — выгружаем неиспользуемые модули на CPU при нехватке.
            try:
                self.pipe.enable_model_cpu_offload()
            except Exception:  # noqa: BLE001 — не критично, просто оптимизация
                pass
            weights_vol.commit()    # зафиксировать только что скачанные в Volume веса

        @modal.method()
        def animate(self, image_bytes: bytes, prompt: str, duration: float = 4.0,
                    negative_prompt: str = "") -> bytes:
            """Картинка (bytes) + prompt → mp4 (bytes). Длительность в секундах → число кадров."""
            import io
            import tempfile
            import torch
            from PIL import Image
            from diffusers.utils import export_to_video

            fps = 24  # нативный fps Wan2.2-TI2V-5B
            # Кадры: duration*fps, но кратно 4 и в разумных рамках (модель тренирована ~121 кадр / 5с).
            num_frames = max(25, min(121, (int(round(duration * fps)) // 4) * 4 + 1))

            # Вертикальный кадр под Shorts/Reels (1080x1920), но кратно 16 и в пределах VRAM.
            # Карточка модели по умолчанию даёт 1280x704 (720p); для портрета 704x1280 ниже.
            width, height = 704, 1280  # TODO: при OOM на A10G снизить до 480x854 или взять A100

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((width, height))

            neg = negative_prompt or (
                "low quality, blurry, distorted, watermark, text, static, frozen, jpeg artifacts"
            )

            generator = torch.Generator(device="cuda").manual_seed(42)
            output = self.pipe(
                image=image,
                prompt=prompt,
                negative_prompt=neg,
                height=height,
                width=width,
                num_frames=num_frames,
                guidance_scale=5.0,
                num_inference_steps=50,   # TODO: 30-40 шагов = быстрее/дешевле при чуть меньшем качестве
                generator=generator,
            ).frames[0]

            # Экспортируем во временный mp4 и читаем байты обратно.
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                out_path = tmp.name
            export_to_video(output, out_path, fps=fps)
            data = pathlib.Path(out_path).read_bytes()
            pathlib.Path(out_path).unlink(missing_ok=True)
            return data

    @app.function(image=_image, timeout=60 * 20)
    @modal.fastapi_endpoint(method="POST")
    def animate_endpoint(payload: dict) -> dict:
        """Веб-эндпоинт. JSON-вход:
            {"image_b64": "<base64 png/jpg>"  ИЛИ  "image_url": "https://...",
             "prompt": "...", "duration": 4.0, "negative_prompt": "(опц.)"}
        Ответ: {"ok": true, "video_b64": "<base64 mp4>", "video_sec": <float>} либо {"ok": false, "error": "..."}.
        """
        try:
            prompt = (payload.get("prompt") or "").strip()
            if not prompt:
                return {"ok": False, "error": "пустой prompt"}
            duration = float(payload.get("duration") or 4.0)

            # Картинка приходит как base64 или как URL — приводим к bytes.
            if payload.get("image_b64"):
                image_bytes = base64.b64decode(payload["image_b64"])
            elif payload.get("image_url"):
                import urllib.request
                req = urllib.request.Request(payload["image_url"], headers={"User-Agent": "content-factory"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    image_bytes = r.read()
            else:
                return {"ok": False, "error": "нет image_b64 и image_url"}

            video = WanEngine().animate.remote(
                image_bytes, prompt, duration, payload.get("negative_prompt", ""),
            )
            return {
                "ok": True,
                "video_b64": base64.b64encode(video).decode("ascii"),
                "video_sec": duration,
            }
        except Exception as e:  # noqa: BLE001 — вернуть ошибку клиенту, а не 500 без тела
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ─────────────────── Опциональные пост-обработчики (отдельные GPU-функции) ───────────────────
    # TODO: RIFE-интерполяция до 60fps — отдельный @app.function(gpu=...) поверх готового mp4.
    #   Реализация: образ с github.com/hzwer/Practical-RIFE (или rife-ncnn-vulkan), вход mp4→выход mp4
    #   с удвоенным fps. Полезно, чтобы 24fps Wan2.2 догнать до плавных 48/60fps под Shorts.
    #
    # TODO: Real-ESRGAN-апскейл — отдельный @app.function(gpu=...): xinntao/Real-ESRGAN, вход mp4→
    #   апскейл x2 до чёткого 1080p+ (Wan2.2 рендерит 704x1280, телефонный экран любит резче).
    #   Оставлены TODO, а не реализованы, чтобы не раздувать файл и не качать лишние веса/кредиты,
    #   пока базовый I2V не обкатан вживую.

    MODAL_AVAILABLE = True
except ImportError:
    # modal не установлен — это НОРМА на локальной машине пайплайна. Клиентская часть ниже работает.
    MODAL_AVAILABLE = False


# ════════════════════════════════════════════════════════════════════════════════════════
#  УЧЁТ БЮДЖЕТА  —  $30 ≠ бесконечность. Простой счётчик секунд видео по месяцам,
#  по образцу ротации LLM-ключей по cooldown. Файл: core.DATA_ROOT/modal_budget.json
# ════════════════════════════════════════════════════════════════════════════════════════

_BUDGET_FILE = core.DATA_ROOT / "modal_budget.json"


def _month_key() -> str:
    """Ключ текущего календарного месяца в MSK — бюджет сбрасывается с новым месяцем."""
    return dt.datetime.now(core.TZ).strftime("%Y-%m")


def _budget_limit_sec() -> float:
    """Месячный лимит секунд видео из env MODAL_MONTHLY_SEC (дефолт 180с ≈ безопасно под ~$30)."""
    try:
        return max(0.0, float(os.environ.get("MODAL_MONTHLY_SEC", "180")))
    except (ValueError, TypeError):
        return 180.0


def _budget_load() -> dict:
    """Прочитать состояние бюджета {month, spent_sec}. Авто-сброс при смене месяца."""
    cur = _month_key()
    try:
        data = json.loads(_BUDGET_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — нет файла/битый → начинаем с нуля
        data = {}
    if data.get("month") != cur:
        data = {"month": cur, "spent_sec": 0.0}
    return data


def budget_spent_sec() -> float:
    """Сколько секунд видео уже потрачено в текущем месяце."""
    return float(_budget_load().get("spent_sec", 0.0))


def budget_left() -> bool:
    """True, если в этом месяце ещё есть бюджет на генерацию видео на Modal."""
    return budget_spent_sec() < _budget_limit_sec()


def _budget_charge(sec: float) -> None:
    """Списать `sec` секунд из месячного бюджета (после успешной генерации)."""
    try:
        data = _budget_load()
        data["spent_sec"] = round(float(data.get("spent_sec", 0.0)) + max(0.0, sec), 2)
        core.DATA_ROOT.mkdir(parents=True, exist_ok=True)
        _BUDGET_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — учёт не должен ронять пайплайн
        core.log_error("modal budget charge", e)


# ════════════════════════════════════════════════════════════════════════════════════════
#  (B) КЛИЕНТ  —  вызывается из пайплайна на локальной машине (modal не нужен)
# ════════════════════════════════════════════════════════════════════════════════════════

def animate_image(image_path: str, prompt: str, out_path: pathlib.Path,
                  duration: float = 4.0) -> pathlib.Path | None:
    """Оживить картинку через задеплоенный Modal-эндпоинт: картинка+prompt → короткий mp4.

    Возвращает путь к mp4 при успехе, иначе None (мягко — пайплайн фолбэкнется на Ken Burns/DepthFlow):
      • нет MODAL_VIDEO_URL в окружении;
      • исчерпан месячный бюджет (MODAL_MONTHLY_SEC);
      • сетевой/серверный сбой или модель вернула ошибку.
    Все ветки логируются через core.log / core.log_error — тихих провалов нет.
    """
    url = os.environ.get("MODAL_VIDEO_URL", "").strip()
    if not url:
        core.log("Modal: нет MODAL_VIDEO_URL — пропускаю оживление (фолбэк Ken Burns)", level="warn")
        return None

    # КРИТИЧНО: бюджет проверяем ДО запроса — $30 не бесконечны, тратим только на топ-сцены.
    if not budget_left():
        core.log(f"Modal: бюджет исчерпан ({budget_spent_sec():.0f}/{_budget_limit_sec():.0f}с), "
                 f"фолбэк Ken Burns", level="warn")
        return None

    src = pathlib.Path(image_path)
    if not src.exists() or src.stat().st_size == 0:
        core.log(f"Modal: нет входной картинки {image_path}", level="warn")
        return None

    try:
        image_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
        body = {"image_b64": image_b64, "prompt": prompt, "duration": float(duration)}
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": core.UA}

        # Рендер 720p может идти минуты — таймаут щедрый. requests если есть, иначе urllib.
        if _HAS_REQUESTS:
            resp = requests.post(url, data=data, headers=headers, timeout=60 * 20)
            resp.raise_for_status()
            payload = resp.json()
        else:
            import urllib.request
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60 * 20) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))

        if not payload or not payload.get("ok"):
            core.log_error("Modal animate", RuntimeError((payload or {}).get("error", "пустой ответ")))
            return None

        video = base64.b64decode(payload["video_b64"])
        if len(video) < 5000:
            core.log("Modal: ответ-видео подозрительно мал — считаю сбоем", level="warn")
            return None

        out_path = pathlib.Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(video)

        # Списываем фактическую длительность (или запрошенную, если сервер не вернул).
        _budget_charge(float(payload.get("video_sec") or duration))
        core.log(f"Modal: кадр оживлён → {out_path.name} "
                 f"(бюджет {budget_spent_sec():.0f}/{_budget_limit_sec():.0f}с)", level="info")
        return out_path
    except Exception as e:  # noqa: BLE001 — любой сбой = мягкий None + лог, ролик не падает
        core.log_error("Modal animate_image", e)
        return None


# ──────────────────────────── Самотест клиента ────────────────────────────
if __name__ == "__main__":
    core.load_local_secrets()
    # Использование:  python3 video_gpu.py <картинка.png> ["промпт"] [длительность_сек]
    if core.has_secret("MODAL_VIDEO_URL") and len(sys.argv) >= 2:
        img = sys.argv[1]
        prm = sys.argv[2] if len(sys.argv) >= 3 else "slow cinematic camera push-in, gentle motion, photoreal"
        dur = float(sys.argv[3]) if len(sys.argv) >= 4 else 4.0
        core.ensure_dirs()
        dest = core.OUTPUT_DIR / "modal_test.mp4"
        print(f"→ Оживляю {img!r} через Modal ({dur}с)…")
        res = animate_image(img, prm, dest, duration=dur)
        if res:
            print(f"✓ Готово: {res}  (потрачено {budget_spent_sec():.0f}/{_budget_limit_sec():.0f}с в этом месяце)")
        else:
            print("✗ Не вышло (см. лог: нет URL / бюджет исчерпан / сбой) — пайплайн фолбэкнется на Ken Burns")
    else:
        print(__doc__)
        print("\n──────────────────────────────────────────────────────────")
        print("СТАТУС:")
        print(f"  modal установлен:    {'да' if MODAL_AVAILABLE else 'нет (норма для клиентской машины)'}")
        print(f"  MODAL_VIDEO_URL:     {'задан' if core.has_secret('MODAL_VIDEO_URL') else 'НЕ задан'}")
        print(f"  бюджет в этом месяце: {budget_spent_sec():.0f}/{_budget_limit_sec():.0f}с "
              f"({'есть' if budget_left() else 'ИСЧЕРПАН'})")
        print("\nДеплой движка:   modal deploy video_gpu.py")
        print("Тест клиента:    python3 video_gpu.py <картинка.png> \"промпт\" 4.0")
