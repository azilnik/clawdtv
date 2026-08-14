"""Compose the 240x240 frame.

Layout is one or two account panels over a shared footer — two accounts stack
in half-height panels, a single account spreads over the full screen. Each
panel is a heading and two metric rows; each row is a fixed tag, a
right-aligned number, and a bar that runs to the right margin. The bars share
one x-range across every row and every account, so their lengths can be
compared directly — that comparison is the whole point of the display, and it
is the first thing lost if each bar is sized by whatever text happens to sit
beside it.

Reset times ride at the right of the panel heading rather than beside their bars,
which is what makes the full-width bars possible — and they only appear once a
window is near its end, so that slot is usually empty. Status messages (stale,
signed out) take the same slot when they apply, since knowing the number is
wrong outranks knowing when the window turns over.

Three rules hold the design together:

* Nothing renders below the type floor in theme.py.
* Color never carries meaning alone. Threshold state is also in the number and
  the bar length; staleness is spelled out in words, not implied by a dimmer gray.
* An unknown value is never drawn as zero. Zero means "you have used nothing",
  which is the most dangerous thing to say when the truth is "I do not know".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from PIL import Image, ImageDraw

from . import theme
from .config import Config
from .sources import AccountUsage, Window

W = H = 240
MARGIN = 8
FOOTER_Y = 212

TAG_X = MARGIN
BAR_RIGHT = W - MARGIN


# Vertical gap between a stacked row's text line and its bar.
STACK_BAR_GAP = 8


@dataclass(frozen=True)
class Geometry:
    """Pixel layout for the account panels, chosen by how many share the frame.

    Two accounts stack in two half-height panels, each row a tag, a number, and
    a bar side by side. A single account gets the whole screen and switches to
    a stacked row: the number rides its own line and the bar runs the full
    frame width beneath it — nearly double the bar resolution, which is the
    point of having the space.
    """

    panel_ys: tuple[int, ...]
    hero_dy: int  # row offsets from the panel top
    secondary_dy: int
    hero_size: int  # numeral sizes, px
    secondary_size: int
    hero_bar_h: int
    secondary_bar_h: int
    number_right: int  # right edge shared by both numbers
    stacked: bool = False  # bar below the text line rather than beside it

    @property
    def bar_left(self) -> int:
        """Stacked bars span the full width; side-by-side bars start one
        standard gutter after the number column — derived, so widening the
        column cannot silently leave the bars overlapping it."""
        return MARGIN if self.stacked else self.number_right + MARGIN

    def bar_top(self, panel_y: int, hero: bool) -> int:
        """One formula shared with the contrast tests, so the pixels they
        sample are the pixels the renderer painted."""
        row_y = panel_y + (self.hero_dy if hero else self.secondary_dy)
        size = self.hero_size if hero else self.secondary_size
        if self.stacked:
            return row_y + size + STACK_BAR_GAP
        bar_h = self.hero_bar_h if hero else self.secondary_bar_h
        return row_y + (size - bar_h) // 2 + 3


TWO_UP = Geometry(
    panel_ys=(4, 108),
    hero_dy=24,
    secondary_dy=68,
    hero_size=40,
    secondary_size=25,
    hero_bar_h=18,
    secondary_bar_h=12,
    number_right=100,
)

# The single-account frame: label, then each window as a full-width band —
# big right-aligned numeral over a margin-to-margin bar.
ONE_UP = Geometry(
    panel_ys=(8,),
    hero_dy=36,
    secondary_dy=140,
    hero_size=56,
    secondary_size=34,
    hero_bar_h=32,
    secondary_bar_h=20,
    number_right=W - MARGIN,
    stacked=True,
)


def geometry(account_count: int) -> Geometry:
    if not 1 <= account_count <= 2:
        raise ValueError(f"the display fits one or two accounts, got {account_count}")
    return ONE_UP if account_count == 1 else TWO_UP


FIVE_HOUR_S = 5 * 3600
SEVEN_DAY_S = 7 * 86400

# How far the on-pace portion of a fill is blended toward the track interior once
# usage runs over pace. 0.45 is the most muting that keeps every fill above 3:1
# against that interior.
PACE_TINT = 0.45

# How far past pace usage has to run before the bar splits. Without this, the
# start of a window is pathological: elapsed time is near zero, so pace is near
# zero, and any usage at all reads as running hot. Ten minutes into a five-hour
# window, spending 3% would light up. The split should mean "you are genuinely
# ahead of a sustainable rate", not "you have used something recently".
PACE_DEADBAND = 5.0


@dataclass
class Row:
    tag: str
    percent: float | None
    size: int
    bar_h: int
    pace: float | None = None


def clock(dt: datetime, quantize_min: int = 1) -> str:
    """4:30p — a colon and one letter is all the space allows.

    The footer's clock is quantized because it is what tells you the frame
    itself is current, and an unquantized one would change every minute, making
    every frame unique and forcing a flash write per tick for no new
    information. At five-minute resolution it still answers "is this thing
    still updating?" while letting unchanged frames skip the write entirely.
    """
    local = dt.astimezone()
    minute = local.minute - (local.minute % quantize_min) if quantize_min > 1 else local.minute
    hour = local.hour % 12 or 12
    return f"{hour}:{minute:02d}{'a' if local.hour < 12 else 'p'}"


FOOTER_CLOCK_QUANTIZE_MIN = 5


# A reset earns space once its own window is under pressure, not once it happens
# to be near in time. "How long until relief" is only worth reading when you are
# far enough through a limit to be deciding whether to push on or stop; at 20%
# used it is trivia whenever it lands. Tied to the warn threshold so this is one
# rule rather than two: when a bar turns amber, its reset time appears with it.
#
# The threshold itself is not yet grounded — see `history.py`. It is a round
# number pending a real distribution of observed peaks.


def relative_time(seconds: float) -> str:
    """"45m", "2h", "2 days" — rounded up, never down.

    Rounding up is the conservative direction for a reset: being told relief is
    two hours away and getting it in ninety minutes is a pleasant surprise, while
    the reverse is the display having lied to you.
    """
    if seconds < 3600:
        return f"{max(1, ceil(seconds / 60))}m"
    if seconds < 86400:
        return f"{ceil(seconds / 3600)}h"
    days = ceil(seconds / 86400)
    return "1 day" if days == 1 else f"{days} days"


def describe_reset(window: Window, prefix: str, now: datetime, threshold: float) -> str | None:
    """How long until this window turns over, or None while that is not yet news."""
    if window.resets_at is None:
        return None
    # A rolled-over window always says so regardless of what its usage was: it is
    # the explanation for the "--" the row is about to render.
    if window.reset_passed(now):
        return f"{prefix} reset"
    if not window.known or window.percent < threshold:
        return None
    remaining = (window.resets_at - now).total_seconds()
    return f"{prefix} in {relative_time(remaining)}" if remaining > 0 else None


def _reset_status(
    draw: ImageDraw.ImageDraw,
    label_w: float,
    five: Window,
    seven: Window,
    now: datetime,
    threshold: float,
) -> str | None:
    """Header reset text, narrowed until it actually fits beside the label.

    Usually empty: a reset only appears once its window is under pressure, so
    most of the time the header is just the account name. Account labels come
    from config and can be any length, so this measures and degrades rather than
    trusting a layout that happened to fit the two names in use today.
    """
    font = theme.font(theme.SIZE_SMALL, theme.WEIGHT_SMALL)

    def fits(text: str) -> bool:
        return MARGIN + label_w + 8 + draw.textlength(text, font=font) <= BAR_RIGHT

    primary = describe_reset(five, "5h", now, threshold)
    weekly = describe_reset(seven, "7d", now, threshold)

    # When both windows are under pressure but only one will fit, the fuller one
    # wins — that is the limit you are closer to actually hitting.
    ordered = sorted(
        [(five.percent or 0, primary), (seven.percent or 0, weekly)],
        key=lambda pair: pair[0],
        reverse=True,
    )
    candidates = []
    if primary and weekly:
        candidates.append(f"{primary} · {weekly}")
    candidates.extend(text for _, text in ordered if text)
    return next((text for text in candidates if fits(text)), None)


def describe_age(seconds: float | None, stale_after: int) -> str | None:
    """Spelled out, because a grayed-out number does not announce that it is old."""
    if seconds is None or seconds < stale_after:
        return None
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m old"
    hours = minutes // 60
    return f"{hours}h old" if hours < 24 else f"{hours // 24}d old"


def pace(window: Window, span_s: float, now: datetime) -> float | None:
    """Where usage would sit right now if the window were being spent evenly.

    Fill beyond this point means the current burn rate does not last to the
    reset. Returns None when there is no reset time to measure the window from.
    """
    if window.resets_at is None or window.reset_passed(now):
        return None
    elapsed = span_s - (window.resets_at - now).total_seconds()
    if elapsed <= 0 or elapsed >= span_s:
        return None
    return 100 * elapsed / span_s


def _mix(a, b, t: float):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _render_bar(
    width: int, height: int, percent: float | None, color, pace_percent: float | None
) -> Image.Image:
    """One bar, drawn as a pressure gauge rather than a plain progress bar.

    The gauge only says something when there is something to say. Under pace,
    the bar is an ordinary solid fill — no marker, no shading for the headroom
    you have not spent, because "you are fine" does not need its own graphic.

    Once usage passes the pace point the bar splits: the on-pace portion mutes
    and the excess stays at full intensity, so the part that is running hot is
    the part that draws the eye. The muted tint still clears 3:1 against the
    track interior, so the fill's extent — the thing that actually encodes the
    value — never depends on perceiving the split.
    """
    bar = Image.new("RGB", (width, height), theme.BG)
    draw = ImageDraw.Draw(bar)
    radius = height // 2
    right = width - 1

    draw.rounded_rectangle(
        [0, 0, right, height - 1],
        radius=radius,
        fill=theme.TRACK_FILL,
        outline=theme.TRACK_EDGE,
        width=1,
    )

    # The bar splits only once usage clears pace by the deadband, but it splits
    # *at* the pace point — the margin decides whether to show the split, not
    # where it sits.
    show_split = (
        pace_percent is not None
        and percent is not None
        and percent > pace_percent + PACE_DEADBAND
    )
    pace_x = int(right * min(pace_percent, 100) / 100) if show_split else None

    filled_to = 0
    if percent is not None and percent > 0:
        # Any nonzero usage gets at least a round nub; a sliver that rounded away
        # would read as "nothing used yet".
        filled_to = max(int(right * min(percent, 100) / 100), height)

    if filled_to:
        over_pace = pace_x is not None and filled_to > pace_x
        layer = Image.new(
            "RGB", (width, height), _mix(color, theme.TRACK_FILL, PACE_TINT) if over_pace else color
        )
        if over_pace:
            ImageDraw.Draw(layer).rectangle([pace_x, 0, width, height], fill=color)
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, filled_to, height - 1], radius=radius, fill=255)
        bar.paste(layer, (0, 0), mask)

    return bar


def _draw_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    panel_y: int,
    row: Row,
    config: Config,
    geo: Geometry,
    hero: bool,
) -> None:
    color = theme.level_color(row.percent, config.warn_at, config.alert_at)
    number_font = theme.font(row.size, theme.WEIGHT_NUMBER)
    small = theme.font(theme.SIZE_SMALL, theme.WEIGHT_SMALL)

    y = panel_y + (geo.hero_dy if hero else geo.secondary_dy)
    baseline = y + row.size
    draw.text((TAG_X, baseline), row.tag, font=small, fill=theme.MUTED, anchor="ls")

    text = "--" if row.percent is None else f"{round(row.percent):d}"
    unit = "" if row.percent is None else "%"
    unit_w = draw.textlength(unit, font=small)
    draw.text(
        (geo.number_right - unit_w, baseline),
        text,
        font=number_font,
        fill=theme.TEXT if row.percent is not None else theme.DEAD,
        anchor="rs",
    )
    if unit:
        draw.text(
            (geo.number_right - unit_w + 1, baseline), unit, font=small, fill=theme.MUTED, anchor="ls"
        )

    bar = _render_bar(BAR_RIGHT - geo.bar_left, row.bar_h, row.percent, color, row.pace)
    image.paste(bar, (geo.bar_left, geo.bar_top(panel_y, hero)))


def _draw_panel(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    y: int,
    usage: AccountUsage,
    config: Config,
    now: datetime,
    geo: Geometry,
) -> None:
    label_font = theme.font(theme.SIZE_LABEL, theme.WEIGHT_LABEL)
    draw.text((MARGIN, y), usage.label, font=label_font, fill=theme.LABEL)
    label_w = draw.textlength(usage.label, font=label_font)

    five, seven = usage.five_hour, usage.seven_day
    status = (
        usage.error
        or describe_age(usage.age_s(now), config.stale_after_s)
        or _reset_status(draw, label_w, five, seven, now, config.warn_at)
    )
    if status:
        small = theme.font(theme.SIZE_SMALL, theme.WEIGHT_SMALL)
        draw.text(
            (BAR_RIGHT - draw.textlength(status, font=small), y + 2), status, font=small, fill=theme.MUTED
        )

    # A window whose reset time has passed has rolled over, so the number we hold
    # describes a window that no longer exists: unknown, not low.
    _draw_row(
        image,
        draw,
        y,
        Row(
            "5H",
            None if five.reset_passed(now) else five.percent,
            geo.hero_size,
            geo.hero_bar_h,
            pace(five, FIVE_HOUR_S, now),
        ),
        config,
        geo,
        hero=True,
    )
    _draw_row(
        image,
        draw,
        y,
        Row(
            "7D",
            None if seven.reset_passed(now) else seven.percent,
            geo.secondary_size,
            geo.secondary_bar_h,
            pace(seven, SEVEN_DAY_S, now),
        ),
        config,
        geo,
        hero=False,
    )


def _format_cost(value: float) -> str:
    return f"${value:,.0f}" if value >= 10 else f"${value:.2f}"


def render(usages: list[AccountUsage], config: Config, now: datetime | None = None) -> Image.Image:
    now = now or datetime.now(UTC)
    geo = geometry(len(usages))
    image = Image.new("RGB", (W, H), theme.BG)
    draw = ImageDraw.Draw(image)

    for panel_y, usage in zip(geo.panel_ys, usages):
        _draw_panel(image, draw, panel_y, usage, config, now, geo)

    footer = theme.font(theme.SIZE_FOOTER, theme.WEIGHT_SMALL)
    draw.text(
        (MARGIN, FOOTER_Y), clock(now, FOOTER_CLOCK_QUANTIZE_MIN), font=footer, fill=theme.FOOTER
    )

    # A lone account's cost needs no initial to say whose it is.
    tagged = len(usages) > 1
    costs = [
        (f"{u.label[0]} " if tagged else "") + _format_cost(u.cost_today)
        for u in usages
        if u.cost_today is not None
    ]
    if costs:
        text = "  ".join(costs)
        draw.text(
            (BAR_RIGHT - draw.textlength(text, font=footer), FOOTER_Y),
            text,
            font=footer,
            fill=theme.FOOTER,
        )

    return image


def save_jpeg(image: Image.Image, path, quality: int = 85) -> bytes:
    image.save(path, "JPEG", quality=quality, optimize=True)
    with open(path, "rb") as handle:
        return handle.read()
