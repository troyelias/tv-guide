"""Keep the TV playlist in step with the subscription's channel lineup and upload it to Dropbox.

Usage: playlist.py TEMPLATE.enc LINEUP.json GUIDE.xml.gz SUMMARY.json
Secrets (env): PROVIDER_XMLTV_URL (login; also the template's encryption key),
               DBX_KEY, DBX_SECRET, DBX_PATH (optional - without them nothing is uploaded).

The template (your curated playlist: folders, names, logos, guide IDs) is stored ENCRYPTED.
Each run:
  - channels whose stream number changed are re-pointed (matched by name),
  - channels that disappeared are dropped,
  - new channels in the groups you use are added to a "New Channels" folder, with a guide ID
    and logo when one can be matched,
  - if anything changed (or it was never uploaded), the playlist replaces the file in Dropbox
    (same file, so the share link keeps working).
"""
import base64, gzip, hashlib, json, os, re, subprocess, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

tpl_file, lineup_file, guide_file, summary_file = sys.argv[1:5]
secret = os.environ.get("PROVIDER_XMLTV_URL", "").strip()
summary = {"ran": False}


def done(**kw):
    summary.update(kw)
    json.dump(summary, open(summary_file, "w"))
    sys.exit(0)


def crypt(data, passenv, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", f"env:{passenv}"]
    if decrypt:
        args.append("-d")
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


if not secret or not os.path.exists(tpl_file) or not os.path.exists(lineup_file):
    done(note="not set up")
try:
    tpl = json.loads(crypt(open(tpl_file, "rb").read(), "PROVIDER_XMLTV_URL", decrypt=True))
except Exception:
    done(note="template can't be opened with the current subscription secret")

u = urllib.parse.urlparse(secret)
q = urllib.parse.parse_qs(u.query)
base = f"{u.scheme}://{u.netloc}/live/{q['username'][0]}/{q['password'][0]}/"
lineup = json.load(open(lineup_file, encoding="utf-8"))
by_sid = {s["stream_id"]: s for s in lineup}


def clean(name):
    name = name.split("▎")[-1] if "▎" in name else name
    return re.sub(r"\s*◉\s*rec\b", "", name).strip()


def norm(s, strip_quality=False):
    s = s.upper().replace("&AMP;", "&").replace("&", " AND ")
    if strip_quality:
        s = re.sub(r"\b(FHD|UHD|HD|SD|4K)\b", " ", s)
    return re.sub(r"[^A-Z0-9]", "", s)


# guide channel names -> guide IDs, for matching new channels
names = {}
try:
    for _, el in ET.iterparse(gzip.open(guide_file), events=("end",)):
        if el.tag == "channel":
            for dn in el.findall("display-name"):
                if dn.text:
                    names.setdefault(norm(dn.text), el.get("id"))
                    names.setdefault(norm(dn.text, True), el.get("id"))
        elif el.tag == "programme":
            break
except Exception:
    pass

entries, added, removed, renumbered = [], 0, 0, 0
used = {e["sid"] for e in tpl["entries"]}
for e in tpl["entries"]:
    if e["sid"] in by_sid:
        entries.append(e)
        continue
    title = e["extinf"].rsplit(",", 1)[1]
    old_name = e.get("pname") or title
    match = next((s for s in lineup if s["stream_id"] not in used and clean(s["name"]) == clean(old_name)), None)
    if match:
        used.add(match["stream_id"])
        entries.append({**e, "sid": match["stream_id"]})
        renumbered += 1
    else:
        removed += 1

cats = set(tpl.get("cats", []))
seen = set(tpl.get("seen", []))
for s in lineup:
    sid = s["stream_id"]
    if sid in seen or sid in used or s.get("category_id") not in cats:
        continue
    nm = clean(s["name"])
    gid = names.get(norm(nm)) or names.get(norm(nm, True)) or (s.get("epg_channel_id") or "")
    attrs = f'tvg-id="{gid}" tvg-name="{nm}" tvg-logo="{s.get("stream_icon") or ""}" group-title="{tpl.get("new_group", "New Channels")}"'
    if str(s.get("tv_archive")) == "1":
        attrs += f' catchup="xc" catchup-days="{s.get("tv_archive_duration") or 1}"'
    entries.append({"extinf": f"#EXTINF:-1 {attrs},{nm}", "sid": sid, "ext": ".ts",
                    "cat": s.get("category_id"), "pname": s["name"]})
    added += 1

text = tpl["header"] + "\n" + "\n".join(f'{e["extinf"]}\n{base}{e["sid"]}{e.get("ext", ".ts")}' for e in entries) + "\n"
digest = hashlib.sha256(text.encode()).hexdigest()
if len(sys.argv) > 5:  # local testing only
    open(sys.argv[5], "w", encoding="utf-8").write(text)
tpl.update(entries=entries, seen=sorted(by_sid))
summary.update(ran=True, channels=len(entries), added=added, removed=removed, renumbered=renumbered)

need_upload = digest != tpl.get("uploaded_hash")
dkey, dsec, dpath = (os.environ.get(k, "").strip() for k in ("DBX_KEY", "DBX_SECRET", "DBX_PATH"))
if need_upload and dkey and dsec and dpath and os.path.exists("secure/dropbox.enc"):
    try:
        refresh = crypt(open("secure/dropbox.enc", "rb").read(), "DBX_SECRET", decrypt=True).decode()
        req = urllib.request.Request("https://api.dropboxapi.com/oauth2/token",
                                     data=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode())
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{dkey}:{dsec}".encode()).decode())
        token = json.load(urllib.request.urlopen(req, timeout=60))["access_token"]
        up = urllib.request.Request("https://content.dropboxapi.com/2/files/upload", data=text.encode("utf-8"))
        up.add_header("Authorization", f"Bearer {token}")
        up.add_header("Content-Type", "application/octet-stream")
        up.add_header("Dropbox-API-Arg", json.dumps({"path": dpath, "mode": "overwrite", "mute": True}))
        urllib.request.urlopen(up, timeout=120).read()
        tpl["uploaded_hash"] = digest
        summary["uploaded"] = True
    except Exception as e:
        summary["upload_error"] = type(e).__name__
elif need_upload:
    summary["waiting_for_dropbox"] = True

open(tpl_file, "wb").write(crypt(json.dumps(tpl, ensure_ascii=False).encode(), "PROVIDER_XMLTV_URL"))
print(f"Playlist: {len(entries)} channels; +{added} -{removed} renumbered {renumbered}; "
      f"{'uploaded' if summary.get('uploaded') else 'no upload'}")
done()
