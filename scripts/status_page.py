"""Write a small phone-friendly status page (no service names, logins or account details).

Usage: status_page.py OUT.html STATS.json CHANNELS.json
"""
import html, json, sys
from datetime import datetime
from zoneinfo import ZoneInfo


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


out, stats, ch = sys.argv[1], load(sys.argv[2]), load(sys.argv[3])
ny, ist = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")


def when(utc_str):
    try:
        t = datetime.strptime(utc_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("UTC"))
        return f"{t.astimezone(ist):%a %-d %b, %-I:%M %p} India · {t.astimezone(ny):%-I:%M %p} New York"
    except Exception:
        return "—"


rows, warn = [], []
total, have = stats.get("channels_total", 0), stats.get("channels_with_listings", 0)
rows.append(("Guide updated", when(stats.get("built_utc", ""))))
rows.append(("Guide covers", f"{have} of {total} channels, about {stats.get('lookahead_hours_median', 0)} hours ahead"))
if total and have < 0.75 * total:
    warn.append("Guide coverage dropped")
if ch:
    rows.append(("Last channel check-up", when(ch.get("checkup_utc", ch.get("ran_utc", "")))))
    rows.append(("Channels in playlist", f"{ch.get('channels', 0)} (+{ch.get('spare_folder', 0)} in the Backup folder)"))
    if ch.get("outage"):
        warn.append("Main service down - everything on backup")
    ob = ch.get("on_backup", [])
    rows.append(("Switched to backup", ", ".join(ob) if ob else "none"))
    dn = ch.get("down_on_both", [])
    rows.append(("Not playing anywhere", ", ".join(dn) if dn else "none"))
    for k, label in (("main_days_left", "Main subscription"), ("backup_days_left", "Backup subscription")):
        d = ch.get(k)
        if isinstance(d, (int, float)):
            rows.append((label, f"renews in {max(d, 0):.0f} days"))
            if d <= 7:
                warn.append(f"{label} renewal due")
    if ch.get("upload_error"):
        warn.append("Playlist upload failing")
    if ch.get("hold"):
        rows.append(("Playlist updates", "paused (undo used)"))
        warn.append("Playlist updates paused")
    mo = ch.get("missing_on", {})
    if mo:
        rows.append(("Not found on a service", ", ".join(f"{'main' if p == 'main' else 'backup'}: {n}" for p, n in mo.items())))
    if any(ch.get("newly_missing", {}).values()):
        warn.append("Some channels no longer found")
    if ch.get("missed_hours"):
        warn.append("A check-up was missed")

state = "All good" if not warn else " · ".join(warn)
cells = "\n".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Guide status</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#6b7280;--ok:#15803d;--bad:#b45309;--line:#e5e7eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#181b22;--fg:#e8eaee;--muted:#9aa1ad;--ok:#4ade80;--bad:#fbbf24;--line:#2a2f3a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif}}
main{{max-width:560px;margin:0 auto;padding:24px 16px}}
h1{{font-size:20px;margin:0 0 4px}} .state{{font-weight:600;color:{'var(--ok)' if not warn else 'var(--bad)'};margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden}}
th,td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-weight:500;color:var(--muted);width:42%}} tr:last-child th,tr:last-child td{{border-bottom:0}}
p{{color:var(--muted);font-size:13px}}
</style></head><body><main>
<h1>Guide status</h1><div class="state">{html.escape(state)}</div>
<table>{cells}</table>
<p>Channel check-ups run 4–7 AM India time. The guide also refreshes around 1 PM India time.</p>
</main></body></html>"""
open(out, "w", encoding="utf-8").write(page)
print("Status page:", state)
