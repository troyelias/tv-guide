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
    days = ch.get("down_days", {})
    msg = ("These must-have channels didn't play on either service at the last check-up: "
           + ", ".join(f"{n} ({days.get(n, 1)} check-up{'s' if days.get(n, 1) != 1 else ''} in a row)" for n in bad) + ".")
    reps = {n: v for n, v in (ch.get("replacements") or {}).items() if n in bad}
    if reps:
        msg += ("\n\n**Possible replacements** (same folder, playing on both services where possible):\n"
                + "\n".join(f"- {n} → {', '.join(v)}" for n, v in reps.items())
                + "\n\nTo swap a favourite, tell Claude which one to use instead.")
    alerts["Must-have channel down"] = msg
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
if ch.get("late_start_ist"):
    alerts["Check-up started late"] = (f"This morning's check-up started at {ch['late_start_ist']} India time instead of about 4:06. "
                                       "It still ran, but a scheduled start may have moved (for example after a clock change). "
                                       "Ask Claude to check the starter task.")
svc = {"main": "main", "backup": "backup"}
dr = ch.get("drops") or {}
if dr:
    alerts["A service lost channels overnight"] = ("Fewer channels played than usual this morning: " + "; ".join(
        f"{svc[p]} service {d['now']} working (yesterday {d['before']}, this week's average {d['week_avg']})"
        for p, d in dr.items()) + ". It may be a temporary problem at the service; the next check-ups will show whether it lasts.")
KNOWN = ["Channels no longer found on a service", "A check-up was missed", "Playlist updates paused (undo used)",
         "Renewal due soon (main service)", "Renewal due soon (backup service)", "Main service down - using backup",
         "Guide coverage dropped", "Playlist upload failing", "Must-have channel down",
         "Check-up started late", "A service lost channels overnight"]

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


# ---- weekly report card: a new issue every Monday morning (India time); last week's is closed ----
def weekly_text(w, ch):
    days = lambda d: f"{max(d, 0):.0f} days" if isinstance(d, (int, float)) else "unknown"  # noqa: E731
    out = [f"**Week {w.get('from')} to {w.get('to')}** ({w.get('mornings')} check-ups)", ""]
    for p, label in (("main", "Main service"), ("backup", "Backup service")):
        r = (w.get("reliability") or {}).get(p)
        if r:
            out.append(f"- **{label}:** played {r['avg_pct']}% of its channels on average (worst morning {r['worst_pct']}%)")
    out += [f"- **Main subscription:** {days(ch.get('main_days_left'))} left",
            f"- **Backup subscription:** {days(ch.get('backup_days_left'))} left", ""]
    for key_, label in (("flaky", "Flakiest channels (sometimes work, sometimes don't)"),
                        ("newly_down", "Stopped working this week"), ("back_again", "Working again this week"),
                        ("down_all_week", "Down on both services all week"), ("switched_now", "Currently playing from the backup service"),
                        ("late_starts", "Check-ups that started late")):
        v = w.get(key_) or []
        if v:
            out.append(f"**{label}:** " + ", ".join(v))
    return "\n".join(out)


w = ch.get("week")
if ch.get("weekly_due") and w:
    title = f"Weekly report card ({w.get('to')})"
    recent = api("GET", "/issues?state=all&per_page=50&sort=created&direction=desc")
    if not any(i["title"] == title for i in recent):
        for i in recent:
            if i["title"].startswith("Weekly report card") and i["state"] == "open":
                api("PATCH", f"/issues/{i['number']}", {"state": "closed"})
        api("POST", "/issues", {"title": title, "body": weekly_text(w, ch), **({"assignees": [owner]} if owner else {})})
        print("Opened", title)
