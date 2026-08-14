#!/bin/bash
# Tee Claude Code's rate_limits into a file clawdtv can read, then hand stdin to
# whatever statusline was already configured.
#
# This is the only first-party source of usage data: Claude Code passes
# rate_limits on stdin to the statusline command for Pro/Max accounts, using
# used_percentage and epoch-second resets_at. It is free, it needs no token, and
# it is exactly current — but it only exists while a session is running, which is
# why it supplements the endpoint rather than replacing it.
#
# Install per account, in that config dir's settings.json, with the account's
# label from config.toml as the first argument:
#   "statusLine": { "type": "command",
#                   "command": "/path/to/clawdtv/tools/statusline-tee.sh PERSONAL <original command>" }
# Passing no original command just prints nothing, which is a valid statusline.

set -uo pipefail

LABEL="${1:-PERSONAL}"
shift || true

STATE_DIR="$HOME/.local/state/clawdtv"
mkdir -p "$STATE_DIR"

INPUT=$(cat)

# The target filename mirrors Account.slug in config.py: lowercase, spaces
# becoming dashes. Write atomically: a half-written file would be read as
# corrupt and discarded, which is survivable, but a rename costs nothing.
SLUG=$(printf '%s' "$LABEL" | tr '[:upper:] ' '[:lower:]-')
printf '%s' "$INPUT" | /usr/bin/python3 -c '
import json, sys, time, os
target = sys.argv[1]
try:
    blob = json.load(sys.stdin)
except Exception:
    sys.exit(0)
limits = blob.get("rate_limits")
if not isinstance(limits, dict):
    sys.exit(0)
tmp = target + ".tmp"
with open(tmp, "w") as handle:
    json.dump({"rate_limits": limits, "written_at": time.time()}, handle)
os.replace(tmp, target)
' "$STATE_DIR/$SLUG.json" 2>/dev/null

# Preserve whatever statusline was already there.
if [ "$#" -gt 0 ]; then
  printf '%s' "$INPUT" | "$@"
fi
