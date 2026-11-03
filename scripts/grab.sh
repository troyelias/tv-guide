#!/usr/bin/env bash
# Download listings with the iptv-org grabber, pinned to a known-good version (grabber.lock).
# If the pinned version produces almost nothing, try the newest version once; whichever works is remembered.
set -u
W="$GITHUB_WORKSPACE"; T="$RUNNER_TEMP"; MIN=3000

run_grab() {
  ( cd "$W/grabber" && npm run grab --- --channels="$W/main.channels.xml" --output="$T/main.xml" \
      --days=2 --maxConnections=1 --delay=2200 --timeout=30000 > "$T/grab-main.log" 2>&1 )
  ( cd "$W/grabber" && npm run grab --- --channels="$W/backup.channels.xml" --output="$T/backup.xml" \
      --days=3 --maxConnections=2 --delay=400 --timeout=30000 > "$T/grab-backup.log" 2>&1 )
}
count() { grep -c "<programme" "$1" 2>/dev/null || echo 0; }

run_grab
CUR=$(git -C "$W/grabber" rev-parse HEAD)
N=$(count "$T/main.xml"); echo "Grabber $CUR: $N main shows, $(count "$T/backup.xml") backup shows"
if [ "$N" -lt "$MIN" ]; then
  # the pinned version stopped working (a listings site changed) - try the newest grabber once
  echo "Too few shows with the pinned grabber - trying the newest version"
  git -C "$W/grabber" fetch -q --depth 1 origin HEAD && git -C "$W/grabber" checkout -q FETCH_HEAD \
    && ( cd "$W/grabber" && npm install --no-audit --no-fund > /dev/null 2>&1 ) && run_grab
  CUR=$(git -C "$W/grabber" rev-parse HEAD); N=$(count "$T/main.xml"); echo "Retry with $CUR: $N main shows"
fi
if [ "$N" -ge "$MIN" ]; then echo "$CUR" > "$T/grabber.lock"; fi
exit 0
