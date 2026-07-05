"""Авто-обложки (thumbnail) для вертикального ролика — AI-постер-фон (NVIDIA FLUX)
или кадр из видео + крупный заголовок поверх (стиль виральных shorts).

Закрывает TODO «авто-обложки». Берём кадр (~15% длительности — обычно уже не
чёрный интро-кадр, но ещё «сочный»), приводим к 1080×1920 cover-crop, затемняем
нижнюю треть градиентом для читаемости, вжигаем ALL-CAPS заголовок крупным жирным
шрифтом (Montserrat Black, кириллица) с толстой чёрной обводкой и акцент-цветом
ниши. Сохраняем JPEG. Любой сбой → фолбэк на сплошной фон бренд-цвета, None при
полном провале (с core.log_error).

#10: По умолчанию фон обложки генерится AI (NVIDIA FLUX → Pollinations, каскад imagegen) —
яркий кликбейт-постер ради CTR; при исчерпании квоты/оффлайне фолбэк на лучший кадр видео.
Отключить AI-фон: env THUMB_AI_BG=0.

Pillow импортируем ЛЕНИВО внутри функций (как договорено в проекте).
"""
import os
import re
import sys
import hashlib
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

# Бренд {P} — акцент по умолчанию, если у ниши нет палитры.
BRAND_ACCENT = "#3DDC97"
BRAND_DARK = "#080A09"


def _font_path() -> str:
    """Montserrat Black из ассетов проекта (кириллица), фолбэк — DejaVuSans-Bold."""
    f = core.ASSETS_DIR / "fonts" / "Montserrat-Black.ttf"
    if f.exists():
        return str(f)
    # запасные варианты внутри проекта, потом системный жирный
    for alt in ("Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf"):
        p = core.ASSETS_DIR / "fonts" / alt
        if p.exists():
            return str(p)
    return core.FONT_BOLD


def _accent_from_niche(niche: dict | None) -> str:
    """Акцент-цвет: первая палитра ниши (пара [тёмный, акцент]) → бренд-зелёный."""
    if niche:
        pal = niche.get("palette") or []
        try:
            hexc = pal[0][1]                       # [[dark, accent], ...]
            hexc = str(hexc).lstrip("#")
            if len(hexc) == 6:
                return "#" + hexc
        except (IndexError, TypeError, KeyError):
            pass
    return BRAND_ACCENT


def _extract_frame(video_path: str, frame_t: float | None, dst: pathlib.Path) -> bool:
    """Вытащить один кадр в момент frame_t (по умолчанию ~15% длительности) в PNG.
    True при успехе непустого файла."""
    dur = core.media_duration(video_path)
    if frame_t is None:
        frame_t = max(0.3, dur * 0.15) if dur > 0 else 1.0
    # не вылезти за конец ролика
    if dur > 0:
        frame_t = min(frame_t, max(0.1, dur - 0.2))
    try:
        # -ss перед -i = быстрый seek; -frames:v 1 = ровно один кадр
        core.run([
            "ffmpeg", "-y", "-ss", f"{frame_t:.3f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(dst),
        ])
    except Exception as e:  # noqa: BLE001 — фолбэк на сплошной фон выше
        core.log_error("thumbnail._extract_frame", e, video=str(video_path))
        return False
    return dst.exists() and dst.stat().st_size > 0


def _frame_score(img) -> float:
    """#8: оценка «обложечности» кадра только через Pillow (без numpy/opencv).
    Score = резкость · яркость-в-безопасном-диапазоне · контраст.
      • резкость   — дисперсия градиента (|сосед-сосед|) уменьшенной L-копии (прокси Лапласа);
      • яркость    — штраф за пере/недо-экспозицию (целимся в средний серый ~115);
      • контраст   — СКО гистограммы яркости (разброс тонов).
    Чем выше — тем «сочнее»/чётче кадр. Все компоненты нормированы в ~[0..1]."""
    from PIL import Image, ImageFilter  # ленивый импорт
    g = img.convert("L")
    # ужимаем до ~256px по длинной стороне — быстрый и устойчивый к шуму замер
    long = max(g.width, g.height)
    if long > 256:
        s = 256 / long
        g = g.resize((max(1, round(g.width * s)), max(1, round(g.height * s))), Image.BILINEAR)

    # --- резкость: дисперсия отклика edge-фильтра (прокси дисперсии Лапласа) ---
    edges = g.filter(ImageFilter.FIND_EDGES)
    eh = edges.histogram()
    n = sum(eh) or 1
    mean_e = sum(i * c for i, c in enumerate(eh)) / n
    var_e = sum(((i - mean_e) ** 2) * c for i, c in enumerate(eh)) / n
    sharp = min(1.0, var_e / 2000.0)          # ~2000 = «достаточно резкий» потолок

    # --- яркость: средняя по гистограмме L; штраф за тьму/пересвет ---
    h = g.histogram()
    nb = sum(h) or 1
    mean_b = sum(i * c for i, c in enumerate(h)) / nb
    # «безопасный» диапазон ~[60..190]; вне — резко падает (гаусс вокруг 115)
    bright = pow(2.718281828, -((mean_b - 115.0) ** 2) / (2 * (55.0 ** 2)))

    # --- контраст: СКО яркости по гистограмме (нормируем к ~64) ---
    var_b = sum(((i - mean_b) ** 2) * c for i, c in enumerate(h)) / nb
    std_b = var_b ** 0.5
    contrast = min(1.0, std_b / 64.0)

    # резкость — главный фактор; яркость как множитель режет «мусорные» кадры
    return (sharp * 0.6 + contrast * 0.4) * (0.35 + 0.65 * bright)


def _best_frame(video_path: str, dst: pathlib.Path) -> bool:
    """#8: вытащить 4-5 кадров-кандидатов (12/20/35/55/75% длительности), оценить каждый
    через _frame_score и оставить лучший как dst. Заменяет «лотерею» dur*0.15.
    Фолбэк: при любом сбое — обычный _extract_frame(None) (тот самый ~15%-кадр)."""
    try:
        from PIL import Image  # ленивый импорт; нет PIL → фолбэк ниже
    except Exception as e:  # noqa: BLE001
        core.log_error("thumbnail._best_frame(PIL import)", e)
        return _extract_frame(video_path, None, dst)

    dur = core.media_duration(video_path)
    if dur <= 0:
        return _extract_frame(video_path, None, dst)

    fracs = (0.12, 0.20, 0.35, 0.55, 0.75)
    best_score, best_path = -1.0, None
    cand_dir = dst.parent
    cands: list[pathlib.Path] = []
    try:
        for i, fr in enumerate(fracs):
            t = min(max(0.3, dur * fr), max(0.1, dur - 0.2))
            cp = cand_dir / (dst.stem + f"_c{i}.png")
            if not _extract_frame(video_path, t, cp):
                continue
            cands.append(cp)
            try:
                with Image.open(cp) as im:
                    sc = _frame_score(im)
            except Exception as e:  # noqa: BLE001 — битый кандидат пропускаем
                core.log_error("thumbnail._best_frame(score)", e, frame=cp.name)
                continue
            if sc > best_score:
                best_score, best_path = sc, cp

        if best_path is None:                 # ни один кандидат не оценился → фолбэк
            for cp in cands:
                cp.unlink(missing_ok=True)
            return _extract_frame(video_path, None, dst)

        # лучший кандидат → dst (заменяя при необходимости), остальные чистим
        dst.unlink(missing_ok=True)
        best_path.replace(dst)
        for cp in cands:
            if cp != best_path:
                cp.unlink(missing_ok=True)
        core.log(f"обложка: выбран лучший кадр (score={best_score:.3f})", thumb=dst.name)
        return dst.exists() and dst.stat().st_size > 0
    except Exception as e:  # noqa: BLE001
        core.log_error("thumbnail._best_frame", e, video=str(video_path))
        for cp in cands:
            cp.unlink(missing_ok=True)
        return _extract_frame(video_path, None, dst)


def _cover_crop(base, W: int, H: int):
    """Масштаб «на покрытие» + центр-кроп до W×H (PIL Image)."""
    from PIL import Image  # ленивый импорт
    scale = max(W / base.width, H / base.height)
    nw, nh = max(1, round(base.width * scale)), max(1, round(base.height * scale))
    base = base.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - W) // 2, (nh - H) // 2
    return base.crop((left, top, left + W, top + H))


def _thumb_bg_prompt(niche: dict | None, query: str = "") -> str:
    """#10: EN-промпт постер-фона обложки — яркий драматичный кадр по теме ниши, тёмный
    низ под заголовок. Субъект берём из query → иначе первый намёк broll_hint ниши."""
    subj = (query or "").strip()
    if not subj and niche:
        hints = [h.strip() for h in (niche.get("broll_hint") or "").split(",") if h.strip()]
        subj = ", ".join(hints[:3])               # 3 намёка = богаче сцена, чем одно слово
    subj = subj or "dramatic abstract scene"
    return (f"eye-catching dramatic poster, {subj}, vivid bold concrete focal subject filling the "
            f"upper two thirds, glossy magazine-cover style, rich saturated colors, strong cinematic "
            f"rim light and glow, deep depth of field, the lower third fades into dark shadow, "
            f"no text, no letters, no captions, no watermark, ultra detailed, photographic, 9:16 vertical")


def _ai_background(niche: dict | None, query: str, dst: pathlib.Path, seed: int) -> bool:
    """#10: сгенерировать AI-фон обложки через NVIDIA FLUX (каскад imagegen.generate_raw).
    True при успехе. Любой сбой/нет ключа/исчерпана квота → False (выше — фолбэк на кадр)."""
    try:
        try:
            from pipeline import imagegen as ig
        except ImportError:
            import imagegen as ig  # запуск из каталога pipeline/
    except Exception as e:  # noqa: BLE001
        core.log_error("thumbnail._ai_background(import)", e)
        return False
    try:
        res = ig.generate_raw(_thumb_bg_prompt(niche, query), dst, seed=seed)
        return bool(res) and dst.exists() and dst.stat().st_size > 5000
    except Exception as e:  # noqa: BLE001
        core.log_error("thumbnail._ai_background", e)
        return False


def make_thumbnail(video_path: str, title: str, out_path: pathlib.Path,
                   niche: dict | None = None, frame_t: float | None = None,
                   ai_bg: bool = True, bg_query: str = "", seed: int = 0) -> pathlib.Path | None:
    """Сгенерировать обложку: AI-постер-фон (NVIDIA FLUX) или кадр из видео + заголовок поверх.
    ai_bg=True (по умолч.) — фон генерится FLUX'ом; при сбое/квоте — фолбэк на лучший кадр.
    Вернуть путь или None."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageColor  # ленивый импорт
    except Exception as e:  # noqa: BLE001
        core.log_error("thumbnail.make_thumbnail(PIL import)", e)
        return None

    W, H = core.W, core.H
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    accent = _accent_from_niche(niche)
    font_file = _font_path()

    try:
        # 1) База обложки (приоритет): AI-постер-фон (NVIDIA FLUX) → лучший кадр из видео →
        #    сплошной бренд-фон. AI = яркий кликбейт-кадр (CTR); кадр — надёжный фолбэк при
        #    исчерпании квоты/оффлайне. #8: при frame_t=None берём ЛУЧШИЙ кадр из кандидатов.
        img = None
        base_kind = "solid"
        tmp = out_path.with_name(out_path.stem + "_frame.png")
        if ai_bg:
            base_seed = seed or int(hashlib.md5(
                ((title or "") + str((niche or {}).get("id", ""))).encode()).hexdigest()[:6], 16)
            n_ab = max(1, min(3, int(os.environ.get("THUMB_AB", "2") or 2)))   # A/B: N фонов → лучший
            best_sc = -1.0
            for k in range(n_ab):
                cand = out_path.with_name(out_path.stem + f"_ai{k}.png")
                if _ai_background(niche, bg_query, cand, (base_seed + k * 1009) & 0x7fffffff):
                    try:
                        im = Image.open(cand).convert("RGB")
                        scv = _frame_score(im)               # «сочность»: резкость+контраст+яркость
                        if scv > best_sc:
                            best_sc, img, base_kind = scv, _cover_crop(im, W, H), "ai"
                    except Exception as e:  # noqa: BLE001
                        core.log_error("thumbnail.ai_open", e)
                cand.unlink(missing_ok=True)
            if base_kind == "ai":
                core.log(f"обложка A/B: лучший из {n_ab} (score={best_sc:.3f})", level="debug")
        if img is None:
            got = _best_frame(video_path, tmp) if frame_t is None \
                else _extract_frame(video_path, frame_t, tmp)
            if got:
                img = _cover_crop(Image.open(tmp).convert("RGB"), W, H)
                base_kind = "frame"
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        if img is None:
            img = Image.new("RGB", (W, H), BRAND_DARK)

        draw = ImageDraw.Draw(img)

        # 2a) Затемнение нижней трети градиентом (сверху прозрачно → снизу почти чёрно):
        #     текст внизу станет читаемым на любом фоне.
        grad_top = int(H * 0.55)                  # начинаем темнить с ~55% высоты
        grad_h = H - grad_top
        overlay = Image.new("RGBA", (W, grad_h), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        for y in range(grad_h):
            a = int(245 * (y / max(1, grad_h - 1)) ** 1.4)   # 0 → ~245, нелинейно (мягче сверху)
            odraw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
        img.paste(overlay, (0, grad_top), overlay)
        draw = ImageDraw.Draw(img)

        # 2b) Опц. маленькая плашка ниши сверху (название ниши акцент-цветом на тёмной полоске).
        try:
            tag = (niche or {}).get("title", "") if niche else ""
            tag = (tag or "").strip().upper()
            if tag:
                if len(tag) > 26:
                    tag = tag[:25].rstrip() + "…"
                tag_font = ImageFont.truetype(font_file, 40)
                tb = draw.textbbox((0, 0), tag, font=tag_font)
                tw, th = tb[2] - tb[0], tb[3] - tb[1]
                pad_x, pad_y = 34, 20
                bx0, by0 = 60, 70
                bx1, by1 = bx0 + tw + pad_x * 2, by0 + th + pad_y * 2
                draw.rounded_rectangle([bx0, by0, bx1, by1], radius=18, fill=(8, 10, 9, 235))
                draw.text((bx0 + pad_x, by0 + pad_y - tb[1]), tag, font=tag_font, fill=accent)
        except Exception as e:  # noqa: BLE001 — плашка не обязательна
            core.log_error("thumbnail.tag", e)

        # 2c) Заголовок ALL CAPS внизу: подбираем размер шрифта так, чтобы 1-3 строки
        #     влезли по ширине и не залезли в нижнюю четверть (UI площадок).
        text = (title or "").strip().upper() or "СМОТРИ ДО КОНЦА"
        max_w = W - 120                           # поля по 60px слева/справа
        max_text_h = int(H * 0.34)                # высота блока заголовка
        # #9: текст теперь короткий хук (≤5 слов) → агрессивнее держим КРУПНЫЙ кегль,
        #     минимум 90px вместо 50px (лучше усечь хвост слов, чем мельчить).
        MIN_SIZE = 90
        stroke = 0
        chosen = None
        for size in range(150, MIN_SIZE - 1, -6):
            font = ImageFont.truetype(font_file, size)
            stroke = max(4, size // 16)           # толстая обводка, масштабируется с кеглем
            lines = _wrap(draw, text, font, max_w, stroke)
            if len(lines) > 3:
                continue
            line_h = _line_height(draw, font, stroke)
            block_h = line_h * len(lines)
            widest = max((_text_w(draw, ln, font, stroke) for ln in lines), default=0)
            if widest <= max_w and block_h <= max_text_h:
                chosen = (font, lines, line_h, block_h)
                break
        if chosen is None:                        # не влез даже на минимуме → держим кегль,
            font = ImageFont.truetype(font_file, MIN_SIZE)   #   усекаем слова, а не мельчим
            stroke = max(4, MIN_SIZE // 16)
            lines = _wrap(draw, text, font, max_w, stroke)[:3]
            line_h = _line_height(draw, font, stroke)
            chosen = (font, lines, line_h, line_h * len(lines))
        font, lines, line_h, block_h = chosen

        # 3) Рисуем заголовок: нижний край блока ~88% высоты (над кнопками платформ),
        #    белый текст, чёрная толстая обводка, последняя строка — акцент-цветом (хук).
        bottom_y = int(H * 0.88)
        y = bottom_y - block_h
        for i, ln in enumerate(lines):
            lw = _text_w(draw, ln, font, stroke)
            x = (W - lw) // 2
            fill = accent if (i == len(lines) - 1 and len(lines) > 1) else "#FFFFFF"
            draw.text((x, y), ln, font=font, fill=fill,
                      stroke_width=stroke, stroke_fill="black")
            y += line_h

        # 4) JPEG quality ~88
        img.convert("RGB").save(out_path, "JPEG", quality=88, optimize=True)
        core.log(f"обложка готова ({base_kind}): {out_path.name}", thumb=str(out_path))
        return out_path
    except Exception as e:  # noqa: BLE001
        core.log_error("thumbnail.make_thumbnail", e, video=str(video_path))
        return None


# ──────────────────────────── Текст-утилиты (PIL 12) ────────────────────────────

def _text_w(draw, text: str, font, stroke: int) -> float:
    """Ширина строки с учётом обводки (textbbox точнее textlength на крупных кеглях)."""
    bb = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return bb[2] - bb[0]


def _line_height(draw, font, stroke: int) -> int:
    """Высота строки с обводкой + межстрочный воздух (~14%)."""
    bb = draw.textbbox((0, 0), "ЙДЯ", font=font, stroke_width=stroke)
    return int((bb[3] - bb[1]) * 1.14)


def _wrap(draw, text: str, font, max_w: float, stroke: int) -> list[str]:
    """Перенос по словам так, чтобы каждая строка влезала в max_w (textbbox-замер)."""
    words = text.split()
    if not words:
        return [text]
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if _text_w(draw, trial, font, stroke) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ──────────────────────────── Из meta.json ────────────────────────────

# Служебные слова: обложка НЕ должна заканчиваться на них (иначе «…живут НА» — обрыв фразы).
_TAIL_STOP = frozenset({
    "на", "в", "во", "с", "со", "к", "ко", "по", "за", "из", "от", "до", "у", "о", "об", "про",
    "и", "а", "но", "или", "да", "же", "бы", "ли", "что", "как", "это", "не", "ни", "для", "без",
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or", "but", "with", "your", "you",
})


def _strip_dangling(head: str) -> str:
    """Убрать висящие служебные слова в конце (предлог/союз/частица) — фраза не должна обрываться на них."""
    ws = head.split()
    while len(ws) > 1 and re.sub(r"[^\w]", "", ws[-1].lower()) in _TAIL_STOP:
        ws.pop()
    return " ".join(ws)


def _thumb_hook(meta: dict, fallback: str) -> str:
    """#9: короткий текст для обложки. Приоритет — meta['thumb_text'] (законченная фраза от LLM);
    иначе meta['hook']/hook_variant, режем до первой «фразы» и до N слов, БЕЗ висящих служебных слов."""
    tt = (meta.get("thumb_text", "") or "").strip()
    if tt:
        tt = re.split(r"[.!?\n]", tt, 1)[0].strip()      # одна фраза
        w = tt.split()
        if 1 <= len(w) <= 6 and len(tt) <= 30:           # валидный короткий thumb_text — берём как есть
            return _strip_dangling(tt) or tt
    raw = (meta.get("hook", "") or "").strip()
    if not raw:
        hv = meta.get("hook_variants") or []
        raw = (str(hv[0]).strip() if hv else "")
    if not raw:
        return fallback
    # обрезаем по первому знаку препинания (берём первую «фразу» хука)
    head = re.split(r"[.!?,:;—–\-…\n]", raw, 1)[0].strip()
    head = head or raw
    words = head.split()
    if len(words) > 5:
        head = " ".join(words[:5])
    if len(head) > 24:
        # режем по словам, чтобы не оборвать на полуслове и держаться ≤24 симв
        acc = []
        for w in head.split():
            cand = (" ".join(acc + [w])).strip()
            if len(cand) > 24:
                break
            acc.append(w)
        head = " ".join(acc) if acc else head[:24].rstrip()
    head = _strip_dangling(head)                          # финально — без висящего предлога/союза
    return head.strip() or fallback


def make_for_meta(video_path: str, meta: dict, out_path: pathlib.Path) -> pathlib.Path | None:
    """Удобная обёртка для пайплайна: заголовок и ниша берутся из meta.json ролика."""
    title = ""
    try:
        title = (meta.get("captions", {}).get("youtube", {}) or {}).get("title", "") or ""
    except (AttributeError, TypeError):
        title = ""
    if not title:
        title = meta.get("topic", "") or ""

    # #9: на обложку — короткий хук-текст, не длинный SEO-title (фолбэк на title)
    thumb_text = _thumb_hook(meta, title)

    niche = None
    niche_id = meta.get("niche")
    if niche_id:
        try:
            niche = core.get_niche(niche_id)
        except Exception as e:  # noqa: BLE001 — ниша не критична, обложка сделается на бренде
            core.log_error("thumbnail.make_for_meta(get_niche)", e, niche=niche_id)
            niche = None
    # #10: AI-фон по умолчанию вкл; THUMB_AI_BG=0 — вернуть прежнее поведение (кадр из видео)
    ai_bg = os.environ.get("THUMB_AI_BG", "1").strip().lower() not in ("0", "false", "no", "off", "")
    return make_thumbnail(video_path, thumb_text, out_path, niche=niche, ai_bg=ai_bg)


def _thumb_texts(meta: dict, primary: str) -> list[str]:
    """Кандидаты текста обложки: приоритетный хук + альтернативы (варианты хука/заголовка).
    Уникальные, коротко-первыми (короткий крупный текст на обложке кликабельнее)."""
    cands = [primary]
    for t in (meta.get("hook_variants") or []):
        cands.append((t or "").strip())
    cands.append((meta.get("hook", "") or "").strip())
    for t in (meta.get("title_variants") or []):
        cands.append((t or "").strip())
    seen, out = set(), []
    for c in cands:
        c = _strip_dangling(c)
        k = c.lower()
        if c and k not in seen and 3 <= len(c) <= 60:
            seen.add(k); out.append(c)
    return out


def make_best_for_meta(video_path: str, meta: dict, out_path: pathlib.Path) -> pathlib.Path | None:
    """Авто-ВЫБОР обложки: генерим несколько вариантов (разный текст/сид), Gemini-зрение выбирает
    самый кликабельный и проверяет читаемость (Vision-QA обложки). Результат — в out_path;
    вердикт зрения кладём в meta['thumb_qa']. Fallback → обычная make_for_meta (один вариант).
    Гейт CF_THUMB_SELECT (по умолч. вкл); при отсутствии Gemini/PIL — тихий фолбэк."""
    if os.environ.get("CF_THUMB_SELECT", "1").strip().lower() in ("0", "false", "no", "off"):
        return make_for_meta(video_path, meta, out_path)
    try:
        from pipeline import vision
    except Exception:  # noqa: BLE001
        return make_for_meta(video_path, meta, out_path)
    if not vision.keys():
        return make_for_meta(video_path, meta, out_path)

    out_path = pathlib.Path(out_path)
    primary = _thumb_hook(meta, (meta.get("captions", {}).get("youtube", {}) or {}).get("title", "")
                          or meta.get("topic", ""))
    texts = _thumb_texts(meta, primary)
    n = max(2, min(3, int(os.environ.get("THUMB_SELECT_N", "2") or 2)))
    texts = texts[:n]
    if len(texts) < 2:                                   # нечего выбирать — обычный путь
        return make_for_meta(video_path, meta, out_path)

    niche = None
    if meta.get("niche"):
        try:
            niche = core.get_niche(meta["niche"])
        except Exception:  # noqa: BLE001
            niche = None
    ai_bg = os.environ.get("THUMB_AI_BG", "1").strip().lower() not in ("0", "false", "no", "off", "")

    variants = []
    for i, txt in enumerate(texts):
        cand = out_path.with_name(out_path.stem + f"_v{i}.jpg")
        seed = int(hashlib.md5((txt + str(i)).encode()).hexdigest()[:6], 16)
        res = make_thumbnail(video_path, txt, cand, niche=niche, ai_bg=ai_bg, seed=seed)
        if res:
            variants.append({"i": i, "text": txt, "path": pathlib.Path(res)})
    if not variants:
        return make_for_meta(video_path, meta, out_path)
    if len(variants) == 1:                               # сгенерился только один — берём его
        chosen = variants[0]["path"]
        chosen.replace(out_path)
        return out_path

    prompt = (
        "Это варианты обложки (превью) для вертикального видео Shorts/Reels под русскую аудиторию. "
        "Выбери САМЫЙ кликабельный: крупный читаемый текст, эмоция/интрига, контраст, нет визуальных "
        "дефектов (кривые лица/руки, кракозябры). Верни СТРОГО JSON: "
        '{"best": <номер лучшего, 0-based>, "readable": true|false, "click_score": 1-10, '
        '"issue": "кратко о проблеме лучшего варианта или пусто"}.')
    verdict = vision.ask_json(prompt, [v["path"] for v in variants], max_tokens=300)

    best_i = 0
    if isinstance(verdict, dict) and isinstance(verdict.get("best"), int) \
            and 0 <= verdict["best"] < len(variants):
        best_i = verdict["best"]
    chosen = variants[best_i]["path"]
    # чистим проигравшие
    for v in variants:
        if v["path"] != chosen:
            v["path"].unlink(missing_ok=True)
    chosen.replace(out_path)

    meta["thumb_qa"] = {
        "chosen_text": variants[best_i]["text"],
        "variants": len(variants),
        "click_score": (verdict or {}).get("click_score"),
        "readable": (verdict or {}).get("readable"),
        "issue": (verdict or {}).get("issue", ""),
    }
    core.log(f"обложка: выбран вариант {best_i}/{len(variants)} "
             f"(score={(verdict or {}).get('click_score')}) «{variants[best_i]['text'][:40]}»",
             level="info")
    return out_path


if __name__ == "__main__":
    core.load_local_secrets()
    if len(sys.argv) >= 3:
        _video = sys.argv[1]
        _title = sys.argv[2]
        _out = pathlib.Path(_video).with_name(pathlib.Path(_video).stem + "_thumb.jpg")
        _res = make_thumbnail(_video, _title, _out)
        print(_res if _res else "FAILED")
    else:
        print("Использование: python3 pipeline/thumbnail.py <video.mp4> <заголовок>")
