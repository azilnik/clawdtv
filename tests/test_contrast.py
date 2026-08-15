"""WCAG contrast enforcement.

Two layers. The first checks the palette constants, which is the easy part. The
second renders real frames, compresses them at the quality we actually ship, and
measures contrast on the decoded pixels — because JPEG is a lossy, chroma-
subsampled format and thin light-on-dark strokes are exactly what it degrades.
A palette that passes on paper and fails after compression is still a failure.

Text is held to 4.5:1 (WCAG 1.4.3) and meaningful non-text to 3:1 (1.4.11).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv import config, demo, render, theme  # noqa: E402

TEXT_MIN = 4.5
NON_TEXT_MIN = 3.0
JPEG_QUALITY = 85

TEXT_COLORS = {
    "TEXT": theme.TEXT,
    "LABEL": theme.LABEL,
    "MUTED": theme.MUTED,
    "FOOTER": theme.FOOTER,
    "DEAD": theme.DEAD,
}
FILL_COLORS = {"OK": theme.OK, "WARN": theme.WARN, "ALERT": theme.ALERT}


@pytest.mark.parametrize("name,color", TEXT_COLORS.items())
def test_text_on_background(name: str, color) -> None:
    ratio = theme.contrast(color, theme.BG)
    assert ratio >= TEXT_MIN, f"{name} on background is {ratio:.2f}:1, needs {TEXT_MIN}:1"


def test_track_edge_is_visible_against_background() -> None:
    """The edge identifies the bar's extent, so it is the part that must clear 3:1.
    The interior is deliberately recessive and exempt."""
    ratio = theme.contrast(theme.TRACK_EDGE, theme.BG)
    assert ratio >= NON_TEXT_MIN, f"TRACK_EDGE on background is {ratio:.2f}:1"


@pytest.mark.parametrize("name,color", FILL_COLORS.items())
def test_fill_against_track(name: str, color) -> None:
    """The boundary between fill and empty lane is what encodes the value."""
    ratio = theme.contrast(color, theme.TRACK_FILL)
    assert ratio >= NON_TEXT_MIN, f"{name} fill against track interior is {ratio:.2f}:1"


@pytest.mark.parametrize("name,color", FILL_COLORS.items())
def test_fill_against_background(name: str, color) -> None:
    ratio = theme.contrast(color, theme.BG)
    assert ratio >= NON_TEXT_MIN, f"{name} fill on background is {ratio:.2f}:1"


def _jpeg_roundtrip(image: Image.Image) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def _stroke_contrast(color, size: int, weight: str, sample: str = "88%") -> float:
    """Render real text, compress it, and measure the brightest surviving pixel.

    Antialiasing means a thin stroke never reaches its nominal color, and JPEG
    erodes it further. The brightest pixel in the glyph is the best case the
    viewer actually gets, so that is what gets measured.
    """
    image = Image.new("RGB", (120, size * 2), theme.BG)
    draw = ImageDraw.Draw(image)
    draw.text((4, size // 2), sample, font=theme.font(size, weight), fill=color)
    decoded = _jpeg_roundtrip(image)

    brightest = max(
        (decoded.getpixel((x, y)) for y in range(decoded.height) for x in range(decoded.width)),
        key=theme.luminance,
    )
    background = decoded.getpixel((decoded.width - 2, 1))
    return theme.contrast(brightest, background)


@pytest.mark.parametrize(
    "name,color,size,weight",
    [
        ("MUTED small", theme.MUTED, theme.SIZE_SMALL, theme.WEIGHT_SMALL),
        ("FOOTER cost", theme.FOOTER, theme.SIZE_FOOTER, theme.WEIGHT_SMALL),
        ("LABEL heading", theme.LABEL, theme.SIZE_LABEL, theme.WEIGHT_LABEL),
        # The two-up hero is the smaller of the two layouts' numerals, so it is
        # the worst case for stroke survival under compression.
        ("DEAD dashes", theme.DEAD, render.TWO_UP.hero_size, theme.WEIGHT_NUMBER),
        ("TEXT hero", theme.TEXT, render.TWO_UP.hero_size, theme.WEIGHT_NUMBER),
    ],
)
def test_text_survives_compression(name: str, color, size: int, weight: str) -> None:
    ratio = _stroke_contrast(color, size, weight)
    assert ratio >= TEXT_MIN, f"{name} after JPEG is {ratio:.2f}:1, needs {TEXT_MIN}:1"


def _hero_bar_top(geo: render.Geometry) -> int:
    """Top edge of the first panel's hero bar, derived rather than guessed."""
    top = geo.panel_ys[0] + geo.hero_dy
    return top + (geo.hero_size - geo.hero_bar_h) // 2 + 3


@pytest.mark.parametrize(
    "state,expected",
    [
        ("warn boundary (60%)", "WARN"),
        ("alert boundary (85%)", "ALERT"),
        ("single / over pace, reset near", "WARN"),
    ],
)
def test_bar_fill_is_distinguishable_from_track_after_compression(state: str, expected: str) -> None:
    """Measured on the real frame: the fill/track boundary is what encodes the value."""
    cfg = config.load()
    usages = demo.states()[state]
    geo = render.geometry(len(usages))
    frame = _jpeg_roundtrip(render.render(usages, cfg, demo.NOW))
    row = _hero_bar_top(geo) + geo.hero_bar_h // 2

    fill = frame.getpixel((geo.bar_x + 12, row))
    track = frame.getpixel((render.BAR_RIGHT - 12, row))
    ratio = theme.contrast(fill, track)
    assert ratio >= NON_TEXT_MIN, f"{expected} fill vs track after JPEG is {ratio:.2f}:1"


def test_track_edge_survives_compression() -> None:
    """A one-pixel line is precisely what JPEG erodes, so measure the real thing.

    Scans the column through an empty part of the bar and takes the brightest
    pixel, which is the edge as actually rendered and decoded.
    """
    cfg = config.load()
    geo = render.TWO_UP
    frame = _jpeg_roundtrip(render.render(demo.states()["zero used"], cfg, demo.NOW))

    column = render.BAR_RIGHT - 24
    bar_top = _hero_bar_top(geo)
    edge = max(
        (frame.getpixel((column, y)) for y in range(bar_top - 2, bar_top + 4)),
        key=theme.luminance,
    )
    background = frame.getpixel((column, geo.panel_ys[0] + 2))
    ratio = theme.contrast(edge, background)
    assert ratio >= NON_TEXT_MIN, f"track edge vs background after JPEG is {ratio:.2f}:1"


def test_every_demo_state_renders_within_bounds() -> None:
    """No state may draw outside the panel or exceed what the device will accept."""
    cfg = config.load()
    for name, usages in demo.states().items():
        image = render.render(usages, cfg, demo.NOW)
        assert image.size == (render.W, render.H), name
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
        assert buffer.tell() < 100_000, f"{name} is {buffer.tell()} bytes; device wants under 100KB"


def test_type_floor_is_enforced() -> None:
    with pytest.raises(ValueError):
        theme.font(theme.MIN_SIZE - 1)


def test_header_text_never_overruns_the_panel() -> None:
    """Account labels come from config, so the header has to measure, not assume."""
    from PIL import ImageDraw

    cfg = config.load()
    draw = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    label_font = theme.font(theme.SIZE_LABEL, theme.WEIGHT_LABEL)
    small = theme.font(theme.SIZE_SMALL, theme.WEIGHT_SMALL)

    for name, usages in demo.states().items():
        for usage in usages:
            label_w = draw.textlength(usage.label, font=label_font)
            status = render._reset_status(
                draw, label_w, usage.five_hour, usage.seven_day, demo.NOW, cfg.warn_at
            )
            if not status:
                continue
            total = render.MARGIN + label_w + 8 + draw.textlength(status, font=small)
            assert total <= render.BAR_RIGHT, f"{name}/{usage.label}: header is {total:.0f}px"


@pytest.mark.parametrize(
    "used,pace_value,expect_split",
    [(42, 40, False), (45, 40, False), (46, 40, True), (80, 20, True), (3, 0.5, False)],
)
def test_pace_deadband(used: float, pace_value: float, expect_split: bool) -> None:
    """Early in a window pace is near zero, so a bare `>` would flag any usage."""
    bar = render._render_bar(120, 18, used, theme.OK, pace_value)
    colors = {bar.getpixel((x, 9)) for x in range(4, 116)}
    muted = render._mix(theme.OK, theme.TRACK_FILL, render.PACE_TINT)
    assert (muted in colors) is expect_split
