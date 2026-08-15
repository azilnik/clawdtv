"""Render every display state to one reviewable sheet.

Produces three artifacts:

  out/contactsheet.png    every state at 1:1, which is the size it will actually
                          be on the panel
  out/contactsheet@2x.png the same states enlarged, for judging type and spacing
  out/colorblind.png      one representative state under simulated deuteranopia,
                          protanopia and tritanopia

The 1:1 sheet is the one that matters. Reviewing a 240px design at 2x flatters
it; the panel does not.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from clawdtv import config, demo, render, theme  # noqa: E402

CAPTION_H = 22
GAP = 14
COLS = 4
CAPTION_FONT = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", 15)
SHEET_BG = (26, 26, 30)
CAPTION_FG = (225, 225, 232)

# Viénot, Brettel & Mollon (1999) dichromat simulation, applied in linear RGB.
MATRICES = {
    "deuteranopia": ((0.625, 0.375, 0.0), (0.7, 0.3, 0.0), (0.0, 0.3, 0.7)),
    "protanopia": ((0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758)),
    "tritanopia": ((0.95, 0.05, 0.0), (0.0, 0.433, 0.567), (0.0, 0.475, 0.525)),
}


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(c: float) -> float:
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def simulate(image: Image.Image, kind: str) -> Image.Image:
    matrix = MATRICES[kind]
    out = Image.new("RGB", image.size)
    source, dest = image.load(), out.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b = (_to_linear(v / 255) for v in source[x, y][:3])
            channels = tuple(sum(m * v for m, v in zip(row, (r, g, b))) for row in matrix)
            dest[x, y] = tuple(
                max(0, min(255, round(_from_linear(max(0.0, min(1.0, c))) * 255))) for c in channels
            )
    return out


def _tile(image: Image.Image, caption: str, scale: int) -> Image.Image:
    width = image.width * scale
    tile = Image.new("RGB", (width, image.height * scale + CAPTION_H), SHEET_BG)
    tile.paste(image.resize((width, image.height * scale), Image.NEAREST), (0, 0))
    ImageDraw.Draw(tile).text((2, image.height * scale + 4), caption, font=CAPTION_FONT, fill=CAPTION_FG)
    return tile


def sheet(images: list[tuple[str, Image.Image]], scale: int, cols: int = COLS) -> Image.Image:
    tiles = [_tile(image, caption, scale) for caption, image in images]
    tile_w, tile_h = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    canvas = Image.new(
        "RGB", (cols * tile_w + (cols + 1) * GAP, rows * tile_h + (rows + 1) * GAP), SHEET_BG
    )
    for index, tile in enumerate(tiles):
        col, row = index % cols, index // cols
        canvas.paste(tile, (GAP + col * (tile_w + GAP), GAP + row * (tile_h + GAP)))
    return canvas


def main() -> None:
    cfg = config.load()
    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)

    frames = [(name, render.render(usages, cfg, demo.NOW)) for name, usages in demo.states().items()]

    sheet(frames, scale=1).save(out / "contactsheet.png")
    sheet(frames, scale=2, cols=3).save(out / "contactsheet@2x.png")

    reference = dict(frames)["alert boundary (85%)"]
    variants = [("normal vision", reference)] + [
        (kind, simulate(reference, kind)) for kind in MATRICES
    ]
    sheet(variants, scale=2, cols=4).save(out / "colorblind.png")

    print(f"{len(frames)} states -> contactsheet.png, contactsheet@2x.png, colorblind.png")
    print(f"palette floor: {theme.MIN_SIZE}px")


if __name__ == "__main__":
    main()
