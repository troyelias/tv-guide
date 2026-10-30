"""Test-play channels one at a time to see which actually stream.

Usage: health_check.py CANDIDATES.txt RESULTS.json
Login comes from the private secret CHECK_URL (an xmltv.php or player_api.php link).
Each channel is opened for a few seconds, one at a time, with a pause in between, so it uses a
single connection - make sure nobody is watching on that login while it runs.
Results contain only stream numbers and ok/failed - no names or login details.
"""
import json, os, re, sys, time, urllib.parse, urllib.request

cands = [int(l) for l in open(sys.argv[1]) if l.strip().isdigit()]
out = sys.argv[2]
secret = os.environ.get("CHECK_URL", "").strip()
u = urllib.parse.urlparse(secret)
q = urllib.parse.parse_qs(u.query)
user, pw = q.get("username", [""])[0].strip(), q.get("password", [""])[0].strip()
if not (u.netloc and user and pw):
    sys.exit("::error::The login secret isn't in the expected format.")
host = f"{u.scheme}://{u.netloc}"
UA = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}


def fetch(url, limit, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = b""
        while len(data) < limit and time.time() - t0 < timeout:
            chunk = r.read(16384)
            if not chunk:
                break
            data += chunk
        return data, r.geturl()


def looks_like_ts(b):
    if len(b) < 188 * 8:
        return False
    for off in range(188):
        if all(b[off + 188 * k] == 0x47 for k in range(6) if off + 188 * k < len(b)):
            return True
    return False


# account check first
try:
    info = json.loads(fetch(f"{host}/player_api.php?username={user}&password={pw}", 200000)[0]).get("user_info", {})
    ok_login = info.get("auth") == 1
except Exception as e:
    info, ok_login = {}, False
summary = {"login_ok": ok_login, "status": info.get("status"), "max_connections": info.get("max_connections"),
           "active_cons_before": info.get("active_cons")}
if not ok_login:
    json.dump({"summary": summary, "results": {}}, open(out, "w"))
    sys.exit("::error::Login failed - check the secret.")
if str(info.get("active_cons", "0")) not in ("0", ""):
    print("::warning::Someone seems to be watching on this login right now - results may show false failures.")

results, fails_in_row = {}, 0
for i, sid in enumerate(cands, 1):
    res = {"ok": False}
    t0 = time.time()
    try:
        data, final = fetch(f"{host}/live/{user}/{pw}/{sid}.ts", 188 * 400)
        if looks_like_ts(data):
            res = {"ok": True, "how": "ts"}
        else:
            res["why"] = f"no video ({len(data)} bytes)"
    except Exception as e:
        res["why"] = type(e).__name__ + (f" {e.code}" if hasattr(e, "code") else "")
    if not res["ok"]:  # second try: HLS
        try:
            pl, final = fetch(f"{host}/live/{user}/{pw}/{sid}.m3u8", 200000)
            text = pl.decode("utf-8", "ignore")
            seg = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), None)
            if text.startswith("#EXTM3U") and seg:
                if seg.endswith(".m3u8"):  # variant playlist
                    sub, final2 = fetch(urllib.parse.urljoin(final, seg), 200000)
                    final = final2
                    seg = next((l.strip() for l in sub.decode("utf-8", "ignore").splitlines() if l.strip() and not l.startswith("#")), None)
                if seg:
                    data, _ = fetch(urllib.parse.urljoin(final, seg), 188 * 400)
                    if looks_like_ts(data):
                        res = {"ok": True, "how": "hls"}
        except Exception:
            pass
    res["secs"] = round(time.time() - t0, 1)
    results[sid] = res
    fails_in_row = 0 if res["ok"] else fails_in_row + 1
    print(f"{i}/{len(cands)} {'ok ' if res['ok'] else 'bad'} {res.get('why', '')}")
    if i == 25 and not any(r["ok"] for r in results.values()):
        summary["stopped_early"] = "first 25 all failed - server may be blocking this checker"
        break
    time.sleep(3)

summary.update(checked=len(results), working=sum(r["ok"] for r in results.values()))
json.dump({"summary": summary, "results": results}, open(out, "w"), indent=0)
print(f"Working: {summary['working']} of {summary['checked']}")
