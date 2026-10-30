"""Shared helpers: turn any provider's channel names into a neutral key, region and quality rank,
and match a provider's lineup to the channel catalog."""
import re

TAG_CHARS = r"[ʰ-˿ᴀ-᷿⁰-₟ⱼ-Ɀ]"  # tiny superscript tags like ᴴᴰ ᴿᴬᵂ ⁴ᵏ
PREFIX = r"^\s*\(?[A-Z0-9][A-Z0-9 \-]{0,11}\)?\s*(\|\||\||:|▎)\s*"


def clean_name(name):
    """Readable name without provider prefixes/tags: 'IN EU ▎ZEE TV SD ◉ rec' -> 'ZEE TV SD'."""
    s = name.replace("\\/", "/")
    s = s.split("▎")[-1] if "▎" in s else re.sub(PREFIX, "", s, flags=re.I)
    s = re.sub(TAG_CHARS, "", s)
    s = re.sub(r"◉|\brec\b", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -.")


def key(name):
    """Neutral matching key: 'IN: STAR PLUS FHD' / 'HINDI ▎STAR PLUS HD' / 'Star Plus HD' -> 'STARPLUS'."""
    s = clean_name(name).upper()
    s = re.sub(r"\((?:FHD|HD|SD|4K|UHD|HEVC|RAW|BK|NA|EU|USA|US|UK|LK)\)", " ", s)
    s = s.replace("&AMP;", "&").replace("&", " AND ").replace("+", " PLUS ")
    s = re.sub(r"\bTV ?(\d+)\b", r"TV\1", s)  # keep 'TV18', 'TV9', 'TV5' as part of the name
    s = re.sub(r"\b(FHD|UHD|HD|SD|4K|8K|HEVC|RAW|BK|NA|EU|USA|US|UK|CANADA|TV|CHANNEL|NEW|LIVE)\b", " ", s)
    s = re.sub(r"_", " ", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def region(name, category=""):
    """Rough feed region: 'pk', 'uk' (UK/Europe feeds), 'us' (North America), or 'in'."""
    n = name.upper().replace("▎", "|")
    n = re.sub(r"UP ?/? ?UK\b|UTTARAKHAND", "UP UTTARAKHAND", n)  # 'UK' here means Uttarakhand, not Britain
    c = (category or "").upper()
    head = re.split(r"\||:", n, maxsplit=1)[0] if re.search(r"\||:", n[:14]) else ""
    if re.search(r"\bPK\b|PAKISTAN", head) or re.search(r"PAKISTAN", c):
        if re.search(r"\bEU\b|\bUK\b", head) or re.search(r"\bEU\b|\(EU\)|\bUK\b|EUROPE", n):
            return "uk"
        return "us" if re.search(r"\(NA\)", n) else "pk"
    if re.search(r"\b(US|USA|CA)\b", head) or re.search(r"\bUSA\b|\(USA\)|\(NA\)|\bCANADA\b", n) or re.search(r"^(US|CA)\|", c):
        return "us"
    if re.search(r"EUROPE", n):
        return "uk"
    if re.search(r"\b(UK|EU)\b", head) or re.search(r"\bUK\b|INDIA EU|ASIA\| ?UK|UK HEVC", c) or re.search(r"\bUK\b", n):
        return "uk"
    return "in"


def quality(name):
    """Lower is better: HD/FHD first, then plain/RAW, SD/backup copies, then heavy HEVC/4K."""
    n = name.upper()
    if re.search(r"HEVC|ʰᵉᵛᶜ", n):
        return 4
    if re.search(r"\b4K\b|\b8K\b|⁴ᵏ|⁸ᴷ", n):
        return 3
    if re.search(r"\(BK\)|⁽ᴮᴷ⁾", n):
        return 2
    if re.search(r"\bFHD\b|\bHD\b|ᶠᴴᴰ|ᴴᴰ", n):
        return 0
    if re.search(r"\bSD\b", n):
        return 2
    return 1


LANGS = ["TAMIL", "TELUGU", "TELEGU", "MALAYALAM", "BANGLA", "BENGALI", "BANGAL", "MARATHI", "GUJARATI", "GUJRATI",
         "PUNJABI", "KANNADA", "ODIA", "ORIYA", "BHOJPURI", "ASSAM", "NEPAL", "SINHALA"]
NO_LANG_CHECK = {"Regional & DD", "Devotional", "Pakistan", "Pakistan UK Feeds", "India UK Feeds"}


def langs(text):
    t = text.upper()
    return {l for l in LANGS if re.search(r"\b" + l, t)}


def match_lineup(catalog, streams, categories):
    """Return {catalog_id: [stream ids, best first]} and a list of unmatched-looking stream names.

    A stream matches a catalog entry when its region is allowed for that entry and its key equals
    one of the entry's keys. The provider's guide tag is used as a tie-breaker."""
    cat_name = {c.get("category_id"): c.get("category_name", "") for c in categories}
    by_key = {}
    for ch in catalog:
        for k in ch["keys"]:
            by_key.setdefault(k, []).append(ch)
    found = {}
    for s in streams:
        nm = s.get("name") or ""
        if nm.strip().startswith("#"):
            continue
        cname = cat_name.get(s.get("category_id"), "")
        k, rg = key(nm), region(nm, cname)
        head = re.split(r"\||:|▎", nm, maxsplit=1)[0] if re.search(r"\||:|▎", nm[:14]) else ""
        slang = langs(head + " " + cname + " " + nm)
        for ch in by_key.get(k, []):
            if ch["folder"] not in NO_LANG_CHECK:
                ok_langs = langs(ch["name"]) | ({"KANNADA"} if ch["folder"] == "Kannada" else set())
                if slang - ok_langs:
                    continue  # e.g. a Tamil or Bangladeshi channel with the same name
            allowed = ch["regions"] + (["uk"] if ch["regions"] == ["in"] else [])  # UK copy of an Indian channel = last resort
            if rg in allowed:
                tag_bonus = 0 if (s.get("epg_channel_id") or "").lower().split("@")[0] in ch.get("tags", []) else 1
                found.setdefault(ch["id"], []).append((allowed.index(rg), quality(nm), tag_bonus, s["stream_id"]))
    return {cid: [sid for *_, sid in sorted(v)] for cid, v in found.items()}
