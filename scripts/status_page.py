"""Write a small phone-friendly status page (no names, logins or service details).

Usage: status_page.py OUT.html STATS.json STATUS.json CHANGES.json PLAYLIST.json
"""
import html, json, sys
from datetime import datetime
from zoneinfo import ZoneInfo


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


out = sys.argv[1]
stats, status, changes, pl = (load(p) for p in sys.argv[2:6])
ny = ZoneInfo("America/New_York")


def local(utc_str):
    try:
        return datetime.strptime(utc_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("UTC")).astimezone(ny).strftime("%a %b %-d, %-I:%M %p")
    except Exception:
        return "—"


rows, warn = [], []
total, have = stats.get("channels_total", 0), stats.get("channels_with_listings", 0)
rows.append(("Guide built", local(stats.get("built_utc", ""))))
rows.append(("Channels with listings", f"{have} of {total}"))
rows.append(("Looks ahead", f"about {stats.get('lookahead_hours_median', 0)} hours"))
rows.append(("“Movie” titles fixed", f"{stats.get('placeholders_filled', 0)} (left: {stats.get('placeholders_left', 0)})"))
if total and have < 0.75 * total:
    warn.append("Guide coverage dropped")
if status:
    days = status.get("days_left")
    ok = status.get("auth") == 1 and str(status.get("status", "")).lower() == "active"
    rows.append(("Subscription", "Active" if ok else "Problem"))
    if isinstance(days, (int, float)):
        rows.append(("Renewal due in", f"{max(days, 0):.0f} days"))
        if days <= 7:
            warn.append("Renewal due soon")
    if not ok:
        warn.append("Subscription check failing")
if changes.get("compared"):
    c = (changes.get("added", 0), changes.get("removed", 0), changes.get("renamed", 0))
    rows.append(("Lineup changes", "none" if not any(c) else f"+{c[0]} / −{c[1]} / {c[2]} renamed"))
if pl.get("ran"):
    st = "updated in Dropbox" if pl.get("uploaded") else ("upload failed" if pl.get("upload_error") else
          ("waiting for Dropbox" if pl.get("waiting_for_dropbox") else "up to date"))
    rows.append(("Playlist", f"{pl.get('channels', 0)} channels · {st}"))

state = "All good" if not warn else " · ".join(warn)
cells = "\n".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Guide status</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#6b7280;--ok:#15803d;--bad:#b45309;--line:#e5e7eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#181b22;--fg:#e8eaee;--muted:#9aa1ad;--ok:#4ade80;--bad:#fbbf24;--line:#2a2f3a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif}}
main{{max-width:520px;margin:0 auto;padding:24px 16px}}
h1{{font-size:20px;margin:0 0 4px}} .state{{font-weight:600;color:{'var(--ok)' if not warn else 'var(--bad)'};margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden}}
th,td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-weight:500;color:var(--muted);width:48%}} tr:last-child th,tr:last-child td{{border-bottom:0}}
p{{color:var(--muted);font-size:13px}}
</style></head><body><main>
<h1>Guide status</h1><div class="state">{html.escape(state)}</div>
<table>{cells}</table>
<p>Updates about twice a day. Times are New York time.</p>
</main></body></html>"""
open(out, "w", encoding="utf-8").write(page)
print("Status page:", state)
