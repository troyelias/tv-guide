"""Keep a copy of every channel logo in this repo (logos/<channel id>.<ext>), so logos never depend on
any outside site staying online. Only downloads logos that aren't copied yet."""
import json, os, urllib.request

os.makedirs("logos", exist_ok=True)
have = {f.rsplit(".", 1)[0] for f in os.listdir("logos")}
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/svg+xml": "svg", "image/gif": "gif"}
n = 0
for ch in json.load(open("catalog.json", encoding="utf-8")):
    url = ch.get("logo")
    if not url or ch["id"] in have:
        continue
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get_content_type()
            data = r.read(400_000)
        if ctype in EXT and len(data) > 200:
            open(f"logos/{ch['id']}.{EXT[ctype]}", "wb").write(data)
            n += 1
    except Exception:
        pass
print(f"Copied {n} new logos")
