"""One pretend check-up for the self-test: fake services, fake Dropbox, no network.
Usage (run inside a scratch copy of the repo): _night.py SCRIPTS_DIR PLAN.json OUTDIR"""
import json, os, runpy, sys, time, types

scripts, plan_file, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, scripts)
plan = json.load(open(plan_file))
flat = lambda v: [y for x in v for y in (x if isinstance(x, list) else [x])]  # noqa: E731
plan["dead"] = {k: flat(v) for k, v in plan["dead"].items()}
plan["flaky"] = {k: flat(v) for k, v in plan["flaky"].items()}
import streamtest  # noqa: E402

lineups = plan["lineups"]  # {"main": {"categories": [...], "streams": [...]}, "backup": {...}}
calls = {}


def api(self, action=None, timeout=180):
    if action is None:
        return {"user_info": {"auth": 1, "status": "Active", "exp_date": str(int(time.time()) + 90 * 86400),
                              "active_cons": "0", "max_connections": "1"}}
    lu = lineups[self.label]
    return lu["categories"] if action == "get_live_categories" else lu["streams"]


def plays(prov, sid):
    k = f"{prov.label}:{sid}"
    calls[k] = calls.get(k, 0) + 1
    if sid in plan["dead"].get(prov.label, []):
        return False
    if sid in plan["flaky"].get(prov.label, []):
        return calls[k] > 2  # fails the first pass and the quick second chance, plays at the later retry
    return True


streamtest.Provider.api = api
streamtest.plays = plays
fake = types.ModuleType("dropbox_upload")
fake.upload = lambda text: open("uploaded.m3u", "w", encoding="utf-8").write(text)
sys.modules["dropbox_upload"] = fake
time.sleep = lambda s: None
sys.argv = ["channels.py", "nightly", out]
try:
    runpy.run_path(os.path.join(scripts, "channels.py"), run_name="__main__")
except SystemExit as e:
    if e.code not in (0, None):
        raise
