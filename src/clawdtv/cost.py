"""Today's usage cost per account, via ccusage.

The transcripts hold raw token counts but no cost, so turning them into dollars
needs a per-model price table including the cache-write and cache-read tiers.
ccusage maintains that table, so we shell out to it rather than keeping a copy
that would quietly drift out of date.

The number it reports is what the same work would have cost on the API. On a
subscription it is a measure of consumption, not of money leaving an account.

This is the slowest thing in a tick by an order of magnitude, so results are
cached on disk and refreshed on their own schedule. Any failure returns None and
the cost line simply does not render — it is the least important thing on screen
and must never hold up the rest.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import STATE_DIR, Account

CCUSAGE_PIN = "ccusage@20.0.19"
REFRESH_S = 900
TIMEOUT_S = 90


def _cache_path(account: Account) -> Path:
    return STATE_DIR / f"cost-{account.slug}.json"


def _read_cache(account: Account, max_age_s: int) -> float | None:
    try:
        blob = json.loads(_cache_path(account).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - blob.get("at", 0) > max_age_s:
        return None
    value = blob.get("cost")
    return float(value) if isinstance(value, (int, float)) else None


def _write_cache(account: Account, value: float) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(account).write_text(json.dumps({"at": time.time(), "cost": value}))


def _run_ccusage(account: Account) -> float | None:
    environment = dict(os.environ)
    environment["CLAUDE_CONFIG_DIR"] = str(account.path)
    today = time.strftime("%Y%m%d")
    try:
        result = subprocess.run(
            ["npx", "-y", CCUSAGE_PIN, "daily", "--json", "--since", today],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        blob = json.loads(result.stdout)
        total = blob["totals"]["totalCost"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(total, (int, float)):
        return None

    if _pricing_is_incomplete(blob):
        # Not an error visible any other way: ccusage still exits 0 and still
        # returns a well-formed, plausible-looking total. It just quietly prices
        # the models it has no rates for at zero.
        print("cost: pricing incomplete, discarding total", file=sys.stderr)
        return None
    return float(total)


def _pricing_is_incomplete(blob: dict) -> bool:
    """True when a model burned tokens but was priced at zero.

    ccusage prices from a table it fetches at runtime; when that fetch fails it
    falls back to a bundled table that lags new model releases, and anything
    missing costs $0. The failure is silent and the shortfall is not small — a
    day dominated by a brand-new model came back as $45 instead of $525, which
    looks entirely believable if you are not checking it against anything. A
    model with tokens and no cost is the tell, and it stays true whatever the
    next unpriced model turns out to be.
    """
    for day in blob.get("daily") or []:
        for model in day.get("modelBreakdowns") or []:
            tokens = sum(
                model.get(field) or 0
                for field in ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens")
            )
            if tokens > 0 and not model.get("cost"):
                return True
    return False


def today(account: Account, force: bool = False) -> float | None:
    if not force and (cached := _read_cache(account, REFRESH_S)) is not None:
        return cached
    value = _run_ccusage(account)
    if value is None:
        # Fall back to a stale figure rather than blanking the line; an hours-old
        # cost is still roughly right, and cost is not the reason to look at this.
        return _read_cache(account, REFRESH_S * 8)
    _write_cache(account, value)
    return value
