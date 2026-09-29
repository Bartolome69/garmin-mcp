#!/usr/bin/env python3
"""Draw the site's mark as real icon files.

The page used to carry its favicon as an inline data URI. That works in a
browser tab and nowhere else: Google's results, Claude's connector list and
the phone home screen all fetch an icon by URL, and none of them found one.

Same mark as the accent: a route climbing over three waypoints, on the green
the site uses everywhere. Needs Pillow. Run it when the mark changes.

    python3 scripts/make-favicon.py docs/
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (47, 107, 79)
GROUND = (244, 246, 243)

# In the 32-unit box the inline SVG used, so the two stay identical.
BOX = 32
RADIUS = 7
ROUTE = [(6, 22), (12.5, 14), (18, 18.5), (26, 8.5)]
STROKE = 3.4

SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX} {BOX}">
<rect width="{BOX}" height="{BOX}" rx="{RADIUS}" fill="rgb{ACCENT}"/>
<path d="M{' L'.join(f'{x} {y}' for x, y in ROUTE)}" fill="none" stroke="rgb{GROUND}" \
stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def render(size: int) -> Image.Image:
    """The mark at `size` px, drawn oversized and shrunk for clean edges."""
    over = 4
    px = size * over
    scale = px / BOX
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, px - 1, px - 1), radius=RADIUS * scale, fill=ACCENT)
    points = [(x * scale, y * scale) for x, y in ROUTE]
    width = STROKE * scale
    draw.line(points, fill=GROUND, width=round(width), joint="curve")
    for x, y in (points[0], points[-1]):  # round caps
        r = width / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=GROUND)
    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    (out / "favicon.svg").write_text(SVG)
    render(512).save(out / "icon-512.png", optimize=True)
    # Home-screen icons get their corners rounded by the phone, so this one is
    # drawn square to the edge: a rounded square inside a rounded square looks
    # like a mistake.
    touch = Image.new("RGB", (180, 180), ACCENT)
    touch.paste(render(180), (0, 0), render(180))
    touch.save(out / "apple-touch-icon.png", optimize=True)
    render(48).save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

    for name in ("favicon.svg", "favicon.ico", "apple-touch-icon.png", "icon-512.png"):
        print(f"wrote {out / name} ({(out / name).stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
