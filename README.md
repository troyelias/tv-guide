# tv-guide

A personal TV setup that looks after itself: one programme guide, one channel list, two TV services.

**Guide link for the TV app:** `https://raw.githubusercontent.com/<this repo>/guide/guide.xml.gz`
**Status page:** `https://<owner>.github.io/<this repo>/`

## How it fits together
- **`catalog.json`** - the channel list: every channel's name, folder, guide ID and logo, in order.
  It belongs to no service. Each night the robot finds each service's copy of every channel
  (by the service's own guide tag or a cleaned-up name), so changing service means changing a login only.
- **Nightly check-up** (`nightly.yml`, about 4:05 AM India time):
  1. test-plays each channel for a few seconds on both services (skips a service if someone is watching),
  2. switches a channel to the backup service if it fails twice on the main one, and back after it works two nights running,
  3. rebuilds the playlist (plus a "Backup" folder of must-have channels from the backup service) and replaces it in Dropbox if anything changed,
  4. marks any channel that plays on neither service with "⚠" in the channel list, and shows
     "⚠ Not available right now" in its guide until the next check-up,
  5. gives anything that failed a second try at about 5:45 AM (channels often drop out for a few minutes),
  6. moves a channel that has played nowhere for 7 check-ups in a row into a "Not working" folder at the bottom
     (it returns to its own folder as soon as it plays again), and suggests replacements for a favourite that's
     been down 3 check-ups in a row,
  7. rebuilds the guide, status page and alerts. Nothing is tested or changed after 6:30 AM India time.
  A self-test (`tests/selftest.py`) rehearses all of this on made-up services first; if it fails, nothing is changed.
- **Midday guide refresh** (`midday-guide.yml`, about 1 PM India time): listings only; no contact with either service.
- **Outage watch** (`outage-watch.yml`, hourly): checks the main service still logs in (no channel is played).
  Two failures in a row switch every channel to the backup service at once.
- **Self-test** (`tests.yml`): runs on every change to the scripts or channel list.
- **On demand:** `Close look at channels`, `Account status`, `Channel health check`, `Fetch backup lineup`, `Dropbox setup (one time)`.

## Privacy
Logins, Dropbox keys and the file location are private repository secrets. The switch-over memory and the
Dropbox key are stored encrypted (`secure/*.enc`). The playlist itself (which contains logins) only ever goes to Dropbox.
