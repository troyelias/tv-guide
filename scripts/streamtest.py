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


def measure(provider, sid):
    """How good is this copy of a channel? Returns None if it doesn't really play, else
    {"start": seconds to first picture data, "height": picture height (e.g. 1080), "kbps": rough data rate}."""
    url = provider.stream_url(sid)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            first = r.read(16384)
            start = time.time() - t0
            data = first
            while len(data) < SAMPLE and time.time() - t0 < 12:
                chunk = r.read(65536)
                if not chunk:
                    break
                data += chunk
        took = max(time.time() - t0, 0.5)
    except Exception:
        return {"start": 99.0, "height": 0, "kbps": 0} if plays(provider, sid) else None  # e.g. only HLS works
    if not _is_video(data) or is_filler(data):
        return None
    return {"start": round(start, 2), "height": _ffprobe_height(data) or 0, "kbps": int(len(data) * 8 / took / 1000)}


def score(m):
    """Higher is better: sharper picture first (up to full HD), then quicker start."""
    return (min(m["height"], 1080), -m["start"]) if m else (-1, 0)


def logo_mask(data, frames=6):
    """Where the channel's on-screen logo sits: pixels in the top corners that stay the same across frames
    while the programme moves. Two copies of the same channel have their logo in the same place.
    Returns a set of (x, y) cells, or None if it can't be worked out."""
    if not shutil.which("ffmpeg"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        W, H = 96, 54
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps=3,scale={W}:{H}", "-frames:v", str(frames),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=40).stdout
        fr = [raw[i:i + W * H] for i in range(0, len(raw) - W * H + 1, W * H)]
        if len(fr) < 3:
            return None
        motion = sum(abs(a - b) for x, y in zip(fr, fr[1:]) for a, b in zip(x, y)) / (len(fr) - 1) / (W * H)
        if motion < 1.0:
            return None  # the whole picture is still: can't separate a logo from the programme
        cells = set()
        for y in range(0, H // 3):
            for x in list(range(0, W // 3)) + list(range(2 * W // 3, W)):
                vals = [f[y * W + x] for f in fr]
                mean = sum(vals) / len(vals)
                if max(vals) - min(vals) <= 6:  # unchanged across frames
                    nb = [fr[0][yy * W + xx] for yy, xx in ((y, max(x - 2, 0)), (y, min(x + 2, W - 1)), (max(y - 2, 0), x), (min(y + 2, H - 1), x))]
                    if max(abs(mean - v) for v in nb) > 25:  # an edge, i.e. part of a logo rather than flat background
                        cells.add((x // 3, y // 3))
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
