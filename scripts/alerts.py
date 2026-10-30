"""Open / close GitHub issues as discreet alerts (you get them as GitHub notifications).

Usage: alerts.py STATS.json STATUS.json CHANGES.json PLAYLIST.json
Wording never names the service or account.
"""
import json, os, sys, urllib.request


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


stats, status, changes, pl = (load(p) for p in sys.argv[1:5])
alerts = {}  # title -> body, for conditions that are currently true

days = status.get("days_left")
if isinstance(days, (int, float)) and days <= 7:
    alerts["Renewal due soon"] = (f"The TV subscription ends in about **{max(days, 0):.0f} days**. "
                                  "Renew it to keep the channels and guide working.")
if status and (status.get("error") or status.get("auth") != 1 or str(status.get("status", "")).lower() != "active"):
    alerts["Subscription check failing"] = ("The subscription didn't log in normally on the last check "
                                            f"(state: {status.get('status') or status.get('error') or 'unknown'}). "
                                            "It may have expired, been suspended, or the server may be down.")
total, have = stats.get("channels_total", 0), stats.get("channels_with_listings", 0)
if total and have < 0.75 * total:
    alerts["Guide coverage dropped"] = f"Only **{have} of {total}** channels got listings in the last build."
if pl.get("upload_error"):
    alerts["Playlist upload failing"] = "The updated playlist couldn't be uploaded to Dropbox. The Dropbox connection may need setting up again."
elif pl.get("waiting_for_dropbox") and (pl.get("added") or pl.get("removed") or pl.get("renumbered")):
    alerts["Playlist changes waiting"] = (f"The channel lineup changed (+{pl.get('added', 0)} / -{pl.get('removed', 0)} / "
                                          f"{pl.get('renumbered', 0)} renumbered) but Dropbox isn't connected, so the playlist wasn't updated.")
KNOWN = ["Renewal due soon", "Subscription check failing", "Guide coverage dropped",
         "Playlist upload failing", "Playlist changes waiting"]

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
for title in KNOWN:
    if title in alerts and title not in open_issues:
        owner = os.environ.get("GITHUB_REPOSITORY_OWNER")  # assigning = a push notification in the GitHub app
        api("POST", "/issues", {"title": title, "body": alerts[title], **({"assignees": [owner]} if owner else {})})
        print("Opened alert:", title)
    elif title in alerts:
        api("PATCH", f"/issues/{open_issues[title]['number']}", {"body": alerts[title]})
    elif title in open_issues:
        api("POST", f"/issues/{open_issues[title]['number']}/comments", {"body": "Resolved - closing."})
        api("PATCH", f"/issues/{open_issues[title]['number']}", {"state": "closed"})
        print("Closed alert:", title)
print("Active alerts:", list(alerts) or "none")
