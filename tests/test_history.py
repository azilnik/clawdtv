"""The usage log that exists to replace guessed thresholds with measured ones.

The dedupe here had a real bug worth pinning: it compared each reading against
the *last row in the file*, but with two accounts writing alternately the last
row is always the other account, so every reading looked new and every tick
wrote. Comparison has to be per account.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv import history  # noqa: E402
from clawdtv.sources import AccountUsage, Window  # noqa: E402

NOW = datetime(2026, 8, 10, 16, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def temp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "STATE_DIR", tmp_path)
    monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "history.csv")
    monkeypatch.setattr(history, "SEEN_PATH", tmp_path / "history-last.json")
    return tmp_path


def _usages(five_a=30.0, seven_a=28.0, five_b=54.0, seven_b=7.0, now=NOW):
    return [
        AccountUsage(
            "PERSONAL",
            five_hour=Window(five_a, now + timedelta(hours=2)),
            seven_day=Window(seven_a, now + timedelta(days=3)),
            observed_at=now,
            source="endpoint",
            cost_today=548.27,
        ),
        AccountUsage(
            "WORK",
            five_hour=Window(five_b, now + timedelta(hours=2)),
            seven_day=Window(seven_b, now + timedelta(days=5)),
            observed_at=now,
            source="endpoint",
        ),
    ]


def test_first_write_creates_a_header_and_one_row_per_account() -> None:
    history.record(_usages(), NOW)
    rows = history.load()
    assert [row["account"] for row in rows] == ["PERSONAL", "WORK"]
    assert rows[0]["five_hour"] == "30.0"


def test_repeated_identical_ticks_do_not_accumulate_rows() -> None:
    """The bug: two accounts alternating made every reading look new."""
    for _ in range(5):
        history.record(_usages(), NOW)
    assert len(history.load()) == 2


def test_movement_is_recorded() -> None:
    history.record(_usages(), NOW)
    history.record(_usages(five_a=45.0), NOW + timedelta(seconds=30))
    rows = history.load()
    assert len(rows) == 3
    assert rows[-1]["account"] == "PERSONAL" and rows[-1]["five_hour"] == "45.0"


def test_a_quiet_account_is_still_sampled_periodically() -> None:
    """Even flat usage should leave a trace, so gaps mean 'not running' rather
    than 'nothing changed'."""
    history.record(_usages(), NOW)
    later = NOW + timedelta(seconds=history.MIN_INTERVAL_S + 1)
    history.record(_usages(now=NOW), later)
    assert len(history.load()) == 4


def test_errored_accounts_are_not_recorded() -> None:
    history.record([AccountUsage("WORK", error="not logged in")], NOW)
    assert history.load() == []


def test_summary_reports_the_distribution() -> None:
    for index in range(10):
        history.record(_usages(five_a=float(index * 10)), NOW + timedelta(seconds=600 * index))
    stats = history.summarize()["PERSONAL"]["five_hour"]
    assert stats["samples"] == 10
    assert stats["max"] == 90.0
    assert stats["over_60"] == pytest.approx(0.4)  # 60, 70, 80, 90


def test_a_missing_log_summarizes_to_nothing() -> None:
    assert history.summarize() == {}


def test_recording_never_raises(monkeypatch) -> None:
    """Losing a data point must never cost a tick."""
    monkeypatch.setattr(history, "STATE_DIR", Path("/nonexistent/denied"))
    monkeypatch.setattr(history, "HISTORY_PATH", Path("/nonexistent/denied/history.csv"))
    history.record(_usages(), NOW)
