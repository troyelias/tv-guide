"""Check the TV subscription: account status and channel lineup.

Usage: account.py OUTDIR
Reads the private secret PROVIDER_XMLTV_URL. Writes to OUTDIR:
  status.enc, lineup.enc   - ENCRYPTED copies (key = the secret), safe to publish
  status.json, lineup.json - plain copies, only used inside this run, never published
  changes.json             - counts of lineup changes since the previous run
Nothing that identifies the account or service is printed or published.
"""
import json, os, subprocess, sys, urllib.request
from datetime import datetime, timezone

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
secret = os.environ.get("PROVIDER_XMLTV_URL", "").strip()
if "xmltv.php" not in secret and "player_api.php" not in secret:
    print("No subscription secret set - skipping.")
    sys.exit(0)
api = secret.replace("xmltv.php", "player_api.php")
now = datetime.now(timezone.utc)


def get(action=None):
    url = api + (f"&action={action}" if action else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def crypt(data, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:PROVIDER_XMLTV_URL"]
    if decrypt:
        args.append("-d")
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


def save(name, obj):
    raw = json.dumps(obj, ensure_ascii=False).encode()
    open(os.path.join(out, name + ".json"), "wb").write(raw)
    open(os.path.join(out, name + ".enc"), "wb").write(crypt(raw))


# ---- account status ----
status = {"checked_utc": now.strftime("%Y-%m-%d %H:%M")}
try:
    u = get().get("user_info", {})
    status.update({k: u.get(k) for k in ("auth", "status", "exp_date", "is_trial", "active_cons", "max_connections")})
    if u.get("exp_date"):
        status["days_left"] = round((int(u["exp_date"]) - now.timestamp()) / 86400, 1)
except Exception as e:
    status["error"] = type(e).__name__
save("status", status)
print("Account check:", "ok" if status.get("auth") == 1 else "problem")

# ---- lineup ----
try:
    cats = {c["category_id"]: c["category_name"] for c in get("get_live_categories")}
    streams = get("get_live_streams")
except Exception as e:
    print(f"Could not read the channel list: {type(e).__name__}")
    sys.exit(0)
keep = ("num", "name", "stream_id", "stream_icon", "epg_channel_id", "category_id", "tv_archive", "tv_archive_duration", "added")
lineup = [{**{k: s.get(k) for k in keep}, "category_name": cats.get(s.get("category_id"), "")} for s in streams]
save("lineup", lineup)
print(f"Lineup saved (encrypted): {len(lineup)} channels")

prev = None
try:
    repo = os.environ["GITHUB_REPOSITORY"]
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/guide/lineup.enc", timeout=60) as r:
        prev = {c["stream_id"]: c for c in json.loads(crypt(r.read(), decrypt=True))}
except Exception:
    pass
cur = {c["stream_id"]: c for c in lineup}
changes = {"channels": len(cur), "compared": prev is not None}
if prev is not None:
    changes.update(added=sum(1 for k in cur if k not in prev), removed=sum(1 for k in prev if k not in cur),
                   renamed=sum(1 for k in cur if k in prev and prev[k]["name"] != cur[k]["name"]))
json.dump(changes, open(os.path.join(out, "changes.json"), "w"))
