"""A record of what usage actually looks like over time.

The thresholds that drive the display — amber at 60%, coral at 85%, and the
reset time appearing with the amber — are round numbers picked by hand. Nothing
derived them from how these accounts are really used, and the daily token volume
they sit on top of has swung by more than two orders of magnitude inside a
fortnight, which is exactly the shape that fixed cutoffs fit badly.

We already fetch a utilization reading every few minutes and throw all but the
newest away. This keeps them. A few weeks of that is enough to answer the
questions the thresholds are guessing at: where do these windows usually peak,
how often is 60% actually reached, is 85% a rare event or a Tuesday.

Deliberately a plain CSV. It should be readable with `column -s, -t` or dropped
into a spreadsheet without this project's help, and it should still be readable
if this project is deleted.

`clawdtv history` prints the distribution. Nothing reads this file automatically
yet — grounding the thresholds is a decision to make from the data, not one to
let the display quietly make for itself.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from .config import STATE_DIR

HISTORY_PATH = STATE_DIR / "history.csv"
SEEN_PATH = STATE_DIR / "history-last.json"
FIELDS = ["observed_at", "account", "five_hour", "seven_day", "source", "cost_today"]

# One row per account per tick would be ~2,900 rows a day of near-duplicates.
# Usage only moves when work happens, so only record real movement.
MIN_INTERVAL_S = 300
MIN_DELTA = 1.0


def _read_seen() -> dict:
    """Last value written per account.

    Kept beside the log rather than derived from it: the comparison has to be
    per account, and re-reading an ever-growing CSV once a minute to find two
    rows would get slower every day it works correctly.
    """
    try:
        return json.loads(SEEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _changed_enough(previous: dict | None, usage, now: datetime) -> bool:
    if not previous:
        return True
    try:
        age = now.timestamp() - float(previous["at"])
        before = float(previous["five_hour"]), float(previous["seven_day"])
    except (KeyError, TypeError, ValueError):
        return True
    if age >= MIN_INTERVAL_S:
        return True
    current = usage.five_hour.percent or 0, usage.seven_day.percent or 0
    return any(abs(a - b) >= MIN_DELTA for a, b in zip(current, before))


def record(usages, now: datetime | None = None) -> None:
    """Append readings worth keeping. Never raises: losing a data point matters
    far less than a tick failing over one."""
    now = now or datetime.now(UTC)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        is_new = not HISTORY_PATH.exists()
        seen = _read_seen()

        rows = []
        for usage in usages:
            if usage.error or not (usage.five_hour.known or usage.seven_day.known):
                continue
            if not _changed_enough(seen.get(usage.label), usage, now):
                continue
            seen[usage.label] = {
                "at": now.timestamp(),
                "five_hour": usage.five_hour.percent or 0,
                "seven_day": usage.seven_day.percent or 0,
            }
            rows.append(
                {
                    "observed_at": f"{(usage.observed_at or now).timestamp():.0f}",
                    "account": usage.label,
                    "five_hour": usage.five_hour.percent if usage.five_hour.known else "",
                    "seven_day": usage.seven_day.percent if usage.seven_day.known else "",
                    "source": usage.source,
                    "cost_today": f"{usage.cost_today:.2f}" if usage.cost_today is not None else "",
                }
            )
        if not rows:
            return
        with HISTORY_PATH.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerows(rows)
        SEEN_PATH.write_text(json.dumps(seen))
    except OSError:
        pass


def load() -> list[dict]:
    try:
        with HISTORY_PATH.open() as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarize() -> dict[str, dict]:
    """Per account, the distribution of each window's observed utilization."""
    by_account: dict[str, dict[str, list[float]]] = {}
    for row in load():
        bucket = by_account.setdefault(row["account"], {"five_hour": [], "seven_day": []})
        for field in ("five_hour", "seven_day"):
            try:
                bucket[field].append(float(row[field]))
            except (TypeError, ValueError):
                continue

    summary = {}
    for account, windows in by_account.items():
        summary[account] = {
            field: {
                "samples": len(values),
                "median": _percentile(values, 0.5),
                "p90": _percentile(values, 0.9),
                "max": max(values) if values else 0.0,
                "over_60": sum(1 for v in values if v >= 60) / len(values) if values else 0.0,
                "over_85": sum(1 for v in values if v >= 85) / len(values) if values else 0.0,
            }
            for field, values in windows.items()
        }
    return summary
