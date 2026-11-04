#!/usr/bin/env bash
# Download listings with the iptv-org grabber, pinned to a known-good version (grabber.lock).
# Runs walled off (no secrets); a newer grabber is only adopted by the weekly grabber test.
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
  # the pinned version may have stopped working (a listings site changed); the weekly grabber test adopts a newer
  # one once it's shown to work - an untested version is never run here
  echo "::warning::Only $N shows from the pinned grabber - the previous guide fills the gaps"
fi
exit 0
