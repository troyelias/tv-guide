"""Self-test: rehearses the morning check-up and the guide merge against made-up services and listings.
Nothing real is contacted and nothing real is changed. Exit code 0 = all good.

Usage: python3 tests/selftest.py
"""
import gzip, json, os, re, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
from catalog_lib import match_lineup  # noqa: E402

failures, passed = [], 0


def check(cond, what):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(what)
        print("  FAIL:", what)


# ------------------------------------------------------------------ 1. several pretend mornings
def fake_lineup(catalog, base):
    cats = [{"category_id": "1", "category_name": "INDIA"}, {"category_id": "2", "category_name": "UK"},
            {"category_id": "3", "category_name": "PAKISTAN"}]
    cid_for = {"in": "1", "uk": "2", "pk": "3", "us": "1"}
    streams = [{"num": i, "name": c["name"], "stream_id": base + i, "category_id": cid_for.get(c["regions"][0], "1"),
                "epg_channel_id": "", "tv_archive": 0} for i, c in enumerate(catalog)]
    return {"categories": cats, "streams": streams}


def parse_playlist(text):
    out = []
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("#EXTINF"):
            name = l.rsplit(",", 1)[1]
            group = re.search(r'group-title="([^"]*)"', l).group(1)
            out.append({"title": name, "group": group, "url": lines[i + 1]})
    return out


def rehearsal():
    print("1. Morning check-up rehearsal (8 pretend mornings)")
    catalog = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
    lu = {"main": fake_lineup(catalog, 10000), "backup": fake_lineup(catalog, 20000)}
    m = {p: match_lineup(catalog, lu[p]["streams"], lu[p]["categories"]) for p in lu}
    both = [c for c in catalog if m["main"].get(c["id"]) and m["backup"].get(c["id"])]
    plain = [c for c in both if not c.get("must")]
    musts = [c for c in both if c.get("must")]
    check(len(both) > 100 and len(musts) >= 3, f"enough channels matched for the rehearsal ({len(both)})")
    if failures:
        return
    A, B, D, E = plain[0], plain[1], plain[2], plain[3]
    C, M2 = musts[0], musts[1]
    crowd = plain[10:40]  # 30 channels that all fail on the main service one morning
    sid = lambda p, c: m[p][c["id"]]  # noqa: E731  (every copy of the channel on that service)
    work = tempfile.mkdtemp()
    try:
        shutil.copy(os.path.join(ROOT, "catalog.json"), work)
        os.makedirs(os.path.join(work, "secure"))
        env = dict(os.environ, DBX_SECRET="selftest-secret", PRO="http://main.test/get.php?username=u&password=p",
                   PRO_2="http://backup.test/get.php?username=u&password=p", PAUSE="0", GITHUB_REPOSITORY="owner/repo")
        env.pop("BACKUP_SLOT", None)
        start = datetime(2026, 1, 1, 22, 40)  # 4:10 AM India time the next day
        base = lambda extra=(): {"main": [sid("main", B), sid("main", C)] + list(extra),  # noqa: E731
                                 "backup": [sid("backup", B), sid("backup", C)]}
        n1 = base([sid("main", A), sid("main", E), sid("main", M2)])
        n1["backup"].append(sid("backup", D))
        nights = [  # (dead, flaky, minutes late)
            (n1, {"main": [sid("main", D)]}, 0),                        # morning 1 (Fri)
            (base(), {}, 0),                                            # 2
            (base([sid("main", E)]), {}, 0),                            # 3: E fails again -> flaky
            (base(), {}, 0),                                            # 4 (Mon: weekly report)
            (base(), {}, 0),                                            # 5
            (base([sid("main", c) for c in crowd]), {}, 0),             # 6: main loses 30 channels
            (base(), {}, 0),                                            # 7
            ({"main": [sid("main", C)], "backup": [sid("backup", C)]}, {}, 40),  # 8: starts 40 minutes late
        ]
        results = []
        for n, (dead, flaky, late) in enumerate(nights, 1):
            plan = os.path.join(work, "plan.json")
            json.dump({"lineups": lu, "dead": dead, "flaky": flaky}, open(plan, "w"))
            out = os.path.join(work, f"out{n}")
            e = dict(env, TEST_NOW=(start + timedelta(days=n - 1, minutes=late)).isoformat())
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "_night.py"), SCRIPTS, plan, out],
                               cwd=work, env=e, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                check(False, f"morning {n} ran without errors:\n{r.stderr[-2000:]}")
                return
            summ = json.load(open(os.path.join(out, "channels.json")))
            pl = parse_playlist(open(os.path.join(work, "uploaded.m3u"), encoding="utf-8").read())
            results.append((summ, pl))

        def find(pl, c, group=None):
            return [x for x in pl if x["title"].replace(" ⚠", "") == c["name"] and (group is None or x["group"] == group)]

        s1, p1 = results[0]
        a = find(p1, A, A["folder"])
        check(a and "backup.test" in a[0]["url"], "morning 1: a channel broken on the main service switches to the backup")
        b = find(p1, B, B["folder"])
        check(b and b[0]["title"].endswith("⚠"), "morning 1: a channel broken on both services gets the warning mark")
        d = find(p1, D, D["folder"])
        check(d and not d[0]["title"].endswith("⚠") and "main.test" in d[0]["url"],
              "morning 1: a channel that was only briefly down is rescued by the later second try")
        check((s1.get("second_try") or {}).get("recovered", 0) >= 1, "morning 1: second try reports what came back")
        check(not find(p1, C, "Backup"), "morning 1: a favourite that doesn't play is left out of the Backup folder")
        check(len([x for x in p1 if x["group"] == "Backup"]) >= 1, "morning 1: the Backup folder still has working favourites")
        s2, p2 = results[1]
        check("backup.test" in find(p2, A, A["folder"])[0]["url"], "morning 2: one good morning isn't enough to switch back")
        s3, p3 = results[2]
        check("main.test" in find(p3, A, A["folder"])[0]["url"], "morning 3: switches back after two good mornings")
        check(C["name"] in (s3.get("replacements") or {}), "morning 3: a favourite down 3 mornings gets replacement ideas")
        alts = (s3.get("replacements") or {}).get(C["name"], [])
        check(alts and all(any(x["name"] == n and x["folder"] == C["folder"] for x in catalog) for n in alts),
              "morning 3: replacement ideas come from the same folder")
        s6, p6 = results[5]
        check(find(p6, B, B["folder"]) and not find(p6, B, "Not working"), "morning 6: still in its own folder before 7 mornings")
        s7, p7 = results[6]
        check(find(p7, B, "Not working") and not find(p7, B, B["folder"]), "morning 7: down 7 mornings -> 'Not working' folder")
        check(p7[-1]["group"] in ("Not working",), "morning 7: the 'Not working' folder is at the very bottom")
        s8, p8 = results[7]
        b8 = find(p8, B, B["folder"])
        check(b8 and not b8[0]["title"].endswith("⚠") and not find(p8, B, "Not working"),
              "morning 8: back in its own folder with no mark once it plays again")
        check(s8.get("channels", 0) >= len(both), "every matched channel stays in the playlist")
        # smarter switching: E failed on the main service on mornings 1 and 3 -> needs 4 good mornings in a row
        check("backup.test" in find(results[4][1], E, E["folder"])[0]["url"], "morning 5: a flaky channel isn't switched back after 2 good mornings")
        check("main.test" in find(results[6][1], E, E["folder"])[0]["url"], "morning 7: ...but is after 4 good mornings in a row")
        check(any(E["name"] in f for f in (results[6][0].get("week") or {}).get("flaky", [])), "weekly report lists the flaky channel")
        # Backup folder uses the service the main copy isn't using
        s2m = find(results[1][1], M2, M2["folder"])
        s2b = find(results[1][1], M2, "Backup")
        check(s2m and "backup.test" in s2m[0]["url"] and s2b and "main.test" in s2b[0]["url"],
              "a favourite playing from the backup service gets its Backup-folder copy from the main service")
        s3b = find(results[2][1], M2, "Backup")
        check(s3b and "backup.test" in s3b[0]["url"], "once back on the main service, its Backup-folder copy comes from the backup service")
        # health drop, late start, weekly report
        check("main" in (results[5][0].get("drops") or {}), "morning 6: a sudden loss of 30 channels on one service is flagged")
        check(not results[4][0].get("drops") and not results[6][0].get("drops"), "normal mornings don't raise the drop warning")
        check(results[7][0].get("late_start_ist") == "04:50", "morning 8: a check-up that started late is flagged")
        check(not results[6][0].get("late_start_ist"), "an on-time check-up isn't flagged as late")
        check([bool(r[0].get("weekly_due")) for r in results] == [False, False, False, True, False, False, False, False],
              "the weekly report card is due on Monday mornings only")
        wk = results[6][0].get("week") or {}
        check(wk.get("mornings") == 7 and set(wk.get("reliability", {})) == {"main", "backup"},
              "weekly report covers 7 mornings and both services")
        check(B["name"] in wk.get("down_all_week", []), "weekly report lists channels down all week")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ 2. guide merge
def merge_test():
    print("2. Guide merge rehearsal")
    work = tempfile.mkdtemp()
    try:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        t = lambda h, mi=0: (now + timedelta(hours=h, minutes=mi)).strftime("%Y%m%d%H%M%S +0000")  # noqa: E731
        p = lambda ch, a, b, title: f'<programme start="{a}" stop="{b}" channel="{ch}"><title>{title}</title></programme>'  # noqa: E731
        a = ('<tv><channel id="X1"><display-name>X1</display-name></channel>'
             + p("X1", t(0), t(1), "Show One") + p("X1", t(1), t(1, 2), "Filler / Promo") + p("X1", t(1, 2), t(3), "Show Two")
             + p("X1", t(3), t(5), "Movie") + p("X1", t(5), t(8), "Long Promo Block Show") + "</tv>")
        b = ('<tv><channel id="X1"><display-name>X1</display-name></channel>'
             + p("X1", t(3), t(5), "The Real Film") + p("X2", t(0), t(5), "Something") + "</tv>")
        open(os.path.join(work, "a.xml"), "w").write(a)
        open(os.path.join(work, "b.xml"), "w").write(b)
        open(os.path.join(work, "ids.txt"), "w").write("X1\nX2\n")
        json.dump({"down_guide_ids": ["X2"]}, open(os.path.join(work, "notes.json"), "w"))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "merge.py"), "ids.txt", "out.xml.gz", "R.md", "s.json",
                            "--notes=notes.json", "A=a.xml", "B=b.xml"], cwd=work, capture_output=True, text=True, timeout=120)
        check(r.returncode == 0, "merge runs without errors" + ("" if r.returncode == 0 else ":\n" + r.stderr[-1500:]))
        if r.returncode:
            return
        x = gzip.open(os.path.join(work, "out.xml.gz")).read().decode()
        check("Filler / Promo" not in x, "1-2 minute filler is folded into the show before it")
        check(f'stop="{t(1, 2)}" channel="X1"><title>Show One' in x, "the show before the filler is extended over it")
        check("The Real Film" in x and ">Movie<" not in x, "a 'Movie' placeholder gets the real title from another source")
        check("⚠ Not available right now" in x, "a channel down on both services gets the guide note")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ 3. black "dead channel" filler detection
def filler_test():
    print("3. Dead-channel filler detection")
    if not shutil.which("ffmpeg"):
        print("  (skipped: ffmpeg not installed here)")
        return
    import streamtest
    work = tempfile.mkdtemp()
    try:
        def make(name, video, audio, extra):
            path = os.path.join(work, name)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio,
                            "-t", "8", "-c:v", "libx264"] + extra + ["-c:a", "aac", "-f", "mpegts", path], check=True, timeout=120)
            return open(path, "rb").read()[:streamtest.SAMPLE]
        filler = make("f.ts", "color=black:size=1280x720:rate=25", "anullsrc=r=48000:cl=stereo", ["-g", "1", "-b:v", "300k"])
        real = make("r.ts", "testsrc2=size=1280x720:rate=25", "sine", ["-g", "50", "-b:v", "4M"])
        still_music = make("s.ts", "color=blue:size=1280x720:rate=25", "sine", ["-g", "1", "-b:v", "100k"])
        check(streamtest.is_filler(filler), "a silent black picture counts as not playing")
        check(not streamtest.is_filler(real), "a normal moving picture counts as playing")
        check(not streamtest.is_filler(still_music), "a still picture with sound (e.g. a music slide) still counts as playing")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ 4. outage rule and alerts
def outage_and_alerts_test():
    print("4. Outage rule and alerts")
    work = tempfile.mkdtemp()
    try:
        w = os.path.join(work, "w.json")
        env = dict(os.environ, PRO="http://127.0.0.1:9/get.php?username=u&password=p")
        run = lambda: subprocess.run([sys.executable, os.path.join(SCRIPTS, "outage_watch.py"), w], env=env,  # noqa: E731
                                     capture_output=True, text=True, timeout=120).stdout.strip()
        check(run() == "none" and run() == "none", "two failed checks minutes apart don't switch everything")
        d = json.load(open(w))
        d["last_fail"] -= 45 * 60
        json.dump(d, open(w, "w"))
        check(run() == "switch", "failed checks 40+ minutes apart do switch everything")
        json.dump({}, open(os.path.join(work, "s.json"), "w"))
        json.dump({"down_on_both": ["Romedy Now HD"], "down_days": {"Romedy Now HD": 4},
                   "replacements": {"Romedy Now HD": ["Movies Now HD"]}}, open(os.path.join(work, "c.json"), "w"))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "alerts.py"), "s.json", "c.json"], cwd=work,
                           env={k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")},
                           capture_output=True, text=True, timeout=60)
        shutil.copy(os.path.join(ROOT, "catalog.json"), work)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "alerts.py"), "s.json", "c.json"], cwd=work,
                           env={k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")},
                           capture_output=True, text=True, timeout=60)
        check(r.returncode == 0 and "Must-have channel down" in r.stdout, "a dead favourite raises the must-have alert")
        json.dump({"late_start_ist": "05:07", "drops": {"main": {"now": 300, "before": 360, "week_avg": 358}}},
                  open(os.path.join(work, "c.json"), "w"))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "alerts.py"), "s.json", "c.json"], cwd=work,
                           env={k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")},
                           capture_output=True, text=True, timeout=60)
        check("Check-up started late" in r.stdout and "A service lost channels overnight" in r.stdout,
              "late start and lost channels raise their alerts")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ 5. files parse
def files_test():
    print("5. Files are readable")
    for f in os.listdir(SCRIPTS):
        if f.endswith(".py"):
            r = subprocess.run([sys.executable, "-m", "py_compile", os.path.join(SCRIPTS, f)], capture_output=True)
            check(r.returncode == 0, f"{f} compiles")
    try:
        import yaml
        wf = os.path.join(ROOT, ".github", "workflows")
        for f in os.listdir(wf):
            try:
                yaml.safe_load(open(os.path.join(wf, f)))
                check(True, f)
            except Exception as e:
                check(False, f"{f} is valid ({e})")
    except ImportError:
        pass
    cat = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
    check(len({c["id"] for c in cat}) == len(cat), "channel list has no duplicate entries")


for t in (files_test, rehearsal, merge_test, filler_test, outage_and_alerts_test):
    try:
        t()
    except Exception as e:  # a crash inside a test is a failure, not a crash of the whole self-test
        check(False, f"{t.__name__} crashed: {type(e).__name__}: {e}")
print(f"\nSelf-test: {passed} checks passed, {len(failures)} failed.")
sys.exit(1 if failures else 0)
