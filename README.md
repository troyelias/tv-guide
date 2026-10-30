# my-epg

One TV guide (EPG) for my whole playlist, rebuilt automatically twice a day.

**Guide link for the TV app (the only one needed):**
`https://raw.githubusercontent.com/<this repo>/guide/guide.xml.gz`

## What goes into it
Everything is downloaded fresh by this repo with the open-source [iptv-org/epg](https://github.com/iptv-org/epg) grabber.
For each channel, the build uses whichever source below has the most real show titles
(Tata Play often only says "Movie" for tomorrow; Airtel/Dish usually have the real names). On a tie, the higher one wins:
1. **Main** - `main.channels.xml`: one source per channel, mostly Tata Play (IDs like `ts8`), plus Airtel Xstream, Zee5 and Dish TV.
2. **Backup** - `backup.channels.xml`: the same channels from Airtel Xstream / Dish TV / Zee5, used only if the main source fails.
3. **mitthu786/tvepg** - extra fallback only (and the only source for Om Shanti `964`). The guide keeps working if that repo stops.
4. **Provider** - the IPTV provider's own guide, only if the private secret `PRO` is set.

Only the channels listed in `channel-ids.txt` are kept, so the file stays small.
The finished guide is published to the `guide` branch; `REPORT.md` lists every channel and where its listings came from.

## Run it by hand
Actions tab -> **Build guide** -> **Run workflow**.

## Add a channel
Find it in `sites/<site>/<site>.channels.xml` in iptv-org/epg, copy its `site` and `site_id`
into `main.channels.xml` (and optionally `backup.channels.xml`) with a new `xmltv_id`, and add that ID to `channel-ids.txt`.

No logins or playlist links are stored here - this repo is public. The provider's channel lineup is kept only
as an encrypted file (`lineup.enc`), and REPORT.md shows only how many channels changed.
