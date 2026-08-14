#!/bin/bash
# Install (or reinstall) the launchd agent. Idempotent.
set -euo pipefail

PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.clawdtv.agent"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$PREFIX/.venv/bin/clawdtv" ]; then
  echo "clawdtv is not installed in $PREFIX/.venv — run: bash tools/setup.sh" >&2
  exit 1
fi

# The venv is guaranteed present just above, so read config through the real
# parser rather than a sed approximation of TOML.
CONFIG=$("$PREFIX/.venv/bin/python" - <<'PY' 2>&1 || true
from clawdtv.config import ConfigError, load
try:
    cfg = load()
    print(cfg.host or "-", cfg.tick_interval_s)
except ConfigError as exc:
    print(f"ERR {exc}")
PY
)
case "$CONFIG" in
  ERR*) echo "config.toml problem: ${CONFIG#ERR }" >&2; exit 1 ;;
esac
HOST="${CONFIG%% *}"
INTERVAL="${CONFIG##* }"
case "$INTERVAL" in
  ''|*[!0-9]*) echo "could not read config.toml:" >&2; echo "$CONFIG" >&2; exit 1 ;;
esac
if [ "$HOST" = "-" ]; then
  echo "config.toml has no device host set — every tick would fail." >&2
  echo "Set host under [device] to your screen's IP address, then re-run." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/state/clawdtv"
sed -e "s|__PREFIX__|$PREFIX|g" -e "s|__HOME__|$HOME|g" -e "s|__INTERVAL__|$INTERVAL|g" \
  "$PREFIX/tools/$LABEL.plist" > "$TARGET"

# Sweep agents from older versions of this project that used a different
# label; leaving one behind would mean two daemons ticking the same config.
launchctl list 2>/dev/null | awk '{print $3}' | { grep -i clawdtv || true; } | { grep -vx "$LABEL" || true; } | while read -r OLD; do
  launchctl bootout "gui/$(id -u)/$OLD" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$OLD.plist"
  echo "removed older agent $OLD"
done

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl enable "gui/$(id -u)/$LABEL"

echo "installed $TARGET"
echo "ticks every ${INTERVAL}s; log at ~/.local/state/clawdtv/clawdtv.log"
echo "stop with: bash $PREFIX/tools/kill.sh"
