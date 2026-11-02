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
from dropbox_upload import upload  # noqa: E402
import difflib  # noqa: E402
from catalog_lib import key as name_key, region as name_region, clean_name  # noqa: E402

MODE, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
NOW = (datetime.fromisoformat(os.environ["TEST_NOW"]).replace(tzinfo=timezone.utc) if os.environ.get("TEST_NOW")
       else datetime.now(timezone.utc))  # TEST_NOW is only used by the self-test
IST = NOW + timedelta(hours=5, minutes=30)
STATE_FILE = "secure/state.enc"
FOLDERS = ["Hindi Entertainment", "Hindi Movies", "Hindi Music", "Hindi News", "Business News", "English News",
           "English Movies", "English Entertainment", "Infotainment", "Lifestyle", "Kids", "Sports", "Devotional",
           "Kannada", "Regional & DD", "India UK Feeds", "Pakistan UK Feeds", "Pakistan"]
BACKUP_FOLDER = "Backup"
DEAD_FOLDER = "Not working"   # channels that have played on neither service for DEAD_AFTER check-ups in a row
DEAD_AFTER = 7
SUGGEST_AFTER = 3             # a must-have down this many check-ups in a row gets replacement suggestions
RETRY_IST = (5, 45)           # second try for failed channels, later in the quiet window
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
    open(os.path.join(OUT, "skip"), "w").write("1")  # a late start: leave everything as it is
    finish()

catalog = json.load(open("catalog.json", encoding="utf-8"))
state = {"channels": {}, "uploaded_hash": None, "outage": False}
if os.path.exists(STATE_FILE) and os.environ.get("DBX_SECRET"):
    try:
        state.update(json.loads(crypt(open(STATE_FILE, "rb").read(), decrypt=True)))
    except Exception:
        summary["state_note"] = "previous state unreadable - starting fresh"

if MODE == "nightly" and os.environ.get("BACKUP_SLOT") == "1" and state.get("last_checkup_utc"):
    since = (NOW - datetime.strptime(state["last_checkup_utc"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)).total_seconds()
    if since < 5 * 3600:
        summary["note"] = "tonight's check-up already ran - nothing to do (tick 'force' to run again)"
        open(os.path.join(OUT, "skip"), "w").write("1")
        finish()

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
# ---------- "couldn't match" report ----------
names = {c["id"]: c["name"] for c in catalog}
by_id = {c["id"]: c for c in catalog}
prev_matched = state.get("matched_ids", {})
missing, newly, review = {}, {}, {"unmatched": {}, "suggestions": {}}
for p in cands:
    miss = [cid for cid in names if cid not in cands[p]]
    missing[p] = len(miss)
    if p in prev_matched:
        newly[p] = [names[c] for c in prev_matched[p] if c in names and c not in cands[p]]
    state.setdefault("matched_ids", {})[p] = sorted(cands[p])
    review["unmatched"][p] = [names[c] for c in miss]
    pool = {}
    for s_ in lineup[p].values():
        pool.setdefault(name_key(s_.get("name") or ""), clean_name(s_.get("name") or ""))
    sug = {}
    for cid in miss:  # closest names the service does have, to help add a matching rule
        close = difflib.get_close_matches(by_id[cid]["keys"][0] if by_id[cid]["keys"] else "", list(pool), n=3, cutoff=0.75)
        if close:
            sug[names[cid]] = [pool[k] for k in close]
    review["suggestions"][p] = sug
summary["missing_on"] = missing
summary["newly_missing"] = newly

summary["login"] = {p: ("ok" if v.get("auth") == 1 and str(v.get("status", "")).lower() == "active" else "problem")
                    for p, v in info.items()}
summary["matched"] = {p: len(v) for p, v in cands.items()}
summary["main_days_left"] = (round((int(info["main"]["exp_date"]) - NOW.timestamp()) / 86400, 1)
                             if info.get("main", {}).get("exp_date") else None)
summary["backup_days_left"] = (round((int(info["backup"]["exp_date"]) - NOW.timestamp()) / 86400, 1)
                               if info.get("backup", {}).get("exp_date") else None)

if MODE == "nightly":
    last = state.get("last_checkup_utc")
    if last:
        gap = (NOW - datetime.strptime(last, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if gap > 30:
            summary["missed_hours"] = round(gap)
    state["last_checkup_utc"] = summary["checkup_utc"] = NOW.strftime("%Y-%m-%d %H:%M")
elif state.get("last_checkup_utc"):
    summary["checkup_utc"] = state["last_checkup_utc"]

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

    # ---- second try later in the morning: channels often drop out for a few minutes ----
    failed = {p: [cid for cid, v in results[p].items() if v is None] for p in results}
    if MODE == "nightly" and any(failed.values()):
        real_now = datetime.now(timezone.utc) if not os.environ.get("TEST_NOW") else NOW
        now_ist = real_now + timedelta(hours=5, minutes=30)
        target = now_ist.replace(hour=RETRY_IST[0], minute=RETRY_IST[1], second=0, microsecond=0)
        target = max(target, now_ist + timedelta(minutes=10))
        if target > now_ist.replace(hour=6, minute=15, second=0, microsecond=0):
            target = now_ist + timedelta(minutes=5)  # a late start: keep it short so it finishes in the window
        time.sleep(max(0, (target - now_ist).total_seconds()))
        recovered = []

        def retry(p):
            prov = provs[p]
            try:
                if str(prov.api(timeout=60).get("user_info", {}).get("active_cons", "0")) not in ("0", "", "None"):
                    skipped.append(p + " (second try skipped: someone was watching)")
                    return
            except Exception:
                return
            for cid in failed[p]:
                for sid in cands[p][cid][:3]:
                    if plays(prov, sid):
                        results[p][cid] = sid
                        recovered.append(cid)
                        break
                    time.sleep(PAUSE)
        threads = [threading.Thread(target=retry, args=(p,)) for p in failed if failed[p] and p not in skipped]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        summary["second_try"] = {"at_ist": target.strftime("%H:%M"), "tried": sum(len(v) for v in failed.values()),
                                 "recovered": len(recovered)}
    summary["skipped_someone_watching"] = skipped
    summary["tested"] = {p: len(r) for p, r in results.items()}
    summary["working"] = {p: sum(1 for v in r.values() if v) for p, r in results.items()}

# ---------- decide ----------
def need_streak(st):
    """Good mornings in a row on the main service before switching back. A channel that failed there more than
    once in the last 7 mornings is flaky, so it has to prove itself for longer."""
    return 2 if st.get("main_hist", []).count(0) <= 1 else 4


chs = state["channels"]
on_backup, down, gone, down_ids = [], [], [], []
for ch in catalog:
    cid = ch["id"]
    st = chs.setdefault(cid, {"use": "main", "main_streak": 0})
    has = {p: bool(cands.get(p, {}).get(cid)) for p in ("main", "backup")}
    if MODE in ("nightly", "dry-run"):
        for p in ("main", "backup"):
            if p in skipped or cid not in results[p]:
                continue
            st[p + "_ok"] = results[p][cid] is not None
            st[p + "_hist"] = (st.get(p + "_hist", []) + [1 if results[p][cid] else 0])[-7:]  # last 7 mornings
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
        elif st["use"] == "backup" and ((m_ok and st["main_streak"] >= need_streak(st)) or (b_ok is False and m_ok)):
            st["use"] = "main"
        if (has["main"] and m_ok is False or not has["main"]) and (has["backup"] and b_ok is False or not has["backup"]) \
                and (has["main"] or has["backup"]):
            down.append(ch["name"])
            down_ids.append(cid)
            st["down_days"] = st.get("down_days", 0) + 1
        else:
            st["down_days"] = 0
    if not has["main"] and not has["backup"]:
        gone.append(ch["name"])
    if st.get("use") == "backup" and has["backup"] and has["main"]:
        on_backup.append(ch["name"])  # switched away from the main service

if MODE in ("nightly", "dry-run"):
    state["down_ids"] = down_ids  # remembered so outage/restore uploads keep the same marks

if MODE == "nightly":
    hist = state.setdefault("history", [])
    prev = hist[-7:]
    today = {"date": IST.strftime("%Y-%m-%d"), "start_ist": IST.strftime("%H:%M"),
             "tested": summary.get("tested", {}), "working": summary.get("working", {}),
             "down": sorted(down), "switched": sorted(set(on_backup) - set(down)),
             "skipped": [x.split()[0] for x in skipped]}
    # a sudden drop in working channels on one service (not a full outage - that's handled hourly)
    drops = {}
    for p in ("main", "backup"):
        now_w = today["working"].get(p)
        past = [h["working"][p] for h in prev if p in h.get("working", {}) and p not in h.get("skipped", [])]
        if p in today["skipped"] or now_w is None or not past:
            continue
        avg, last = sum(past) / len(past), past[-1]
        limit = max(15, 0.05 * avg)
        if last - now_w >= limit or avg - now_w >= limit:
            drops[p] = {"now": now_w, "before": last, "week_avg": round(avg)}
    summary["drops"] = drops
    if (IST.hour, IST.minute) > (4, 30):
        summary["late_start_ist"] = IST.strftime("%H:%M")
    hist.append(today)
    state["history"] = hist[-35:]

    # ---- weekly report card (sent on Monday mornings, shown on the status page every day) ----
    week = state["history"][-7:]
    def pct(p, h):
        t = h.get("tested", {}).get(p)
        return 100 * h["working"][p] / t if t and p in h.get("working", {}) and p not in h.get("skipped", []) else None
    rel = {}
    for p in ("main", "backup"):
        vals = [v for v in (pct(p, h) for h in week) if v is not None]
        if vals:
            rel[p] = {"avg_pct": round(sum(vals) / len(vals), 1), "worst_pct": round(min(vals), 1),
                      "mornings": len(vals)}
    flaky = []
    for c in catalog:
        stc = chs.get(c["id"], {})
        h = stc.get("main_hist" if stc.get("use", "main") == "main" or not stc.get("backup_hist") else "backup_hist", [])
        fails = h.count(0)
        if 2 <= fails < len(h):  # sometimes works, sometimes doesn't
            flaky.append((-fails, c["name"], f"{fails} of {len(h)}"))
    first_down = set(week[0]["down"]) if week else set()
    always_down = set.intersection(*[set(h["down"]) for h in week]) if week else set()
    summary["week"] = {
        "from": week[0]["date"] if week else None, "to": today["date"], "mornings": len(week),
        "late_starts": [h["date"] + " " + h["start_ist"] for h in week if h.get("start_ist", "00:00") > "04:30"],
        "reliability": rel,
        "flaky": [f"{n} (failed {x} mornings)" for _, n, x in sorted(flaky)[:8]],
        "newly_down": sorted(set(down) - first_down),
        "back_again": sorted(first_down - set(down)),
        "down_all_week": sorted(always_down) if len(week) >= 7 else [],
        "switched_now": today["switched"],
    }
    summary["weekly_due"] = IST.weekday() == 0  # Monday, India time
else:
    down_ids = [i for i in state.get("down_ids", []) if any(c["id"] == i for c in catalog)]
    down = [c["name"] for c in catalog if c["id"] in down_ids]
DOWN = set(down_ids)
MARK = " \u26a0"  # warning sign shown after the name of a channel that plays on neither service

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
    title = ch["name"] + (MARK if ch["id"] in DOWN else "")  # tvg-name stays unchanged so the guide still matches
    return f'#EXTINF:-1 {attrs},{title}\n{provs[p].stream_url(sid)}'


lines, counts = [f'#EXTM3U url-tvg="{guide}" x-tvg-url="{guide}"'], {"main": 0, "backup": 0}
order = {f: i for i, f in enumerate(FOLDERS)}
long_dead = [c for c in catalog if c["id"] in DOWN and chs.get(c["id"], {}).get("down_days", 0) >= DEAD_AFTER]
LONG_DEAD = {c["id"] for c in long_dead}
for ch in sorted(catalog, key=lambda c: order.get(c["folder"], 99)):
    if ch["id"] in LONG_DEAD:
        continue  # moved to the "Not working" folder at the very bottom
    p, sid = pick(ch["id"], chs.get(ch["id"], {}).get("use", "main"))
    if p:
        lines.append(entry(ch, ch["folder"], p, sid))
        counts[p] += 1
spare = 0
for ch in catalog:  # Backup folder: each favourite again, from whichever service its main copy ISN'T using
    if not ch.get("must"):
        continue
    st = chs.get(ch["id"], {})
    used, _ = pick(ch["id"], st.get("use", "main"))
    other = "main" if used == "backup" else "backup"
    if not cands.get(other, {}).get(ch["id"]) or st.get(other + "_ok") is False or (outage and other == "main"):
        continue  # the other service doesn't have it, or it didn't play there at the last check-up
    sid = st.get(other + "_sid")
    sid = sid if sid in lineup.get(other, {}) else cands[other][ch["id"]][0]
    lines.append(entry(ch, BACKUP_FOLDER, other, sid))
    spare += 1
not_working = []
for ch in sorted(long_dead, key=lambda c: order.get(c["folder"], 99)):
    p, sid = pick(ch["id"], chs.get(ch["id"], {}).get("use", "main"))
    if p:
        lines.append(entry(ch, DEAD_FOLDER, p, sid))
        counts[p] += 1
        not_working.append(ch["name"])

# replacement ideas for must-haves that have been down for a while: same folder, playing now, sturdiest first
replacements = {}
for ch in catalog:
    if ch.get("must") and ch["id"] in DOWN and chs.get(ch["id"], {}).get("down_days", 0) >= SUGGEST_AFTER:
        alts = []
        for i, c in enumerate(catalog):
            if c["folder"] != ch["folder"] or c.get("must") or c["id"] in DOWN:
                continue
            s_ = chs.get(c["id"], {})
            both = bool(s_.get("main_ok")) + bool(s_.get("backup_ok"))
            if both:
                alts.append((-both, i, c["name"]))
        if alts:
            replacements[ch["name"]] = [n for _, _, n in sorted(alts)[:3]]
text = "\n".join(lines) + "\n"
digest = hashlib.sha256(text.encode()).hexdigest()
summary.update(channels=counts["main"] + counts["backup"], from_main=counts["main"], from_backup=counts["backup"],
               spare_folder=spare, on_backup=sorted(set(on_backup) - set(down)), down_on_both=sorted(down), not_on_either=sorted(gone),
               outage=bool(state.get("outage")), not_working_folder=not_working, replacements=replacements,
               down_days={c["name"]: chs[c["id"]].get("down_days", 0) for c in catalog if c["id"] in DOWN})
# guide IDs whose every channel is down on both services -> the guide shows a note for them
gid_all = {}
for c in catalog:
    gid_all.setdefault(c["guide_id"], []).append(c["id"])
summary["down_guide_ids"] = sorted(g for g, ids in gid_all.items() if g and all(i in DOWN for i in ids))

# provider guide id -> catalog guide id (lets the guide use either service's own listings)
json.dump({p: {tag: next(c["guide_id"] for c in catalog if c["id"] == cid) for cid, tag in m.items()} for p, m in epg.items()},
          open(os.path.join(OUT, "provmap.json"), "w"))

# ---------- upload ----------
changed = digest != state.get("uploaded_hash")
summary["playlist_changed"] = changed
may_upload = MODE in ("outage", "restore") or (MODE == "nightly" and in_window)
summary["hold"] = bool(state.get("hold"))
if state.get("hold") and MODE != "outage":
    may_upload = False  # updates paused after an undo, until resumed
if changed and may_upload and lineup:
    try:
        upload(text)
        state["uploaded_hash"] = digest
        summary["uploaded"] = True
        # keep this and the previous playlist (encrypted) so an undo is one tap away
        if os.path.exists("secure/playlist_cur.enc"):
            os.replace("secure/playlist_cur.enc", "secure/playlist_prev.enc")
        open("secure/playlist_cur.enc", "wb").write(crypt(text.encode()))
    except Exception as e:
        summary["upload_error"] = type(e).__name__

# private review for Claude (encrypted with the main login): what couldn't be matched + close names
if os.environ.get("PRO"):
    review["summary"] = {k: v for k, v in summary.items()}
    review["unmatched_must"] = {p: [n for n in v if any(c["name"] == n and c.get("must") for c in catalog)]
                                for p, v in review["unmatched"].items()}
    enc = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:PRO"],
                         input=json.dumps(review, ensure_ascii=False).encode(), capture_output=True, check=True).stdout
    open(os.path.join(OUT, "review.enc"), "wb").write(enc)
if MODE in ("dry-run", "preview") and os.environ.get("DRYRUN_OUT"):
    open(os.environ["DRYRUN_OUT"], "w", encoding="utf-8").write(text)  # local testing only

if MODE not in ("dry-run", "preview") and os.environ.get("DBX_SECRET"):
    state["saved_utc"] = NOW.strftime("%Y-%m-%d %H:%M")
    open(STATE_FILE, "wb").write(crypt(json.dumps(state).encode()))
finish()
