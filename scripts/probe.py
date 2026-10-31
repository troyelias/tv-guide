"""Look closely at a few channels on both services: does real picture come through, or just an empty stream?

Usage: probe.py OUT.txt   (channel names in env NAMES, comma-separated; FORCE=1 to probe even if someone is watching)
For each channel it opens the stream for ~15 seconds (one at a time) and reports the HTTP answer, how much data
arrived, which kinds of video/audio are inside, how much of it is picture, and whether a full picture frame
(a "key frame") arrived. Prints no logins, servers or stream numbers.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from streamtest import Provider, UA
from catalog_lib import match_lineup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
catalog = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
wanted = [n.strip().lower() for n in os.environ.get("NAMES", "").split(",") if n.strip()]
FORCE = os.environ.get("FORCE") == "1"
SECS, LIMIT = 15, 6_000_000
KIND = {0x1b: "H.264 video", 0x24: "HEVC video", 0x02: "MPEG-2 video", 0x01: "MPEG-1 video", 0x10: "MPEG-4 video",
        0x0f: "AAC audio", 0x11: "AAC audio", 0x03: "MP2 audio", 0x04: "MP2 audio", 0x81: "AC-3 audio",
        0x87: "E-AC-3 audio", 0x06: "private data"}
VIDEO = {0x1b, 0x24, 0x02, 0x01, 0x10}
lines = []


def say(s=""):
    print(s)
    lines.append(s)


def grab(url):
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            moved = urllib.parse.urlparse(r.geturl()).netloc != urllib.parse.urlparse(url).netloc
            data = bytearray()
            while len(data) < LIMIT and time.time() - t0 < SECS:
                chunk = r.read(65536)
                if not chunk:
                    break
                data += chunk
            return r.status, r.headers.get("Content-Type", "?"), bytes(data), time.time() - t0, moved, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, "", b"", time.time() - t0, False, url
    except Exception as e:
        return type(e).__name__, "", b"", time.time() - t0, False, url


def analyse(b, secs):
    off = next((o for o in range(min(188, len(b))) if all(o + 188 * k < len(b) and b[o + 188 * k] == 0x47 for k in range(5))), None)
    if off is None:
        return "not a TV stream (no transport-stream data)"
    pkts = [b[i:i + 188] for i in range(off, len(b) - 187, 188) if b[i] == 0x47]
    pmt_pids, streams, count, rai, keyf = set(), {}, {}, {}, {}
    for p in pkts:
        pid = ((p[1] & 0x1f) << 8) | p[2]
        pusi = p[1] & 0x40
        afc = (p[3] >> 4) & 3
        count[pid] = count.get(pid, 0) + 1
        pay = 4
        if afc in (2, 3):
            alen = p[4]
            if alen and p[5] & 0x40:
                rai[pid] = rai.get(pid, 0) + 1
            pay = 5 + alen
        if afc == 2 or pay >= 188:
            continue
        body = p[pay:]
        if pid == 0 and pusi:
            sec = body[1 + body[0]:]
            n = ((sec[1] & 0x0f) << 8 | sec[2]) - 9
            for i in range(8, 8 + max(0, n), 4):
                if i + 3 < len(sec) and (sec[i] << 8 | sec[i + 1]):
                    pmt_pids.add(((sec[i + 2] & 0x1f) << 8) | sec[i + 3])
        elif pid in pmt_pids and pusi:
            sec = body[1 + body[0]:]
            slen = (sec[1] & 0x0f) << 8 | sec[2]
            pil = (sec[10] & 0x0f) << 8 | sec[11]
            i = 12 + pil
            while i + 4 < 3 + slen - 4 and i + 4 < len(sec):
                st, epid, eil = sec[i], ((sec[i + 1] & 0x1f) << 8) | sec[i + 2], (sec[i + 3] & 0x0f) << 8 | sec[i + 4]
                streams[epid] = st
                i += 5 + eil
        elif streams.get(pid) in (0x1b, 0x24):
            j = body.find(b"\x00\x00\x01")
            while 0 <= j < len(body) - 4:
                h = body[j + 3]
                if streams[pid] == 0x1b and (h & 0x1f) == 5 or streams[pid] == 0x24 and 16 <= ((h >> 1) & 0x3f) <= 21:
                    keyf[pid] = keyf.get(pid, 0) + 1
                j = body.find(b"\x00\x00\x01", j + 3)
    if not streams:
        return f"stream data arrived ({len(pkts)} packets) but no programme table - can't tell what's inside"
    parts = []
    for pid, st in sorted(streams.items()):
        kbps = count.get(pid, 0) * 188 * 8 / max(secs, 1) / 1000
        extra = ""
        if st in VIDEO:
            extra = f", key frames: {keyf.get(pid, 0) or rai.get(pid, 0)}"
        parts.append(f"{KIND.get(st, hex(st))} {kbps:.0f} kbit/s{extra}")
    has_video = any(st in VIDEO and count.get(pid, 0) > 50 for pid, st in streams.items())
    verdict = "PICTURE LIKELY" if has_video and any(keyf.values() or rai.values()) else ("NO PICTURE (no video inside)" if not has_video else "VIDEO BUT NO KEY FRAME YET")
    return f"{verdict} | " + "; ".join(parts)


def probe(prov, sid, name):
    st, ctype, data, secs, moved, final = grab(prov.stream_url(sid))
    head = f"  {name}: .ts -> HTTP {st}, {len(data) // 1024} KB in {secs:.0f}s ({len(data) * 8 / max(secs, 1) / 1000:.0f} kbit/s), type {ctype or '-'}{', redirected' if moved else ''}"
    say(head)
    if data:
        say("     " + analyse(data, secs))
    if not data or st != 200:
        st, ctype, pl, secs, moved, final = grab(prov.stream_url(sid, ".m3u8"))
        text = pl.decode("utf-8", "ignore")
        say(f"     .m3u8 -> HTTP {st}, {'playlist' if text.startswith('#EXTM3U') else 'no playlist'}")
        seg = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), None)
        if seg:
            if seg.endswith(".m3u8"):
                _, _, sub, _, _, final = grab(urllib.parse.urljoin(final, seg))
                seg = next((l.strip() for l in sub.decode("utf-8", "ignore").splitlines() if l.strip() and not l.startswith("#")), None)
            if seg:
                st, ctype, d2, s2, _, _ = grab(urllib.parse.urljoin(final, seg))
                say(f"     first piece -> HTTP {st}, {len(d2) // 1024} KB | " + (analyse(d2, max(s2, 6)) if d2 else "empty"))


for label, env in (("Main service", "PRO"), ("Backup service", "PRO_2")):
    prov = Provider(label, os.environ.get(env))
    say(f"== {label}")
    if not prov.ok:
        say("  no login set")
        continue
    try:
        u = prov.api(timeout=60).get("user_info", {})
    except Exception as e:
        say(f"  login check failed ({type(e).__name__})")
        continue
    say(f"  account: {u.get('status')}, watching now: {u.get('active_cons')} of {u.get('max_connections')} allowed")
    if str(u.get("active_cons", "0")) not in ("0", "", "None") and not FORCE:
        say("  someone is watching on this service - skipped so they aren't interrupted")
        continue
    streams = prov.api("get_live_streams")
    cats = prov.api("get_live_categories")
    m = match_lineup(catalog, streams, cats)
    for c in catalog:
        if wanted and c["name"].lower() not in wanted:
            continue
        sids = m.get(c["id"])
        if not sids:
            say(f"  {c['name']}: not on this service")
            continue
        probe(prov, sids[0], c["name"])
        time.sleep(3)
    say()

open(sys.argv[1], "w").write("\n".join(lines) + "\n")
