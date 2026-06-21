"""Рекламный баннер NightFox VPN — анимированный overlay поверх ГОТОВОГО видео.

Изолированный пост-шаг: НЕ трогает рендер-граф assemble. Берёт video.mp4 → накладывает
зацикленный баннер (qtrle с альфой: лого-лиса + «NightFox VPN» + тэглайн + @NightFoxVPN_bot,
пульс-свечение + блик-проводка) сверху по центру. Луп генерится ОДИН раз и кэшируется в assets.

Выключатель: env VPN_BANNER=0. Позиция: VPN_BANNER_POS=top|bottom (по умолч. top — снизу субтитры).
Pillow импортируем ЛЕНИВО (как договорено в проекте).
"""
import os
import math
import shutil
import pathlib

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

VERSION = "v1"                                   # бамп при смене дизайна → перегенерит кэш
LOGO = core.ASSETS_DIR / "nightfox_logo.jpg"
LOOP_PATH = core.ASSETS_DIR / "banners" / f"nightfox_loop_{VERSION}.mov"

# палитра/размеры (утверждённый сэмпл)
NAVY1 = (20, 25, 44); NAVY2 = (30, 23, 58)
PURPLE = (123, 92, 255); PURPLE_L = (150, 120, 255)
CREAM = (242, 238, 226); GRAY = (176, 182, 208)
CARD_W, CARD_H, RAD = 980, 168, 34
MARGIN = 34
FW, FH = CARD_W + MARGIN * 2, CARD_H + MARGIN * 2
NFRAMES, FPS = 120, 30
NAME = "NightFox "; ACCENT_WORD = "VPN"; TAGLINE = "Быстрый VPN без границ"; CTA = "@NightFoxVPN_bot"


def enabled() -> bool:
    return os.environ.get("VPN_BANNER", "1").strip().lower() not in ("0", "false", "no", "off", "")


# Ниши БЕЗ VPN-баннера: личный бренд не рекламирует чужой/свой VPN на своём контенте.
# Дополнить можно через env VPN_BANNER_EXCLUDE="niche1,niche2".
def _exclude_niches() -> set:
    base = {"personal_brand"}
    extra = os.environ.get("VPN_BANNER_EXCLUDE", "")
    return base | {x.strip() for x in extra.split(",") if x.strip()}


def _render_frames(dst: pathlib.Path) -> None:
    """120 RGBA-кадров анимации (пульс-свечение + бегущий блик) в dst/frame_###.png."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter  # ленивый импорт
    fonts = core.ASSETS_DIR / "fonts"
    f_name = ImageFont.truetype(str(fonts / "Montserrat-ExtraBold.ttf"), 52)
    f_tag = ImageFont.truetype(str(fonts / "Montserrat-Bold.ttf"), 27)
    f_dom = ImageFont.truetype(str(fonts / "Montserrat-Bold.ttf"), 25)

    logo = Image.open(str(LOGO)).convert("RGB")
    s = logo.width; R = int(s * 0.445); c = s // 2
    logo = logo.crop((c - R, c - R, c + R, c + R)).resize((130, 130), Image.LANCZOS)
    lmask = Image.new("L", (130, 130), 0); ImageDraw.Draw(lmask).ellipse((1, 1, 129, 129), fill=255)
    logo_rgba = Image.new("RGBA", (130, 130), (0, 0, 0, 0)); logo_rgba.paste(logo, (0, 0), lmask)

    def rmask(w, h, r):
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
        return m

    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    grad = Image.new("RGB", (CARD_W, CARD_H)); gd = grad.load()
    for y in range(CARD_H):
        for x in range(CARD_W):
            k = (y / CARD_H) * 0.6 + (x / CARD_W) * 0.4
            gd[x, y] = (int(NAVY1[0] + (NAVY2[0] - NAVY1[0]) * k),
                        int(NAVY1[1] + (NAVY2[1] - NAVY1[1]) * k),
                        int(NAVY1[2] + (NAVY2[2] - NAVY1[2]) * k))
    cmask = rmask(CARD_W, CARD_H, RAD); card.paste(grad, (0, 0), cmask)
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle((1, 1, CARD_W - 2, CARD_H - 2), radius=RAD, outline=(*PURPLE, 150), width=2)
    card.paste(logo_rgba, (24, (CARD_H - 130) // 2), logo_rgba)
    tx = 178
    cd.text((tx, 40), NAME, font=f_name, fill=CREAM)
    nf_w = cd.textlength(NAME, font=f_name)
    cd.text((tx + nf_w, 40), ACCENT_WORD, font=f_name, fill=PURPLE_L)
    cd.text((tx + 2, 104), TAGLINE, font=f_tag, fill=GRAY)
    dw = cd.textlength(CTA, font=f_dom); pill_w = int(dw + 44); pill_h = 52
    px1 = CARD_W - pill_w - 28; py1 = (CARD_H - pill_h) // 2
    cd.rounded_rectangle((px1, py1, px1 + pill_w, py1 + pill_h), radius=pill_h // 2, fill=(*PURPLE, 235))
    cd.text((px1 + 22, py1 + (pill_h - 31) // 2), CTA, font=f_dom, fill=(255, 255, 255))

    glow = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        (MARGIN - 6, MARGIN - 6, MARGIN + CARD_W + 6, MARGIN + CARD_H + 6), radius=RAD + 6, fill=(*PURPLE, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(22))

    band = Image.new("RGBA", (260, CARD_H + 120), (0, 0, 0, 0)); bd = band.load()
    for x in range(band.width):
        a = max(0.0, 1 - abs(x - band.width / 2) / (band.width / 2)); av = int(70 * (a ** 2))
        for y in range(band.height):
            bd[x, y] = (255, 255, 255, av)
    band = band.rotate(20, expand=True)
    full_mask = Image.new("L", (FW, FH), 0); full_mask.paste(cmask, (MARGIN, MARGIN))

    for i in range(NFRAMES):
        t = i / NFRAMES
        frame = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
        g = 0.30 + 0.35 * (0.5 + 0.5 * math.sin(2 * math.pi * t))
        gl = glow.copy(); gl.putalpha(gl.split()[3].point(lambda a: int(a * g)))
        frame = Image.alpha_composite(frame, gl)
        frame.alpha_composite(card, (MARGIN, MARGIN))
        sl = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
        sl.alpha_composite(band, (int(-band.width + (FW + band.width) * t), MARGIN - 60))
        clip = Image.composite(sl.split()[3], Image.new("L", (FW, FH), 0), full_mask); sl.putalpha(clip)
        frame = Image.alpha_composite(frame, sl)
        frame.save(dst / f"frame_{i:03d}.png")


def _ensure_loop() -> pathlib.Path | None:
    """Путь к кэшированному альфа-лупу баннера; генерит при отсутствии. None при невозможности."""
    if LOOP_PATH.exists() and LOOP_PATH.stat().st_size > 10000:
        return LOOP_PATH
    if not LOGO.exists():
        core.log("VPN-баннер: нет assets/nightfox_logo.jpg — пропуск", level="warn")
        return None
    try:
        tmp = core.CACHE_DIR / "nf_banner_frames"
        shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True, exist_ok=True)
        _render_frames(tmp)
        LOOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(tmp / "frame_%03d.png"),
                  "-c:v", "qtrle", str(LOOP_PATH)])
        shutil.rmtree(tmp, ignore_errors=True)
        return LOOP_PATH if (LOOP_PATH.exists() and LOOP_PATH.stat().st_size > 10000) else None
    except Exception as e:  # noqa: BLE001
        core.log_error("banner._ensure_loop", e)
        return None


def overlay(video_path, niche_id: str = "") -> bool:
    """Наложить зацикленный баннер на видео IN-PLACE. True при успехе; False → видео НЕ тронуто.
    Ниши из _exclude_niches() (личный бренд) баннер НЕ получают."""
    if not enabled():
        return False
    if niche_id and niche_id in _exclude_niches():
        core.log("VPN-баннер пропущен: ниша исключена", level="debug", niche=niche_id)
        return False
    vp = pathlib.Path(video_path)
    loop = _ensure_loop()
    if not loop:
        return False
    pos = os.environ.get("VPN_BANNER_POS", "top").strip().lower()
    x = (core.W - FW) // 2
    y = (core.H - FH - 24) if pos == "bottom" else 16
    out = vp.with_name(vp.stem + "_bn.mp4")
    try:
        core.run(["ffmpeg", "-y", "-i", str(vp), "-stream_loop", "-1", "-i", str(loop),
                  "-filter_complex", f"[0:v][1:v]overlay={x}:{y}:shortest=1[v]",
                  "-map", "[v]", "-map", "0:a?", "-c:a", "copy", "-c:v", "libx264",
                  "-crf", "19", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)])
        if out.exists() and out.stat().st_size > 10000:
            os.replace(str(out), str(vp))
            core.log("VPN-баннер наложен", video=vp.name, pos=pos)
            return True
    except Exception as e:  # noqa: BLE001
        core.log_error("banner.overlay", e, video=vp.name)
    out.unlink(missing_ok=True)
    return False


if __name__ == "__main__":
    core.load_local_secrets()
    import sys as _sys
    if len(_sys.argv) >= 2:
        print("overlay:", overlay(_sys.argv[1]))
    else:
        print("loop:", _ensure_loop())
