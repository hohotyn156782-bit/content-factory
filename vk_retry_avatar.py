#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, io, json, time, ssl, random, urllib.parse, urllib.request

SECRETS = "/home/baronpavel/.config/content-factory/secrets.env"
OUTDIR = "/mnt/d/content-factory-data/vk_branding"
API = "https://api.vk.com/method/"
V = "5.199"
CTX = ssl.create_default_context()

def load_secrets():
    d = {}
    with open(SECRETS) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, _, val = line.partition("=")
            d[k.strip()] = val.strip().strip('"').strip("'")
    return d
SEC = load_secrets()

def vk_get(method, params, token):
    p = dict(params); p["access_token"] = token; p["v"] = V
    url = API + method + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"{method} err {data['error'].get('error_code')}: {data['error'].get('error_msg')}")
    return data["response"]

def mp_upload(upload_url, filepath, field="photo", filename="p.jpg"):
    boundary = "----vkbnd"+str(random.randint(10**9,10**10))
    with open(filepath,"rb") as f: fd = f.read()
    body = io.BytesIO()
    body.write((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\nContent-Type: image/jpeg\r\n\r\n").encode())
    body.write(fd); body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(upload_url, data=body.getvalue(), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
        return json.loads(r.read().decode())

# gid -> user token env
TARGETS = [
    (239724041, "VK_USER_TOKEN"),
    (239724782, "VK_USER_1118117659_TOKEN"),
]

for gid, env in TARGETS:
    tok = SEC.get(env, "")
    path = os.path.join(OUTDIR, f"{gid}_avatar.jpg")
    ok = False
    for attempt in range(4):
        try:
            srv = vk_get("photos.getOwnerPhotoUploadServer", {"owner_id": -gid}, tok)
            up = mp_upload(srv["upload_url"], path)
            photo_empty = (not up.get("photo")) or up.get("photo") in ('[]','{}','')
            print(f"gid{gid} try{attempt} upload keys={list(up.keys())} photo_empty={photo_empty} server={up.get('server')} hash_len={len(str(up.get('hash','')))}")
            res = vk_get("photos.saveOwnerPhoto", {"server": up["server"], "hash": up["hash"], "photo": up["photo"]}, tok)
            print(f"gid{gid} SAVED ok: {json.dumps(res, ensure_ascii=False)[:200]}")
            ok = True
            break
        except Exception as e:
            print(f"gid{gid} try{attempt} FAIL: {e}")
            time.sleep(2.5)
    print(f"=> gid{gid} avatar {'OK' if ok else 'STILL_ERR'}\n")
