"""Regenerate the images the README embeds.

Kept as a script rather than hand-made screenshots so the documentation cannot
drift away from what the renderer actually produces. Everything here comes from
demo.py's state list — the same states the contrast tests measure.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

from clawdtv import config, demo, render  # noqa: E402
from clawdtv.sources import AccountUsage, Window  # noqa: E402

import contactsheet  # noqa: E402

ASSETS = Path(__file__).resolve().parents[1] / "assets"

# README walkthrough shots: filename -> demo state. One image per behavior the
# README explains, so the prose and the pixels stay in sync.
WALKTHROUGH = {
    "state-comfortable": "fresh / low",
    "state-warn-reset": "amber 5h shows its reset",
    "state-over-pace": "weekly urgent",
    "state-alert": "alert boundary (85%)",
    "state-unknown": "unknown values",
    "state-stale": "stale data",
    "state-rolled-over": "window rolled over",
    "state-signed-out": "not signed in",
    "single-account": "single / over pace, reset near",
}


def hero(cfg) -> Image.Image:
    """One panel over pace and one under, with a reset close enough to be worth
    saying — every distinct behavior the display has, in one believable frame.
    No cost line: the dollars are opt-in, so the default look is the honest one."""
    now = demo.NOW
    usages = [
        AccountUsage(
            label="PERSONAL",
            five_hour=Window(78, now + timedelta(minutes=48)),
            seven_day=Window(88, now + timedelta(days=2)),
            observed_at=now,
        ),
        AccountUsage(
            label="WORK",
            five_hour=Window(26, now + timedelta(hours=3, minutes=20)),
            seven_day=Window(4, now + timedelta(days=5)),
            observed_at=now,
        ),
    ]
    return render.render(usages, cfg, now)


def at_2x(frame: Image.Image) -> Image.Image:
    # Nearest-neighbor so the upscale stays honest about the pixel grid.
    return frame.resize((frame.width * 2, frame.height * 2), Image.NEAREST)


def main() -> None:
    cfg = config.load()
    ASSETS.mkdir(exist_ok=True)
    states = demo.states()

    at_2x(hero(cfg)).save(ASSETS / "screen.png")

    for filename, state in WALKTHROUGH.items():
        at_2x(render.render(states[state], cfg, demo.NOW)).save(ASSETS / f"{filename}.png")

    frames = [(name, render.render(u, cfg, demo.NOW)) for name, u in states.items()]
    contactsheet.sheet(frames, scale=1, cols=5).save(ASSETS / "states.png")

    print(f"wrote screen.png, {len(WALKTHROUGH)} walkthrough shots, and states.png ({len(frames)} states)")


if __name__ == "__main__":
    main()
