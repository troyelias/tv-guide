"""Hourly login check of the main service (uses no viewing connection).
Usage: outage_watch.py WATCH.json  -> prints 'switch' when everything should move to the backup service now."""
import json, os, sys
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
w["fails"] = 0 if ok else w.get("fails", 0) + 1
if ok:
    w["switched"] = False  # the 4-7 AM check-up switches channels back
action = "switch" if (w["fails"] >= 2 and not w.get("switched")) else "none"
if action == "switch":
    w["switched"] = True
json.dump(w, open(path, "w"))
print(action)
