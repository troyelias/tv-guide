"""Leak check: before anything is published (public guide branch, status page, report on main),
make sure no login detail or private location appears in it.

Usage: leakcheck.py PATH [PATH ...]      (files or folders; .gz files are checked unpacked)
Reads the private values from the environment (PRO, PRO_2, DBX_KEY, DBX_SECRET, DBX_PATH, TRIAL) and looks for:
the whole value, the server address, the server name, the username and the password.
Exit code 1 (and the names of the offending files - never the values) if anything is found.
"""
import gzip, os, re, sys, urllib.parse

needles = set()      # matched anywhere (whole links, server addresses)
in_links = set()     # usernames/passwords: matched only inside a link (after / or =), so a common word can't trip it
for var in ("PRO", "PRO_2", "DBX_KEY", "DBX_SECRET", "DBX_PATH", "TRIAL"):
    v = (os.environ.get(var) or "").strip()
    if not v:
        continue
    needles.add(v)
    u = urllib.parse.urlparse(v)
    if u.netloc:
        needles.add(u.netloc)
        if u.hostname:
            needles.add(u.hostname)
        q = urllib.parse.parse_qs(u.query)
        for k in ("username", "password"):
            in_links.update(x.strip() for x in q.get(k, []))
        parts = [x for x in u.path.split("/") if x]  # /live/USER/PASS/... style links
        if len(parts) >= 3 and parts[0] in ("live", "movie", "series"):
            in_links.update(parts[1:3])
needles = {n for n in needles if len(n) >= 5}  # very short values would match ordinary text
in_links = {n for n in in_links if len(n) >= 3}
link_res = [re.compile(rb"[/=]" + re.escape(n.encode()) + rb"(?=[/&\"'\s<?]|$)") for n in in_links]
FIX = "--fix" in sys.argv
if not needles and not in_links:
    print("Leak check: no private values available here - skipped")
    sys.exit(0)

hits, fixed = [], []


def found(data):
    low = data.lower()
    return any(n.encode() in data or n.lower().encode() in low for n in needles) or any(r.search(data) for r in link_res)


def scrub(data):
    """Guide files only: drop picture links / attributes that point at a service's own server."""
    def bad(v):
        return found(v)
    data = re.sub(rb"<icon\b[^>]*/>", lambda m: b"" if bad(m.group(0)) else m.group(0), data)
    data = re.sub(rb'\s[\w:-]+="[^"]*"', lambda m: b"" if bad(m.group(0)) else m.group(0), data)
    return data


def scan(path):
    try:
        raw = open(path, "rb").read()
        data = gzip.decompress(raw) if path.endswith(".gz") else raw
    except Exception:
        return
    if not found(data):
        return
    if FIX and (path.endswith(".xml") or path.endswith(".xml.gz")):
        clean = scrub(data)
        if not found(clean):
            open(path, "wb").write(gzip.compress(clean) if path.endswith(".gz") else clean)
            fixed.append(path)
            return
    hits.append(path)


for target in [a for a in sys.argv[1:] if a != "--fix"]:
    if os.path.isdir(target):
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                if not f.endswith(".enc"):  # encrypted files are meant to be unreadable
                    scan(os.path.join(root, f))
    elif os.path.exists(target):
        scan(target)

if hits:
    print("::error::Leak check FAILED - private details found in: " + ", ".join(sorted(set(hits))) + ". Nothing was published.")
    sys.exit(1)
if fixed:
    print("Leak check: removed service picture links from " + ", ".join(fixed))
print(f"Leak check passed ({len(needles) + len(in_links)} private values checked).")
