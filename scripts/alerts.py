"""Open / close GitHub issues as discreet alerts (they reach you as GitHub notifications).

Usage: alerts.py STATS.json CHANNELS.json
Wording never names a service or account.
"""
import json, os, sys, urllib.request


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


stats, ch = load(sys.argv[1]), load(sys.argv[2])
cat = load("catalog.json") or []
must = {c["name"] for c in cat if c.get("must")}
alerts = {}

for who, label in (("main_days_left", "main"), ("backup_days_left", "backup")):
    d = ch.get(who)
    if isinstance(d, (int, float)) and d <= 7:
        alerts[f"Renewal due soon ({label} service)"] = (f"The {label} TV subscription ends in about **{max(d, 0):.0f} days**. "
                                                         "Renew it to keep its channels working.")
if ch.get("outage"):
    alerts["Main service down - using backup"] = ("The main service stopped logging in, so every channel was switched to the "
                                                 "backup service. It will switch back automatically during the 4-7 AM (India) "
                                                 "check-up once the main service works again.")
total, have = stats.get("channels_total", 0), stats.get("channels_with_listings", 0)
if total and have < 0.75 * total:
    alerts["Guide coverage dropped"] = f"Only **{have} of {total}** guide channels got listings in the last build."
if ch.get("upload_error"):
    alerts["Playlist upload failing"] = "The updated playlist couldn't be uploaded to Dropbox. The Dropbox connection may need setting up again."
bad = sorted(set(ch.get("down_on_both", [])) & must)
if bad:
    alerts["Must-have channel down"] = ("These must-have channels didn't play on either service at the last check-up: "
                                        + ", ".join(bad) + ".")
nm = ch.get("newly_missing", {})
lost = {p: v for p, v in nm.items() if v}
if lost:
    alerts["Channels no longer found on a service"] = ("These channels were found last time but not at the latest check-up "
        "(the service may have renamed or dropped them - ask Claude to add a matching rule): "
        + "; ".join(f"{'main' if p == 'main' else 'backup'} service: {', '.join(v)}" for p, v in lost.items()) + ".")
if ch.get("missed_hours"):
    alerts["A check-up was missed"] = (f"The previous check-up was {ch['missed_hours']} hours earlier instead of about 24. "
                                       "GitHub may have skipped a scheduled run.")
if ch.get("hold"):
    alerts["Playlist updates paused (undo used)"] = ("The previous playlist was restored and automatic updates are paused. "
                                                    "Run 'Playlist undo' with 'resume' when you want them back on.")
KNOWN = ["Channels no longer found on a service", "A check-up was missed", "Playlist updates paused (undo used)",
         "Renewal due soon (main service)", "Renewal due soon (backup service)", "Main service down - using backup",
         "Guide coverage dropped", "Playlist upload failing", "Must-have channel down"]

repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
if not (repo and token):
    print("Alerts:", list(alerts) or "none")
    sys.exit(0)


def api(method, path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


open_issues = {i["title"]: i for i in api("GET", "/issues?state=open&per_page=100") if "pull_request" not in i}
owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
for title in KNOWN:
    if title in alerts and title not in open_issues:
        api("POST", "/issues", {"title": title, "body": alerts[title], **({"assignees": [owner]} if owner else {})})
        print("Opened alert:", title)
    elif title in alerts:
        api("PATCH", f"/issues/{open_issues[title]['number']}", {"body": alerts[title]})
    elif title in open_issues:
        api("POST", f"/issues/{open_issues[title]['number']}/comments", {"body": "Resolved - closing."})
        api("PATCH", f"/issues/{open_issues[title]['number']}", {"state": "closed"})
        print("Closed alert:", title)
print("Active alerts:", list(alerts) or "none")
