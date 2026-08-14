"""Palette, type scale, and the contrast math that keeps them honest.

The panel is 240x240 in a 1.54" square, which works out to roughly 220 pixels per
inch. A 16px glyph is about 1.8mm tall there, so type that would be unremarkable
on a monitor is genuinely hard to read on a desk. Hence the floor below: nothing
smaller than 18px ships, and when something does not fit, content gets cut rather
than shrunk.

Colors are checked against WCAG 2.2: 4.5:1 for text (1.4.3) and 3:1 for the
meaningful non-text parts (1.4.11). test_contrast.py enforces this on the
compressed JPEG, not just on these constants.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PIL import ImageFont

RGB = tuple[int, int, int]

# --- palette ---------------------------------------------------------------
# The bar track is drawn as a dark interior inside a lighter edge rather than as
# one flat mid-gray. A flat track has to be bright enough to clear 3:1 against
# the background, or you cannot see how long the bar could be — but that same
# brightness makes every empty bar a prominent slab competing with the fills,
# and it squeezes the fill-versus-track contrast down toward the 3:1 floor.
# Putting the contrast in the *edge* satisfies the same requirement, because the
# edge is what identifies the component's extent. The interior is then free to
# recede, and fills clear 7:1 or better against it instead of barely 3:1.
BG: RGB = (0, 0, 0)
TEXT: RGB = (255, 255, 255)
LABEL: RGB = (200, 200, 212)
MUTED: RGB = (154, 154, 166)
FOOTER: RGB = (190, 190, 202)
TRACK_FILL: RGB = (36, 36, 44)
TRACK_EDGE: RGB = (108, 108, 124)

OK: RGB = (52, 211, 153)
WARN: RGB = (251, 191, 36)
# Leaned slightly toward rose rather than pure salmon: blue perception survives
# red-green color blindness, so the extra blue keeps ALERT separable from WARN
# for a deuteranope instead of both collapsing to the same pale yellow.
ALERT: RGB = (250, 150, 175)
DEAD: RGB = (125, 125, 137)

# --- type scale ------------------------------------------------------------
# The numeral sizes themselves live with the layout in render.Geometry, since
# they differ between the one- and two-account frames. What lives here is
# policy: the floor, and the sizes shared by every layout.
MIN_SIZE = 18
SIZE_LABEL = 19
SIZE_SMALL = 18
# The footer sits at the bottom edge and gets read last and least; at the 18px
# floor it was legible in principle and a squint in practice on the dimmed panel.
SIZE_FOOTER = 21

# Condensed is reserved for the large numerals, where it buys ~23% of the width
# at no cost to legibility. Anything at or near the floor stays full-width, since
# narrowing letterforms is exactly the wrong move at 18px.
WEIGHT_NUMBER = "Condensed Bold"
WEIGHT_LABEL = "Semibold"
WEIGHT_SMALL = "Medium"

# The San Francisco variable font ships with every macOS this tool can run on;
# Helvetica is the fallback if Apple ever moves it. Helvetica has no variable
# weights, so the weight request silently degrades — legible, just less tuned.
FONT_CANDIDATES = (
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


@cache
def _font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "no usable system font found — clawdtv renders with macOS system fonts"
    )


@cache
def font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    """A system font at a given size and weight.

    Sizes below the floor are a bug, not a style choice, so they raise rather
    than quietly rendering something unreadable.
    """
    if size < MIN_SIZE:
        raise ValueError(f"{size}px is below the {MIN_SIZE}px legibility floor")
    face = ImageFont.truetype(_font_path(), size)
    try:
        face.set_variation_by_name(weight)
    except (OSError, AttributeError):
        pass
    return face


# --- contrast --------------------------------------------------------------
def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: RGB) -> float:
    r, g, b = (_channel(c) for c in color[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def level_color(percent: float | None, warn_at: float, alert_at: float) -> RGB:
    """Threshold color. Never the only signal — the number and bar length carry
    the same information for anyone who cannot separate these hues."""
    if percent is None:
        return DEAD
    if percent >= alert_at:
        return ALERT
    if percent >= warn_at:
        return WARN
    return OK
