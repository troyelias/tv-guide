"""Merge several XMLTV guides into one, keeping only the channels in channel-ids.txt.

Usage:
  merge.py channel-ids.txt OUTPUT.xml.gz REPORT.md STATS.json [--previous=FILE] [--map=FILE] [--notes=channels.json] name=file[#mapkey] ...
  A source written as name=file#key has its channel IDs renamed using section "key" of the --map file
  (a service's own guide tags -> our guide IDs).

For each channel:
  1. Base listings come from the source with the most real (not "Movie") upcoming titles;
     on a tie the earlier source in the list wins.
  2. Placeholder titles ("Movie", "Filler / Promo"...) are replaced by the real title of an
     overlapping show from another source, when one exists.
  3. Listings are extended past the base source's last show with later shows from other sources.
  4. The last ~26 hours are kept from the previous guide, so catch-up shows real titles.
"""
import copy, gzip, json, re, sys, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone

args = sys.argv[1:]
previous = next((a.split("=", 1)[1] for a in args if a.startswith("--previous=")), None)
mapfile = next((a.split("=", 1)[1] for a in args if a.startswith("--map=")), None)
notesfile = next((a.split("=", 1)[1] for a in args if a.startswith("--notes=")), None)
args = [a for a in args if not a.startswith(("--previous=", "--map=", "--notes="))]
try:
    MAPS = json.load(open(mapfile)) if mapfile else {}
except Exception:
    MAPS = {}
ids_file, out_file, report_file, stats_file, *sources = args

wanted = [l.strip() for l in open(ids_file, encoding="utf-8") if l.strip() and not l.startswith("#")]
wanted_set = set(wanted)
NOW = datetime.now(timezone.utc)
KEEP_PAST = timedelta(hours=26)

PLACEHOLDERS = {"movie", "movies", "filler / promo", "filler", "promo", "to be announced", "tba",
                "no information", "no information available", "program", "programme", "feature film",
                "film", "hindi movie", "english movie"}


def open_any(path):
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def parse_time(s):
    s = s or ""
    try:
        return datetime.strptime(s[:14] + s[15:].replace(" ", ""), "%Y%m%d%H%M%S%z")
    except ValueError:
        return None


def read(path, rename=None):
    """Return {channel_id: channel element}, {channel_id: [(start, stop, programme)]} for wanted IDs."""
    chans, progs = {}, defaultdict(list)
    rename = rename or {}
    try:
        for _, el in ET.iterparse(open_any(path), events=("end",)):
            if rename:
                for attr in ("id", "channel"):
                    if el.get(attr) in rename:
                        el.set(attr, rename[el.get(attr)])
            if el.tag == "channel":
                if el.get("id") in wanted_set:
                    chans[el.get("id")] = el
                else:
                    el.clear()
            elif el.tag == "programme":
                cid = el.get("channel")
                if cid in wanted_set:
                    a, b = parse_time(el.get("start")), parse_time(el.get("stop"))
                    if a and b and b > a:
                        progs[cid].append((a, b, el))
                else:
                    el.clear()
    except (OSError, ET.ParseError, EOFError) as e:
        print(f"  ! could not read {path}: {type(e).__name__}")
    for v in progs.values():
        v.sort(key=lambda t: t[0])
    return chans, progs


def is_placeholder(p):
    return (p.findtext("title") or "").strip().lower() in PLACEHOLDERS


def real_upcoming(items):
    return sum(1 for a, b, p in items if b >= NOW and not is_placeholder(p))


ORDER = ["title", "sub-title", "desc", "credits", "date", "category", "keyword", "language", "orig-language",
         "length", "icon", "url", "country", "episode-num", "video", "audio", "previously-shown", "premiere",
         "last-chance", "new", "subtitles", "rating", "star-rating", "review", "image"]


def reorder(p):
    kids = list(p)
    for k in kids:
        p.remove(k)
    for k in sorted(kids, key=lambda k: ORDER.index(k.tag) if k.tag in ORDER else len(ORDER)):
        p.append(k)


def overlap(a1, b1, a2, b2):
    return max(timedelta(0), min(b1, b2) - max(a1, a2))


loaded = []  # (source name, chans, progs) in priority order
stats = {}
for spec in sources:
    name, path = spec.split("=", 1)
    path, _, mkey = path.partition("#")
    chans, progs = read(path, MAPS.get(mkey) if mkey else None)
    stats[name] = sum(len(v) for v in progs.values())
    print(f"{name}: {len(progs)} channels, {stats[name]} shows")
    loaded.append((name, chans, progs))
prev_chans, prev_progs = read(previous) if previous else ({}, {})
print(f"Previous guide: {len(prev_progs)} channels")

chosen = {}
filled_total = extended_total = past_total = 0
for cid in wanted:
    cands = [(real_upcoming(progs[cid]), -i, name, chans, progs[cid])
             for i, (name, chans, progs) in enumerate(loaded) if progs.get(cid)]
    if not cands:
        continue
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, base_name, base_chans, base = cands[0]
    others = [c[4] for c in cands[1:]]
    items = [(a, b, copy.deepcopy(p)) for a, b, p in base]

    # 2. replace placeholder titles with an overlapping real title from another source
    filled = 0
    for a, b, p in items:
        if not is_placeholder(p):
            continue
        best, best_ov = None, timedelta(0)
        for other in others:
            for a2, b2, p2 in other:
                if a2 >= b:
                    break
                ov = overlap(a, b, a2, b2)
                if ov > best_ov and not is_placeholder(p2):
                    best, best_ov = p2, ov
        if best is not None and best_ov >= (b - a) / 2:
            for tag in ("title", "sub-title", "desc", "category"):
                for old in p.findall(tag):
                    p.remove(old)
            for tag in ("title", "sub-title", "desc", "category"):
                for new in best.findall(tag):
                    p.insert(0 if tag == "title" else len(p), copy.deepcopy(new))
            if p.find("icon") is None and best.find("icon") is not None:
                p.append(copy.deepcopy(best.find("icon")))
            reorder(p)
            filled += 1

    # 3. extend past the base source's last show
    extended = 0
    end = items[-1][1]
    for other in others:
        for a2, b2, p2 in other:
            if a2 >= end - timedelta(minutes=1) and b2 > end:
                p2 = copy.deepcopy(p2)
                if a2 < end:
                    p2.set("start", end.strftime("%Y%m%d%H%M%S %z"))
                    a2 = end
                items.append((a2, b2, p2))
                end = b2
                extended += 1

    # 4. keep the recent past from the previous guide (for catch-up)
    past = 0
    first = items[0][0]
    for a, b, p in prev_progs.get(cid, []):
        if b <= first and b >= NOW - KEEP_PAST:
            items.append((a, b, p))
            past += 1

    items = [t for t in items if t[1] >= NOW - KEEP_PAST]
    items.sort(key=lambda t: t[0])
    if not items:
        continue

    ch = base_chans.get(cid)
    if ch is None:
        ch = next((c[cid] for _, c, _ in loaded if cid in c), prev_chans.get(cid))
    if ch is None:
        ch = ET.Element("channel", id=cid)
        ET.SubElement(ch, "display-name").text = cid
    for dn in ch.findall("display-name"):  # tidy provider-style names like "IN EU ▎ZEE TV SD ◉ rec"
        if dn.text and "▎" in dn.text:
            dn.text = re.sub(r"\s*◉\s*rec\b", "", dn.text.split("▎")[-1]).strip()
    chosen[cid] = (base_name, ch, items, filled, extended, past)
    filled_total += filled
    extended_total += extended
    past_total += past

# Channels that played on neither service at the last check-up: show a note instead of listings
# from now until the next check-up has finished (7:00 AM India time = 01:30 UTC).
try:
    DOWN_GIDS = set(json.load(open(notesfile)).get("down_guide_ids", [])) if notesfile else set()
except Exception:
    DOWN_GIDS = set()
noted = 0
for gid in DOWN_GIDS & wanted_set:
    start = NOW.replace(minute=0, second=0, microsecond=0)
    until = NOW.replace(hour=1, minute=30, second=0, microsecond=0)
    while until <= NOW + timedelta(hours=1):
        until += timedelta(days=1)
    if gid in chosen:  # start the note when the show that's on now started, so there's no gap
        start = min([start] + [a for a, b, _ in chosen[gid][2] if a < start < b])
    note = ET.Element("programme", start=start.strftime("%Y%m%d%H%M%S +0000"), stop=until.strftime("%Y%m%d%H%M%S +0000"), channel=gid)
    ET.SubElement(note, "title", lang="en").text = "\u26a0 Not available right now"
    ET.SubElement(note, "desc", lang="en").text = ("This channel didn't play on either service at the morning check-up. "
                                                   "Try the Backup folder, or check again after the next check-up (4-7 AM India time).")
    if gid in chosen:
        base_name, ch_el, items, *rest = chosen[gid]
        items = [t for t in items if t[1] <= start or t[0] >= until] + [(start, until, note)]
        items.sort(key=lambda t: t[0])
        chosen[gid] = (base_name, ch_el, items, *rest)
    else:
        ch_el = ET.Element("channel", id=gid)
        ET.SubElement(ch_el, "display-name").text = gid
        chosen[gid] = ("Note", ch_el, [(start, until, note)], 0, 0, 0)
    noted += 1
if noted:
    print(f"Added 'not available' notes for {noted} channels")

total = sum(len(v[2]) for v in chosen.values())
if total == 0:
    sys.exit("No listings from any source - not publishing.")

with gzip.open(out_file, "wb") as out:
    out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="tv-guide">\n')
    for cid in wanted:
        if cid in chosen:
            chosen[cid][1].tail = None
            out.write(ET.tostring(chosen[cid][1], encoding="utf-8").strip() + b"\n")
    for cid in wanted:
        if cid in chosen:
            for a, b, p in chosen[cid][2]:
                p.tail = None
                out.write(ET.tostring(p, encoding="utf-8").strip() + b"\n")
    out.write(b"</tv>\n")

# ---- report + stats ----
by_src = defaultdict(int)
for v in chosen.values():
    by_src[v[0]] += 1
missing = [c for c in wanted if c not in chosen]
placeholders_left = sum(1 for v in chosen.values() for a, b, p in v[2] if b >= NOW and is_placeholder(p))
ahead = sorted((max(b for a, b, p in v[2]) - NOW).total_seconds() / 3600 for v in chosen.values())
median_ahead = ahead[len(ahead) // 2] if ahead else 0

r = ["# Guide report", f"Built {NOW:%Y-%m-%d %H:%M} UTC\n",
     f"**{len(chosen)} of {len(wanted)} channels have listings** ({total} shows).",
     f"Placeholder titles replaced: {filled_total} (still left: {placeholders_left}) · "
     f"shows added past the main source: {extended_total} · past shows kept for catch-up: {past_total} · "
     f"typical look-ahead: {median_ahead:.0f} h\n",
     "| Source | Channels it supplied |", "|---|---|"]
r += [f"| {n} | {by_src.get(n, 0)} |" for n in stats]
r += ["", "## Channels", "| Guide ID | Name | Main source | Shows | Listings until (UTC) |", "|---|---|---|---|---|"]
for cid in wanted:
    if cid in chosen:
        src, ch, items = chosen[cid][:3]
        last = max(b for a, b, p in items)
        until = f"{last:%a %d %b %H:%M}" + (" ⚠️ old" if last < NOW else "")
        nm = (ch.findtext("display-name") or "").replace("|", "/")
        r.append(f"| `{cid}` | {nm} | {src} | {len(items)} | {until} |")
if missing:
    r += ["", f"## No listings ({len(missing)})", ", ".join(f"`{c}`" for c in missing)]
open(report_file, "w", encoding="utf-8").write("\n".join(r) + "\n")

json.dump({"built_utc": NOW.strftime("%Y-%m-%d %H:%M"), "channels_total": len(wanted),
           "channels_with_listings": len(chosen), "shows": total, "placeholders_filled": filled_total,
           "placeholders_left": placeholders_left, "lookahead_hours_median": round(median_ahead),
           "sources": {n: by_src.get(n, 0) for n in stats}, "missing": len(missing)},
          open(stats_file, "w"), indent=1)
print(f"Wrote {len(chosen)}/{len(wanted)} channels, {total} shows; filled {filled_total}, "
      f"extended {extended_total}, past {past_total}")
