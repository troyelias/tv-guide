#!/usr/bin/env bash
# Install the picture checker (ffmpeg) quickly, or give up: everything still works without it, just without the
# "black filler" check. GitHub's package mirrors sometimes stall for most of an hour, so every step is time-limited.
command -v ffmpeg >/dev/null && exit 0
APT="sudo apt-get -qq -o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20"
timeout 180 $APT install -y --no-install-recommends ffmpeg >/dev/null 2>&1 && exit 0      # usually enough
timeout 240 $APT update >/dev/null 2>&1
timeout 240 $APT install -y --no-install-recommends ffmpeg >/dev/null 2>&1 && exit 0
echo "::warning::Picture checker couldn't be installed in time - carrying on without the black-filler check"
exit 0
