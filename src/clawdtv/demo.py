"""Synthetic display states.

Shared by the contact sheet and the contrast tests so that visual review and
automated checks are looking at exactly the same set of cases. Every state the
renderer can produce should appear here — a state that is not in this list is a
state nobody has ever looked at. That includes both layouts: two stacked
accounts and the full-height single-account frame.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .sources import AccountUsage, Window

NOW = datetime(2026, 8, 10, 16, 2, tzinfo=UTC)

FIVE_RESET = timedelta(hours=2, minutes=38)
SEVEN_RESET = timedelta(days=2)


def _account(
    label: str,
    five: float | None,
    seven: float | None,
    five_reset: timedelta = FIVE_RESET,
    seven_reset: timedelta = SEVEN_RESET,
    **kwargs,
) -> AccountUsage:
    """Reset offsets are relative to NOW (negative means already rolled over).

    Each percentage is stated exactly once; passing a whole Window would repeat
    it in two places that have to be hand-synced, a footgun this file has
    already needed a regression test for.
    """
    defaults = {
        "five_hour": Window(five, NOW + five_reset),
        "seven_day": Window(seven, NOW + seven_reset),
        "observed_at": NOW,
        "source": "endpoint",
    }
    defaults.update(kwargs)
    return AccountUsage(label=label, **defaults)


def states() -> dict[str, list[AccountUsage]]:
    """Named states; most are two-account frames, the last few single-account."""
    return {
        "fresh / low": [
            _account("PERSONAL", 5, 23, cost_today=4.20),
            _account("WORK", 12, 8, cost_today=1.10),
        ],
        "just under warn (59%)": [
            _account("PERSONAL", 59, 44, cost_today=18.0),
            _account("WORK", 59, 59, cost_today=6.5),
        ],
        "warn boundary (60%)": [
            _account("PERSONAL", 60, 61, cost_today=22.4),
            _account("WORK", 60, 60, cost_today=7.0),
        ],
        "alert boundary (85%)": [
            _account("PERSONAL", 85, 85, cost_today=41.0),
            _account("WORK", 86, 92, cost_today=13.25),
        ],
        "maxed (100%)": [
            _account("PERSONAL", 100, 100, cost_today=120.0),
            _account("WORK", 100, 97, cost_today=88.5),
        ],
        "zero used": [
            _account("PERSONAL", 0, 0, cost_today=0.0),
            _account("WORK", 0, 0),
        ],
        "stale data": [
            _account("PERSONAL", 47, 33, observed_at=NOW - timedelta(minutes=52), source="cache"),
            _account("WORK", 71, 64, observed_at=NOW - timedelta(hours=9), source="cache"),
        ],
        "unknown values": [
            _account("PERSONAL", None, None),
            _account("WORK", None, 55),
        ],
        "window rolled over": [
            _account(
                "PERSONAL",
                93,
                40,
                five_reset=-timedelta(minutes=3),
                seven_reset=timedelta(days=1),
            ),
            _account("WORK", 22, 19),
        ],
        "amber 5h shows its reset": [
            _account("PERSONAL", 72, 40, five_reset=timedelta(minutes=42), cost_today=88.0),
            _account("WORK", 12, 8, cost_today=1.10),
        ],
        "high 5h shows its reset": [
            _account("PERSONAL", 88, 40, five_reset=timedelta(minutes=42), cost_today=241.0),
            _account("WORK", 12, 8, cost_today=1.10),
        ],
        "high weekly shows its reset": [
            _account("PERSONAL", 30, 91, seven_reset=timedelta(days=1, hours=6), cost_today=60.0),
            _account("WORK", 22, 19),
        ],
        "both windows high": [
            _account(
                "PERSONAL",
                71,
                84,
                five_reset=timedelta(minutes=55),
                seven_reset=timedelta(hours=30),
                cost_today=95.0,
            ),
            _account("WORK", 40, 22),
        ],
        "weekly urgent": [
            _account("PERSONAL", 30, 91, cost_today=241.0),
            _account("WORK", 30, 12, seven_reset=timedelta(days=5), cost_today=8.0),
        ],
        "long label crowds header": [
            _account("PERSONAL-WORK", 30, 88, cost_today=60.0),
            _account("WORK", 44, 12),
        ],
        "not signed in": [
            _account("PERSONAL", 31, 27, cost_today=9.0),
            AccountUsage(label="WORK", error="not logged in"),
        ],
        "token expired": [
            _account("PERSONAL", 66, 51),
            AccountUsage(label="WORK", error="token expired"),
        ],
        "both signed out": [
            AccountUsage(label="PERSONAL", error="not logged in"),
            AccountUsage(label="WORK", error="not logged in"),
        ],
        # -- single-account layout -------------------------------------------
        "single / comfortable": [
            _account("PERSONAL", 34, 18, cost_today=12.0),
        ],
        "single / over pace, reset near": [
            _account("PERSONAL", 78, 88, five_reset=timedelta(minutes=48), cost_today=241.0),
        ],
        "single / unknown": [
            _account("PERSONAL", None, None),
        ],
        "single / signed out": [
            AccountUsage(label="PERSONAL", error="not logged in"),
        ],
    }
