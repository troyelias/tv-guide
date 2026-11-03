"""Write a small phone-friendly status page (no service names, logins or account details).

Usage: status_page.py OUT.html STATS.json CHANNELS.json [SPOT.json] [GUIDE_HISTORY.json]
GUIDE_HISTORY.json (optional) is updated with today's guide coverage and drawn as a 60-day chart.
"""
import html, json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


out, stats, ch = sys.argv[1], load(sys.argv[2]), load(sys.argv[3])
spot = load(sys.argv[4]).get("summary", {}) if len(sys.argv) > 4 else {}
ghist_path = sys.argv[5] if len(sys.argv) > 5 else None
ghist = load(ghist_path) if ghist_path else {}
if ghist_path and stats.get("built_utc") and stats.get("channels_total"):
    ghist[stats["built_utc"][:10]] = round(100 * stats.get("channels_with_listings", 0) / stats["channels_total"], 1)
    ghist = dict(sorted(ghist.items())[-60:])
    os.makedirs(os.path.dirname(ghist_path) or ".", exist_ok=True)
    json.dump(ghist, open(ghist_path, "w"), indent=1)


def chart(title, series, unit="%", lo=None, hi=None):
    """A small line chart (inline SVG, no scripts). series: [(label, [(x_label, value or None)...], css colour var)]."""
    pts = [v for _, s_, _ in series for _, v in s_ if v is not None]
    if len(pts) < 2:
        return ""
    lo = min(pts) if lo is None else lo
    hi = max(pts) if hi is None else hi
    if hi - lo < 1:
        hi, lo = hi + 1, lo - 1
    W, H, P = 520, 140, 28
    n = max(len(s_) for _, s_, _ in series)
    sx = lambda i: P + (W - 2 * P) * (i / max(n - 1, 1))  # noqa: E731
    sy = lambda v: H - 18 - (H - 36) * ((v - lo) / (hi - lo))  # noqa: E731
    lines, keys = [], []
    for name, s_, col in series:
        d = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, (_, v) in enumerate(s_) if v is not None)
        lines.append(f'<polyline fill="none" stroke="var({col})" stroke-width="2" points="{d}"/>')
        last = next((v for _, v in reversed(s_) if v is not None), None)
        keys.append(f'<span style="color:var({col})">■</span> {html.escape(name)}: {last:g}{unit}' if last is not None else "")
    first, lastx = series[0][1][0][0], series[0][1][-1][0]
    return (f'<h2>{html.escape(title)}</h2><figure><svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title)}">'
            f'<line x1="{P}" x2="{W - P}" y1="{sy(lo):.1f}" y2="{sy(lo):.1f}" stroke="var(--line)"/>'
            f'<text x="{P}" y="{H - 2}" class="ax">{html.escape(first)}</text>'
            f'<text x="{W - P}" y="{H - 2}" class="ax" text-anchor="end">{html.escape(lastx)}</text>'
            f'<text x="2" y="{sy(hi) + 4:.1f}" class="ax">{hi:g}</text><text x="2" y="{sy(lo) + 4:.1f}" class="ax">{lo:g}</text>'
            + "".join(lines) + f'</svg><figcaption>{" · ".join(k for k in keys if k)}</figcaption></figure>')
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
    nw = ch.get("not_working_folder", [])
    if nw:
        rows.append(("Moved to the 'Not working' folder (down 7+ check-ups)", ", ".join(nw)))
    st2 = ch.get("second_try")
    if st2:
        rows.append(("Second try", f"{st2.get('recovered', 0)} of {st2.get('tried', 0)} came back at {st2.get('at_ist', '?')} India time"))
    w = ch.get("week") or {}
    for p_, label in (("main", "Main service this week"), ("backup", "Backup service this week")):
        r = (w.get("reliability") or {}).get(p_)
        if r:
            rows.append((label, f"played {r['avg_pct']}% of its channels on average (worst morning {r['worst_pct']}%)"))
    if w.get("flaky"):
        rows.append(("Flakiest channels this week", ", ".join(w["flaky"])))
    if ch.get("late_start_ist"):
        warn.append(f"Check-up started late ({ch['late_start_ist']})")
    for p_, d in (ch.get("drops") or {}).items():
        warn.append(f"{'Main' if p_ == 'main' else 'Backup'} service lost channels ({d['before']} → {d['now']})")
    if spot.get("favourite_plays_pct_7d") is not None:
        rows.append(("Daytime spot checks (7 days)", f"favourites played {spot['favourite_plays_pct_7d']}% of the time "
                                                     f"({spot.get('checks_7d', 0)} checks)"))
    if spot.get("down_now"):
        warn.append("Favourite down during the day")
        rows.append(("Favourites down at the last spot checks", ", ".join(spot["down_now"])))
    bc = (ch.get("best_copy") or {}).get("switched_to_better_copy") or {}
    if bc:
        rows.append(("Moved to a sharper/faster copy", "; ".join(f"{'main' if p_ == 'main' else 'backup'}: {', '.join(v)}" for p_, v in bc.items())))
    if ch.get("upload_blocked"):
        warn.append("Playlist update held back")
    if stats.get("guide_held"):
        warn.append("Guide update held back")
    sh = ch.get("shadow")
    if sh:
        rows.append(("Proposed rule change (shadow mode)", (sh.get("about") or "") + " — "
                     + ("would differ on: " + "; ".join(sh["differs"]) if sh.get("differs") else "no differences yet")))
    reps = ch.get("replacements") or {}
    if reps:
        rows.append(("Replacement ideas", "; ".join(f"{n} → {', '.join(v)}" for n, v in reps.items())))
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

tr = ch.get("trend") or []
charts = chart("Share of channels playing each morning", [
    ("Main service", [(t["date"][5:], t.get("main_pct") and round(t["main_pct"], 1)) for t in tr], "--a"),
    ("Backup service", [(t["date"][5:], t.get("backup_pct") and round(t["backup_pct"], 1)) for t in tr], "--b")])
charts += chart("Channels playing nowhere (⚠)", [("⚠ channels", [(t["date"][5:], t.get("down")) for t in tr], "--bad")], unit="", lo=0)
charts += chart("Guide coverage", [("Channels with listings", [(d[5:], v) for d, v in ghist.items()], "--ok")])
state = "All good" if not warn else " · ".join(warn)
cells = "\n".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Guide status</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#6b7280;--ok:#15803d;--bad:#b45309;--line:#e5e7eb;--a:#2563eb;--b:#9333ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#181b22;--fg:#e8eaee;--muted:#9aa1ad;--ok:#4ade80;--bad:#fbbf24;--line:#2a2f3a;--a:#60a5fa;--b:#c084fc}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif}}
main{{max-width:560px;margin:0 auto;padding:24px 16px}}
h1{{font-size:20px;margin:0 0 4px}} .state{{font-weight:600;color:{'var(--ok)' if not warn else 'var(--bad)'};margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden}}
th,td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-weight:500;color:var(--muted);width:42%}} tr:last-child th,tr:last-child td{{border-bottom:0}}
p{{color:var(--muted);font-size:13px}}
h2{{font-size:15px;margin:22px 0 6px}} figure{{margin:0;background:var(--card);border-radius:12px;padding:10px}}
svg{{width:100%;height:auto;display:block}} .ax{{font-size:11px;fill:var(--muted)}} figcaption{{font-size:13px;color:var(--muted);padding:4px 4px 0}}
</style></head><body><main>
<h1>Guide status</h1><div class="state">{html.escape(state)}</div>
<table>{cells}</table>
{charts}
<p>Channel check-ups run 4–7 AM India time. The guide also refreshes around 1 PM India time.</p>
</main></body></html>"""
open(out, "w", encoding="utf-8").write(page)
print("Status page:", state)
