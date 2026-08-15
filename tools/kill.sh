#!/bin/bash
# Pull the plug on clawdtv.
#
# Deliberately pure bash and curl: a kill switch that needs the project's own
# virtualenv, or its Python, or a working config, is not a kill switch. This runs
# even if everything else is broken.
#
#   ./tools/kill.sh            stop the daemon; leave everything installed
#   ./tools/kill.sh --purge    also remove the agent, local state, and our image
#                              from the device
#
# Neither form touches the repo, your Claude Code logins, or the Keychain.

set -uo pipefail

LABEL="com.clawdtv.agent"
PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$HOME/.local/state/clawdtv"
PURGE=0

case "${1:-}" in
  --purge) PURGE=1 ;;
  -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
esac

say() { printf '  %s\n' "$1"; }

# Read a single-quoted or double-quoted TOML string value; both are valid TOML
# and a sed that only knows one style would silently miss the other.
toml_str() {
  sed -n -e "s/^$1 *= *\"\([^\"]*\)\".*/\1/p" -e "s/^$1 *= *'\([^']*\)'.*/\1/p" \
    "$PREFIX/config.toml" 2>/dev/null | head -1
}

echo "stopping the daemon"
# Any label mentioning clawdtv counts: a rename must not orphan an older agent
# that would keep ticking behind the kill switch's back.
LABELS=$(launchctl list 2>/dev/null | awk '{print $3}' | grep -i clawdtv)
if [ -n "$LABELS" ]; then
  for L in $LABELS; do
    launchctl bootout "gui/$(id -u)/$L" 2>/dev/null
  done
  # bootout is asynchronous; make sure they are actually gone before saying so.
  for _ in 1 2 3 4 5; do
    launchctl list 2>/dev/null | awk '{print $3}' | grep -qi clawdtv || break
    sleep 1
  done
  if launchctl list 2>/dev/null | awk '{print $3}' | grep -qi clawdtv; then
    say "FAILED to unload — try: launchctl bootout gui/$(id -u)/$LABEL"
    exit 1
  fi
  say "unloaded ($(echo "$LABELS" | tr '\n' ' ' | sed 's/ $//')); no more ticks"
else
  say "was not running"
fi

if [ "$PURGE" -eq 0 ]; then
  echo
  echo "stopped. the panel keeps showing the last frame it received."
  echo "restart with:  bash $PREFIX/tools/install.sh"
  echo "remove completely with:  bash $PREFIX/tools/kill.sh --purge"
  exit 0
fi

echo "clearing our image off the device"
# Parse config.toml with sed rather than a TOML library, so this still works if
# the venv is gone. No hardcoded fallback for the address: an address we
# guessed could be somebody else's device.
HOST=$(toml_str host)
FILE=$(toml_str filename)
FILE="${FILE:-usage.jpg}"

if [ -z "$HOST" ]; then
  say "no device address in config.toml — skipped (nothing local depends on this)"
elif curl -s -m 6 "http://$HOST/v.json" >/dev/null 2>&1; then
  curl -s -m 10 "http://$HOST/delete?file=/image/$FILE" >/dev/null 2>&1
  # Leave it on the clock rather than an empty photo album, so the device is
  # useful on its own instead of showing a stale frame forever.
  curl -s -m 10 "http://$HOST/set?theme=6" >/dev/null 2>&1
  say "deleted /image/$FILE and set the device back to its clock"
else
  say "device at $HOST unreachable — skipped (nothing local depends on this)"
  say "if it comes back, it will still be showing the last frame we sent"
fi

echo "removing the launchd agent"
FOUND=0
for P in "$HOME/Library/LaunchAgents/"*clawdtv*.plist; do
  [ -f "$P" ] || continue
  rm -f "$P" && say "deleted $P"
  FOUND=1
done
[ "$FOUND" -eq 0 ] && say "none installed"

echo "removing local state"
if [ -d "$STATE" ]; then
  rm -rf "$STATE" && say "deleted $STATE (poller state, cost cache, last frame, log)"
else
  say "none found"
fi

# The statusline tee is installed per account config dir; check each one named
# in config.toml, plus the default.
LEFTOVER=""
while IFS= read -r DIR; do
  [ -n "$DIR" ] || continue
  if grep -q "clawdtv" "$DIR/settings.json" 2>/dev/null; then
    LEFTOVER="$LEFTOVER $DIR/settings.json"
  fi
done <<EOF
$HOME/.claude
$(sed -n -e 's/^config_dir *= *"\([^"]*\)".*/\1/p' -e "s/^config_dir *= *'\([^']*\)'.*/\1/p" "$PREFIX/config.toml" 2>/dev/null)
EOF
echo
echo "purged."
if [ -n "$LEFTOVER" ]; then
  echo "NOTE: these still reference clawdtv in their statusLine:$LEFTOVER"
  echo "      Remove those entries by hand — this script will not edit your Claude config."
fi
echo "left alone on purpose: this repo, your Claude Code logins, and the Keychain."
echo "reinstall any time with:  bash $PREFIX/tools/install.sh"
