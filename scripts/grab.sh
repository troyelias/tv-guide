#!/usr/bin/env bash
# Download listings with the iptv-org grabber. If a fresh grabber version produces almost nothing,
# retry once with the last version that worked (saved in grabber.lock).
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
LOCK=$(cat "$W/grabber.lock" 2>/dev/null || true)
if [ "$N" -lt "$MIN" ] && [ -n "$LOCK" ] && [ "$LOCK" != "$CUR" ]; then
  echo "Too few shows - retrying with the last working grabber version"
  git -C "$W/grabber" fetch -q --depth 1 origin "$LOCK" && git -C "$W/grabber" checkout -q "$LOCK" \
    && ( cd "$W/grabber" && npm install --no-audit --no-fund > /dev/null 2>&1 ) && run_grab
  CUR=$(git -C "$W/grabber" rev-parse HEAD); N=$(count "$T/main.xml"); echo "Retry: $N main shows"
fi
if [ "$N" -ge "$MIN" ]; then echo "$CUR" > "$T/grabber.lock"; fi
exit 0
