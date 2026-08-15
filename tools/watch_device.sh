#!/bin/bash
# Sample the device's stored image every 15s and log md5 changes, so you can
# tell whether something else on the network (a Home Assistant integration, a
# weather dashboard) is overwriting your frames, and how often.
#
#   tools/watch_device.sh <device-ip> [filename]
#
# No `set -e`: this exists to watch a flaky situation, so one failed sample
# must log and continue, not abort the whole watch.
set -uo pipefail

D="${1:?usage: watch_device.sh <device-ip> [filename]}"
FILE="${2:-usage.jpg}"
MINE=$(md5 -q "$HOME/.local/state/clawdtv/frame.jpg" 2>/dev/null || echo "none")

for _ in $(seq 1 24); do
  T=$(date +%H:%M:%S)
  if BODY=$(curl -sf -m 10 "http://$D/image/$FILE"); then
    M=$(printf '%s' "$BODY" | md5 -q)
    if [ "$M" = "$MINE" ]; then echo "$T ours"; else echo "$T FOREIGN md5=$M"; fi
  else
    echo "$T unreachable"
  fi
  sleep 15
done
