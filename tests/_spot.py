"""One pretend daytime spot check for the self-test (fake services, no network).
Usage: _spot.py SCRIPTS_DIR PLAN.json SPOT.json"""
import json, runpy, sys, time
scripts, plan_file, spot = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, scripts)
plan = json.load(open(plan_file))
import streamtest  # noqa: E402

lu = plan["lineups"]


def api(self, action=None, timeout=180):
    if action is None:
        return {"user_info": {"auth": 1, "status": "Active", "active_cons": "0"}}
    return lu[self.label]["categories"] if action == "get_live_categories" else lu[self.label]["streams"]


streamtest.Provider.api = api
streamtest.grab_sample = lambda prov, sid, **k: None if sid in plan["dead"].get(prov.label, []) else f"{prov.label}:{sid}".encode()
streamtest.plays = lambda prov, sid: False
# pictures: same channel = same picture unless the plan says a copy shows something else
streamtest.content_frames = lambda data: [[0.0]] * 6 if data.decode() not in plan.get("other", []) else [[99.0]] * 6
streamtest.content_distance = lambda a, b: abs(a[0][0] - b[0][0])
time.sleep = lambda s: None
sys.argv = ["spotcheck.py", spot]
runpy.run_path(scripts + "/spotcheck.py", run_name="__main__")
