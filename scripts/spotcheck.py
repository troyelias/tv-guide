"""Daytime spot check of the favourite channels, only while nobody is watching.

Usage: spotcheck.py SPOT.json        (the file is read and updated; it lives on the 'spot' branch)
- Each service is checked only if its account shows nobody watching, and that is re-checked every few channels;
  the moment someone starts watching, that service is left alone.
- Changes nothing on the TV: it only records what played, for the status page, weekly report and alerts.
Writes channel names and yes/no results only - no logins, servers or stream numbers.
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from streamtest import Provider, plays, grab_sample  # noqa: E402
from catalog_lib import match_lineup  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
catalog = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
musts = [c for c in catalog if c.get("must")]
path = sys.argv[1]
try:
    spot = json.load(open(path))
except Exception:
    spot = {}
spot.setdefault("runs", [])
spot.setdefault("down_streak", {})
NOW = datetime.now(timezone.utc)
# Note: automatic "wrong channel" detection was tried (logo position, picture matching) and calibrated on real
# streams; the two services run tens of seconds apart, so it could not tell same from different channels reliably.
# Wrong-channel reports are checked by eye instead: 'Report a problem' saves locked sample pictures.

run = {"utc": NOW.strftime("%Y-%m-%d %H:%M"), "ok": {}, "skipped": []}
for label, env in (("main", "PRO"), ("backup", "PRO_2")):
    prov = Provider(label, os.environ.get(env))
    if not prov.ok:
        continue

    def idle():
        try:
            return str(prov.api(timeout=60).get("user_info", {}).get("active_cons", "0")) in ("0", "", "None")
        except Exception:
            return False

    if not idle():
        run["skipped"].append(label)
        continue
    try:
        m = match_lineup(catalog, prov.api("get_live_streams"), prov.api("get_live_categories"))
    except Exception:
        run["skipped"].append(label)
        continue
    for i, c in enumerate(musts):
        if i and i % 4 == 0:
            time.sleep(20)  # let our own connection close before asking whether someone else is watching
            if not idle():
                run["skipped"].append(label + " (stopped: someone started watching)")
                break
        sids = m.get(c["id"])
        if not sids:
            continue
        ok = False
        for sid in sids[:2]:
            if grab_sample(prov, sid):
                ok = True
                break
            ok = plays(prov, sid)  # e.g. only the HLS form works
            if ok:
                break
            time.sleep(2)
        run["ok"].setdefault(c["name"], {})[label] = ok
        time.sleep(2)

# once a day (the early-morning run, India time) measure which service runs ahead for a few sports channels,
# rotating through them; used by the proposed "sports from the faster service" rule (shadow mode)
if os.environ.get("MEASURE_LEAD") == "1" or NOW.hour == 4:  # the 9:53 AM (India) run
    from streamtest import record, scene_cuts, align_cuts
    import threading
    provs = {l: Provider(l, os.environ.get(e)) for l, e in (("main", "PRO"), ("backup", "PRO_2"))}
    try:
        idle_all = all(str(pv.api(timeout=60).get("user_info", {}).get("active_cons", "0")) in ("0", "", "None") for pv in provs.values())
        lm = {l: match_lineup(catalog, pv.api("get_live_streams"), pv.api("get_live_categories")) for l, pv in provs.items()}
    except Exception:
        idle_all, lm = False, {}
    leads = spot.setdefault("lead", {})
    sports = [c for c in catalog if c["folder"] == "Sports" and all(lm.get(l, {}).get(c["id"]) for l in provs)]
    sports.sort(key=lambda c: leads.get(c["name"], {}).get("utc", ""))  # least recently measured first
    for c in sports[:int(os.environ.get("LEAD_PER_RUN", "4"))] if idle_all else []:
        time.sleep(20)
        if not all(str(pv.api(timeout=60).get("user_info", {}).get("active_cons", "0")) in ("0", "", "None") for pv in provs.values()):
            run["skipped"].append("lead (someone started watching)")
            break
        rec = {}
        ths = [threading.Thread(target=lambda l=l: rec.__setitem__(l, record(provs[l], lm[l][c["id"]][0], secs=50))) for l in provs]
        [t.start() for t in ths]
        [t.join() for t in ths]
        (da, ta), (db, tb) = rec["main"], rec["backup"]
        ca, _ = scene_cuts(da)
        cb, _ = scene_cuts(db)
        res = align_cuts(ca, cb)
        if res and res[0] is not None and res[1] >= 3:
            shift = res[0] + (ta - tb)
            leads[c["name"]] = {"ahead": "main" if shift < 0 else "backup", "secs": round(abs(shift), 1),
                                "utc": NOW.strftime("%Y-%m-%d %H:%M")}

# a favourite that played on neither service (where it was checked)
for name, res in run["ok"].items():
    if res and not any(res.values()):
        spot["down_streak"][name] = spot["down_streak"].get(name, 0) + 1
    else:
        spot["down_streak"].pop(name, None)

# the record is public, so it keeps what played - never when a check was skipped because someone was watching
run.pop("skipped", None)
spot["runs"] = [{k: v for k, v in r.items() if k != "skipped"} for r in spot["runs"]
                if r["utc"] >= (NOW - timedelta(days=8)).strftime("%Y-%m-%d %H:%M")] + [run]
# summary for the status page and weekly report: share of favourite checks that played, last 7 days
week = [r for r in spot["runs"] if r["utc"] >= (NOW - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")]
tot = sum(1 for r in week for res in r["ok"].values() for v in res.values())
good = sum(1 for r in week for res in r["ok"].values() for v in res.values() if v)
spot["summary"] = {"checks_7d": len(week), "favourite_plays_pct_7d": round(100 * good / tot, 1) if tot else None,
                   "down_now": sorted(n for n, k in spot["down_streak"].items() if k >= 2),
                   "last_run_utc": run["utc"],
                   "lead": spot.get("lead", {})}
json.dump(spot, open(path, "w"), indent=1)
print(json.dumps({k: spot["summary"][k] for k in ("checks_7d", "favourite_plays_pct_7d", "down_now")}))
