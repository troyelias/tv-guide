"""Small helpers to talk to a provider (Xtream-style login link) and test-play a stream for a few seconds."""
import json, time, urllib.parse, urllib.request

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


def plays(provider, sid):
    """True if the stream delivers real video within ~12 seconds (tries .ts, then HLS)."""
    try:
        data, _ = _fetch(provider.stream_url(sid), 188 * 400)
        if _is_video(data):
            return True
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
                data, _ = _fetch(urllib.parse.urljoin(final, seg), 188 * 400)
                return _is_video(data)
    except Exception:
        pass
    return False
