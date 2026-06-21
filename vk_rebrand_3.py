#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebrand 3 VK communities: generate + upload covers & avatars."""
import os, sys, io, json, time, ssl, mimetypes, urllib.parse, urllib.request, random, math, traceback
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

OUTDIR = "/mnt/d/content-factory-data/vk_branding"
SECRETS = "/home/baronpavel/.config/content-factory/secrets.env"
os.makedirs(OUTDIR, exist_ok=True)

FONT_BLACK_CANDS = [
    os.path.expanduser("~/.fonts/Montserrat-Black.ttf"),
    "/home/baronpavel/projects/content-factory/assets/fonts/Montserrat-Black.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONT_BOLD_CANDS = [
    os.path.expanduser("~/.fonts/Montserrat-Bold.ttf"),
    "/home/baronpavel/projects/content-factory/assets/fonts/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
def pick(c):
    for p in c:
        if os.path.exists(p):
            return p
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_BLACK = pick(FONT_BLACK_CANDS)
FONT_BOLD = pick(FONT_BOLD_CANDS)

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

GROUPS = [
    dict(gid=239648838, name="Крах империй",      slogan="истории великих крахов",
         prompt="crashing red stock market chart, falling graph, dramatic financial collapse, dark smoke, cinematic, no text",
         accent="E8493A", ctok="VK_MYSTIC_TOKEN"),
    dict(gid=239462120, name="Тёмная психология", slogan="то, что скрывает разум",
         prompt="dark shadowy human silhouette profile, mysterious purple smoke, psychological abstract, moody cinematic, no text, no face details",
         accent="8B5CF6", ctok="VK_ACC1_TOKEN"),
    dict(gid=239390950, name="Сделано в СССР",     slogan="легенды эпохи",
         prompt="vintage soviet retro objects, old radio, red star aesthetic, nostalgic warm dark tones, cinematic, no text",
         accent="D4302F", ctok="VK_ACC2_TOKEN"),
]
FALLBACK_TOKENS = ["VK_USER_TOKEN", "VK_USER_TOKEN2", "VK_USER_TOKEN3"]

def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

# -------- A. background --------
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
    base = Image.new("RGB", (w, h), (8, 10, 9))
    top = tuple(max(0, int(c * 0.38)) for c in accent_rgb)
    grad = Image.new("L", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        grad.putpixel((0, y), int(255 * (1 - t) * 0.6 + 30))
    grad = grad.resize((w, h))
    tint = Image.new("RGB", (w, h), top)
    return Image.composite(tint, base, grad)

def cover_fit(img, w, h):
    sr = img.width / img.height
    dr = w / h
    if sr > dr:
        nh, nw = h, int(h * sr)
    else:
        nw, nh = w, int(w / sr)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)

def fit_font(text, base_path, start_size, max_w, min_size=40):
    size = start_size
    while size > min_size:
        f = load_font(base_path, size)
        w = f.getbbox(text)[2] - f.getbbox(text)[0]
        if w <= max_w:
            return f, size
        size -= 4
    return load_font(base_path, min_size), min_size

def draw_center(draw, cx, y, text, font, fill):
    bb = font.getbbox(text)
    tw = bb[2] - bb[0]
    x = cx - tw // 2 - bb[0]
    yy = y - bb[1]
    draw.text((x + 3, yy + 3), text, font=font, fill=(0, 0, 0, 175))
    draw.text((x, yy), text, font=font, fill=fill)
    return bb[3] - bb[1]

# -------- B. cover --------
def make_cover(bg, cfg, path):
    W, H = 1920, 768
    base = cover_fit(bg, W, H).convert("RGB")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 115))  # ~45%
    img = Image.alpha_composite(base.convert("RGBA"), overlay)
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        t = y / (H - 1)
        grad.putpixel((0, y), int(195 * (t ** 2.1)))
    grad = grad.resize((W, H))
    black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    img = Image.composite(black, img, grad).convert("RGBA")

    draw = ImageDraw.Draw(img)
    accent = hex2rgb(cfg["accent"])
    cx = W // 2
    safe_w = 1180

    name_font, _ = fit_font(cfg["name"], FONT_BLACK, 110, safe_w, 60)
    slo_font, _ = fit_font(cfg["slogan"], FONT_BOLD, 46, safe_w, 30)
    bot_font = load_font(FONT_BOLD, 34)
    bottom_text = "Новое видео каждый день"

    nh = name_font.getbbox(cfg["name"])[3] - name_font.getbbox(cfg["name"])[1]
    sh = slo_font.getbbox(cfg["slogan"])[3] - slo_font.getbbox(cfg["slogan"])[1]
    gap1 = 36
    block_h = nh + gap1 + sh
    y = (H - block_h) // 2 - 30
    draw_center(draw, cx, y, cfg["name"], name_font, (255, 255, 255, 255))
    y += nh + gap1
    draw_center(draw, cx, y, cfg["slogan"], slo_font, accent + (255,))
    draw_center(draw, cx, H - 80, bottom_text, bot_font, (255, 255, 255, 217))

    img.convert("RGB").save(path, "JPEG", quality=88)
    return path

# -------- C. avatar --------
def make_avatar(bg, cfg, path):
    S = 1000
    side = min(bg.width, bg.height)
    left = (bg.width - side) // 2
    top = (bg.height - side) // 2
    sq = bg.crop((left, top, left + side, top + side)).resize((S, S), Image.LANCZOS).convert("RGBA")

    import numpy as np
    cx = cy = S / 2.0
    maxd = math.hypot(cx, cy)
    yy, xx = np.ogrid[0:S, 0:S]
    d = np.hypot(xx - cx, yy - cy) / maxd
    v = np.clip((d - 0.55) / 0.45 * 165.0, 0, 255).astype("uint8")
    grad = Image.fromarray(v, "L")
    black = Image.new("RGBA", (S, S), (0, 0, 0, 255))
    img = Image.composite(black, sq, grad)

    draw = ImageDraw.Draw(img)
    accent = hex2rgb(cfg["accent"])
    bw = 12
    for i in range(bw):
        draw.rectangle([i, i, S - 1 - i, S - 1 - i], outline=accent + (255,))
    img.convert("RGB").save(path, "JPEG", quality=90)
    return path

# -------- VK API --------
def vk_get(method, params, token):
    p = dict(params); p["access_token"] = token; p["v"] = V
    url = API + method + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        data = json.loads(r.read().decode())
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"err {e.get('error_code')}: {e.get('error_msg')}|CODE={e.get('error_code')}")
    return data["response"]

def err_code(exc):
    s = str(exc)
    if "CODE=" in s:
        try:
            return int(s.split("CODE=")[1].strip())
        except Exception:
            return None
    return None

def multipart_upload(upload_url, filepath, field="photo", filename="p.jpg"):
    boundary = "----vkbnd" + str(random.randint(10**9, 10**10))
    with open(filepath, "rb") as f:
        filedata = f.read()
    body = io.BytesIO()
    body.write((f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                f"Content-Type: image/jpeg\r\n\r\n").encode())
    body.write(filedata)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    payload = body.getvalue()
    req = urllib.request.Request(upload_url, data=payload, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
        return json.loads(r.read().decode())

def upload_cover_once(gid, filepath, token):
    srv = vk_get("photos.getOwnerCoverPhotoUploadServer",
                 {"group_id": gid, "crop_x": 0, "crop_y": 0, "crop_x2": 1920, "crop_y2": 768}, token)
    up = multipart_upload(srv["upload_url"], filepath)
    if "hash" not in up or "photo" not in up:
        raise RuntimeError(f"cover upload bad resp: {up}")
    # save with retry on 129
    last = None
    for attempt in range(3):
        try:
            return vk_get("photos.saveOwnerCoverPhoto", {"hash": up["hash"], "photo": up["photo"]}, token)
        except Exception as e:
            last = e
            if err_code(e) == 129 and attempt < 2:
                time.sleep(2); continue
            raise
    raise last

def upload_avatar_once(gid, filepath, token):
    srv = vk_get("photos.getOwnerPhotoUploadServer", {"owner_id": -gid}, token)
    up = multipart_upload(srv["upload_url"], filepath)
    if "photo" not in up:
        raise RuntimeError(f"avatar upload bad resp: {up}")
    last = None
    for attempt in range(3):
        try:
            return vk_get("photos.saveOwnerPhoto",
                          {"server": up["server"], "hash": up["hash"], "photo": up["photo"]}, token)
        except Exception as e:
            last = e
            if err_code(e) == 129 and attempt < 2:
                time.sleep(2); continue
            raise
    raise last

def candidate_tokens(ctok_name):
    out = []
    if ctok_name and SEC.get(ctok_name):
        out.append((ctok_name, SEC[ctok_name]))
    for fb in FALLBACK_TOKENS:
        if SEC.get(fb):
            out.append((fb, SEC[fb]))
    return out

def try_upload(kind, gid, filepath, ctok_name, notes):
    fn = upload_cover_once if kind == "cover" else upload_avatar_once
    for tname, tval in candidate_tokens(ctok_name):
        try:
            fn(gid, filepath, tval)
            return True, tname
        except Exception as e:
            notes.append(f"{kind} {tname} fail: {e}")
    return False, "-"

def main():
    print(f"Fonts: BLACK={FONT_BLACK}  BOLD={FONT_BOLD}")
    print(f"ctok present: " + ", ".join(f"{g['ctok']}={'Y' if SEC.get(g['ctok']) else 'N'}" for g in GROUPS))
    results = []
    for cfg in GROUPS:
        gid = cfg["gid"]
        r = {"gid": gid, "name": cfg["name"], "cover": "ERR", "avatar": "ERR",
             "cover_tok": "-", "avatar_tok": "-", "notes": []}
        try:
            try:
                bg = fetch_pollinations(cfg["prompt"])
                r["bg"] = "flux"
            except Exception as e:
                r["notes"].append(f"pollinations->gradient: {e}")
                bg = gradient_bg(hex2rgb(cfg["accent"]), 1280, 720)
                r["bg"] = "gradient"

            cover_path = os.path.join(OUTDIR, f"{gid}_cover.jpg")
            avatar_path = os.path.join(OUTDIR, f"{gid}_avatar.jpg")
            cp = ap = None
            try:
                make_cover(bg, cfg, cover_path); cp = cover_path
            except Exception as e:
                r["notes"].append(f"cover gen fail: {e}")
            try:
                make_avatar(bg, cfg, avatar_path); ap = avatar_path
            except Exception as e:
                r["notes"].append(f"avatar gen fail: {e}")

            if cp:
                ok, tn = try_upload("cover", gid, cp, cfg["ctok"], r["notes"])
                r["cover"] = "OK" if ok else "ERR"; r["cover_tok"] = tn
            if ap:
                ok, tn = try_upload("avatar", gid, ap, cfg["ctok"], r["notes"])
                r["avatar"] = "OK" if ok else "ERR"; r["avatar_tok"] = tn
        except Exception as e:
            r["notes"].append(f"FATAL: {e}\n{traceback.format_exc()}")
        results.append(r)
        print(f"[done] club{gid} {cfg['name']}: bg={r.get('bg')} cover={r['cover']}({r['cover_tok']}) avatar={r['avatar']}({r['avatar_tok']})")
        for n in r["notes"]:
            print("   note:", n[:280])
        sys.stdout.flush()

    succ = sum(1 for r in results if r["cover"] == "OK" and r["avatar"] == "OK")
    print(f"\nFully successful (both OK): {succ} / 3")
    print("===JSON===")
    print(json.dumps(results, ensure_ascii=False))

if __name__ == "__main__":
    main()
