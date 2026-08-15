"""Reset times appear only under pressure, and read relatively.

Two separate decisions here.

*When* to show one: only once that window's usage has reached the warn
threshold. "How long until relief" is worth reading when you are deciding
whether to push on or stop; at 20% used it is trivia no matter how soon it
lands. Tying it to the warn threshold makes it one rule rather than two — when a
bar turns amber, its reset time appears with it.

*How* to show it: as a duration, because "in 40m" is directly actionable while
"4:30p" makes you do the subtraction.

The threshold itself is a hand-picked round number, not a derived one. See
history.py, which exists to replace it with something measured.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv import render  # noqa: E402
from clawdtv.sources import Window  # noqa: E402

NOW = datetime(2026, 8, 10, 16, 0, tzinfo=UTC)
WARN = 60.0


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (30, "1m"),          # never "0m"; something is always still to come
        (60, "1m"),
        (44 * 60, "44m"),
        (59 * 60 + 59, "60m"),
        (3600, "1h"),
        (90 * 60, "2h"),     # rounded up: promising 1h and delivering 1.5h would be a lie
        (23 * 3600, "23h"),
        (86400, "1 day"),
        (36 * 3600, "2 days"),
        (7 * 86400, "7 days"),
    ],
)
def test_relative_time(seconds: int, expected: str) -> None:
    assert render.relative_time(seconds) == expected


@pytest.mark.parametrize("used,shown", [(0, False), (45, False), (59, False), (60, True), (95, True)])
def test_reset_appears_only_at_or_above_the_threshold(used: float, shown: bool) -> None:
    window = Window(used, NOW + timedelta(hours=2))
    result = render.describe_reset(window, "5h", NOW, WARN)
    assert (result is not None) is shown


def test_time_remaining_does_not_decide_visibility() -> None:
    """The old rule was closeness. A reset five minutes out but barely used is
    still not worth saying; one days out but nearly spent is."""
    barely_used_but_imminent = Window(10, NOW + timedelta(minutes=5))
    heavily_used_but_distant = Window(92, NOW + timedelta(days=3))
    assert render.describe_reset(barely_used_but_imminent, "5h", NOW, WARN) is None
    assert render.describe_reset(heavily_used_but_distant, "7d", NOW, WARN) == "7d in 3 days"


def test_reset_text_is_relative_not_a_clock_time() -> None:
    window = Window(90, NOW + timedelta(minutes=40))
    assert render.describe_reset(window, "5h", NOW, WARN) == "5h in 40m"


def test_a_passed_reset_is_always_reported_whatever_the_usage() -> None:
    """Rolled over is the explanation for the '--' the row is about to draw, so
    it is reported even for a window that never got busy."""
    window = Window(3, NOW - timedelta(minutes=1))
    assert render.describe_reset(window, "5h", NOW, WARN) == "5h reset"


def test_unknown_usage_shows_no_reset() -> None:
    assert render.describe_reset(Window(None, NOW + timedelta(hours=1)), "5h", NOW, WARN) is None


def test_no_reset_time_means_nothing_to_say() -> None:
    assert render.describe_reset(Window(90, None), "5h", NOW, WARN) is None


def test_the_common_case_shows_no_reset_at_all() -> None:
    """Below the threshold, the header should be just the account name."""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    five = Window(30, NOW + timedelta(hours=3))
    seven = Window(20, NOW + timedelta(days=5))
    assert render._reset_status(draw, 96, five, seven, NOW, WARN) is None


def test_when_both_are_high_the_fuller_window_wins_the_slot() -> None:
    """Both forms will not fit beside a label, so the limit you are closer to
    actually hitting is the one that gets said."""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    five = Window(64, NOW + timedelta(hours=1))
    seven = Window(93, NOW + timedelta(days=2))
    assert render._reset_status(draw, 96, five, seven, NOW, WARN) == "7d in 2 days"
