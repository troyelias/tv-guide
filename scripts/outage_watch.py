"""Hourly login check of the main service (uses no viewing connection).
Usage: outage_watch.py WATCH.json  -> prints 'switch' when everything should move to the backup service now."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from streamtest import Provider  # noqa: E402

path = sys.argv[1]
try:
    w = json.load(open(path))
except Exception:
    w = {"fails": 0, "switched": False}
ok = False
try:
    u = Provider("main", os.environ.get("PRO")).api(timeout=60).get("user_info", {})
    ok = u.get("auth") == 1 and str(u.get("status", "")).lower() == "active"
except Exception:
    pass
# Two starters can run this check (GitHub's hourly timer and an outside task), sometimes minutes apart.
# A failure only counts if the last counted failure was at least 40 minutes ago, so a short blip
# can never switch everything - the main service has to be down for 40+ minutes.
now = time.time()
if ok:
    w["fails"] = 0
elif now - w.get("last_fail", 0) >= 40 * 60:
    w["fails"] = w.get("fails", 0) + 1
    w["last_fail"] = now
if ok:
    w["switched"] = False  # the 4-7 AM check-up switches channels back
action = "switch" if (w["fails"] >= 2 and not w.get("switched")) else "none"
if action == "switch":
    w["switched"] = True
json.dump(w, open(path, "w"))
print(action)
