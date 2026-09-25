#!/usr/bin/env python3
"""Draw the link-preview image, matching the page's climbing profile.

Needs Pillow and the two fonts the page uses; both are fetched by this script's
caller. Run it when the page's look changes, then commit docs/og.png.

    python3 scripts/make-og-image.py fonts/ docs/og.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630

GROUND = (244, 246, 243)
INK = (23, 33, 27)
MUTED = (85, 101, 91)
ACCENT = (47, 107, 79)
ACCENT_INK = (35, 83, 64)
CLAY = (188, 90, 42)
HAIRLINE = (211, 219, 211)

# The same ascending profile as the page hero, scaled to this canvas.
PROFILE = [
    (0, 60), (60, 56), (120, 52), (180, 54), (240, 44), (300, 40), (360, 36),
    (420, 38), (480, 28), (540, 24), (600, 20), (660, 18), (720, 13),
]
DOTS = [(120, 52), (360, 36), (600, 20)]


def archivo(fonts: Path, size: int, weight: float) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(fonts / "Archivo.ttf"), size)
    # The axis order in this font is [Weight, Width] — passing them the other
    # way round silently renders Regular, which looks like a missing font.
    font.set_variation_by_axes([weight, 100.0])
    return font


def tracked(draw: ImageDraw.ImageDraw, xy, text, font, fill, spacing) -> None:
    """Letter-spaced text; Pillow has no tracking of its own."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + spacing


def main() -> int:
    fonts = Path(sys.argv[1])
    out = Path(sys.argv[2])

    image = Image.new("RGB", (W, H), GROUND)
    draw = ImageDraw.Draw(image)

    # -- the profile, occupying the lower third ---------------------------
    left, right = 80, W - 80
    top, base = 430, 540
    span_x, span_y = right - left, base - top

    def place(px: float, py: float) -> tuple[float, float]:
        return left + px / 720 * span_x, base - (70 - py) / 57 * span_y

    points = [place(*p) for p in PROFILE]

    # Area fill, drawn as a translucent overlay so it tints rather than covers.
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).polygon(
        points + [(points[-1][0], base), (points[0][0], base)],
        fill=(*ACCENT, 26),
    )
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)

    draw.line([(left, base), (right, base)], fill=HAIRLINE, width=2)
    draw.line(points, fill=ACCENT, width=5, joint="curve")

    for index, dot in enumerate(DOTS):
        x, y = place(*dot)
        colour = CLAY if index == len(DOTS) - 1 else ACCENT
        draw.ellipse([x - 16, y - 16, x + 16, y + 16], fill=GROUND)
        draw.ellipse([x - 11, y - 11, x + 11, y + 11], fill=colour)

    # -- type -------------------------------------------------------------
    kicker = ImageFont.truetype(str(fonts / "PlexMono.ttf"), 22)
    tracked(draw, (80, 88), "CLAUDE + GARMIN CONNECT", kicker, ACCENT_INK, 3.2)

    draw.text((80, 138), "Talk to your", font=archivo(fonts, 104, 800), fill=INK)
    draw.text((80, 236), "training", font=archivo(fonts, 104, 800), fill=INK)

    sub = archivo(fonts, 32, 400)
    draw.text(
        (80, 352),
        "Read your runs. Send the next session to your watch.",
        font=sub,
        fill=MUTED,
    )

    url = ImageFont.truetype(str(fonts / "PlexMono.ttf"), 26)
    draw.text((80, base + 44), "garmin.daash.run", font=url, fill=ACCENT_INK)

    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {W}x{H})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
