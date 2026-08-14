"""Corner-marker calibration frame.

Settles two things the sources disagree on: the panel's true pixel size and its
orientation. Anything cropped, letterboxed, or rotated shows up immediately.
"""

import sys

from PIL import Image, ImageDraw

W = H = int(sys.argv[1]) if len(sys.argv) > 1 else 240
out = sys.argv[2] if len(sys.argv) > 2 else "out/testframe.jpg"

img = Image.new("RGB", (W, H), (0, 0, 0))
d = ImageDraw.Draw(img)

# Outermost ring of pixels: if any edge is missing, the panel is cropping.
d.rectangle([0, 0, W - 1, H - 1], outline=(255, 255, 255))
d.rectangle([1, 1, W - 2, H - 2], outline=(255, 0, 0))

# Asymmetric corner blocks — distinct colors make rotation and mirroring obvious.
corners = [
    (0, 0, "TL", (0, 255, 0)),
    (W - 40, 0, "TR", (0, 128, 255)),
    (0, H - 40, "BL", (255, 255, 0)),
    (W - 40, H - 40, "BR", (255, 0, 255)),
]
for x, y, label, color in corners:
    d.rectangle([x, y, x + 39, y + 39], fill=color)
    d.text((x + 8, y + 14), label, fill=(0, 0, 0))

d.line([W // 2, 0, W // 2, H], fill=(80, 80, 80))
d.line([0, H // 2, W, H // 2], fill=(80, 80, 80))

# Ticks every 20px along the top edge, taller every 100px, so a scaled image is measurable.
for x in range(0, W, 20):
    d.line([x, 0, x, 12 if x % 100 == 0 else 6], fill=(255, 255, 255))

d.text((W // 2 - 26, H // 2 - 28), f"{W}x{H}", fill=(255, 255, 255))

# Type-size ladder: the smallest line still readable sets the real floor for the layout.
for i, size in enumerate([8, 10, 12, 14, 16, 18]):
    d.text((52, 96 + i * 12), f"{size}px sample 88%", fill=(220, 220, 220))

img.save(out, "JPEG", quality=90)
print(f"{out} {img.size}")
