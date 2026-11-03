"""Small helpers to talk to a provider (Xtream-style login link) and test-play a stream for a few seconds."""
import json, os, shutil, subprocess, tempfile, time, urllib.parse, urllib.request

UA = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}


class Provider:
    def __init__(self, label, link):
        u = urllib.parse.urlparse((link or "").strip())
        q = urllib.parse.parse_qs(u.query)
        self.label = label
        self.user = q.get("username", [""])[0].strip()
        self.pw = q.get("password", [""])[0].strip()
        self.host = f"{u.scheme}://{u.netloc}" if u.netloc else ""
        self.ok = bool(self.host and self.user and self.pw)

    def api(self, action=None, timeout=180):
        url = f"{self.host}/player_api.php?username={self.user}&password={self.pw}" + (f"&action={action}" if action else "")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def stream_url(self, sid, ext=".ts"):
        return f"{self.host}/live/{self.user}/{self.pw}/{sid}{ext}"

    def xmltv_url(self):
        return f"{self.host}/xmltv.php?username={self.user}&password={self.pw}"


def _fetch(url, limit, timeout=12):
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


def _is_video(b):
    if len(b) < 188 * 8:
        return False
    for off in range(188):
        if all(b[off + 188 * k] == 0x47 for k in range(6) if off + 188 * k < len(b)):
            return True
    return False


SAMPLE = 188 * 4000  # ~750 KB: enough to tell real programmes from a service's "dead channel" filler


def _ts_stats(b):
    """Share of the data that is sound, and how many key frames (full pictures) the video has."""
    off = next((o for o in range(min(188, len(b))) if all(o + 188 * k < len(b) and b[o + 188 * k] == 0x47 for k in range(5))), None)
    if off is None:
        return None
    pmt, kinds, count, keys = set(), {}, {}, 0
    for i in range(off, len(b) - 187, 188):
        p = b[i:i + 188]
        if p[0] != 0x47:
            continue
        pid, pusi, afc = ((p[1] & 0x1f) << 8) | p[2], p[1] & 0x40, (p[3] >> 4) & 3
        count[pid] = count.get(pid, 0) + 1
        pay = 5 + p[4] if afc in (2, 3) else 4
        if afc == 2 or pay >= 188:
            continue
        body = p[pay:]
        try:
            if pid == 0 and pusi:
                sec = body[1 + body[0]:]
                for j in range(8, 8 + ((sec[1] & 0x0f) << 8 | sec[2]) - 9, 4):
                    if sec[j] << 8 | sec[j + 1]:
                        pmt.add(((sec[j + 2] & 0x1f) << 8) | sec[j + 3])
            elif pid in pmt and pusi:
                sec = body[1 + body[0]:]
                end, j = 3 + ((sec[1] & 0x0f) << 8 | sec[2]) - 4, 12 + ((sec[10] & 0x0f) << 8 | sec[11])
                while j + 4 < min(end, len(sec)):
                    kinds[((sec[j + 1] & 0x1f) << 8) | sec[j + 2]] = sec[j]
                    j += 5 + ((sec[j + 3] & 0x0f) << 8 | sec[j + 4])
            elif kinds.get(pid) in (0x1b, 0x24):
                k = body.find(b"\x00\x00\x01")
                while 0 <= k < len(body) - 4:
                    h = body[k + 3]
                    if (kinds[pid] == 0x1b and (h & 0x1f) == 5) or (kinds[pid] == 0x24 and 16 <= ((h >> 1) & 0x3f) <= 21):
                        keys += 1
                    k = body.find(b"\x00\x00\x01", k + 3)
        except IndexError:
            continue
    video = sum(count.get(q, 0) for q, t in kinds.items() if t in (0x1b, 0x24, 0x02, 0x01, 0x10))
    audio = sum(count.get(q, 0) for q, t in kinds.items() if t in (0x0f, 0x11, 0x03, 0x04, 0x81, 0x87, 0x06))
    if not kinds or not video:
        return {"video": video, "audio_share": 1.0 if audio else 0.0, "keys": keys}
    return {"video": video, "audio_share": audio / max(video + audio, 1), "keys": keys}


def _still_and_silent(data):
    """Decode a little with ffmpeg (if installed): True if the picture never changes and there's no sound."""
    if not shutil.which("ffmpeg"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "fps=4,scale=32:18", "-frames:v", "4",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=30).stdout
        frames = [raw[i:i + 576] for i in range(0, len(raw) - 575, 576)]
        aud = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, timeout=30, text=True).stderr
        vol = next((float(l.split("mean_volume:")[1].split()[0]) for l in aud.splitlines() if "mean_volume:" in l), None)
        still = len(frames) >= 2 and max(sum(abs(a - b) for a, b in zip(x, y)) / 576 for x, y in zip(frames, frames[1:])) < 0.5
        return still and (vol is None or vol <= -80)
    except Exception:
        return None
    finally:
        os.unlink(path)


def is_filler(data):
    """Some services send a silent black picture instead of an error when a channel is dead.
    Its tell-tale signs: every frame is a full picture, and 'sound' is a big share of the data.
    Real channels: a handful of full pictures per few seconds, sound well under a fifth of the data."""
    st = _ts_stats(data)
    if st is None:
        return False
    if st["video"] == 0:
        return st["audio_share"] == 0  # no picture and no sound
    if st["keys"] < 15 and st["audio_share"] <= 0.25:
        return False  # looks like an ordinary broadcast
    confirm = _still_and_silent(data)  # look at the actual picture and sound
    if confirm is None:  # no decoder available: only call it filler when both signs are present
        return st["keys"] >= 15 and st["audio_share"] > 0.25
    return confirm


def _real_video(data):
    return _is_video(data) and not is_filler(data)


def plays(provider, sid):
    """True if the stream delivers real programmes within ~12 seconds (tries .ts, then HLS).
    A silent black "dead channel" filler counts as not playing."""
    try:
        data, _ = _fetch(provider.stream_url(sid), SAMPLE)
        if _is_video(data):
            return not is_filler(data)
    except Exception:
        pass
    try:
        pl, final = _fetch(provider.stream_url(sid, ".m3u8"), 200000)
        text = pl.decode("utf-8", "ignore")
        seg = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), None)
        if text.startswith("#EXTM3U") and seg:
            if seg.endswith(".m3u8"):
                sub, final = _fetch(urllib.parse.urljoin(final, seg), 200000)
                seg = next((l.strip() for l in sub.decode("utf-8", "ignore").splitlines() if l.strip() and not l.startswith("#")), None)
            if seg:
                data, _ = _fetch(urllib.parse.urljoin(final, seg), SAMPLE)
                return _real_video(data)
    except Exception:
        pass
    return False


def _ffprobe_height(data):
    if not shutil.which("ffprobe"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=20).stdout
        return int(out.strip().splitlines()[0]) if out.strip() else None
    except Exception:
        return None
    finally:
        os.unlink(path)


def _ffprobe_duration(data):
    if not shutil.which("ffprobe"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return float(out) if out and out != "N/A" else None
    except Exception:
        return None
    finally:
        os.unlink(path)


def measure(provider, sid, secs=6):
    """How good is this copy of a channel? None if it doesn't really play, else
    {"start": seconds to first data, "height": picture height, "kbps": data rate,
     "keeps_up": True if it delivered at least as much programme as time passed (no buffering)}."""
    url = provider.stream_url(sid)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            data = r.read(16384)
            start = time.time() - t0
            t1 = time.time()
            while time.time() - t1 < secs and len(data) < 30_000_000:
                chunk = r.read(65536)
                if not chunk:
                    break
                data += chunk
            took = max(time.time() - t1, 0.5)
    except Exception:
        return {"start": 99.0, "height": 0, "kbps": 0, "keeps_up": False} if plays(provider, sid) else None  # e.g. only HLS
    if not _is_video(data) or is_filler(data[:SAMPLE]):
        return None
    dur = _ffprobe_duration(data)
    keeps = True if dur is None else dur >= 0.9 * min(took + start, took + 2)
    return {"start": round(start, 2), "height": _ffprobe_height(data[:SAMPLE]) or 0,
            "kbps": int(len(data) * 8 / took / 1000), "keeps_up": keeps}


def score(m, catchup=False):
    """Higher is better: no buffering first, then sharper picture (up to full HD), then catch-up available,
    then quicker start."""
    if not m:
        return (-1, 0, 0, 0)
    return (1 if m.get("keeps_up", True) else 0, min(m["height"], 1080), 1 if catchup else 0, -m["start"])


def logo_mask(data, frames=8):
    """Where the channel's on-screen logo sits: small spots in the top corners that stay the same across frames
    spread over a few seconds, while the programme moves. Two copies of the same channel have their logo in the
    same place. Returns a set of spots, or None if it can't be worked out (e.g. the whole picture is still)."""
    if not shutil.which("ffmpeg"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        W, H = 128, 72
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps=2,scale={W}:{H}", "-frames:v", str(frames),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=40).stdout
        fr = [raw[i:i + W * H] for i in range(0, len(raw) - W * H + 1, W * H)]
        if len(fr) < 3:
            return None
        motion = sum(abs(a - b) for x, y in zip(fr, fr[1:]) for a, b in zip(x, y)) / (len(fr) - 1) / (W * H)
        if motion < 1.0:
            return None
        cells = set()
        for y in range(1, H // 3):
            for x in list(range(1, W // 3)) + list(range(2 * W // 3, W - 1)):
                vals = [f[y * W + x] for f in fr]
                if max(vals) - min(vals) > 10:
                    continue  # changes over time: programme, not logo
                f0 = fr[0]
                grad = max(abs(f0[y * W + x + 1] - f0[y * W + x - 1]), abs(f0[(y + 1) * W + x] - f0[(y - 1) * W + x]))
                if grad > 14:  # an edge: part of a logo or caption, not flat background
                    cells.add((x // 2, y // 2))
        return cells
    except Exception:
        return None
    finally:
        os.unlink(path)


def grab_sample(provider, sid, limit=3_000_000, secs=10):
    try:
        data, _ = _fetch(provider.stream_url(sid), limit, timeout=secs)
        return data if _is_video(data) and not is_filler(data[:SAMPLE]) else None
    except Exception:
        return None


def content_frames(data, fps=2, frames=20, W=24, H=14):
    """Tiny greyscale thumbnails of the picture, a few per second, each with its average brightness removed.
    Two copies of the same live channel show the same pictures, perhaps a few seconds apart."""
    if not shutil.which("ffmpeg"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={W}:{H}", "-frames:v", str(frames),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=60).stdout
        out = []
        for i in range(0, len(raw) - W * H + 1, W * H):
            fr = raw[i:i + W * H]
            m = sum(fr) / len(fr)
            out.append([v - m for v in fr])
        return out or None
    except Exception:
        return None
    finally:
        os.unlink(path)


def content_distance(a, b, min_overlap=3):
    """Smallest average picture difference between two thumbnail sequences over all time offsets
    (0 = identical). Same channel: low; different channels: high. None if there's too little to compare."""
    if not a or not b:
        return None
    best = None
    for k in range(-(len(b) - min_overlap), len(a) - min_overlap + 1):
        pairs = [(a[i], b[i - k]) for i in range(max(k, 0), min(len(a), len(b) + k))]
        if len(pairs) < min_overlap:
            continue
        d = sum(sum(abs(x - y) for x, y in zip(fa, fb)) / len(fa) for fa, fb in pairs) / len(pairs)
        best = d if best is None else min(best, d)
    return best


def record(provider, sid, secs=50, limit=60_000_000):
    """Record about `secs` seconds of a channel (as it arrives). Returns (bytes, wall-clock start)."""
    t0 = time.time()
    try:
        req = urllib.request.Request(provider.stream_url(sid), headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = bytearray()
            while time.time() - t0 < secs + 5 and len(data) < limit:
                chunk = r.read(65536)
                if not chunk:
                    break
                data += chunk
        return bytes(data), t0
    except Exception:
        return None, t0


def scene_cuts(data, fps=5, W=32, H=18):
    """Times (seconds from the start of the recording) where the picture changes abruptly - a cut between shots."""
    if not data or not shutil.which("ffmpeg"):
        return None, 0
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={W}:{H}",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=120).stdout
        fr = [raw[i:i + W * H] for i in range(0, len(raw) - W * H + 1, W * H)]
        diffs = [sum(abs(a - b) for a, b in zip(x, y)) / (W * H) for x, y in zip(fr, fr[1:])]
        if not diffs:
            return None, 0
        med = sorted(diffs)[len(diffs) // 2]
        cuts = [(i + 1) / fps for i, d in enumerate(diffs) if d > max(18, 4 * med)
                and (i == 0 or diffs[i - 1] <= d) and (i + 1 >= len(diffs) or diffs[i + 1] <= d)]
        return cuts, len(fr) / fps
    except Exception:
        return None, 0
    finally:
        os.unlink(path)


def align_cuts(a, b, max_shift=45.0, step=0.2, tol=0.41):
    """Best time shift s so that cuts in a at time t match cuts in b at t - s. Returns (shift, matched, of)."""
    if not a or not b:
        return None
    best = (0, None)
    s = -max_shift
    while s <= max_shift:
        m = sum(1 for t in a if any(abs(t - s - u) <= tol for u in b))
        if m > best[0]:
            best = (m, round(s, 1))
        s += step
    return best[1], best[0], min(len(a), len(b))
