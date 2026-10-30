# tv-guide

A personal TV guide (EPG), rebuilt automatically about twice a day.

**Guide link for the TV app:** `https://raw.githubusercontent.com/<this repo>/guide/guide.xml.gz`

## What each run does
1. **Grabs listings** with the open-source [iptv-org/epg](https://github.com/iptv-org/epg) grabber:
   `main.channels.xml` (mostly Tata Play) and `backup.channels.xml` (Airtel Xstream, Dish TV, Zee5, Sky UK).
   If a new grabber version breaks, it retries with the last version that worked (`grabber.lock`).
2. **Downloads fallback guides** (mitthu786/tvepg, and the provider's guide if the private secret is set).
3. **Merges** them into one guide for the channels in `channel-ids.txt`:
   - each channel uses the source with the most real show titles,
   - "Movie"-type placeholder titles are replaced by the real title from another source when one overlaps,
   - listings are extended with later shows from other sources,
   - the last ~26 hours are kept so catch-up shows real titles.
4. **Checks the subscription and lineup** - stored only in encrypted form.
5. **Updates the playlist** from an encrypted template and replaces it in Dropbox when something changed.
6. **Raises alerts** as GitHub issues (renewal due, login failing, coverage dropped, playlist problems)
   and closes them when resolved.
7. **Publishes** the guide and a small status page.

## Privacy
No logins, playlist links, account details or channel lineups are stored in readable form.
Everything sensitive lives in private repository secrets or in files encrypted with them (`*.enc`).

## Run it by hand
Actions tab → **Build guide** → **Run workflow**. **Account status** does a quick subscription check only.
