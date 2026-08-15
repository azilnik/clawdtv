"""Guarding against silently mispriced cost totals.

ccusage prices from a table it fetches at runtime. When that fetch fails it falls
back to a bundled table that lags new model releases and prices anything missing
at zero — while still exiting 0 and returning a well-formed, believable total.
Observed in the wild: a day that really cost $527 came back as $45 because the
model responsible for $482 of it had no entry. Nothing about the output says so;
the only tell is a model with tokens and no cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv.cost import _pricing_is_incomplete  # noqa: E402


def _blob(*models: dict) -> dict:
    return {"daily": [{"period": "2026-08-10", "modelBreakdowns": list(models)}]}


def _model(name: str, cost: float = 1.0, output: int = 1000, cache_read: int = 0, inp: int = 10) -> dict:
    return {
        "modelName": name,
        "cost": cost,
        "inputTokens": inp,
        "outputTokens": output,
        "cacheCreationTokens": 0,
        "cacheReadTokens": cache_read,
    }


def test_fully_priced_day_is_accepted() -> None:
    assert not _pricing_is_incomplete(_blob(_model("a", 32.77), _model("b", 1.65)))


def test_unpriced_model_is_caught() -> None:
    """The real failure: one new model at zero drags the total down by 90%."""
    assert _pricing_is_incomplete(_blob(_model("sonnet", 32.77), _model("opus-5", 0.0, output=1_664_223)))


def test_missing_cost_field_is_caught() -> None:
    model = _model("opus-5")
    del model["cost"]
    assert _pricing_is_incomplete(_blob(model))


def test_a_model_with_no_tokens_may_legitimately_cost_nothing() -> None:
    """Zero cost is only suspicious when tokens were actually burned."""
    assert not _pricing_is_incomplete(_blob(_model("idle", 0.0, output=0, inp=0)))


def test_cache_reads_alone_count_as_usage() -> None:
    """A cache-read-only day still costs money, so zero there is still wrong."""
    assert _pricing_is_incomplete(
        _blob(_model("cached", 0.0, output=0, inp=0, cache_read=500_000))
    )


@pytest.mark.parametrize("blob", [{}, {"daily": []}, {"daily": [{}]}, {"daily": None}])
def test_shapes_without_breakdowns_are_not_flagged(blob: dict) -> None:
    """Absent detail is not evidence of mispricing; only fail on positive evidence."""
    assert not _pricing_is_incomplete(blob)
