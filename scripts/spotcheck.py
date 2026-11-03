"""Daytime spot check of the favourite channels, only while nobody is watching.

Usage: spotcheck.py SPOT.json        (the file is read and updated; it lives on the 'spot' branch)
- Each service is checked only if its account shows nobody watching, and that is re-checked every few channels;
  the moment someone starts watching, that service is left alone.
- Changes nothing on the TV: it only records what played, for the status page, weekly report and alerts.
- Also compares the on-screen logo of channels that play on both services, to spot a mislabelled copy.
Writes channel names and yes/no results only - no logins, servers or stream numbers.
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from streamtest import Provider, plays, grab_sample, logo_mask  # noqa: E402
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
spot.setdefault("mixup_streak", {})
NOW = datetime.now(timezone.utc)
SAME, DIFF = float(os.environ.get("LOGO_SAME", "0.35")), float(os.environ.get("LOGO_DIFF", "0.08"))

run = {"utc": NOW.strftime("%Y-%m-%d %H:%M"), "ok": {}, "skipped": [], "logo": {}}
samples = {}
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
            d = grab_sample(prov, sid)
            if d:
                ok, samples[(c["id"], label)] = True, d
                break
            ok = plays(prov, sid)  # e.g. only the HLS form works
            if ok:
                break
            time.sleep(2)
        run["ok"].setdefault(c["name"], {})[label] = ok
        time.sleep(2)

# logo comparison for favourites that played on both services
for c in musts:
    a, b = samples.get((c["id"], "main")), samples.get((c["id"], "backup"))
    if not (a and b):
        continue
    ma, mb = logo_mask(a), logo_mask(b)
    if not ma or not mb or len(ma) < 8 or len(mb) < 8:
        continue
    iou = len(ma & mb) / max(1, len(ma | mb))
    run["logo"][c["name"]] = round(iou, 2)
    if iou < DIFF:
        spot["mixup_streak"][c["name"]] = spot["mixup_streak"].get(c["name"], 0) + 1
    elif iou >= SAME:
        spot["mixup_streak"].pop(c["name"], None)

# a favourite that played on neither service (where it was checked)
for name, res in run["ok"].items():
    if res and not any(res.values()):
        spot["down_streak"][name] = spot["down_streak"].get(name, 0) + 1
    else:
        spot["down_streak"].pop(name, None)

spot["runs"] = [r for r in spot["runs"] if r["utc"] >= (NOW - timedelta(days=8)).strftime("%Y-%m-%d %H:%M")] + [run]
# summary for the status page and weekly report: share of favourite checks that played, last 7 days
week = [r for r in spot["runs"] if r["utc"] >= (NOW - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")]
tot = sum(1 for r in week for res in r["ok"].values() for v in res.values())
good = sum(1 for r in week for res in r["ok"].values() for v in res.values() if v)
spot["summary"] = {"checks_7d": len(week), "favourite_plays_pct_7d": round(100 * good / tot, 1) if tot else None,
                   "down_now": sorted(n for n, k in spot["down_streak"].items() if k >= 2),
                   "possible_mixups": sorted(n for n, k in spot["mixup_streak"].items() if k >= 2),
                   "last_run_utc": run["utc"], "last_skipped": run["skipped"]}
json.dump(spot, open(path, "w"), indent=1)
print(json.dumps(spot["summary"]))
