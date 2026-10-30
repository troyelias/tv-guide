"""Nightly channel check-up, automatic switch-over and playlist update.

Usage: channels.py MODE OUTDIR
  MODE = nightly   test both services, decide switches, rebuild playlist, upload if changed (only 4:00-6:30 AM India time)
         dry-run   same, but never uploads (for testing at any time)
         outage    no testing: main service is down, so point every channel at the backup service and upload
         restore   no testing: undo an outage switch using last night's decisions
         preview   no testing, no upload, nothing saved - just shows what the playlist would contain

Secrets (env): PRO (main service login link), PRO_2 (backup service login link),
               DBX_KEY, DBX_SECRET, DBX_PATH (Dropbox), GITHUB_REPOSITORY.
State is kept ENCRYPTED in secure/state.enc (key: DBX_SECRET). Nothing written to OUTDIR contains logins.
"""
import base64, hashlib, json, os, subprocess, sys, threading, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from catalog_lib import match_lineup  # noqa: E402
from streamtest import Provider, plays  # noqa: E402

MODE, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
NOW = datetime.now(timezone.utc)
IST = NOW + timedelta(hours=5, minutes=30)
STATE_FILE = "secure/state.enc"
FOLDERS = ["Hindi Entertainment", "Hindi Movies", "Hindi Music", "Hindi News", "Business News", "English News",
           "English Movies", "English Entertainment", "Infotainment", "Lifestyle", "Kids", "Sports", "Devotional",
           "Kannada", "Regional & DD", "India UK Feeds", "Pakistan UK Feeds", "Pakistan"]
BACKUP_FOLDER = "Backup"
PAUSE = float(os.environ.get("PAUSE", "2"))
summary = {"mode": MODE, "ran_utc": NOW.strftime("%Y-%m-%d %H:%M")}


def crypt(data, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:DBX_SECRET"]
    if decrypt:
        args.append("-d")
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


def finish(code=0):
    json.dump(summary, open(os.path.join(OUT, "channels.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in summary.items() if not isinstance(v, (list, dict))}))
    sys.exit(code)


in_window = (IST.hour >= 4 and (IST.hour, IST.minute) < (6, 30))
if MODE == "nightly" and not in_window:
    summary["note"] = "outside the 4:00-6:30 AM India time window - nothing tested or changed"
    finish()

catalog = json.load(open("catalog.json", encoding="utf-8"))
state = {"channels": {}, "uploaded_hash": None, "outage": False}
if os.path.exists(STATE_FILE) and os.environ.get("DBX_SECRET"):
    try:
        state.update(json.loads(crypt(open(STATE_FILE, "rb").read(), decrypt=True)))
    except Exception:
        summary["state_note"] = "previous state unreadable - starting fresh"

provs = {"main": Provider("main", os.environ.get("PRO")), "backup": Provider("backup", os.environ.get("PRO_2"))}
info, lineup, cands, epg = {}, {}, {}, {}
for p, prov in provs.items():
    if not prov.ok:
        info[p] = {"error": "not set"}
        continue
    try:
        u = prov.api(timeout=60).get("user_info", {})
        info[p] = {k: u.get(k) for k in ("auth", "status", "exp_date", "active_cons", "max_connections")}
    except Exception as e:
        info[p] = {"error": type(e).__name__}
        continue
    if MODE in ("outage", "restore") and p == "main" and MODE == "outage":
        continue  # main is down; don't bother listing it
    try:
        cats, streams = prov.api("get_live_categories"), prov.api("get_live_streams")
    except Exception as e:
        info[p]["error"] = "lineup: " + type(e).__name__
        continue
    lineup[p] = {s["stream_id"]: s for s in streams}
    cands[p] = match_lineup(catalog, streams, cats)
    epg[p] = {}
    for cid, sids in cands[p].items():
        for sid in sids:
            tag = (lineup[p][sid].get("epg_channel_id") or "").strip()
            if tag:
                epg[p].setdefault(cid, tag)
                break
summary["login"] = {p: ("ok" if v.get("auth") == 1 and str(v.get("status", "")).lower() == "active" else "problem")
                    for p, v in info.items()}
summary["matched"] = {p: len(v) for p, v in cands.items()}
summary["main_days_left"] = (round((int(info["main"]["exp_date"]) - NOW.timestamp()) / 86400, 1)
                             if info.get("main", {}).get("exp_date") else None)
summary["backup_days_left"] = (round((int(info["backup"]["exp_date"]) - NOW.timestamp()) / 86400, 1)
                               if info.get("backup", {}).get("exp_date") else None)

# ---------- test-play (nightly / dry-run) ----------
results = {"main": {}, "backup": {}}  # cid -> working stream id, or None if all tried candidates failed
skipped = []
if MODE in ("nightly", "dry-run"):
    def run(p):
        prov = provs[p]
        if str(info.get(p, {}).get("active_cons", "0")) not in ("0", "", "None"):
            skipped.append(p)  # someone is watching on this service - leave it alone tonight
            return
        todo = list(cands.get(p, {}).items())

        def someone_watching():
            for _ in range(2):  # our own test connection can linger for a moment, so look twice
                time.sleep(30)
                try:
                    if str(prov.api(timeout=60).get("user_info", {}).get("active_cons", "0")) in ("0", ""):
                        return False
                except Exception:
                    return False
            return True

        for i, (cid, sids) in enumerate(todo):
            if i and i % 50 == 0 and someone_watching():
                skipped.append(p + " (stopped part-way: someone started watching)")
                return
            results[p][cid] = None
            for sid in sids[:2]:
                if plays(prov, sid):
                    results[p][cid] = sid
                    break
                time.sleep(PAUSE)
            time.sleep(PAUSE)
        time.sleep(30)
        for cid, sids in todo:  # second chance for anything that failed
            if results[p][cid] is None:
                for sid in sids[:3]:
                    if plays(prov, sid):
                        results[p][cid] = sid
                        break
                    time.sleep(PAUSE)
    threads = [threading.Thread(target=run, args=(p,)) for p in cands]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    summary["skipped_someone_watching"] = skipped
    summary["tested"] = {p: len(r) for p, r in results.items()}
    summary["working"] = {p: sum(1 for v in r.values() if v) for p, r in results.items()}

# ---------- decide ----------
chs = state["channels"]
on_backup, down, gone = [], [], []
for ch in catalog:
    cid = ch["id"]
    st = chs.setdefault(cid, {"use": "main", "main_streak": 0})
    has = {p: bool(cands.get(p, {}).get(cid)) for p in ("main", "backup")}
    if MODE in ("nightly", "dry-run"):
        for p in ("main", "backup"):
            if p in skipped or cid not in results[p]:
                continue
            st[p + "_ok"] = results[p][cid] is not None
            if results[p][cid]:
                st[p + "_sid"] = results[p][cid]
        if "main" not in skipped and cid in results["main"]:
            st["main_streak"] = st.get("main_streak", 0) + 1 if st.get("main_ok") else 0
        m_ok, b_ok = st.get("main_ok"), st.get("backup_ok")
        if not has["main"] and has["backup"]:
            st["use"] = "backup"
        elif has["main"] and not has["backup"]:
            st["use"] = "main"
        elif st["use"] == "main" and m_ok is False and b_ok:
            st["use"] = "backup"
        elif st["use"] == "backup" and ((m_ok and st["main_streak"] >= 2) or (b_ok is False and m_ok)):
            st["use"] = "main"
        if (has["main"] and m_ok is False or not has["main"]) and (has["backup"] and b_ok is False or not has["backup"]) \
                and (has["main"] or has["backup"]):
            down.append(ch["name"])
    if not has["main"] and not has["backup"]:
        gone.append(ch["name"])
    if st.get("use") == "backup" and has["backup"] and has["main"]:
        on_backup.append(ch["name"])  # switched away from the main service

outage = MODE == "outage" or (state.get("outage") and MODE not in ("nightly", "dry-run", "restore"))
if MODE in ("nightly", "dry-run", "restore"):
    state["outage"] = False
if MODE == "outage":
    state["outage"] = True


def pick(cid, prefer):
    """Return (provider, stream id) to use for a channel, falling back to the other service."""
    st = chs.get(cid, {})
    order = [prefer, "backup" if prefer == "main" else "main"]
    for p in order:
        if not cands.get(p, {}).get(cid):
            continue
        if outage and p == "main":
            continue
        sid = st.get(p + "_sid") if st.get(p + "_sid") in lineup.get(p, {}) else cands[p][cid][0]
        return p, sid
    return None, None


# ---------- build playlist ----------
repo = os.environ.get("GITHUB_REPOSITORY", "")
guide = f"https://raw.githubusercontent.com/{repo}/guide/guide.xml.gz"
logo_base = f"https://raw.githubusercontent.com/{repo}/main/logos/"
mirrored = set(os.listdir("logos")) if os.path.isdir("logos") else set()


def logo(ch):
    hit = next((f for f in mirrored if f.rsplit(".", 1)[0] == ch["id"]), None)
    return logo_base + hit if hit else ch.get("logo", "")


def entry(ch, folder, p, sid):
    s = lineup[p][sid]
    attrs = (f'tvg-id="{ch["guide_id"]}" tvg-name="{ch["name"]}" tvg-logo="{logo(ch)}" group-title="{folder}"')
    if str(s.get("tv_archive")) == "1":
        attrs += f' catchup="xc" catchup-days="{s.get("tv_archive_duration") or 1}"'
    return f'#EXTINF:-1 {attrs},{ch["name"]}\n{provs[p].stream_url(sid)}'


lines, counts = [f'#EXTM3U url-tvg="{guide}" x-tvg-url="{guide}"'], {"main": 0, "backup": 0}
order = {f: i for i, f in enumerate(FOLDERS)}
for ch in sorted(catalog, key=lambda c: order.get(c["folder"], 99)):
    p, sid = pick(ch["id"], chs.get(ch["id"], {}).get("use", "main"))
    if p:
        lines.append(entry(ch, ch["folder"], p, sid))
        counts[p] += 1
spare = 0
for ch in catalog:
    if ch.get("must") and cands.get("backup", {}).get(ch["id"]):
        sid = chs.get(ch["id"], {}).get("backup_sid")
        sid = sid if sid in lineup["backup"] else cands["backup"][ch["id"]][0]
        lines.append(entry(ch, BACKUP_FOLDER, "backup", sid))
        spare += 1
text = "\n".join(lines) + "\n"
digest = hashlib.sha256(text.encode()).hexdigest()
summary.update(channels=counts["main"] + counts["backup"], from_main=counts["main"], from_backup=counts["backup"],
               spare_folder=spare, on_backup=sorted(on_backup), down_on_both=sorted(down), not_on_either=sorted(gone),
               outage=bool(state.get("outage")))

# provider guide id -> catalog guide id (lets the guide use either service's own listings)
json.dump({p: {tag: next(c["guide_id"] for c in catalog if c["id"] == cid) for cid, tag in m.items()} for p, m in epg.items()},
          open(os.path.join(OUT, "provmap.json"), "w"))

# ---------- upload ----------
changed = digest != state.get("uploaded_hash")
summary["playlist_changed"] = changed
may_upload = MODE in ("outage", "restore") or (MODE == "nightly" and in_window)
if changed and may_upload and lineup:
    try:
        key, sec, path = (os.environ.get(k, "").strip() for k in ("DBX_KEY", "DBX_SECRET", "DBX_PATH"))
        refresh = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:DBX_SECRET"],
                                 input=open("secure/dropbox.enc", "rb").read(), capture_output=True, check=True).stdout.decode()
        req = urllib.request.Request("https://api.dropboxapi.com/oauth2/token",
                                     data=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode())
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{key}:{sec}".encode()).decode())
        token = json.load(urllib.request.urlopen(req, timeout=60))["access_token"]
        up = urllib.request.Request("https://content.dropboxapi.com/2/files/upload", data=text.encode("utf-8"))
        up.add_header("Authorization", f"Bearer {token}")
        up.add_header("Content-Type", "application/octet-stream")
        up.add_header("Dropbox-API-Arg", json.dumps({"path": path, "mode": "overwrite", "mute": True}))
        urllib.request.urlopen(up, timeout=120).read()
        state["uploaded_hash"] = digest
        summary["uploaded"] = True
    except Exception as e:
        summary["upload_error"] = type(e).__name__
if MODE in ("dry-run", "preview") and os.environ.get("DRYRUN_OUT"):
    open(os.environ["DRYRUN_OUT"], "w", encoding="utf-8").write(text)  # local testing only

if MODE not in ("dry-run", "preview") and os.environ.get("DBX_SECRET"):
    state["saved_utc"] = NOW.strftime("%Y-%m-%d %H:%M")
    open(STATE_FILE, "wb").write(crypt(json.dumps(state).encode()))
finish()
