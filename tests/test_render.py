"""Rendering depends on the time it is given, never on the time it is run.

This was a real bug. `Window.reset_passed` read the wall clock instead of the
`now` threaded through the renderer, so a frame rendered for a fixed instant
changed meaning as the day wore on: once real time passed a demo state's reset
timestamp, that state quietly began rendering as "reset" with no value and no
bar. Tests passed in the morning and failed in the afternoon, and the renderer
was internally inconsistent — the rest of the frame used the passed-in time.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv import config, demo, render  # noqa: E402
from clawdtv.sources import AccountUsage, Window  # noqa: E402

PAST = datetime(2020, 1, 1, tzinfo=UTC)
FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


def test_reset_passed_uses_the_given_time_not_the_clock() -> None:
    window = Window(50, datetime(2026, 8, 10, 18, 40, tzinfo=UTC))
    assert not window.reset_passed(window.resets_at - timedelta(minutes=1))
    assert window.reset_passed(window.resets_at + timedelta(minutes=1))
    # The bug: real "now" is neither of these, and used to decide the answer.
    assert not window.reset_passed(PAST)
    assert window.reset_passed(FUTURE)


def test_pace_is_computed_against_the_given_time() -> None:
    """Three hours into a five-hour window is 60% elapsed, whenever this runs."""
    now = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    window = Window(40, now + timedelta(hours=2))
    assert round(render.pace(window, render.FIVE_HOUR_S, now)) == 60


def test_a_fixed_state_renders_identically_every_time() -> None:
    cfg = config.load()
    first = render.render(demo.states()["fresh / low"], cfg, demo.NOW)
    second = render.render(demo.states()["fresh / low"], cfg, demo.NOW)
    assert first.tobytes() == second.tobytes()


def test_demo_states_still_show_their_values() -> None:
    """The bug's visible symptom: every window read as rolled over, so every
    number became '--' and every bar emptied. Guard the symptom, not just the
    cause."""
    for name, usages in demo.states().items():
        if name == "window rolled over":
            continue  # the one state where a passed reset is the point
        for usage in usages:
            if usage.error or not usage.five_hour.known:
                continue
            assert not usage.five_hour.reset_passed(demo.NOW), (
                f"{name}/{usage.label}: five-hour window reads as already reset at demo.NOW"
            )


def test_window_rolled_over_state_is_deliberate() -> None:
    """One state is supposed to have a passed reset; it should stay that way."""
    usage = demo.states()["window rolled over"][0]
    assert usage.five_hour.reset_passed(demo.NOW)
    assert not usage.seven_day.reset_passed(demo.NOW)


def test_one_account_uses_the_full_height_layout() -> None:
    """A lone account is not a two-account frame with an empty half: its rows
    are larger and sit lower, so the two layouts must render differently."""
    cfg = config.load()
    account = demo.states()["fresh / low"][0]
    single = render.render([account], cfg, demo.NOW)
    top_of_pair = render.render([account, AccountUsage("WORK", error="not logged in")], cfg, demo.NOW)
    assert single.tobytes() != top_of_pair.tobytes()

    # The single layout must leave no dead lower half: its secondary row starts
    # below where the two-up frame's second panel would.
    geo = render.geometry(1)
    assert geo.panel_ys[0] + geo.secondary_dy > render.TWO_UP.panel_ys[1]


def test_zero_or_three_accounts_is_a_bug() -> None:
    cfg = config.load()
    with pytest.raises(ValueError):
        render.render([], cfg, demo.NOW)
    with pytest.raises(ValueError):
        render.render([demo.states()["fresh / low"][0]] * 3, cfg, demo.NOW)


def test_single_account_cost_carries_no_initial() -> None:
    """'P $12' disambiguates between two accounts; alone it is just noise."""
    cfg = config.load()
    account = demo.states()["fresh / low"][0]  # cost_today set
    pair = demo.states()["fresh / low"]
    assert account.cost_today is not None
    # Rendered pixels differ in the footer's right corner; compare crops.
    single = render.render([account], cfg, demo.NOW).crop((120, render.FOOTER_Y, 240, 240))
    double = render.render(pair, cfg, demo.NOW).crop((120, render.FOOTER_Y, 240, 240))
    assert single.tobytes() != double.tobytes()


def test_unknown_never_renders_as_zero() -> None:
    cfg = config.load()
    unknown = [
        AccountUsage("PERSONAL", five_hour=Window(None, None), seven_day=Window(None, None)),
        AccountUsage("WORK", error="not logged in"),
    ]
    zeroed = [
        AccountUsage(
            "PERSONAL",
            five_hour=Window(0, demo.NOW + timedelta(hours=3)),
            seven_day=Window(0, demo.NOW + timedelta(days=3)),
            observed_at=demo.NOW,
        ),
        AccountUsage("WORK", error="not logged in"),
    ]
    assert render.render(unknown, cfg, demo.NOW).tobytes() != render.render(zeroed, cfg, demo.NOW).tobytes()
