"""Replace the playlist file in Dropbox (same file, so the share link keeps working).
Needs env DBX_KEY, DBX_SECRET, DBX_PATH and the encrypted long-lived key in secure/dropbox.enc."""
import base64, json, os, subprocess, urllib.parse, urllib.request


def upload(text):
    key, sec, path = (os.environ.get(k, "").strip() for k in ("DBX_KEY", "DBX_SECRET", "DBX_PATH"))
    refresh = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:DBX_SECRET"],
                             input=open("secure/dropbox.enc", "rb").read(), capture_output=True, check=True).stdout.decode()
    req = urllib.request.Request("https://api.dropboxapi.com/oauth2/token",
                                 data=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode())
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{key}:{sec}".encode()).decode())
    token = json.load(urllib.request.urlopen(req, timeout=60))["access_token"]
    up = urllib.request.Request("https://content.dropboxapi.com/2/files/upload", data=text.encode("utf-8"))
    up.add_header("Authorization", f"Bearer {token}")
    up.add_header("Content-Type", "application/octet-stream")
    up.add_header("Dropbox-API-Arg", json.dumps({"path": path, "mode": "overwrite", "mute": True}))
    urllib.request.urlopen(up, timeout=120).read()


def crypt(data, decrypt=False, passenv="DBX_SECRET"):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", f"env:{passenv}"]
    if decrypt:
        args.append("-d")
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout
