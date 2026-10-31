"""Download both services' own programme guides. Works whatever form the login link is in
(guide link, playlist link or app link). Prints only sizes and status codes - never the link."""
import os, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from streamtest import Provider

out_dir = sys.argv[1]
for name, env in (("svc1.xml", "PRO"), ("svc2.xml", "PRO_2")):
    p = Provider(name, os.environ.get(env))
    if not p.ok:
        print(f"{name}: no login set")
        continue
    dest = os.path.join(out_dir, name)
    for attempt in range(3):
        try:
            req = urllib.request.Request(p.xmltv_url(), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=900) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            size = os.path.getsize(dest)
            print(f"{name}: {size} bytes")
            if size > 1000:
                break
        except urllib.error.HTTPError as e:
            print(f"{name}: attempt {attempt + 1} refused (HTTP {e.code})")
        except Exception as e:
            print(f"{name}: attempt {attempt + 1} failed ({type(e).__name__})")
        time.sleep(20)
