#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate + upload VK covers & avatars for 9 communities."""
import os, sys, io, json, time, ssl, mimetypes, urllib.parse, urllib.request, random, math, traceback
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

OUTDIR = "/mnt/d/content-factory-data/vk_branding"
SECRETS = "/home/baronpavel/.config/content-factory/secrets.env"
FONT_BLACK = "/home/baronpavel/projects/content-factory/assets/fonts/Montserrat-Black.ttf"
FONT_BOLD  = "/home/baronpavel/projects/content-factory/assets/fonts/Montserrat-Bold.ttf"
FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
API = "https://api.vk.com/method/"
V = "5.199"

SSL_CTX = ssl.create_default_context()

def load_secrets():
    d = {}
    with open(SECRETS) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            d[k.strip()] = val.strip().strip('"').strip("'")
    return d

SEC = load_secrets()

# group config
GROUPS = [
    dict(gid=239724041, niche="psy",    name="Тайны мозга",            slogan="психология за 30 секунд",   accent="3DDC97", grp="VK_GRP_MIND_TOKEN",   usr="VK_USER_TOKEN"),
    dict(gid=239724067, niche="money",  name="Деньги на пальцах",       slogan="финансы понятным языком",   accent="E6B34D", grp="VK_GRP_MONEY_TOKEN",  usr="VK_USER_TOKEN"),
    dict(gid=239724082, niche="ai",     name="AI-фишки каждый день",    slogan="нейросети для жизни",       accent="4DA3FF", grp="VK_GRP_AI_TOKEN",     usr="VK_USER_TOKEN"),
    dict(gid=239724118, niche="mystic", name="Грань реальности",        slogan="мистика, которая цепляет",  accent="B57BFF", grp="VK_GRP_MYSTIC_TOKEN", usr="VK_USER_1091794411_TOKEN"),
    dict(gid=239724176, niche="object", name="Если бы вещи говорили",   slogan="взгляд на привычное иначе", accent="FF8C42", grp="VK_GRP_OBJECTS_TOKEN", usr="VK_USER_1091794411_TOKEN"),
    dict(gid=239724198, niche="hist",   name="Загадки истории",         slogan="история без скуки",         accent="C9A227", grp="VK_GRP_HISTORY_TOKEN", usr="VK_USER_1091794411_TOKEN"),
    dict(gid=239724782, niche="psy",    name="Мозг без цензуры",        slogan="психология начистоту",     accent="2BD4B0", grp="VK_GRP_MIND2_TOKEN",  usr="VK_USER_1118117659_TOKEN"),
    dict(gid=239724814, niche="money",  name="Финансы просто",          slogan="деньги без занудства",      accent="F0C419", grp="VK_GRP_MONEY2_TOKEN", usr="VK_USER_1118117659_TOKEN"),
    dict(gid=239724831, niche="ai",     name="Нейросети каждый день",   slogan="AI-навыки на каждый день",  accent="5BC0EB", grp="VK_GRP_AI2_TOKEN",    usr="VK_USER_1118117659_TOKEN"),
]

PROMPTS = {
    "psy":    "glowing human brain made of light, neural connections, dark cinematic background, particles, ultra detailed, no text",
    "money":  "golden coins, abstract financial growth, dark luxury bokeh, cinematic gold light, no text",
    "ai":     "futuristic AI neural network, glowing circuit lines, dark tech, cyan glow, no text",
    "mystic": "eerie foggy dark forest, supernatural purple glow, cinematic, no text",
    "object": "surreal floating everyday objects, playful still life, warm light, dark bg, no text",
    "hist":   "ancient artifacts and old map, candlelight, dark museum, amber tones, no text",
}

def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def fetch_pollinations(prompt, retries=2):
    enc = urllib.parse.quote(prompt, safe="")
    url = f"https://image.pollinations.ai/prompt/{enc}?width=1280&height=720&nologo=true&model=flux"
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
                data = r.read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if img.width >= 64 and img.height >= 64:
                return img
            last = "tiny image"
        except Exception as e:
            last = repr(e)
            time.sleep(3)
    raise RuntimeError(f"pollinations failed: {last}")

def gradient_bg(accent_rgb, w, h):
    """Dark gradient fallback with accent tint."""
    base = Image.new("RGB", (w, h), (8, 10, 9))
    top = tuple(max(0, int(c * 0.35)) for c in accent_rgb)
    grad = Image.new("L", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        grad.putpixel((0, y), int(255 * (1 - t) * 0.6 + 40))
    grad = grad.resize((w, h))
    tint = Image.new("RGB", (w, h), top)
    out = Image.composite(tint, base, grad)
    # radial vignette toward center
    return out

def cover_fit(img, w, h):
    """Scale + center crop to exactly w x h."""
    src_ratio = img.width / img.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        nh = h
        nw = int(h * src_ratio)
    else:
        nw = w
        nh = int(w / src_ratio)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.truetype(FONT_FALLBACK, size)

def fit_font(text, base_path, start_size, max_w, min_size=40):
    size = start_size
    while size > min_size:
        f = load_font(base_path, size)
        w = f.getbbox(text)[2] - f.getbbox(text)[0]
        if w <= max_w:
            return f, size
        size -= 4
    return load_font(base_path, min_size), min_size

def draw_text_center(draw, cx, y, text, font, fill, shadow=True):
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = cx - tw // 2 - bbox[0]
    yy = y - bbox[1]
    if shadow:
        for dx, dy in [(3, 3)]:
            draw.text((x + dx, yy + dy), text, font=font, fill=(0, 0, 0, 170))
    draw.text((x, yy), text, font=font, fill=fill)
    return th

def make_cover(bg, cfg, path):
    W, H = 1920, 768
    base = cover_fit(bg, W, H).convert("RGB")
    # darken overlay 45% black
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 115))
    img = Image.alpha_composite(base.convert("RGBA"), overlay)
    # bottom gradient strengthen
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        t = y / (H - 1)
        grad.putpixel((0, y), int(180 * (t ** 2.2)))
    grad = grad.resize((W, H))
    black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    img = Image.composite(black, img, grad)
    img = img.convert("RGBA")

    draw = ImageDraw.Draw(img)
    accent = hex2rgb(cfg["accent"])
    cx = W // 2
    safe_w = 1100  # central safe zone width

    # community name big
    name_font, ns = fit_font(cfg["name"], FONT_BLACK, 110, safe_w, 60)
    # slogan
    slo_font, ss = fit_font(cfg["slogan"], FONT_BOLD, 46, safe_w, 30)
    bottom_text = "🎬 Новое видео каждый день"
    bot_font = load_font(FONT_BOLD, 36)

    # vertical layout centered
    nh = name_font.getbbox(cfg["name"])[3] - name_font.getbbox(cfg["name"])[1]
    sh = slo_font.getbbox(cfg["slogan"])[3] - slo_font.getbbox(cfg["slogan"])[1]
    gap1 = 34
    block_h = nh + gap1 + sh
    start_y = (H - block_h) // 2 - 30

    y = start_y
    th = draw_text_center(draw, cx, y, cfg["name"], name_font, (255, 255, 255, 255))
    y += nh + gap1
    draw_text_center(draw, cx, y, cfg["slogan"], slo_font, accent + (255,))

    # bottom line (emoji may not render in Montserrat -> fallback to DejaVu for that)
    try:
        bb = bot_font.getbbox(bottom_text)
        if bb[2] - bb[0] < 50:
            raise ValueError
        draw_text_center(draw, cx, H - 90, bottom_text, bot_font, (255, 255, 255, 178))
    except Exception:
        df = ImageFont.truetype(FONT_FALLBACK, 36)
        draw_text_center(draw, cx, H - 90, bottom_text, df, (255, 255, 255, 178))

    img.convert("RGB").save(path, "JPEG", quality=88)
    return path

def make_avatar(bg, cfg, path):
    S = 1000
    sq = bg
    # center square crop from source then resize
    side = min(bg.width, bg.height)
    left = (bg.width - side) // 2
    top = (bg.height - side) // 2
    sq = bg.crop((left, top, left + side, top + side)).resize((S, S), Image.LANCZOS).convert("RGB")
    img = sq.convert("RGBA")

    # radial vignette (vectorized)
    import numpy as np
    cx = cy = S / 2.0
    maxd = math.hypot(cx, cy)
    yy, xx = np.ogrid[0:S, 0:S]
    d = np.hypot(xx - cx, yy - cy) / maxd
    v = np.clip((d - 0.55) / 0.45 * 160.0, 0, 255).astype("uint8")
    grad = Image.fromarray(v, "L")
    black = Image.new("RGBA", (S, S), (0, 0, 0, 255))
    img = Image.composite(black, img, grad)

    # accent border ~12px
    draw = ImageDraw.Draw(img)
    accent = hex2rgb(cfg["accent"])
    bw = 12
    for i in range(bw):
        draw.rectangle([i, i, S - 1 - i, S - 1 - i], outline=accent + (255,))
    img.convert("RGB").save(path, "JPEG", quality=90)
    return path

# ---------------- VK API ----------------
def vk_get(method, params, token):
    p = dict(params)
    p["access_token"] = token
    p["v"] = V
    url = API + method + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"{method} err {data['error'].get('error_code')}: {data['error'].get('error_msg')}")
    return data["response"]

def multipart_upload(upload_url, filepath, field="photo", filename="p.jpg"):
    boundary = "----vkbnd" + str(random.randint(10**9, 10**10))
    with open(filepath, "rb") as f:
        filedata = f.read()
    body = io.BytesIO()
    pre = (f"--{boundary}\r\n"
           f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
           f"Content-Type: image/jpeg\r\n\r\n").encode()
    body.write(pre)
    body.write(filedata)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    payload = body.getvalue()
    req = urllib.request.Request(upload_url, data=payload, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
        return json.loads(r.read().decode())

def upload_cover(gid, filepath, token):
    srv = vk_get("photos.getOwnerCoverPhotoUploadServer",
                 {"group_id": gid, "crop_x": 0, "crop_y": 0, "crop_x2": 1920, "crop_y2": 768}, token)
    up = multipart_upload(srv["upload_url"], filepath)
    if "hash" not in up or "photo" not in up:
        raise RuntimeError(f"cover upload bad resp: {up}")
    res = vk_get("photos.saveOwnerCoverPhoto", {"hash": up["hash"], "photo": up["photo"]}, token)
    return res

def upload_avatar(gid, filepath, token):
    srv = vk_get("photos.getOwnerPhotoUploadServer", {"owner_id": -gid}, token)
    up = multipart_upload(srv["upload_url"], filepath)
    if "photo" not in up:
        raise RuntimeError(f"avatar upload bad resp: {up}")
    res = vk_get("photos.saveOwnerPhoto",
                 {"server": up["server"], "hash": up["hash"], "photo": up["photo"]}, token)
    return res

def main():
    results = []
    for cfg in GROUPS:
        gid = cfg["gid"]
        r = {"gid": gid, "name": cfg["name"], "cover": "ERR", "avatar": "ERR",
             "token_used": "-", "cover_path": "", "avatar_path": "", "notes": []}
        try:
            # A. background
            bg = None
            used_fallback = False
            try:
                bg = fetch_pollinations(PROMPTS[cfg["niche"]])
            except Exception as e:
                r["notes"].append(f"pollinations fail->gradient: {e}")
                bg = gradient_bg(hex2rgb(cfg["accent"]), 1280, 720)
                used_fallback = True
            r["fallback_bg"] = used_fallback

            # B/C generate
            cover_path = os.path.join(OUTDIR, f"{gid}_cover.jpg")
            avatar_path = os.path.join(OUTDIR, f"{gid}_avatar.jpg")
            try:
                make_cover(bg, cfg, cover_path)
                r["cover_path"] = cover_path
            except Exception as e:
                r["notes"].append(f"cover gen fail: {e}")
            try:
                make_avatar(bg, cfg, avatar_path)
                r["avatar_path"] = avatar_path
            except Exception as e:
                r["notes"].append(f"avatar gen fail: {e}")

            # D. upload — try community token, then user token
            grp_token = SEC.get(cfg["grp"], "")
            usr_token = SEC.get(cfg["usr"], "")

            # cover
            cover_done = False
            for label, tok in (("community", grp_token), ("user", usr_token)):
                if not tok or not r["cover_path"]:
                    continue
                try:
                    upload_cover(gid, cover_path, tok)
                    r["cover"] = "OK"
                    r["token_used"] = label
                    cover_done = True
                    break
                except Exception as e:
                    r["notes"].append(f"cover up {label} fail: {e}")
            # avatar
            for label, tok in (("community", grp_token), ("user", usr_token)):
                if not tok or not r["avatar_path"]:
                    continue
                try:
                    upload_avatar(gid, avatar_path, tok)
                    r["avatar"] = "OK"
                    if r["token_used"] == "-":
                        r["token_used"] = label
                    elif r["token_used"] != label:
                        r["token_used"] = r["token_used"] + "+" + label
                    break
                except Exception as e:
                    r["notes"].append(f"avatar up {label} fail: {e}")

            # E. verify
            try:
                vtok = grp_token or usr_token
                info = vk_get("groups.getById", {"group_id": gid, "fields": "has_photo,cover"}, vtok)
                grp = info[0] if isinstance(info, list) else info.get("groups", [{}])[0]
                cov = grp.get("cover", {})
                r["verify_cover_enabled"] = cov.get("enabled", "?")
                r["verify_has_photo"] = grp.get("has_photo", "?")
            except Exception as e:
                r["notes"].append(f"verify fail: {e}")
        except Exception as e:
            r["notes"].append(f"FATAL: {e}\n{traceback.format_exc()}")
        results.append(r)
        print(f"[done] club{gid} {cfg['name']}: cover={r['cover']} avatar={r['avatar']} token={r['token_used']}")
        for n in r["notes"]:
            print("    note:", n[:300])
        sys.stdout.flush()

    print("\n===JSON===")
    print(json.dumps(results, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
