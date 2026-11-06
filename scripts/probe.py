"""Look closely at a few channels on both services: does real picture come through, or just an empty stream?

Usage: probe.py OUT.txt   (channel names in env NAMES, comma-separated; FORCE=1 to probe even if someone is watching)
For each channel it opens the stream for ~15 seconds (one at a time) and reports the HTTP answer, how much data
arrived, which kinds of video/audio are inside, how much of it is picture, and whether a full picture frame
(a "key frame") arrived. Prints no logins, servers or stream numbers.
"""
import hashlib, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from streamtest import Provider, UA, is_filler, _is_video
from catalog_lib import match_lineup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
catalog = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
wanted = [n.strip().lower() for n in os.environ.get("NAMES", "").split(",") if n.strip()]
FORCE = os.environ.get("FORCE") == "1"
SECS, LIMIT = 15, 12_000_000
SHOTS = os.environ.get("SHOTS_DIR")  # if set, save a few small pictures per channel here
seen = {}  # picture fingerprint -> first channel that showed it
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


def look(data, name):
    """Decode a few frames with ffmpeg: brightness, how much changes between frames, sound level,
    and a fingerprint so identical pictures on different channels can be spotted."""
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "fps=2,scale=32:18", "-frames:v", "6",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=60).stdout
        frames = [raw[i:i + 576] for i in range(0, len(raw) - 575, 576)]
        aud = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, timeout=60, text=True).stderr
        vol = next((l.split("mean_volume:")[1].strip() for l in aud.splitlines() if "mean_volume:" in l), "?")
        if SHOTS:
            safe = "".join(ch for ch in name if ch.isalnum())[:40]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", "fps=1,scale=480:-2", "-frames:v", "3",
                            os.path.join(SHOTS, safe + "_%d.jpg")], timeout=60)
        if not frames:
            return f"no picture could be decoded; sound {vol}"
        f0 = frames[0]
        mean = sum(f0) / 576
        spread = (sum((v - mean) ** 2 for v in f0) / 576) ** 0.5
        motion = max((sum(abs(a - b) for a, b in zip(x, y)) / 576 for x, y in zip(frames, frames[1:])), default=0)
        fp = hashlib.md5(bytes(v // 32 for v in f0)).hexdigest()[:8]
        same = seen.setdefault(fp, name)
        desc = "blank" if spread < 4 else ("still picture" if motion < 1 else "moving picture")
        return (f"{len(frames)} frames: {desc} (brightness {mean:.0f}/255, detail {spread:.0f}, change {motion:.1f}); "
                f"sound {vol}" + (f"; SAME PICTURE AS {same}" if same != name else ""))
    except Exception as e:
        return f"couldn't decode ({type(e).__name__})"
    finally:
        os.unlink(path)


def probe(prov, sid, name):
    st, ctype, data, secs, moved, final = grab(prov.stream_url(sid))
    head = f"  {name}: .ts -> HTTP {st}, {len(data) // 1024} KB in {secs:.0f}s ({len(data) * 8 / max(secs, 1) / 1000:.0f} kbit/s), type {ctype or '-'}{', redirected' if moved else ''}"
    say(head)
    if data:
        say("     " + analyse(data, secs))
        say("     " + look(data, name))
        say("     nightly test says: " + ("PLAYS" if _is_video(data) and not is_filler(data[:188 * 4000]) else "DOES NOT PLAY"))
    if not data or st != 200:
        verdict = "DOES NOT PLAY"
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
                if d2:
                    say("     " + look(d2, name))
                    if _is_video(d2) and not is_filler(d2[:188 * 4000]):
                        verdict = "PLAYS"
        say("     nightly test says: " + verdict)


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
    say(f"  account: {u.get('status')}, {u.get('max_connections')} screen(s) allowed")  # when people watch is never published
    try:
        exp = int(u.get("exp_date"))
        say(f"  subscription ends: {time.strftime('%d %b %Y %H:%M UTC', time.gmtime(exp))} "
            f"(about {(exp - time.time()) / 86400:.1f} days from now)")
    except (TypeError, ValueError):
        say(f"  subscription ends: {u.get('exp_date') or 'no end date given'}")
    if not wanted:
        say("  (account check only - no channels opened)")
        continue
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
        names = {x["stream_id"]: x.get("name", "") for x in streams}
        for n, sid in enumerate(sids[:4] if os.environ.get("ALL_COPIES") == "1" else sids[:1], 1):
            probe(prov, sid, f"{c['name']} ({label.split()[0].lower()}, copy {n}: {names.get(sid, '?')})")
            time.sleep(3)
    say()

# ---- picture comparison (LOGOS=1): do two copies show the same programme? (calibration for mix-up detection) ----
if os.environ.get("LOGOS") == "1" and wanted:
    from streamtest import grab_sample, content_frames, content_distance
    seqs = {}
    for label, env in (("main", "PRO"), ("backup", "PRO_2")):
        prov = Provider(label, os.environ.get(env))
        if not prov.ok:
            continue
        try:
            if str(prov.api(timeout=60).get("user_info", {}).get("active_cons", "0")) not in ("0", "", "None") and not FORCE:
                say(f"pictures: {label} in use - skipped")
                continue
            m = match_lineup(catalog, prov.api("get_live_streams"), prov.api("get_live_categories"))
        except Exception:
            continue
        for c in catalog:
            if c["name"].lower() in wanted and m.get(c["id"]):
                d = grab_sample(prov, m[c["id"]][0], limit=8_000_000, secs=14)
                seqs[(c["name"], label)] = content_frames(d) if d else None
                time.sleep(2)
    say("== Picture comparison (0 = identical pictures; same channel should be low, different channels high)")
    keys = sorted(k for k in seqs if seqs[k])
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a[1] == b[1]:
                continue  # only compare across services
            d = content_distance(seqs[a], seqs[b])
            if d is not None:
                say(f"  {a[0]} ({a[1]}, {len(seqs[a])} frames) vs {b[0]} ({b[1]}, {len(seqs[b])} frames): {d:.1f}  "
                    f"[{'SAME channel' if a[0] == b[0] else 'different'}]")
    for k in sorted(seqs):
        if not seqs[k]:
            say(f"  {k[0]} ({k[1]}): no picture")

# ---- delay comparison (DELAY=1): which service shows the same moment first? ----
if os.environ.get("DELAY") == "1" and wanted:
    import threading
    from streamtest import record, scene_cuts, align_cuts
    provs = {lab: Provider(lab, os.environ.get(env)) for lab, env in (("main", "PRO"), ("backup", "PRO_2"))}
    lineup_m = {}
    for lab, prov in provs.items():
        try:
            if str(prov.api(timeout=60).get("user_info", {}).get("active_cons", "0")) not in ("0", "", "None") and not FORCE:
                say(f"delay: {lab} in use - skipped")
                continue
            lineup_m[lab] = match_lineup(catalog, prov.api("get_live_streams"), prov.api("get_live_categories"))
        except Exception:
            pass
    say("== Delay comparison (recording both services at the same moment and lining up the scene cuts)")
    if len(lineup_m) == 2:
        for c in catalog:
            if c["name"].lower() not in wanted or not all(lineup_m[l].get(c["id"]) for l in lineup_m):
                continue
            busy = [l for l, pv in provs.items()
                    if str(pv.api(timeout=60).get("user_info", {}).get("active_cons", "0")) not in ("0", "", "None")]
            if busy and not FORCE:
                say(f"  stopped: someone started watching on the {' and '.join(busy)} service")
                break
            rec = {}
            ths = [threading.Thread(target=lambda l=l: rec.__setitem__(l, record(provs[l], lineup_m[l][c["id"]][0])))
                   for l in ("main", "backup")]
            [t.start() for t in ths]
            [t.join() for t in ths]
            (da, ta), (db, tb) = rec["main"], rec["backup"]
            ca, la = scene_cuts(da)
            cb, lb = scene_cuts(db)
            res = align_cuts(ca, cb)
            if not res or res[0] is None or res[1] < 3:
                say(f"  {c['name']}: couldn't line up ({len(ca or [])}/{len(cb or [])} cuts in {la:.0f}/{lb:.0f}s)")
            else:
                shift = res[0] + (ta - tb)  # correct for the two recordings starting a moment apart
                who = "main" if shift < 0 else "backup"
                say(f"  {c['name']}: {who} service is {abs(shift):.1f} s AHEAD ({res[1]} of {res[2]} cuts matched; "
                    f"recorded {la:.0f}s / {lb:.0f}s)")
            time.sleep(5)

open(sys.argv[1], "w").write("\n".join(lines) + "\n")
