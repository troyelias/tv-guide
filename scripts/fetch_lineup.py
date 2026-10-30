"""Fetch a provider's full channel list + group names + account status, saved ENCRYPTED.

Usage: fetch_lineup.py OUT.enc
Env: SRC_URL (login link of the provider to read), KEY (passphrase used for encryption).
"""
import json, os, subprocess, sys, urllib.parse, urllib.request

src = os.environ.get("SRC_URL", "").strip()
u = urllib.parse.urlparse(src)
q = urllib.parse.parse_qs(u.query)
user, pw = q.get("username", [""])[0].strip(), q.get("password", [""])[0].strip()
if not (u.netloc and user and pw):
    sys.exit("::error::The login secret isn't in the expected format.")
api = f"{u.scheme}://{u.netloc}/player_api.php?username={user}&password={pw}"


def get(action=None):
    req = urllib.request.Request(api + (f"&action={action}" if action else ""), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


try:
    info = get().get("user_info", {})
    cats = get("get_live_categories")
    streams = get("get_live_streams")
except Exception as e:
    sys.exit(f"::error::Couldn't read the channel list ({type(e).__name__}).")
keep = ("num", "name", "stream_id", "stream_icon", "epg_channel_id", "category_id", "tv_archive", "tv_archive_duration")
data = {"user_info": {k: info.get(k) for k in ("auth", "status", "exp_date", "is_trial", "active_cons", "max_connections")},
        "categories": cats, "streams": [{k: s.get(k) for k in keep} for s in streams]}
enc = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:KEY"],
                     input=json.dumps(data, ensure_ascii=False).encode(), capture_output=True, check=True).stdout
open(sys.argv[1], "wb").write(enc)
print(f"Saved encrypted list: {len(streams)} channels in {len(cats)} groups")
