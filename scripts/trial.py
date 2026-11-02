"""Try a new service before buying it: what it carries and what really plays.

Usage: trial.py OUT.json      (login link in env TRIAL - any form: guide, playlist or app link)
Reads the service's channel list, matches it against catalog.json, then test-plays (one at a time,
with the same picture-and-sound check as the nightly check-up):
  1. every channel of ours it carries (up to 2 copies each),
  2. extra Indian / UK / Pakistani channels it has that we don't (one copy each, best quality first).
Contacts only the new service. The result (which includes channel names) is encrypted by the workflow.
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from streamtest import Provider, plays
from catalog_lib import match_lineup, key, region, quality

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
catalog = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
EXTRA_MAX = int(os.environ.get("EXTRA_MAX", "600"))
SOUTH_ASIA = re.compile(r"\b(INDIA|INDIAN|HINDI|DESI|PAK|PAKISTAN|PK|IN|KANNADA|PUNJABI|URDU|ASIA|ASIAN|BOLLYWOOD|SOUTH ?ASIA)\b", re.I)
OTHER_LANG = re.compile(r"TAMIL|TELUGU|TELEGU|MALAYALAM|BANGLA|BENGALI|MARATHI|GUJARATI|GUJRATI|ODIA|ORIYA|BHOJPURI|ASSAM|NEPAL|SINHALA", re.I)
SKIP_CATS = re.compile(r"adult|xxx|18\+|vod|movie[s]? ?\d{4}|series|radio|24/7|replay|ppv|event", re.I)

prov = Provider("trial", os.environ.get("TRIAL"))
out = {"login": None, "catalog": {}, "extras": [], "tested": 0}
if not prov.ok:
    json.dump(out, open(sys.argv[1], "w"))
    sys.exit("::error::TRIAL login isn't in a form with username= and password= in it.")
u = prov.api(timeout=90).get("user_info", {})
out["login"] = {k: u.get(k) for k in ("auth", "status", "exp_date", "is_trial", "active_cons", "max_connections")}
if u.get("auth") != 1:
    json.dump(out, open(sys.argv[1], "w"))
    sys.exit("::error::The new service refused the login.")
cats = prov.api("get_live_categories")
streams = prov.api("get_live_streams")
cat_name = {c.get("category_id"): c.get("category_name", "") for c in cats}
by_sid = {s["stream_id"]: s for s in streams}
out["lineup_size"] = len(streams)
m = match_lineup(catalog, streams, cats)
print(f"Login ok. {len(streams)} live channels; {len(m)} of our {len(catalog)} found.")


def test(sid):
    out["tested"] += 1
    ok = plays(prov, sid)
    time.sleep(1)
    return ok


# 1. our channels
for i, c in enumerate(catalog, 1):
    sids = m.get(c["id"], [])
    r = {"found": bool(sids), "plays": None, "name_there": None}
    for sid in sids[:2]:
        r["name_there"] = by_sid[sid].get("name")
        if test(sid):
            r["plays"] = True
            break
        r["plays"] = False
    out["catalog"][c["id"]] = r
    if i % 50 == 0:
        print(f"  ...{i} of ours checked")

# 2. extras: Indian / UK / Pakistani live channels we don't have, one copy per channel
ours = {k for c in catalog for k in c["keys"]}
best = {}
for s in streams:
    nm = s.get("name") or ""
    cn = cat_name.get(s.get("category_id"), "")
    if nm.strip().startswith("#") or SKIP_CATS.search(cn) or SKIP_CATS.search(nm):
        continue
    if not SOUTH_ASIA.search(cn) and not SOUTH_ASIA.search(nm.split("|")[0].split("▎")[0].split(":")[0]):
        continue  # not an Indian / Pakistani / Asian-in-UK group
    rg = region(nm, cn)
    if rg not in ("in", "uk", "pk"):
        continue
    k = key(nm)
    if not k or k in ours:
        continue
    cand = (quality(nm), s["stream_id"])
    if k not in best or cand < best[k][0]:
        best[k] = (cand, s, rg, cn)
# Hindi / English / Kannada / Pakistani first; other Indian languages after (they're less likely to be wanted)
WANTED_GROUP = re.compile(r"HINDI|PAK|ENGLISH|SPORT|KANNADA|NEWS|MOVIE|CINEMA|MUSIC|KIDS|\bUK\b|DEVOTION|RELIGI|INDIA|BUSINESS|INFOTAIN|LIFESTYLE", re.I)


def priority(kv):
    s, cn = kv[1][1], kv[1][3]
    text = (s.get("name", "") + " " + cn)
    if OTHER_LANG.search(text):
        return 2
    return 0 if WANTED_GROUP.search(cn) else 1


order = sorted(best.items(), key=priority)
for k, ((_, sid), s, rg, cn) in order[:EXTRA_MAX]:
    out["extras"].append({"name": s.get("name"), "group": cn, "region": rg, "plays": test(sid),
                          "has_guide_tag": bool((s.get("epg_channel_id") or "").strip())})
print(f"Done: {out['tested']} test-plays; {len(best)} extra candidates ({min(len(best), EXTRA_MAX)} tested).")
json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False)
