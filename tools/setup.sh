#!/bin/bash
# One-time setup: build the virtualenv and install clawdtv into it.
#
# Prefers uv (which can fetch a suitable Python by itself); falls back to any
# python3 of 3.11 or newer. Safe to re-run.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if command -v uv >/dev/null 2>&1; then
  uv venv --quiet --python 3.12 --allow-existing
  uv pip install --quiet -e .
elif python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet -e .
else
  echo "clawdtv needs Python 3.11+ and none was found." >&2
  echo "Easiest fix — install uv, then re-run this script:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

echo "installed into .venv"
echo
echo "next steps:"
echo "  1. edit config.toml — set host under [device] to your screen's IP address"
echo "  2. ./.venv/bin/clawdtv check     # verifies device, accounts, data, palette"
echo "  3. bash tools/install.sh         # starts the every-5-minutes updater"
