#!/usr/bin/env python3
"""Draw the site's mark as real icon files.

Google's results, Claude's connector list and the phone home screen all fetch
an icon by URL, so the mark lives here as files rather than inline.

The mark is a heavy italic D, white on the Daash red, slanted like the
"Daash" wordmark. It is drawn as a shape, not set as a letter, so it looks the
same on every device instead of taking whatever font is there. The same files
go to daash.run (its app/icon.svg, apple-icon.png and favicon.ico), which is
where Claude takes the icon it shows beside each Daash answer. Needs Pillow.
Run it when the mark changes.

    python3 scripts/make-favicon.py docs/
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

RED = (224, 49, 42)
WHITE = (255, 255, 255)

# In a 32-unit box: a D with its stem at 9.5 and its bowl round the centre,
# then slanted 12 degrees and enlarged so it fills the tile without crowding
# its edges, and nudged left so the slant sits in the middle.
BOX = 32
RADIUS = 7
SLANT = 12
SCALE = 1.25
NUDGE = -1.3
OUTER = (9.5, 7, 9)  # stem x, top y, bowl radius
INNER = (14, 11.3, 4.7)

PATH = (
    "M9.5 7 H16 A9 9 0 0 1 16 25 H9.5 Z "
    "M14 11.3 H16 A4.7 4.7 0 0 1 16 20.7 H14 Z"
)
SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX} {BOX}">
<rect width="{BOX}" height="{BOX}" rx="{RADIUS}" fill="rgb{RED}"/>
<path fill="rgb{WHITE}" fill-rule="evenodd" transform="translate(16 16) skewX(-{SLANT}) \
scale({SCALE}) translate(-16 -16) translate({NUDGE} 0)" d="{PATH}"/>
</svg>
"""


def _place(x: float, y: float) -> tuple[float, float]:
    """A point of the upright D where the SVG's transform puts it."""
    x, y = (x + NUDGE - 16) * SCALE, (y - 16) * SCALE
    return 16 + x - math.tan(math.radians(SLANT)) * y, 16 + y


def _outline(stem: float, top: float, radius: float) -> list[tuple[float, float]]:
    """The D's edge, its bowl sampled finely enough to stay round at 512 px."""
    points = [(stem, top), (16, top)]
    for i in range(1, 64):
        a = -math.pi / 2 + math.pi * i / 64
        points.append((16 + radius * math.cos(a), 16 + radius * math.sin(a)))
    points += [(16, 32 - top), (stem, 32 - top)]
    return [_place(x, y) for x, y in points]


def render(size: int, rounded: bool = True) -> Image.Image:
    """The mark at `size` px, drawn oversized and shrunk for clean edges."""
    over = 4
    px = size * over
    scale = px / BOX
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if rounded:
        draw.rounded_rectangle((0, 0, px - 1, px - 1), radius=RADIUS * scale, fill=RED)
    else:
        draw.rectangle((0, 0, px - 1, px - 1), fill=RED)
    draw.polygon([(x * scale, y * scale) for x, y in _outline(*OUTER)], fill=WHITE)
    draw.polygon([(x * scale, y * scale) for x, y in _outline(*INNER)], fill=RED)
    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    (out / "favicon.svg").write_text(SVG)
    render(512).save(out / "icon-512.png", optimize=True)
    # Home-screen icons get their corners rounded by the phone, so this one is
    # drawn square to the edge: a rounded square inside a rounded square looks
    # like a mistake.
    render(180, rounded=False).convert("RGB").save(out / "apple-touch-icon.png", optimize=True)
    render(48).save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

    for name in ("favicon.svg", "favicon.ico", "apple-touch-icon.png", "icon-512.png"):
        print(f"wrote {out / name} ({(out / name).stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
