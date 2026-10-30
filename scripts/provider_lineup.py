"""Check the IPTV provider's channel lineup for changes.

The lineup is saved ENCRYPTED (lineup.enc) with the private secret as the key, so it can't be read
from this public repo. Only change counts go into REPORT.md."""
import json, os, subprocess, sys, urllib.request

secret = os.environ.get("PROVIDER_XMLTV_URL", "").strip()
enc_out, note_out = sys.argv[1], sys.argv[2]
if "xmltv.php" not in secret and "player_api.php" not in secret:
    sys.exit(0)
api = secret.replace("xmltv.php", "player_api.php")


def get(action):
    req = urllib.request.Request(f"{api}&action={action}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def crypt(data, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:PROVIDER_XMLTV_URL"]
    if decrypt:
        args.append("-d")
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


try:
    cats = {c["category_id"]: c["category_name"] for c in get("get_live_categories")}
    streams = get("get_live_streams")
except Exception as e:
    print(f"Could not read the channel list: {type(e).__name__}")
    sys.exit(0)

keep = ("num", "name", "stream_id", "stream_icon", "epg_channel_id", "category_id", "tv_archive", "tv_archive_duration", "added")
now = [{**{k: s.get(k) for k in keep}, "category_name": cats.get(s.get("category_id"), "")} for s in streams]
open(enc_out, "wb").write(crypt(json.dumps(now, ensure_ascii=False).encode()))
print(f"Saved encrypted lineup: {len(now)} channels")

prev = None
try:
    repo = os.environ["GITHUB_REPOSITORY"]
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/guide/lineup.enc", timeout=60) as r:
        prev = {c["stream_id"]: c for c in json.loads(crypt(r.read(), decrypt=True))}
except Exception:
    pass

cur = {c["stream_id"]: c for c in now}
lines = ["", "## Provider lineup", f"{len(cur)} channels."]
if prev is not None:
    added = sum(1 for k in cur if k not in prev)
    gone = sum(1 for k in prev if k not in cur)
    renamed = sum(1 for k in cur if k in prev and prev[k]["name"] != cur[k]["name"])
    if added or gone or renamed:
        lines.append(f"**Changed since last run: {added} added, {gone} removed, {renamed} renamed** - ask Claude to rebuild the playlist.")
    else:
        lines.append("No changes since the last run.")
open(note_out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
