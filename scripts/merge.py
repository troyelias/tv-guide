"""Merge several XMLTV guides into one, keeping only the channels in channel-ids.txt.

Usage: merge.py channel-ids.txt OUTPUT.xml.gz REPORT.md  name=file [name=file ...]
For each channel, listings come from the source with the most real (not "Movie")
upcoming titles; on a tie the earlier source wins. A show never appears twice.
"""
import gzip, re, sys, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

ids_file, out_file, report_file, *sources = sys.argv[1:]
wanted = [l.strip() for l in open(ids_file, encoding="utf-8") if l.strip() and not l.startswith("#")]
wanted_set = set(wanted)


def open_any(path):
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def read(path):
    """Return {channel_id: channel element}, {channel_id: [programme elements]} for wanted IDs."""
    chans, progs = {}, defaultdict(list)
    try:
        for _, el in ET.iterparse(open_any(path), events=("end",)):
            if el.tag == "channel":
                if el.get("id") in wanted_set:
                    chans[el.get("id")] = el
                else:
                    el.clear()
            elif el.tag == "programme":
                if el.get("channel") in wanted_set:
                    progs[el.get("channel")].append(el)
                else:
                    el.clear()
    except (OSError, ET.ParseError, EOFError) as e:
        print(f"  ! could not read {path}: {e}")
    return chans, progs


def stop_time(p):
    s = p.get("stop", "")
    try:
        return datetime.strptime(s[:14] + s[15:].replace(" ", ""), "%Y%m%d%H%M%S%z")
    except ValueError:
        return None


PLACEHOLDERS = {"movie", "movies", "filler / promo", "filler", "promo", "to be announced", "tba",
                "no information", "no information available", "program", "programme"}


def real_shows(progs):
    """Shows with a real title that haven't finished yet."""
    now = datetime.now(timezone.utc)
    return sum(1 for p in progs
               if (p.findtext("title") or "").strip().lower() not in PLACEHOLDERS
               and (stop_time(p) or now) >= now)


loaded = []  # (source name, chans, progs) in priority order
stats = {}
for spec in sources:
    name, path = spec.split("=", 1)
    chans, progs = read(path)
    stats[name] = sum(len(v) for v in progs.values())
    print(f"{name}: {len(progs)} of my channels, {stats[name]} shows")
    loaded.append((name, chans, progs))

# For each channel use the source with the most real (non-placeholder) upcoming shows;
# on a tie, the earlier source in the list wins.
chosen = {}  # id -> (source name, channel el, programmes)
for cid in wanted:
    best, best_score = None, -1
    for name, chans, progs in loaded:
        if progs.get(cid):
            score = real_shows(progs[cid])
            if score > best_score:
                best, best_score = (name, chans, progs), score
    if best:
        name, chans, progs = best
        ch = chans.get(cid)
        if ch is None:  # take the channel name/logo from any source that has it
            ch = next((c[cid] for _, c, _ in loaded if cid in c), None)
        if ch is None:
            ch = ET.Element("channel", id=cid)
            ET.SubElement(ch, "display-name").text = cid
        for dn in ch.findall("display-name"):  # tidy provider-style names like "IN EU ▎ZEE TV SD ◉ rec"
            if dn.text:
                dn.text = re.sub(r"\s*◉\s*rec\b", "", dn.text.split("▎")[-1]).strip() if "▎" in dn.text else dn.text
        chosen[cid] = (name, ch, progs[cid])

total =sum(len(v[2]) for v in chosen.values())
if total == 0:
    sys.exit("No listings from any source - not publishing.")

with gzip.open(out_file, "wb") as out:
    out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="my-epg">\n')
    for cid in wanted:
        if cid in chosen:
            out.write(ET.tostring(chosen[cid][1], encoding="utf-8") .strip() + b"\n")
    for cid in wanted:
        if cid in chosen:
            for p in sorted(chosen[cid][2], key=lambda p: p.get("start", "")):
                p.tail = None
                out.write(ET.tostring(p, encoding="utf-8").strip() + b"\n")
    out.write(b"</tv>\n")

# ---- report ----
now = datetime.now(timezone.utc)
by_src = defaultdict(int)
for v in chosen.values():
    by_src[v[0]] += 1
missing = [c for c in wanted if c not in chosen]
r = ["# Guide report", f"Built {now:%Y-%m-%d %H:%M} UTC\n",
     f"**{len(chosen)} of {len(wanted)} channels have listings** ({total} shows).\n",
     "| Source | Channels it supplied |", "|---|---|"]
r += [f"| {n} | {by_src.get(n, 0)} |" for n in stats]
r += ["", "## Channels", "| Guide ID | Name | From | Shows | Listings until (UTC) |", "|---|---|---|---|---|"]
for cid in wanted:
    if cid in chosen:
        src, ch, ps = chosen[cid]
        stops = [t for t in map(stop_time, ps) if t]
        until = f"{max(stops).astimezone(timezone.utc):%a %d %b %H:%M}" if stops else "?"
        if stops and max(stops) < now:
            until += " ⚠️ old"
        nm = (ch.findtext("display-name") or "").replace("|", "/")
        r.append(f"| `{cid}` | {nm} | {src} | {len(ps)} | {until} |")
if missing:
    r += ["", f"## No listings ({len(missing)})", ", ".join(f"`{c}`" for c in missing)]
open(report_file, "w", encoding="utf-8").write("\n".join(r) + "\n")
print(f"Wrote {len(chosen)}/{len(wanted)} channels, {total} shows")
